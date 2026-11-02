"""Conexión con el bróker. Todas las clases exponen la misma interfaz."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from .config import ROOT


class Broker:
    def cash(self) -> float: ...
    def positions(self) -> dict[str, float]:
        """{símbolo: valor de mercado en USD}"""
    def equity(self) -> float:
        return self.cash() + sum(self.positions().values())
    def market_open(self) -> bool:
        return True
    def submit(self, symbol: str, usd: float) -> dict:
        """Compra (usd>0) o vende (usd<0) por un monto en dólares."""


class PaperLocalBroker(Broker):
    """Simulación local: guarda el estado en state/paper_account.json."""

    def __init__(self, prices: dict[str, float], monthly_usd: float, state_path: Path | None = None):
        self.prices = prices
        self.monthly = monthly_usd
        self.path = state_path or ROOT / "state" / "paper_account.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
        else:
            self.state = {"cash": 0.0, "shares": {}, "deposits": []}
        self._simulate_deposit()

    def _simulate_deposit(self):
        """Simula tu depósito mensual (una vez por mes calendario)."""
        month = datetime.now(timezone.utc).strftime("%Y-%m")
        if month not in self.state["deposits"]:
            self.state["deposits"].append(month)
            self.state["cash"] += self.monthly
            self._save()

    def _save(self):
        self.path.write_text(json.dumps(self.state, indent=2))

    def cash(self):
        return self.state["cash"]

    def positions(self):
        return {s: q * self.prices[s] for s, q in self.state["shares"].items() if q > 0}

    def submit(self, symbol, usd):
        px = self.prices[symbol]
        qty = usd / px
        have = self.state["shares"].get(symbol, 0.0)
        if qty < 0:
            qty = max(qty, -have)
        if qty > 0 and qty * px > self.state["cash"] + 1e-6:
            raise RuntimeError("Efectivo insuficiente")
        self.state["shares"][symbol] = have + qty
        self.state["cash"] -= qty * px
        self._save()
        return {"symbol": symbol, "usd": round(qty * px, 2), "qty": qty, "price": px, "status": "filled"}


class AlpacaBroker(Broker):
    """API REST de Alpaca. Claves en ALPACA_KEY_ID / ALPACA_SECRET_KEY."""

    def __init__(self, live: bool = False):
        self.base = "https://api.alpaca.markets" if live else "https://paper-api.alpaca.markets"
        key, secret = os.environ.get("ALPACA_KEY_ID"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("Faltan ALPACA_KEY_ID y ALPACA_SECRET_KEY (ponelas en el archivo .env)")
        self.s = requests.Session()
        self.s.headers.update({"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})

    def _get(self, path):
        r = self.s.get(self.base + path, timeout=20)
        r.raise_for_status()
        return r.json()

    def cash(self):
        return float(self._get("/v2/account")["cash"])

    def equity(self):
        return float(self._get("/v2/account")["equity"])

    def positions(self):
        return {p["symbol"]: float(p["market_value"]) for p in self._get("/v2/positions")}

    def market_open(self):
        return bool(self._get("/v2/clock")["is_open"])

    def submit(self, symbol, usd):
        body = {
            "symbol": symbol,
            "notional": f"{abs(usd):.2f}",   # monto en dólares: permite fracciones de acción
            "side": "buy" if usd > 0 else "sell",
            "type": "market",
            "time_in_force": "day",
        }
        r = self.s.post(self.base + "/v2/orders", json=body, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"Alpaca rechazó la orden de {symbol}: {r.text}")
        o = r.json()
        return {"symbol": symbol, "usd": round(usd, 2), "id": o.get("id"), "status": o.get("status")}
