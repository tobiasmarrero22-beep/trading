"""Lectura del mercado en lenguaje simple: tendencia, fuerza, RSI, volatilidad y decisión."""
import pandas as pd

from .strategies import add_indicators, entry_signal


def efficiency(close: pd.Series, n: int = 20) -> float:
    """0 = va de lado (mucho ruido), 1 = va en línea recta. Por encima de ~0,3 hay tendencia clara."""
    if len(close) <= n:
        return 0.0
    move = abs(close.iloc[-1] - close.iloc[-n - 1])
    path = close.diff().abs().iloc[-n:].sum()
    return float(move / path) if path else 0.0


def analyze(df: pd.DataFrame, strategy: str, p: dict) -> dict:
    ind = add_indicators(df, p)
    last = ind.iloc[-1]
    close = ind["close"]
    ema_slope = (ind["ema"].iloc[-1] / ind["ema"].iloc[-21] - 1) if len(ind) > 21 else 0.0
    er = efficiency(close)
    atr_pct = ind["atr"] / close
    vol_ratio = float(last["vol_ratio"]) if not pd.isna(last["vol_ratio"]) else 1.0
    max_vol = p.get("max_vol_ratio", float("inf"))

    above = last["close"] > last["ema"]
    if above and ema_slope > 0:
        tendencia = "ALCISTA"
    elif not above and ema_slope < 0:
        tendencia = "BAJISTA"
    else:
        tendencia = "INDEFINIDA"
    fuerza = "con dirección clara" if er >= 0.3 else ("débil" if er >= 0.15 else "de lado (sin dirección)")
    rsi_v = float(last["rsi"])
    rsi_txt = "sobrevendido (cayó mucho)" if rsi_v < 30 else "sobrecomprado (subió mucho)" if rsi_v > 70 else "neutral"
    vol_txt = "MUY ALTA" if vol_ratio > 1.8 else "alta" if vol_ratio > 1.3 else "baja" if vol_ratio < 0.7 else "normal"

    reasons = []
    if not above:
        reasons.append("el precio está por debajo de su media de tendencia (el bot solo compra en tendencia alcista)")
    if vol_ratio > max_vol:
        reasons.append("la volatilidad está muy alta: los stops saltan fácil")
    signal = entry_signal(last, strategy, p)
    if above and not signal and vol_ratio <= max_vol:
        reasons.append(f"no hay señal de entrada de la estrategia '{strategy}' en esta vela")
    operaria = bool(signal)

    return {
        "precio": round(float(last["close"]), 2),
        "tendencia": tendencia,
        "fuerza": f"{fuerza} ({er:.2f})",
        "rsi": f"{rsi_v:.0f}: {rsi_txt}",
        "volatilidad": f"{vol_txt} (ATR {100 * atr_pct.iloc[-1]:.2f} % del precio)",
        "operaria_ahora": "SÍ" if operaria else "NO",
        "por_que": "hay señal de compra y las condiciones acompañan" if operaria else "; ".join(reasons),
    }
