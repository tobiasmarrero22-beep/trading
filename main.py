"""Uso:
    python main.py backtest              # prueba la estrategia con datos históricos
    python main.py backtest --synthetic  # idem, con datos inventados (sin internet)
    python main.py run --dry-run         # muestra qué haría hoy, sin operar
    python main.py run                   # ejecuta (respeta modo, límites y STOP)
    python main.py status                # muestra la cuenta
"""
import argparse
import json

from bot.backtest import compare
from bot.config import all_symbols, load_config
from bot.data import load_prices, synthetic_prices


def main():
    ap = argparse.ArgumentParser(description="Bot de inversión periódica")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backtest")
    b.add_argument("--synthetic", action="store_true")
    b.add_argument("--refresh", action="store_true", help="volver a bajar precios")
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--force", action="store_true", help="operar aunque ya se haya operado este mes")
    sub.add_parser("status")
    ap.add_argument("--config", default=None)
    a = ap.parse_args()

    cfg = load_config(a.config) if a.config else load_config()

    if a.cmd == "backtest":
        syms = list(cfg["portfolio"]) + [cfg["defensive_asset"]]
        prices = synthetic_prices(syms) if a.synthetic else load_prices(syms, cfg["backtest"]["start"], a.refresh)
        print(f"Período: {prices.index[0].date()} a {prices.index[-1].date()}  "
              f"({'DATOS SINTÉTICOS' if a.synthetic else 'datos reales'})\n")
        import pandas as pd
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(compare(cfg, prices).T.to_string())
        print("\nRendimientos pasados no garantizan rendimientos futuros.")
    elif a.cmd == "run":
        from bot.runner import run_once
        print(json.dumps(run_once(cfg, dry_run=a.dry_run, force=a.force), indent=2, ensure_ascii=False))
    elif a.cmd == "status":
        from bot.runner import load_env, make_broker
        load_env()
        prices = load_prices(all_symbols(cfg), refresh=True).iloc[-1].to_dict()
        br = make_broker(cfg, prices)
        print(json.dumps({"modo": cfg["mode"], "equity": round(br.equity(), 2), "efectivo": round(br.cash(), 2),
                          "posiciones": {k: round(v, 2) for k, v in br.positions().items()}}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
