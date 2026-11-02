"""Bot de trading diario (cripto).

    python daytrade.py backtest                  # prueba con los últimos 365 días de velas reales
    python daytrade.py backtest --days 90 --synthetic
    python daytrade.py run                       # opera en el modo de daytrade.yaml (Ctrl+C para frenar)
    python daytrade.py run --once                # una sola pasada (para cron)
    python daytrade.py report                    # ganancias y pérdidas por día, según el registro
    python daytrade.py check                     # prueba la conexión y los permisos de la clave
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
    b.add_argument("--strategy", default="ambas", choices=["ambas", "reversion", "breakout"])
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--refresh", action="store_true")
    b.add_argument("--trades", action="store_true", help="guardar cada operación en un CSV")
    r = sub.add_parser("run")
    r.add_argument("--once", action="store_true")
    sub.add_parser("check", help="probar la conexión con Binance")
    rp = sub.add_parser("report")
    rp.add_argument("--mode", default=None)
    a = ap.parse_args()
    load_env()
    cfg = load(a.config)
    {"backtest": cmd_backtest, "run": cmd_run, "report": cmd_report, "check": cmd_check}[a.cmd](cfg, a)


if __name__ == "__main__":
    main()
