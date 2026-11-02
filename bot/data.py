"""Precios diarios de cierre ajustado. Usa yfinance con caché en CSV."""
from pathlib import Path
import numpy as np
import pandas as pd

from .config import ROOT

CACHE = ROOT / "data" / "cache"


def load_prices(symbols: list[str], start: str = "2005-01-01", refresh: bool = False) -> pd.DataFrame:
    CACHE.mkdir(parents=True, exist_ok=True)
    frames = {}
    for sym in symbols:
        path = CACHE / f"{sym}.csv"
        if refresh or not path.exists():
            s = _download(sym, start)
            s.to_csv(path, header=["close"])
        s = pd.read_csv(path, index_col=0, parse_dates=True)["close"]
        frames[sym] = s
    df = pd.DataFrame(frames).sort_index()
    # Solo fechas donde todos los activos tienen precio (evita sesgos por ETFs más nuevos)
    return df.dropna()


def _download(sym: str, start: str) -> pd.Series:
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("Instalá yfinance: pip install -r requirements.txt") from e
    df = yf.download(sym, start=start, auto_adjust=True, progress=False)
    if df.empty:
        raise RuntimeError(f"No se pudieron bajar precios de {sym}")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close.rename(sym)


def synthetic_prices(symbols: list[str], days: int = 2500, seed: int = 0) -> pd.DataFrame:
    """Precios inventados (paseo aleatorio) para tests y demos sin internet."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2010-01-01", periods=days)
    out = {}
    for i, sym in enumerate(symbols):
        drift, vol = 0.0003 + 0.0001 * i, 0.012 - 0.002 * i
        rets = rng.normal(drift, max(vol, 0.001), days)
        out[sym] = 100 * np.exp(np.cumsum(rets))
    return pd.DataFrame(out, index=idx)
