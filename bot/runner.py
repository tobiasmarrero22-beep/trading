"""Una ejecución del bot: mirar la cuenta, decidir y (si corresponde) operar.

Pensado para correr todos los días hábiles (cron / Programador de tareas).
Solo opera una vez por mes, a partir de `contribution.day_of_month`.
"""
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .brokers import AlpacaBroker, Broker, PaperLocalBroker
from .config import ROOT, all_symbols
from .data import load_prices
from .risk import apply_limits, check_can_trade
from .strategy import plan_orders, target_weights

STATE = ROOT / "state" / "runner.json"
LOG = ROOT / "logs" / "trades.csv"


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def make_broker(cfg: dict, last_prices: dict[str, float]) -> Broker:
    mode = cfg["mode"]
    if mode == "paper_local":
        return PaperLocalBroker(last_prices, cfg["contribution"]["monthly_usd"])
    return AlpacaBroker(live=(mode == "alpaca_live"))


def _read_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def _write_state(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def _log(rows: list[dict]) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    new = not LOG.exists()
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["fecha", "modo", "simbolo", "usd", "estado", "detalle"])
        if new:
            w.writeheader()
        w.writerows(rows)


def run_once(cfg: dict, dry_run: bool = False, force: bool = False, prices=None, broker: Broker | None = None) -> dict:
    """Ejecuta una vez y guarda el valor de la cartera para el control de caída diaria."""
    state = _read_state()
    prev_equity = state.get("last_equity")
    report = _run(cfg, state, prev_equity, dry_run, force, prices, broker)
    if not dry_run:
        state["last_equity"] = report["equity_final"]
        _write_state(state)
    return report


def _run(cfg, state, prev_equity, dry_run, force, prices, broker) -> dict:
    load_env()
    now = datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")

    if prices is None:
        start = (now.replace(year=now.year - 2)).strftime("%Y-%m-%d")
        prices = load_prices(all_symbols(cfg), start=start, refresh=True)
    last = prices.iloc[-1].to_dict()
    broker = broker or make_broker(cfg, last)

    equity = broker.equity()
    report = {"fecha": now.isoformat(timespec="seconds"), "modo": cfg["mode"], "equity": round(equity, 2),
              "efectivo": round(broker.cash(), 2), "ordenes": {}, "avisos": [],
              "equity_final": equity}

    if not force:
        if now.day < cfg["contribution"].get("day_of_month", 1):
            report["avisos"].append("Todavía no llegó el día de inversión del mes.")
            return report
        if state.get("last_trade_month") == month:
            report["avisos"].append("Ya se operó este mes. Nada que hacer.")
            return report

    check_can_trade(cfg, equity, prev_equity)
    if not dry_run and not broker.market_open():
        report["avisos"].append("El mercado está cerrado; se reintenta en la próxima ejecución.")
        return report

    targets = target_weights(cfg, prices)
    report["pesos_objetivo"] = targets
    raw = plan_orders(broker.positions(), broker.cash(), targets,
                      cfg["strategy"]["rebalance_threshold"], cfg["risk"].get("min_order_usd", 5))
    orders, notes = apply_limits(cfg, raw, broker.cash())
    report["ordenes"], report["avisos"] = orders, report["avisos"] + notes

    if dry_run:
        report["avisos"].append("Simulacro (--dry-run): no se mandó ninguna orden.")
        return report

    rows = []
    for sym, usd in sorted(orders.items(), key=lambda kv: kv[1]):  # ventas primero
        try:
            res = broker.submit(sym, usd)
            rows.append({"fecha": report["fecha"], "modo": cfg["mode"], "simbolo": sym, "usd": usd,
                         "estado": res.get("status"), "detalle": res.get("id", "")})
        except Exception as e:  # una orden fallida no frena las demás
            rows.append({"fecha": report["fecha"], "modo": cfg["mode"], "simbolo": sym, "usd": usd,
                         "estado": "error", "detalle": str(e)[:200]})
    _log(rows)
    report["resultados"] = rows

    state["last_trade_month"] = month
    report["equity_final"] = broker.equity()
    return report
