"""Búsqueda de estrategias con validación fuera de muestra.

Para cada moneda y cada marco de tiempo:
1. Se prueban todas las combinaciones de parámetros en el período de ENTRENAMIENTO (los primeros 2/3).
2. Las mismas combinaciones se prueban en el período de PRUEBA (el último 1/3), que no se usó para elegir.
3. Una combinación es "candidata" solo si gana en los dos períodos, con suficientes operaciones.

Si algo funciona solo en entrenamiento, fue casualidad (sobreajuste) y no se debe usar.
"""
import copy
import itertools
import math
import time

import pandas as pd

from .backtest import run

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
TIMEFRAMES = ["1h", "4h"]

COMMON = {
    "ema_trend": [50, 200],
    "stop_atr": [1.5, 3.0],
    "take_profit_atr": [3.0, 6.0],
    "max_hold_bars": [24, 72],
}
SPECIFIC = {
    "reversion": {"rsi_entry": [25, 35], "rsi_exit": [50, 65]},
    "breakout": {"breakout_bars": [20, 50]},
    "momentum": {"ema_fast,ema_slow": [(10, 30), (20, 50)]},
}

MIN_TRADES_TRAIN, MIN_TRADES_TEST = 30, 15
MIN_PF_TRAIN, MIN_PF_TEST = 1.2, 1.1


def combos():
    for strat, spec in SPECIFIC.items():
        grid = {**COMMON, **spec}
        keys = list(grid)
        for values in itertools.product(*grid.values()):
            params = {}
            for k, v in zip(keys, values):
                if "," in k:
                    params.update(dict(zip(k.split(","), v)))
                else:
                    params[k] = v
            yield strat, params


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    # descartar la última vela si está incompleta
    if len(df) and out.index[-1] + pd.Timedelta(rule) > df.index[-1] + (df.index[1] - df.index[0]):
        out = out.iloc[:-1]
    return out


def metrics(r) -> dict:
    t = r.trades
    gains = t.pnl[t.pnl > 0].sum() if len(t) else 0.0
    losses = -t.pnl[t.pnl <= 0].sum() if len(t) else 0.0
    pf = gains / losses if losses > 0 else (math.inf if gains > 0 else 0.0)
    eq = r.equity
    return {
        "rend_%": round(100 * (eq.iloc[-1] / r.initial - 1), 1),
        "pf": round(pf, 2) if math.isfinite(pf) else 99.0,
        "ops": len(t),
        "caida_%": round(100 * (eq / eq.cummax() - 1).min(), 1),
        "usd_dia": round(r.daily.mean(), 3) if len(r.daily) else 0.0,
        "byh_%": round(100 * r.buy_hold_pct, 1),
    }


def evaluate(df: pd.DataFrame, base_cfg: dict, symbol: str, tf: str, split: pd.Timestamp, progress=None) -> list[dict]:
    rows = []
    train_df = df[df.index < split]
    isplit = int(df.index.searchsorted(split))
    test_df = df.iloc[max(0, isplit - 600):]  # 600 velas previas alcanzan para calentar los indicadores
    for strat, params in combos():
        cfg = copy.deepcopy(base_cfg)
        cfg["strategy"] = strat
        cfg["params"].update(params)
        tr = metrics(run(train_df, cfg))
        te = metrics(run(test_df, cfg, start_at=split))
        row = {"simbolo": symbol, "tf": tf, "estrategia": strat, **params}
        row.update({f"ent_{k}": v for k, v in tr.items()})
        row.update({f"prueba_{k}": v for k, v in te.items()})
        row["candidata"] = (tr["ops"] >= MIN_TRADES_TRAIN and tr["pf"] >= MIN_PF_TRAIN and tr["rend_%"] > 0
                            and te["ops"] >= MIN_TRADES_TEST and te["pf"] >= MIN_PF_TEST and te["rend_%"] > 0)
        rows.append(row)
        if progress:
            progress()
    return rows


def research(base_cfg: dict, data: dict[tuple[str, str], pd.DataFrame], train_frac: float = 2 / 3) -> pd.DataFrame:
    total = len(data) * sum(1 for _ in combos())
    done, t0 = [0], time.time()

    def tick():
        done[0] += 1
        if done[0] % 25 == 0 or done[0] == total:
            el = time.time() - t0
            print(f"  {done[0]}/{total} pruebas ({el / done[0] * (total - done[0]) / 60:.1f} min restantes)", flush=True)

    out = []
    for (symbol, tf), df in data.items():
        split = df.index[int(len(df) * train_frac)]
        out += evaluate(df, base_cfg, symbol, tf, split, tick)
    return pd.DataFrame(out)


def verdict(res: pd.DataFrame) -> str:
    lines = []
    good_train = res[(res.ent_pf >= MIN_PF_TRAIN) & (res.ent_ops >= MIN_TRADES_TRAIN) & (res["ent_rend_%"] > 0)]
    still = good_train[(good_train.prueba_pf >= 1.0) & (good_train["prueba_rend_%"] > 0)]
    lines.append(f"Combinaciones probadas: {len(res)}")
    lines.append(f"Rentables en entrenamiento: {len(good_train)}")
    lines.append(f"...de esas, siguieron ganando en el período de prueba: {len(still)}"
                 + (f" ({100 * len(still) / len(good_train):.0f} %)" if len(good_train) else ""))
    cand = res[res.candidata].sort_values("prueba_pf", ascending=False)
    lines.append(f"Candidatas (ganan en los dos períodos con suficientes operaciones): {len(cand)}")
    if len(cand):
        lines.append("Ojo: con cientos de combinaciones, algunas pasan el filtro de pura suerte (en una prueba "
                     "con precios aleatorios aparecieron 5 de 256). Una candidata todavía tiene que demostrar "
                     "que gana en modo práctica durante semanas.")
    if len(good_train) and len(still) / len(good_train) < 0.5:
        lines.append("Menos de la mitad de lo que funcionaba en el pasado siguió funcionando: "
                     "mucho de lo que 'gana' es casualidad.")
    return "\n".join(lines)
