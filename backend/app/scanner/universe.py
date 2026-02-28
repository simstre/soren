"""Shared universe fetching and batch data download utilities.

Used by both the fitness scanner and the percentile scanner.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import httpx
import pandas as pd
import yfinance as yf
import structlog

logger = structlog.get_logger()

# ── Symbol filtering ──

_NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
_OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
_EXCLUDE_SUFFIXES = {"W", "R", "U", "Z", "P"}
_ETFS = {
    "SPY", "QQQ", "IWM", "DIA", "VOO", "VTI", "IVV", "VEA", "VWO",
    "EFA", "AGG", "BND", "GLD", "SLV", "TLT", "XLF", "XLK", "XLE",
    "XLV", "XLI", "XLC", "XLY", "XLP", "XLB", "XLU", "XLRE", "ARKK",
    "SOXL", "TQQQ", "SQQQ", "UVXY", "VXX", "HYG", "LQD", "EMB",
}


def _is_valid_common_stock(sym: str) -> bool:
    if not sym or len(sym) > 5:
        return False
    if any(c in sym for c in "-.$^+/"):
        return False
    if sym in _ETFS:
        return False
    if len(sym) >= 5 and sym[-1] in _EXCLUDE_SUFFIXES:
        return False
    return True


def _parse_nasdaq(text: str) -> list[str]:
    symbols = []
    for line in text.strip().splitlines():
        if line.startswith("Symbol|") or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 4:
            continue
        sym = parts[0].strip()
        if parts[3].strip().upper() == "Y":
            continue
        if _is_valid_common_stock(sym):
            symbols.append(sym)
    return symbols


def _parse_other(text: str) -> list[str]:
    symbols = []
    for line in text.strip().splitlines():
        if line.startswith("ACT Symbol|") or line.startswith("File Creation"):
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue
        sym = parts[0].strip()
        if parts[4].strip().upper() == "Y":
            continue
        if len(parts) > 6 and parts[6].strip().upper() == "Y":
            continue
        if _is_valid_common_stock(sym):
            symbols.append(sym)
    return symbols


async def fetch_us_equity_universe() -> list[str]:
    """Fetch full US equity universe from NASDAQ FTP (~7000 symbols)."""
    all_symbols: set[str] = set()
    async with httpx.AsyncClient(timeout=30) as client:
        for url, parser in [(_NASDAQ_URL, _parse_nasdaq), (_OTHER_URL, _parse_other)]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    parsed = parser(resp.text)
                    all_symbols.update(parsed)
                    logger.info("universe_parsed", source=url.split("/")[-1], count=len(parsed))
            except Exception as e:
                logger.warning("universe_fetch_error", url=url, error=str(e))
    if not all_symbols:
        raise RuntimeError("NASDAQ FTP returned empty universe")
    return sorted(all_symbols)


# ── Batch download helpers ──

def flatten_batch_columns(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Flatten a multi-ticker yf.download() result into per-symbol DataFrames."""
    result: dict[str, pd.DataFrame] = {}
    if isinstance(df.columns, pd.MultiIndex):
        try:
            tickers = df.columns.get_level_values("Ticker").unique()
        except KeyError:
            tickers = df.columns.get_level_values(0).unique()
        for sym in tickers:
            try:
                sub = df.xs(sym, level="Ticker", axis=1).copy()
            except (KeyError, TypeError):
                try:
                    sub = df[sym].copy()
                except KeyError:
                    continue
            sub.columns = [c.lower() for c in sub.columns]
            sub = sub.dropna(subset=["close"])
            if len(sub) >= 20:
                result[sym] = sub
    else:
        df.columns = [c.lower() for c in df.columns]
        df = df.dropna(subset=["close"])
        if len(df) >= 20:
            result["SINGLE"] = df
    return result


async def batch_download_daily(
    symbols: list[str],
    days: int = 90,
    min_price: float = 10.0,
    min_avg_volume: int = 500_000,
    chunk_size: int = 500,
) -> dict[str, pd.DataFrame]:
    """Batch download daily data for symbols, applying hard price/volume filter.

    Returns {symbol: DataFrame} for tickers passing the hard filter.
    """
    end = datetime.now() + timedelta(days=1)
    start = end - timedelta(days=days)
    start_str, end_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    all_dfs: dict[str, pd.DataFrame] = {}
    total_batches = (len(symbols) - 1) // chunk_size + 1

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        batch_num = i // chunk_size + 1
        logger.info("batch_download", batch=f"{batch_num}/{total_batches}", tickers=len(chunk))
        try:
            raw = yf.download(
                " ".join(chunk),
                start=start_str,
                end=end_str,
                interval="1d",
                group_by="ticker",
                progress=False,
                auto_adjust=True,
                threads=True,
            )
            if raw.empty:
                continue
            per_sym = flatten_batch_columns(raw)
            for sym, df in per_sym.items():
                try:
                    price = float(df["close"].iloc[-1])
                    avg_volume = int(df["volume"].mean())
                    if price < min_price or avg_volume < min_avg_volume:
                        continue
                    all_dfs[sym] = df
                except Exception:
                    continue
        except Exception as e:
            logger.warning("batch_download_error", batch=batch_num, error=str(e))
            continue

    logger.info("batch_download_complete", requested=len(symbols), survived=len(all_dfs))
    return all_dfs
