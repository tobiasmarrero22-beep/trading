"""Acceso al exchange. PaperExchange simula; CcxtExchange opera en Binance (testnet o real)."""
import os
import time

import pandas as pd

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}


def _ccxt():
    try:
        import ccxt
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Instalá ccxt: pip install -r requirements.txt") from e
    return ccxt


def to_df(rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True)
    return df[~df.index.duplicated()].sort_index()


def fetch_history(symbol: str, timeframe: str, days: int, client=None) -> pd.DataFrame:
    """Descarga velas históricas (datos públicos, sin claves)."""
    ex = client or _ccxt().binance({"enableRateLimit": True})
    step = TF_MS[timeframe]
    since = ex.milliseconds() - days * 86_400_000
    rows = []
    while since < ex.milliseconds() - step:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)
        if not batch:
            break
        rows += batch
        since = batch[-1][0] + step
    return _drop_open_candle(to_df(rows), timeframe)


def _drop_open_candle(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC")
    if len(df) and df.index[-1] + pd.Timedelta(milliseconds=TF_MS[timeframe]) > now:
        df = df.iloc[:-1]  # la última vela todavía no cerró
    return df


class PaperExchange:
    """Precios reales de Binance; las órdenes se simulan con comisión y deslizamiento."""

    def __init__(self, fee_pct: float, slippage_pct: float, client=None):
        self.client = client or _ccxt().binance({"enableRateLimit": True})
        self.fee, self.slip = fee_pct, slippage_pct

    def candles(self, symbol, timeframe, limit):
        return _drop_open_candle(to_df(self.client.fetch_ohlcv(symbol, timeframe, limit=limit)), timeframe)

    def price(self, symbol):
        return float(self.client.fetch_ticker(symbol)["last"])

    def free_usd(self, symbol):
        return float("inf")  # el capital lo controla el bot

    def buy(self, symbol, usd):
        px = self.price(symbol) * (1 + self.slip)
        fee = usd * self.fee
        return {"qty": (usd - fee) / px, "price": px, "cost": usd, "fee": fee, "id": f"paper-{int(time.time())}"}

    def sell(self, symbol, qty):
        px = self.price(symbol) * (1 - self.slip)
        gross = qty * px
        fee = gross * self.fee
        return {"price": px, "proceeds": gross - fee, "fee": fee, "id": f"paper-{int(time.time())}"}


class CcxtExchange(PaperExchange):
    """Órdenes reales a mercado en Binance. Claves en BINANCE_API_KEY / BINANCE_API_SECRET."""

    def __init__(self, testnet: bool):
        key, secret = os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Faltan BINANCE_API_KEY y BINANCE_API_SECRET (ponelas en el archivo .env)")
        client = _ccxt().binance({"apiKey": key, "secret": secret, "enableRateLimit": True})
        if testnet:
            client.set_sandbox_mode(True)
        client.load_markets()
        super().__init__(0, 0, client)

    def free_usd(self, symbol):
        quote = symbol.split("/")[1]
        return float(self.client.fetch_balance()["free"].get(quote, 0.0))

    def _fees(self, order, base):
        """Comisiones de la orden -> (total en USD, parte cobrada en moneda base, parte cobrada aparte en USD).

        Binance descuenta la comisión de lo que recibís (BTC al comprar, USDT al vender),
        salvo que pagues comisiones con BNB (en ese caso se aproxima su valor en USD).
        """
        total, in_base, other = 0.0, 0.0, 0.0
        for f in order.get("fees") or ([order["fee"]] if order.get("fee") else []):
            if not f or not f.get("cost"):
                continue
            c = float(f["cost"])
            if f.get("currency") == base:
                in_base += c
                total += c * float(order["average"])
            else:
                other += c
                total += c
        return total, in_base, other

    def buy(self, symbol, usd):
        o = self.client.create_market_buy_order_with_cost(symbol, usd)
        o = self._wait(o, symbol)
        fee_usd, fee_base, fee_other = self._fees(o, symbol.split("/")[0])
        return {"qty": float(o["filled"]) - fee_base, "price": float(o["average"]),
                "cost": float(o["cost"]) + fee_other, "fee": fee_usd, "id": o["id"]}

    def sell(self, symbol, qty):
        amount = float(self.client.amount_to_precision(symbol, qty))
        o = self._wait(self.client.create_market_sell_order(symbol, amount), symbol)
        fee_usd, _, fee_other = self._fees(o, symbol.split("/")[0])
        return {"price": float(o["average"]), "proceeds": float(o["cost"]) - fee_other, "fee": fee_usd, "id": o["id"]}

    def _wait(self, order, symbol):
        for _ in range(10):
            if order.get("status") == "closed" and order.get("average"):
                return order
            time.sleep(1)
            order = self.client.fetch_order(order["id"], symbol)
        raise RuntimeError(f"La orden {order['id']} no se completó a tiempo")
