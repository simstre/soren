"""Technical indicators used across the analysis modules."""

import numpy as np
import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range."""
    high = df["high"]
    low = df["low"]
    close = df["close"]

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return true_range.rolling(window=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def volume_profile(df: pd.DataFrame, bins: int = 20) -> list[dict]:
    """Calculate volume profile (price levels with most volume)."""
    price_range = df["close"].max() - df["close"].min()
    if price_range == 0:
        return []

    bin_edges = np.linspace(df["close"].min(), df["close"].max(), bins + 1)
    volumes = []

    for i in range(len(bin_edges) - 1):
        mask = (df["close"] >= bin_edges[i]) & (df["close"] < bin_edges[i + 1])
        vol = df.loc[mask, "volume"].sum()
        volumes.append({
            "price_low": round(bin_edges[i], 2),
            "price_high": round(bin_edges[i + 1], 2),
            "price_mid": round((bin_edges[i] + bin_edges[i + 1]) / 2, 2),
            "volume": int(vol),
        })

    return sorted(volumes, key=lambda x: x["volume"], reverse=True)


def bollinger_bands(
    series: pd.Series, period: int = 20, std_dev: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands — returns (upper, middle, lower)."""
    middle = series.rolling(window=period).mean()
    rolling_std = series.rolling(window=period).std()
    upper = middle + std_dev * rolling_std
    lower = middle - std_dev * rolling_std
    return upper, middle, lower


def vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative VWAP using typical price * volume."""
    typical = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol
