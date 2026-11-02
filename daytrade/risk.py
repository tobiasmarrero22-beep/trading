"""Tamaño de posición y límites diarios."""
from dataclasses import dataclass


def position_size(equity: float, cash: float, entry: float, stop: float, risk: dict) -> float:
    """Monto en USD a comprar para que, si toca el stop, se pierda ~risk_per_trade_pct de la cuenta."""
    if stop >= entry or equity <= 0:
        return 0.0
    risk_usd = equity * risk["risk_per_trade_pct"]
    notional = risk_usd / ((entry - stop) / entry)
    notional = min(notional, equity * risk["max_position_pct"], cash)
    return notional if notional >= risk["min_notional_usd"] else 0.0


@dataclass
class DayGuard:
    """Lleva la cuenta del día (UTC) y decide si se pueden abrir operaciones nuevas."""
    day: str = ""
    start_equity: float = 0.0
    pnl: float = 0.0
    trades: int = 0

    def roll(self, day: str, equity: float) -> None:
        if day != self.day:
            self.day, self.start_equity, self.pnl, self.trades = day, equity, 0.0, 0

    def can_open(self, risk: dict) -> tuple[bool, str]:
        if self.trades >= risk["max_trades_per_day"]:
            return False, "límite de operaciones del día"
        if self.pnl <= -risk["daily_loss_limit_pct"] * self.start_equity:
            return False, "límite de pérdida diaria alcanzado"
        target = risk.get("daily_profit_target_usd")
        if target and self.pnl >= target:
            return False, "objetivo de ganancia del día alcanzado"
        return True, ""

    def opened(self) -> None:
        self.trades += 1

    def closed(self, pnl: float) -> None:
        self.pnl += pnl

    def to_dict(self):
        return {"day": self.day, "start_equity": self.start_equity, "pnl": self.pnl, "trades": self.trades}
