"""Lógica de decisión: qué pesos queremos y qué órdenes hacen falta."""
import pandas as pd


def target_weights(cfg: dict, prices: pd.DataFrame) -> dict[str, float]:
    """Pesos objetivo para la última fecha de `prices`.

    Con trend_filter, el peso de cada activo que cotiza por debajo de su media
    móvil se traslada al activo defensivo.
    """
    weights = dict(cfg["portfolio"])
    st = cfg["strategy"]
    if not st.get("trend_filter"):
        return weights
    n = st.get("sma_days", 200)
    defensive = cfg["defensive_asset"]
    out = {defensive: 0.0}
    for sym, w in weights.items():
        series = prices[sym].dropna()
        if len(series) >= n and series.iloc[-1] < series.iloc[-n:].mean():
            out[defensive] += w
            out[sym] = 0.0
        else:
            out[sym] = w
    return out


def plan_orders(
    holdings_value: dict[str, float],
    cash: float,
    targets: dict[str, float],
    rebalance_threshold: float,
    min_order: float = 1.0,
) -> dict[str, float]:
    """Devuelve {símbolo: USD} (positivo = comprar, negativo = vender).

    - Si ningún activo se desvía más del umbral (o tiene peso 0 y hay que salir),
      solo se invierte el efectivo nuevo en los activos que están por debajo del
      objetivo ("rebalanceo con aportes": sin ventas, sin impuestos, menos comisiones).
    - Si hay un desvío grande, se rebalancea completo (con ventas).
    """
    syms = set(targets) | set(holdings_value)
    hv = {s: holdings_value.get(s, 0.0) for s in syms}
    tw = {s: targets.get(s, 0.0) for s in syms}
    total = sum(hv.values()) + cash
    if total <= 0:
        return {}

    invested = sum(hv.values())
    drift = max(abs(hv[s] / invested - tw[s]) for s in syms) if invested > 0 else 0.0
    must_exit = any(tw[s] == 0 and hv[s] > min_order for s in syms)

    if drift > rebalance_threshold or must_exit:
        orders = {s: tw[s] * total - hv[s] for s in syms}
    else:
        # Repartir el efectivo según cuánto le falta a cada activo para su objetivo
        gaps = {s: max(tw[s] * total - hv[s], 0.0) for s in syms}
        gap_sum = sum(gaps.values())
        if gap_sum <= 0 or cash <= 0:
            return {}
        orders = {s: cash * g / gap_sum for s, g in gaps.items()}

    return {s: round(v, 2) for s, v in orders.items() if abs(v) >= min_order}
