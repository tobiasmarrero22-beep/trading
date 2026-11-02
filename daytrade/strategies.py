"""Señales. Las mismas funciones se usan en el backtest y en vivo."""
import pandas as pd

from .indicators import atr, ema, rsi


def add_indicators(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = ema(df["close"], p["ema_trend"])
    df["rsi"] = rsi(df["close"], p["rsi_period"])
    df["atr"] = atr(df, p["atr_period"])
    df["hh"] = df["high"].rolling(p["breakout_bars"]).max().shift(1)  # máximo previo, sin la vela actual
    # momentum: cruce de una media rápida por encima de una lenta
    fast, slow = ema(df["close"], p.get("ema_fast", 20)), ema(df["close"], p.get("ema_slow", 50))
    df["fast_above"] = fast > slow
    df["cross_up"] = df["fast_above"] & ~df["fast_above"].shift(1, fill_value=True)
    # volatilidad actual comparada con la habitual (1 = normal, 2 = el doble de lo normal)
    atr_pct = df["atr"] / df["close"]
    df["vol_ratio"] = atr_pct / atr_pct.rolling(200, min_periods=50).median()
    return df


def entry_signal(row: pd.Series, strategy: str, p: dict) -> bool:
    if pd.isna(row["ema"]) or pd.isna(row["atr"]) or row["close"] <= row["ema"]:
        return False
    # Con volatilidad extrema (noticias, pánico) no entra: los stops saltan fácil
    if not pd.isna(row["vol_ratio"]) and row["vol_ratio"] > p.get("max_vol_ratio", float("inf")):
        return False
    if strategy == "reversion":
        return row["rsi"] < p["rsi_entry"]
    if strategy == "breakout":
        return not pd.isna(row["hh"]) and row["close"] > row["hh"]
    if strategy == "momentum":
        return bool(row["cross_up"])
    raise ValueError(f"estrategia desconocida: {strategy}")


def exit_signal(row: pd.Series, strategy: str, p: dict) -> bool:
    """Salida por señal (además de stop, toma de ganancia y tiempo máximo)."""
    if strategy == "reversion":
        return row["rsi"] > p["rsi_exit"]
    if strategy == "breakout":
        return row["close"] < row["ema"]
    if strategy == "momentum":
        return not row["fast_above"]
    return False


def levels(entry: float, atr_value: float, p: dict) -> tuple[float, float]:
    return entry - p["stop_atr"] * atr_value, entry + p["take_profit_atr"] * atr_value
