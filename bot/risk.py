"""Controles de seguridad que se aplican ANTES de mandar cualquier orden."""
import os
from pathlib import Path

from .config import ROOT


class RiskError(RuntimeError):
    pass


def check_can_trade(cfg: dict, equity_today: float | None = None, equity_yesterday: float | None = None) -> None:
    risk = cfg["risk"]
    if (ROOT / risk.get("kill_switch_file", "STOP")).exists():
        raise RiskError("Interruptor de apagado activo (existe el archivo STOP). No se opera.")
    if cfg["mode"] == "alpaca_live" and os.environ.get("BOT_LIVE_CONFIRM") != "SI":
        raise RiskError("Modo con dinero real: falta la variable de entorno BOT_LIVE_CONFIRM=SI")
    if equity_today and equity_yesterday:
        change = equity_today / equity_yesterday - 1
        if change < -risk.get("max_daily_loss_pct", 0.05):
            raise RiskError(f"La cartera cayó {change:.1%} desde la última ejecución. Pausado por seguridad.")


def apply_limits(cfg: dict, orders: dict[str, float], cash: float) -> tuple[dict[str, float], list[str]]:
    """Recorta órdenes según los límites. Devuelve (órdenes_finales, avisos)."""
    risk = cfg["risk"]
    max_order = risk.get("max_order_usd", 500)
    min_order = risk.get("min_order_usd", 5)
    max_n = risk.get("max_orders_per_run", 10)
    notes: list[str] = []
    out: dict[str, float] = {}

    sells = {s: v for s, v in orders.items() if v < 0}
    buys = {s: v for s, v in orders.items() if v > 0}
    budget = cash + sum(-v for v in sells.values())

    for sym, usd in list(sells.items()) + sorted(buys.items(), key=lambda kv: -kv[1]):
        if len(out) >= max_n:
            notes.append(f"Límite de {max_n} órdenes por ejecución alcanzado; se omite {sym}")
            break
        if abs(usd) > max_order:
            notes.append(f"{sym}: orden de {usd:.2f} USD recortada a {max_order} USD")
            usd = max_order if usd > 0 else -max_order
        if usd > 0:
            if usd > budget:
                usd = round(budget, 2)
            budget -= usd
        if abs(usd) < min_order:
            continue
        out[sym] = round(usd, 2)
    return out, notes
