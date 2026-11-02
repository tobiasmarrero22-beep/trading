"""Señales. Las mismas funciones se usan en el backtest y en vivo."""
import pandas as pd

from .indicators import atr, ema, rsi


def add_indicators(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    df = df.copy()
    df["ema"] = ema(df["close"], p["ema_trend"])
    df["rsi"] = rsi(df["close"], p["rsi_period"])
    df["atr"] = atr(df, p["atr_period"])
    df["hh"] = df["high"].rolling(p["breakout_bars"]).max().shift(1)  # máximo previo, sin la vela actual
    return df


def entry_signal(row: pd.Series, strategy: str, p: dict) -> bool:
    if pd.isna(row["ema"]) or pd.isna(row["atr"]) or row["close"] <= row["ema"]:
        return False
    if strategy == "reversion":
        return row["rsi"] < p["rsi_entry"]
    if strategy == "breakout":
        return not pd.isna(row["hh"]) and row["close"] > row["hh"]
    raise ValueError(f"estrategia desconocida: {strategy}")


def exit_signal(row: pd.Series, strategy: str, p: dict) -> bool:
    """Salida por señal (además de stop, toma de ganancia y tiempo máximo)."""
    if strategy == "reversion":
        return row["rsi"] > p["rsi_exit"]
    if strategy == "breakout":
        return row["close"] < row["ema"]
    return False


def levels(entry: float, atr_value: float, p: dict) -> tuple[float, float]:
    return entry - p["stop_atr"] * atr_value, entry + p["take_profit_atr"] * atr_value
