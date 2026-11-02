"""Motor en vivo: se llama cada pocos segundos con las últimas velas y el precio actual."""
import csv
import json
import os
from pathlib import Path

import pandas as pd

from bot.config import ROOT
from .risk import DayGuard, position_size
from .strategies import add_indicators, entry_signal, exit_signal, levels

JOURNAL_FIELDS = ["entrada", "salida", "simbolo", "modo", "precio_entrada", "precio_salida", "usd", "pnl", "fees", "motivo"]


class Engine:
    def __init__(self, cfg: dict, exchange, state_path: Path | None = None, journal_path: Path | None = None):
        self.cfg, self.ex = cfg, exchange
        self.p, self.risk = cfg["params"], cfg["risk"]
        self.state_path = state_path or ROOT / "state" / f"daytrade_{cfg['mode']}.json"
        self.journal = journal_path or ROOT / "logs" / "daytrade_trades.csv"
        s = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        # `capital` = efectivo asignado al bot. Aunque la cuenta tenga más, el bot no usa más que esto.
        self.capital = s.get("capital", float(cfg["initial_usd"]))
        self.pos = s.get("position")
        self.last_candle = s.get("last_candle")
        self.guard = DayGuard(**s["guard"]) if s.get("guard") else DayGuard()

    # ---------- persistencia ----------
    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"capital": self.capital, "position": self.pos,
                                               "last_candle": self.last_candle, "guard": self.guard.to_dict()}, indent=2))

    def _journal(self, row):
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        new = not self.journal.exists()
        with open(self.journal, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JOURNAL_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

    def equity(self, price):
        return self.capital + (self.pos["qty"] * price if self.pos else 0.0)

    # ---------- operaciones ----------
    def _open(self, price, atr_v, now):
        stop, tp = levels(price, atr_v, self.p)
        cash = min(self.capital, self.ex.free_usd(self.cfg["symbol"]))
        usd = position_size(self.capital, cash / (1 + self.cfg["costs"]["fee_pct"]), price, stop, self.risk)
        if usd <= 0:
            return "Señal de compra, pero el monto queda por debajo del mínimo"
        fill = self.ex.buy(self.cfg["symbol"], round(usd, 2))
        stop, tp = levels(fill["price"], atr_v, self.p)  # niveles desde el precio real de compra
        cost = fill["cost"]  # USD que salieron de la cuenta, comisión incluida
        self.capital -= cost
        self.pos = {"qty": fill["qty"], "entry": fill["price"], "stop": stop, "tp": tp, "bars": 0,
                    "cost": cost, "fee_in": fill["fee"], "time": now.isoformat()}
        self.guard.opened()
        return f"COMPRA {fill['qty']:.6f} a {fill['price']:.2f} (stop {stop:.2f}, objetivo {tp:.2f})"

    def _close(self, reason, now):
        fill = self.ex.sell(self.cfg["symbol"], self.pos["qty"])
        pnl = fill["proceeds"] - self.pos["cost"]
        self.capital += fill["proceeds"]
        self.guard.closed(pnl)
        self._journal({"entrada": self.pos["time"], "salida": now.isoformat(), "simbolo": self.cfg["symbol"],
                       "modo": self.cfg["mode"], "precio_entrada": round(self.pos["entry"], 2),
                       "precio_salida": round(fill["price"], 2), "usd": round(self.pos["cost"], 2),
                       "pnl": round(pnl, 4), "fees": round(self.pos["fee_in"] + fill["fee"], 4), "motivo": reason})
        self.pos = None
        return f"VENTA a {fill['price']:.2f} ({reason}): {pnl:+.2f} USD | hoy: {self.guard.pnl:+.2f} USD"

    def step(self, candles: pd.DataFrame, price: float, now: pd.Timestamp | None = None) -> list[str]:
        now = now or pd.Timestamp.now(tz="UTC")
        events = []
        self.guard.roll(now.strftime("%Y-%m-%d"), self.equity(price))

        # Stop y toma de ganancia: se revisan en cada llamada, con el precio actual
        if self.pos:
            if price <= self.pos["stop"]:
                events.append(self._close("stop", now))
            elif price >= self.pos["tp"]:
                events.append(self._close("toma de ganancia", now))

        # Decisiones al cierre de cada vela nueva
        last_ts = candles.index[-1].isoformat()
        if last_ts != self.last_candle:
            self.last_candle = last_ts
            row = add_indicators(candles, self.p).iloc[-1]
            if self.pos:
                self.pos["bars"] += 1
                if self.pos["bars"] >= self.p["max_hold_bars"]:
                    events.append(self._close("tiempo máximo", now))
                elif exit_signal(row, self.cfg["strategy"], self.p):
                    events.append(self._close("señal", now))
            elif entry_signal(row, self.cfg["strategy"], self.p):
                ok, why = self.guard.can_open(self.risk)
                if (ROOT / self.risk.get("kill_switch_file", "STOP")).exists():
                    ok, why = False, "interruptor STOP activo"
                events.append(self._open(price, row["atr"], now) if ok else f"Señal de compra ignorada: {why}")
        self.save()
        return events


def check_live_allowed(cfg: dict) -> None:
    if cfg["mode"] == "binance_live" and os.environ.get("DAYTRADE_LIVE_CONFIRM") != "SI":
        raise RuntimeError("Modo con dinero real: falta la variable de entorno DAYTRADE_LIVE_CONFIRM=SI")
