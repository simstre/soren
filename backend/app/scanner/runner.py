"""Scanner runner - executes scans using yfinance data."""

from dataclasses import dataclass

import yfinance as yf
import structlog

from app.scanner.presets import PRESETS, ScannerPresetConfig
from app.scanner.fitness import run_fitness_scan, FitnessScore

logger = structlog.get_logger()

# Common US stocks to scan (expanded watchlist)
SCAN_UNIVERSE = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD",
    "NFLX", "CRM", "ORCL", "ADBE", "INTC", "QCOM", "AVGO", "TXN",
    "MU", "AMAT", "LRCX", "KLAC", "MRVL", "SNPS", "CDNS", "ON",
    "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA",
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "TMO", "ABT",
    "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "VLO", "PSX",
    "DIS", "CMCSA", "T", "VZ", "TMUS",
    "BA", "CAT", "DE", "GE", "HON", "RTX", "LMT", "NOC",
    "SPY", "QQQ", "IWM", "DIA",
]


@dataclass
class ScanResult:
    symbol: str
    name: str
    price: float
    volume: int
    change_pct: float
    prev_close: float


async def run_scan(preset_id: str, max_results: int = 10) -> list[ScanResult]:
    """Run a scanner preset against the stock universe."""
    # Strategy fitness has its own dedicated path
    if preset_id == "strategy_fitness":
        fitness_results = await run_fitness_scan(top_n=max_results)
        return [
            ScanResult(
                symbol=f.symbol,
                name=f.symbol,
                price=f.price,
                volume=f.avg_volume,
                change_pct=f.total_score,  # repurpose change_pct as fitness score
                prev_close=0.0,
            )
            for f in fitness_results
        ]

    preset = PRESETS.get(preset_id)
    if not preset:
        logger.error("unknown_preset", preset_id=preset_id)
        return []

    results: list[ScanResult] = []

    # Fetch data in batch
    symbols_str = " ".join(SCAN_UNIVERSE)
    try:
        tickers = yf.Tickers(symbols_str)

        for symbol in SCAN_UNIVERSE:
            try:
                ticker = tickers.tickers.get(symbol)
                if not ticker:
                    continue

                info = ticker.fast_info
                price = float(info.last_price) if hasattr(info, 'last_price') else 0
                prev_close = float(info.previous_close) if hasattr(info, 'previous_close') else 0
                volume = int(info.last_volume) if hasattr(info, 'last_volume') else 0

                if price <= 0 or prev_close <= 0:
                    continue

                change_pct = round((price - prev_close) / prev_close * 100, 2)

                # Apply filters
                if volume < preset.min_volume:
                    continue
                if price < preset.min_price or price > preset.max_price:
                    continue

                # Preset-specific filters
                if preset_id == "gap_up" and change_pct < 2.0:
                    continue
                if preset_id == "gap_down" and change_pct > -2.0:
                    continue

                results.append(
                    ScanResult(
                        symbol=symbol,
                        name=symbol,  # yfinance fast_info doesn't have full name
                        price=round(price, 2),
                        volume=volume,
                        change_pct=change_pct,
                        prev_close=round(prev_close, 2),
                    )
                )
            except Exception as e:
                logger.debug("scan_skip", symbol=symbol, error=str(e))
                continue

    except Exception as e:
        logger.error("scan_batch_error", error=str(e))
        return []

    # Sort by preset criteria
    sort_key = preset.sort_by
    if sort_key == "volume":
        results.sort(key=lambda x: x.volume, reverse=preset.sort_desc)
    elif sort_key == "change_pct":
        results.sort(key=lambda x: x.change_pct, reverse=preset.sort_desc)

    logger.info("scan_complete", preset=preset_id, results=len(results))
    return results[:max_results]
