"""Backtest con aportes mensuales, comisiones y deslizamiento."""
import copy
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import plan_orders, target_weights


@dataclass
class Result:
    name: str
    equity: pd.Series          # valor de la cartera cada día
    contributed: pd.Series     # dinero aportado acumulado
    nav: pd.Series             # valor de una "cuotaparte" (rendimiento sin efecto de los aportes)
    trades: int
    fees: float

    def summary(self) -> dict:
        years = (self.nav.index[-1] - self.nav.index[0]).days / 365.25
        cagr = (self.nav.iloc[-1] / self.nav.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0
        dd = (self.nav / self.nav.cummax() - 1).min()
        daily = self.nav.pct_change().dropna()
        vol = daily.std() * np.sqrt(252)
        return {
            "estrategia": self.name,
            "aportado_usd": round(self.contributed.iloc[-1], 2),
            "valor_final_usd": round(self.equity.iloc[-1], 2),
            "ganancia_usd": round(self.equity.iloc[-1] - self.contributed.iloc[-1], 2),
            "rend_anual_%": round(100 * cagr, 2),
            "peor_caida_%": round(100 * dd, 2),
            "volatilidad_%": round(100 * vol, 2),
            "operaciones": self.trades,
            "comisiones_usd": round(self.fees, 2),
        }


def run_backtest(cfg: dict, prices: pd.DataFrame, name: str = "estrategia", start_index: int | None = None) -> Result:
    """Simula desde prices.index[start_index]; las filas anteriores solo sirven de historial
    para la media móvil. Por defecto start_index = sma_days."""
    bt = cfg["backtest"]
    monthly = cfg["contribution"]["monthly_usd"]
    thr = cfg["strategy"]["rebalance_threshold"]
    fee, slip = bt.get("commission_usd", 0.0), bt.get("slippage_pct", 0.0)
    min_order = cfg["risk"].get("min_order_usd", 1.0)
    if start_index is None:
        start_index = cfg["strategy"].get("sma_days", 200)
    if start_index >= len(prices):
        raise ValueError("No hay suficientes datos para el período de calentamiento")

    shares = {s: 0.0 for s in prices.columns}
    cash = float(bt.get("initial_usd", 0.0))
    contributed = cash
    units = cash  # cada cuotaparte arranca valiendo 1 USD
    trades, fees = 0, 0.0
    eq, contrib, nav = [], [], []
    last_month = None

    for i in range(start_index, len(prices)):
        day, row = prices.index[i], prices.iloc[i]
        value = cash + sum(shares[s] * row[s] for s in shares)
        unit_price = value / units if units > 0 else 1.0

        new_month = last_month != (day.year, day.month)
        if new_month:
            last_month = (day.year, day.month)
            deposit = monthly
            cash += deposit
            contributed += deposit
            units += deposit / unit_price

            targets = target_weights(cfg, prices.iloc[: i + 1])
            hv = {s: shares[s] * row[s] for s in shares}
            orders = plan_orders(hv, cash, targets, thr, min_order)
            # Primero ventas (liberan efectivo), después compras
            for sym, usd in sorted(orders.items(), key=lambda kv: kv[1]):
                px = row[sym] * (1 + slip if usd > 0 else 1 - slip)
                if usd > 0:
                    usd = min(usd, cash - fee)
                    if usd < min_order:
                        continue
                qty = usd / px
                if usd < 0:
                    qty = max(qty, -shares[sym])
                shares[sym] += qty
                cash -= qty * px + fee
                trades += 1
                fees += fee

        value = cash + sum(shares[s] * row[s] for s in shares)
        eq.append(value)
        contrib.append(contributed)
        nav.append(value / units if units > 0 else 1.0)

    idx = prices.index[start_index:]
    return Result(name, pd.Series(eq, idx), pd.Series(contrib, idx), pd.Series(nav, idx), trades, fees)


def compare(cfg: dict, prices: pd.DataFrame) -> pd.DataFrame:
    """Compara la configuración actual con y sin filtro de tendencia, mismo período."""
    rows = []
    for trend in (True, False):
        c = copy.deepcopy(cfg)
        c["strategy"]["trend_filter"] = trend
        name = "con filtro de tendencia" if trend else "sin filtro (comprar y rebalancear)"
        rows.append(run_backtest(c, prices, name).summary())
    return pd.DataFrame(rows).set_index("estrategia")
