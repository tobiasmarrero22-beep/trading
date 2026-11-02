"""Bot de trading diario (cripto).

    python daytrade.py backtest                  # prueba con los últimos 365 días de velas reales
    python daytrade.py backtest --days 90 --synthetic
    python daytrade.py run                       # opera en el modo de daytrade.yaml (Ctrl+C para frenar)
    python daytrade.py run --once                # una sola pasada (para cron)
    python daytrade.py report                    # ganancias y pérdidas por día, según el registro
    python daytrade.py check                     # prueba la conexión y los permisos de la clave
    python daytrade.py analizar                  # qué ve el bot en el mercado ahora y si operaría
    python daytrade.py research                  # prueba cientos de variantes con validación fuera de muestra
"""
import argparse
import time

import pandas as pd
import yaml

from bot.config import ROOT
from bot.runner import load_env
from daytrade import backtest as bt
from daytrade.engine import Engine, check_live_allowed
from daytrade.exchange import CcxtExchange, PaperExchange, fetch_history

MODES = {"paper_local", "binance_testnet", "binance_live"}


def load(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if cfg["mode"] not in MODES:
        raise SystemExit(f"mode debe ser uno de {sorted(MODES)}")
    return cfg


def cmd_backtest(cfg, a):
    if a.synthetic:
        df = bt.synthetic_candles(a.days)
    else:
        cache = ROOT / "data" / "cache" / f"{cfg['symbol'].replace('/', '')}_{cfg['timeframe']}_{a.days}d.csv"
        if cache.exists() and not a.refresh:
            df = pd.read_csv(cache, index_col=0, parse_dates=True)
        else:
            print("Bajando velas de Binance...")
            df = fetch_history(cfg["symbol"], cfg["timeframe"], a.days)
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache)
    target = cfg["risk"].get("daily_profit_target_usd") or 9
    cols = {}
    for s in ["reversion", "breakout"] if a.strategy == "ambas" else [a.strategy]:
        c = dict(cfg, strategy=s)
        r = bt.run(df, c)
        cols[s] = {k: (float(v) if hasattr(v, "item") else v) for k, v in r.summary(target).items()}
        if a.trades:
            r.trades.to_csv(ROOT / f"backtest_trades_{s}.csv", index=False)
    print(f"\n{cfg['symbol']} {cfg['timeframe']}  {df.index[0]:%Y-%m-%d} a {df.index[-1]:%Y-%m-%d}"
          f"  ({'DATOS SINTÉTICOS: no sirven para decidir' if a.synthetic else 'datos reales'})\n")
    print(pd.DataFrame(cols).to_string())
    print("\nRendimientos pasados no garantizan rendimientos futuros.")


def cmd_research(cfg, a):
    from daytrade import research as rs
    data = {}
    for sym in a.symbols.split(","):
        cache = ROOT / "data" / "cache" / f"{sym.replace('/', '')}_1h_{a.days}d.csv"
        if cache.exists() and not a.refresh:
            h1 = pd.read_csv(cache, index_col=0, parse_dates=True)
        else:
            print(f"Bajando {a.days} días de velas de 1 hora de {sym}...", flush=True)
            h1 = fetch_history(sym, "1h", a.days)
            cache.parent.mkdir(parents=True, exist_ok=True)
            h1.to_csv(cache)
        data[(sym, "1h")] = h1
        data[(sym, "4h")] = rs.resample(h1, "4h")
    first = next(iter(data.values()))
    split = first.index[int(len(first) * 2 / 3)]
    print(f"\nEntrenamiento: {first.index[0]:%Y-%m-%d} a {split:%Y-%m-%d} | "
          f"Prueba: {split:%Y-%m-%d} a {first.index[-1]:%Y-%m-%d}\n", flush=True)
    res = rs.research(cfg, data)
    out = ROOT / "research_resultados.csv"
    res.to_csv(out, index=False)

    cols = ["simbolo", "tf", "estrategia", "ent_rend_%", "ent_pf", "ent_ops",
            "prueba_rend_%", "prueba_pf", "prueba_ops", "prueba_byh_%", "prueba_usd_dia", "prueba_caida_%"]
    print("\nLa mejor de cada grupo SEGÚN ENTRENAMIENTO, y cómo le fue después en la prueba:")
    best = (res[res.ent_ops >= rs.MIN_TRADES_TRAIN].sort_values("ent_pf", ascending=False)
            .groupby(["simbolo", "tf", "estrategia"]).head(1).sort_values(["simbolo", "tf", "estrategia"]))
    print(best[cols].to_string(index=False))
    print("\n" + rs.verdict(res))
    cand = res[res.candidata].sort_values("prueba_pf", ascending=False)
    if len(cand):
        print("\nCandidatas (para probar en modo práctica, NO con plata real todavía):")
        param_cols = [c for c in res.columns if not c.startswith(("ent_", "prueba_")) and c != "candidata"]
        print(cand.head(10)[param_cols + ["prueba_rend_%", "prueba_pf", "prueba_ops", "prueba_usd_dia"]].to_string(index=False))
    else:
        print("\nNo apareció ninguna combinación que gane de forma confiable en los dos períodos.")
    print(f"\nTodos los resultados quedaron en {out.name} (se abre con Excel).")


def cmd_analizar(cfg, a):
    from daytrade.analysis import analyze
    ex = PaperExchange(0, 0)
    for sym in a.symbols.split(","):
        for tf in a.timeframes.split(","):
            try:
                df = ex.candles(sym, tf, 1000)
            except Exception as e:
                print(f"{sym} {tf}: error bajando datos: {e}")
                continue
            r = analyze(df, cfg["strategy"], cfg["params"])
            print(f"\n=== {sym}  velas de {tf}  ({df.index[-1]:%Y-%m-%d %H:%M} UTC) ===")
            for k, v in r.items():
                print(f"  {k.replace('_', ' '):16s} {v}")
    print("\nEsto es una lectura técnica automática, no una recomendación de inversión.")


def make_exchange(cfg):
    if cfg["mode"] == "paper_local":
        return PaperExchange(cfg["costs"]["fee_pct"], cfg["costs"]["slippage_pct"])
    check_live_allowed(cfg)
    return CcxtExchange(testnet=cfg["mode"] == "binance_testnet")


def cmd_run(cfg, a):
    ex = make_exchange(cfg)
    eng = Engine(cfg, ex)
    limit = cfg["params"]["ema_trend"] * 3
    print(f"Modo {cfg['mode']} | {cfg['symbol']} {cfg['timeframe']} | estrategia {cfg['strategy']} | "
          f"capital del bot {eng.capital:.2f} USD")
    while True:
        try:
            candles = ex.candles(cfg["symbol"], cfg["timeframe"], limit)
            price = ex.price(cfg["symbol"])
            for e in eng.step(candles, price):
                print(f"[{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M:%S}] {e}", flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as e:  # errores de red: se reintenta en la próxima vuelta
            print(f"[error] {e}", flush=True)
        if a.once:
            break
        time.sleep(cfg.get("poll_seconds", 30))
    pos = eng.pos
    print(f"Capital {eng.capital:.2f} USD | posición abierta: {'sí' if pos else 'no'} | hoy: {eng.guard.pnl:+.2f} USD")


def cmd_check(cfg, a):
    """Prueba la conexión y los permisos de la clave, sin operar."""
    if cfg["mode"] == "paper_local":
        print("El modo es paper_local: no usa claves. Cambiá mode a binance_testnet o binance_live en daytrade.yaml.")
        return
    try:
        ex = CcxtExchange(testnet=cfg["mode"] == "binance_testnet")
    except Exception as e:
        print(f"No se pudo conectar: {e}")
        return
    print(f"Conectado a Binance ({cfg['mode']}).")
    try:
        bal = ex.client.fetch_balance()
        quote, base = cfg["symbol"].split("/")[1], cfg["symbol"].split("/")[0]
        print(f"Saldo disponible: {bal['free'].get(quote, 0):.2f} {quote} | {bal['free'].get(base, 0):.6f} {base}")
        print(f"El bot va a usar como máximo {cfg['initial_usd']} {quote} (initial_usd en daytrade.yaml).")
    except Exception as e:
        print(f"Error leyendo el saldo (¿la clave tiene permiso de lectura?): {e}")
    if cfg["mode"] == "binance_live":
        try:
            r = ex.client.sapi_get_account_apirestrictions()
            print(f"Trading al contado habilitado: {'sí' if r.get('enableSpotAndMarginTrading') else 'NO'}")
            if r.get("enableWithdrawals"):
                print("PELIGRO: esta clave tiene los RETIROS habilitados. Desactivalos en Binance antes de seguir.")
            else:
                print("Retiros deshabilitados: bien.")
            if not r.get("ipRestrict"):
                print("Recomendación: restringí la clave a la IP de tu compu.")
        except Exception as e:
            print(f"No se pudieron leer los permisos de la clave: {e}")
    try:
        print(f"Precio actual de {cfg['symbol']}: {ex.price(cfg['symbol']):.2f}")
    except Exception as e:
        print(f"Error leyendo el precio: {e}")


def cmd_report(cfg, a):
    path = ROOT / "logs" / "daytrade_trades.csv"
    if not path.exists():
        print("Todavía no hay operaciones registradas.")
        return
    t = pd.read_csv(path, parse_dates=["entrada", "salida"])
    t = t[t.modo == (a.mode or cfg["mode"])]
    if t.empty:
        print("No hay operaciones en este modo.")
        return
    t["dia"] = t.salida.dt.strftime("%Y-%m-%d")
    daily = t.groupby("dia").agg(operaciones=("pnl", "size"), ganancia_usd=("pnl", "sum"),
                                 aciertos=("pnl", lambda x: f"{(x > 0).mean():.0%}"), comisiones=("fees", "sum"))
    print(daily.round(2).to_string())
    first, last = t.entrada.min(), t.salida.max()
    n_days = max((last - first).days + 1, 1)
    target = cfg["risk"].get("daily_profit_target_usd") or 9
    print(f"\nTotal: {t.pnl.sum():+.2f} USD en {len(t)} operaciones, {n_days} días "
          f"(promedio {t.pnl.sum() / n_days:+.2f} USD/día). Aciertos: {(t.pnl > 0).mean():.0%}. "
          f"Comisiones: {t.fees.sum():.2f} USD. Días con {target:g} USD o más: {(daily.ganancia_usd >= target).sum()}.")


def main():
    ap = argparse.ArgumentParser(description="Bot de trading diario (cripto)")
    ap.add_argument("--config", default=str(ROOT / "daytrade.yaml"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--days", type=int, default=365)
    b.add_argument("--strategy", default="ambas", choices=["ambas", "reversion", "breakout", "momentum"])
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--trades", action="store_true", help="guardar cada operación en un CSV")
    rs = sub.add_parser("research", help="buscar estrategias con validación fuera de muestra")
    rs.add_argument("--days", type=int, default=1095)
    rs.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT")
    rs.add_argument("--refresh", action="store_true")
    an = sub.add_parser("analizar", help="qué ve el bot en el mercado ahora y si operaría")
    an.add_argument("--symbols", default="BTC/USDT,ETH/USDT,SOL/USDT")
    an.add_argument("--timeframes", default="1h,4h")
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    sub.add_parser("check", help="probar la conexión con Binance")
    rp = sub.add_parser("report")
    rp.add_argument("--mode", default=None)
    a = ap.parse_args()
    load_env()
    cfg = load(a.config)
    {"backtest": cmd_backtest, "run": cmd_run, "report": cmd_report, "check": cmd_check,
     "research": cmd_research, "analizar": cmd_analizar}[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
