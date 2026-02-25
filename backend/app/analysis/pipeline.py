"""Analysis pipeline orchestrator - runs all analysis modules on candle data."""

from dataclasses import dataclass, field

import pandas as pd
import structlog

from app.analysis.support_resistance import SRLevel, detect_sr_levels
from app.analysis.channels import Channel, detect_channels
from app.analysis.indicators import rsi as calc_rsi

logger = structlog.get_logger()


@dataclass
class AnalysisResult:
    symbol: str
    sr_levels: list[SRLevel] = field(default_factory=list)
    channels: list[Channel] = field(default_factory=list)
    current_price: float = 0.0
    has_signals: bool = False
    rsi_values: list[float] = field(default_factory=list)
    rsi_current: float = 50.0

    def to_dict(self, timestamps: list[str] | None = None) -> dict:
        ts = timestamps or []

        def _idx_to_time(idx: int) -> str | None:
            if 0 <= idx < len(ts):
                return ts[idx]
            return None

        return {
            "symbol": self.symbol,
            "sr_levels": [
                {
                    "price": sr.price,
                    "type": sr.level_type,
                    "strength": sr.strength,
                    "touches": sr.touch_count,
                }
                for sr in self.sr_levels
            ],
            "channels": [
                {
                    "direction": ch.direction,
                    "confidence": ch.confidence,
                    "upper_slope": ch.upper_slope,
                    "upper_intercept": ch.upper_intercept,
                    "lower_slope": ch.lower_slope,
                    "lower_intercept": ch.lower_intercept,
                    "start_idx": ch.start_idx,
                    "end_idx": ch.end_idx,
                    "start_time": _idx_to_time(ch.start_idx),
                    "end_time": _idx_to_time(ch.end_idx),
                }
                for ch in self.channels
            ],
            "patterns": [],
            "current_price": self.current_price,
        }


def run_analysis(symbol: str, df: pd.DataFrame) -> AnalysisResult:
    """
    Run the full analysis pipeline on candle data.

    Args:
        symbol: Ticker symbol
        df: DataFrame with columns: open, high, low, close, volume
    """
    if df.empty or len(df) < 20:
        logger.warning("insufficient_data", symbol=symbol, rows=len(df))
        return AnalysisResult(symbol=symbol)

    current_price = float(df["close"].iloc[-1])

    # 1. Detect support and resistance levels
    sr_levels = detect_sr_levels(df)
    logger.debug("sr_detected", symbol=symbol, count=len(sr_levels))

    # 2. Detect channels
    channels = detect_channels(df)
    logger.debug("channels_detected", symbol=symbol, count=len(channels))

    has_signals = _price_at_sr_level(current_price, sr_levels)

    # 3. Compute RSI
    rsi_series = calc_rsi(df["close"], period=14)
    rsi_vals = rsi_series.dropna().tolist()
    rsi_current = rsi_vals[-1] if rsi_vals else 50.0

    return AnalysisResult(
        symbol=symbol,
        sr_levels=sr_levels,
        channels=channels,
        current_price=current_price,
        has_signals=has_signals,
        rsi_values=rsi_vals,
        rsi_current=rsi_current,
    )


def _price_at_sr_level(price: float, levels: list[SRLevel], tolerance_pct: float = 0.5) -> bool:
    """Check if current price is near a support or resistance level."""
    for level in levels:
        diff_pct = abs(price - level.price) / price * 100
        if diff_pct <= tolerance_pct:
            return True
    return False
