"""Disk cache for historical candle data (parquet-based)."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import structlog

logger = structlog.get_logger()

CACHE_DIR = Path(__file__).resolve().parent.parent.parent.parent / "candle_cache"


def _cache_path(symbol: str, interval: str) -> Path:
    return CACHE_DIR / f"{symbol}_{interval}.parquet"


def load_cached(symbol: str, interval: str) -> pd.DataFrame | None:
    """Read parquet cache for symbol/interval. Returns DataFrame or None."""
    path = _cache_path(symbol, interval)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if not isinstance(df.index, pd.DatetimeIndex):
            df.index = pd.to_datetime(df.index)
        return df
    except Exception as e:
        logger.warning("candle_cache_read_error", path=str(path), error=str(e))
        path.unlink(missing_ok=True)
        return None


def save_cache(symbol: str, interval: str, df: pd.DataFrame) -> None:
    """Write DataFrame to parquet cache (overwrite)."""
    if df.empty:
        return
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(symbol, interval)
    try:
        df.to_parquet(path)
        logger.info("candle_cache_saved", path=str(path), rows=len(df))
    except Exception as e:
        logger.warning("candle_cache_write_error", path=str(path), error=str(e))


def get_cache_range(symbol: str, interval: str) -> tuple[datetime, datetime] | None:
    """Return (earliest, latest) timestamps in cache, or None."""
    df = load_cached(symbol, interval)
    if df is None or df.empty:
        return None
    earliest = df.index.min().to_pydatetime()
    latest = df.index.max().to_pydatetime()
    return (earliest, latest)
