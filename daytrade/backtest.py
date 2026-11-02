"""Backtest vela a vela, con comisiones, deslizamiento y límites diarios.

Supuestos conservadores:
- La señal se calcula al cierre de una vela y la compra se ejecuta en la apertura de la siguiente.
- Si en una misma vela se tocan el stop y la toma de ganancia, se asume que tocó primero el stop.
- Si el precio abre por debajo del stop (salto), se vende al precio de apertura.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .risk import DayGuard, position_size
from .strategies import add_indicators, entry_signal, exit_signal, levels


@dataclass
class BTResult:
    trades: pd.DataFrame
    equity: pd.Series
    daily: pd.Series
    initial: float
    buy_hold_pct: float

    def summary(self, target_usd: float | None = 9.0) -> dict:
        t, d = self.trades, self.daily
        wins = t[t.pnl > 0] if len(t) else t
        losses = t[t.pnl <= 0] if len(t) else t
        dd = (self.equity / self.equity.cummax() - 1).min() if len(self.equity) else 0.0
        pf = wins.pnl.sum() / -losses.pnl.sum() if len(losses) and losses.pnl.sum() < 0 else float("inf")
        out = {
            "capital_inicial_usd": self.initial,
            "capital_final_usd": round(self.equity.iloc[-1], 2),
            "rendimiento_total_%": round(100 * (self.equity.iloc[-1] / self.initial - 1), 2),
            "comprar_y_mantener_%": round(100 * self.buy_hold_pct, 2),
            "dias": len(d),
            "ganancia_promedio_por_dia_usd": round(d.mean(), 3) if len(d) else 0,
            "dias_positivos_%": round(100 * (d > 0).mean(), 1) if len(d) else 0,
            "dias_negativos_%": round(100 * (d < 0).mean(), 1) if len(d) else 0,
            "mejor_dia_usd": round(d.max(), 2) if len(d) else 0,
            "peor_dia_usd": round(d.min(), 2) if len(d) else 0,
            "operaciones": len(t),
            "aciertos_%": round(100 * len(wins) / len(t), 1) if len(t) else 0,
            "factor_de_beneficio": round(pf, 2) if np.isfinite(pf) else "∞",
            "comisiones_usd": round(t.fees.sum(), 2) if len(t) else 0,
            "peor_caida_%": round(100 * dd, 2),
        }
        if target_usd:
            out[f"dias_con_{target_usd:g}_usd_o_mas_%"] = round(100 * (d >= target_usd).mean(), 1) if len(d) else 0
        return out


def run(df: pd.DataFrame, cfg: dict) -> BTResult:
    p, risk, costs = cfg["params"], cfg["risk"], cfg["costs"]
    fee, slip, strat = costs["fee_pct"], costs["slippage_pct"], cfg["strategy"]
    df = add_indicators(df, p)
    cash = float(cfg["initial_usd"])
    guard = DayGuard()
    pos = None  # dict(qty, entry, stop, tp, bars, fee_in, time)
    pending_entry = None
    pending_exit = False
    trades, eq = [], []
    rows = df.to_dict("records")
    idx = df.index

    def close_pos(i, price, reason):
        nonlocal cash, pos
        px = price * (1 - slip)
        gross = pos["qty"] * px
        f = gross * fee
        cash += gross - f
        pnl = gross - f - pos["cost"]
        guard.closed(pnl)
        trades.append({"entrada": pos["time"], "salida": idx[i], "precio_entrada": pos["entry"], "precio_salida": px,
                       "usd": pos["cost"], "pnl": pnl, "fees": pos["fee_in"] + f, "motivo": reason})
        pos = None

    for i, r in enumerate(rows):
        day = idx[i].strftime("%Y-%m-%d")
        mark = cash + (pos["qty"] * r["open"] if pos else 0.0)
        guard.roll(day, mark)

        # 1) Ejecutar en la apertura lo decidido al cierre anterior
        if pos and pending_exit:
            close_pos(i, r["open"], "señal")
        pending_exit = False
        if pending_entry and not pos:
            ok, _ = guard.can_open(risk)
            atr_v = pending_entry
            px = r["open"] * (1 + slip)
            stop, tp = levels(px, atr_v, p)
            equity = cash
            usd = position_size(equity, cash / (1 + fee), px, stop, risk) if ok else 0
            if usd > 0:
                f = usd * fee
                cash -= usd + f
                pos = {"qty": usd / px, "entry": px, "stop": stop, "tp": tp, "bars": 0,
                       "fee_in": f, "cost": usd + f, "time": idx[i]}
                guard.opened()
        pending_entry = None

        # 2) Stop / toma de ganancia dentro de la vela
        if pos:
            if r["open"] <= pos["stop"]:
                close_pos(i, r["open"], "stop (salto)")
            elif r["low"] <= pos["stop"]:
                close_pos(i, pos["stop"], "stop")
            elif r["high"] >= pos["tp"]:
                close_pos(i, pos["tp"], "toma de ganancia")

        # 3) Al cierre: tiempo máximo, señal de salida o de entrada
        if pos:
            pos["bars"] += 1
            if pos["bars"] >= p["max_hold_bars"]:
                close_pos(i, r["close"], "tiempo máximo")
            elif exit_signal(r, strat, p):
                pending_exit = True
        elif i + 1 < len(rows) and entry_signal(r, strat, p):
            pending_entry = r["atr"]

        eq.append(cash + (pos["qty"] * r["close"] if pos else 0.0))

    if pos:
        close_pos(len(rows) - 1, rows[-1]["close"], "fin del backtest")
        eq[-1] = cash

    equity = pd.Series(eq, idx)
    tdf = pd.DataFrame(trades, columns=["entrada", "salida", "precio_entrada", "precio_salida", "usd", "pnl", "fees", "motivo"])
    # Ganancia/pérdida por día (incluye días sin operaciones)
    days = pd.Series(0.0, index=pd.Index(sorted({t.strftime("%Y-%m-%d") for t in idx})))
    if len(tdf):
        by_day = tdf.groupby(tdf.salida.dt.strftime("%Y-%m-%d")).pnl.sum()
        days.loc[by_day.index] = by_day.values
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    return BTResult(tdf, equity, days, float(cfg["initial_usd"]), bh)


def synthetic_candles(days: int = 120, tf_minutes: int = 15, seed: int = 0) -> pd.DataFrame:
    """Velas inventadas, para tests y demos sin internet. No sirven para sacar conclusiones."""
    rng = np.random.default_rng(seed)
    n = days * 24 * 60 // tf_minutes
    rets = rng.normal(0.00002, 0.004, n)
    close = 50000 * np.exp(np.cumsum(rets))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.abs(rng.normal(0, 0.002, n)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    idx = pd.date_range("2025-01-01", periods=n, freq=f"{tf_minutes}min", tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1.0}, index=idx)
