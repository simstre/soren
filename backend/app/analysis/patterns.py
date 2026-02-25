"""Chart pattern recognition: double top/bottom, head & shoulders, triangles."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from app.analysis.support_resistance import SRLevel


@dataclass
class PatternMatch:
    pattern_type: str
    confidence: float  # 0.0 - 1.0
    direction: str  # "bullish" or "bearish"
    key_points: list[tuple[int, float]]  # (index, price) pairs
    target_price: float
    invalidation_price: float
    description: str


def detect_patterns(
    df: pd.DataFrame,
    sr_levels: list[SRLevel],
    lookback: int = 5,
) -> list[PatternMatch]:
    """Run all pattern detectors and return matches."""
    if len(df) < 30:
        return []

    patterns = []
    patterns.extend(_detect_double_top(df, lookback))
    patterns.extend(_detect_double_bottom(df, lookback))
    patterns.extend(_detect_head_and_shoulders(df, lookback))
    patterns.extend(_detect_inverse_head_and_shoulders(df, lookback))
    patterns.extend(_detect_ascending_triangle(df, lookback))
    patterns.extend(_detect_descending_triangle(df, lookback))

    # Sort by confidence
    patterns.sort(key=lambda p: p.confidence, reverse=True)
    return patterns


def _detect_double_top(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect double top pattern (bearish reversal)."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_max_idx) < 2:
        return []

    patterns = []

    # Check last two peaks
    for i in range(len(local_max_idx) - 1):
        peak1_idx = local_max_idx[i]
        peak2_idx = local_max_idx[i + 1]
        peak1_price = highs[peak1_idx]
        peak2_price = highs[peak2_idx]

        # Peaks should be at similar levels (within 1.5%)
        price_diff_pct = abs(peak1_price - peak2_price) / peak1_price
        if price_diff_pct > 0.015:
            continue

        # Find the trough between the two peaks
        between_min_idx = [idx for idx in local_min_idx if peak1_idx < idx < peak2_idx]
        if not between_min_idx:
            continue

        neckline_idx = between_min_idx[0]
        neckline_price = lows[neckline_idx]

        # Pattern height for target calculation
        pattern_height = ((peak1_price + peak2_price) / 2) - neckline_price
        target = round(neckline_price - pattern_height, 2)

        # Only valid if current price is near or below second peak
        if peak2_idx < len(closes) - 10:  # Pattern should be recent
            continue

        confidence = max(0.5, 1.0 - price_diff_pct * 20)

        patterns.append(
            PatternMatch(
                pattern_type="double_top",
                confidence=round(confidence, 2),
                direction="bearish",
                key_points=[
                    (int(peak1_idx), round(peak1_price, 2)),
                    (int(neckline_idx), round(neckline_price, 2)),
                    (int(peak2_idx), round(peak2_price, 2)),
                ],
                target_price=target,
                invalidation_price=round(max(peak1_price, peak2_price) * 1.01, 2),
                description=f"Double top at ${round((peak1_price + peak2_price) / 2, 2)}",
            )
        )

    return patterns


def _detect_double_bottom(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect double bottom pattern (bullish reversal)."""
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_min_idx) < 2:
        return []

    patterns = []

    for i in range(len(local_min_idx) - 1):
        trough1_idx = local_min_idx[i]
        trough2_idx = local_min_idx[i + 1]
        trough1_price = lows[trough1_idx]
        trough2_price = lows[trough2_idx]

        price_diff_pct = abs(trough1_price - trough2_price) / trough1_price
        if price_diff_pct > 0.015:
            continue

        between_max_idx = [idx for idx in local_max_idx if trough1_idx < idx < trough2_idx]
        if not between_max_idx:
            continue

        neckline_idx = between_max_idx[0]
        neckline_price = highs[neckline_idx]

        pattern_height = neckline_price - ((trough1_price + trough2_price) / 2)
        target = round(neckline_price + pattern_height, 2)

        if trough2_idx < len(closes) - 10:
            continue

        confidence = max(0.5, 1.0 - price_diff_pct * 20)

        patterns.append(
            PatternMatch(
                pattern_type="double_bottom",
                confidence=round(confidence, 2),
                direction="bullish",
                key_points=[
                    (int(trough1_idx), round(trough1_price, 2)),
                    (int(neckline_idx), round(neckline_price, 2)),
                    (int(trough2_idx), round(trough2_price, 2)),
                ],
                target_price=target,
                invalidation_price=round(min(trough1_price, trough2_price) * 0.99, 2),
                description=f"Double bottom at ${round((trough1_price + trough2_price) / 2, 2)}",
            )
        )

    return patterns


def _detect_head_and_shoulders(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect head and shoulders pattern (bearish reversal)."""
    highs = df["high"].values
    lows = df["low"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_max_idx) < 3:
        return []

    patterns = []

    for i in range(len(local_max_idx) - 2):
        left_idx = local_max_idx[i]
        head_idx = local_max_idx[i + 1]
        right_idx = local_max_idx[i + 2]

        left_price = highs[left_idx]
        head_price = highs[head_idx]
        right_price = highs[right_idx]

        # Head must be higher than both shoulders
        if head_price <= left_price or head_price <= right_price:
            continue

        # Shoulders should be at similar levels (within 3%)
        shoulder_diff = abs(left_price - right_price) / left_price
        if shoulder_diff > 0.03:
            continue

        # Head should be significantly higher (at least 1%)
        if (head_price - max(left_price, right_price)) / head_price < 0.01:
            continue

        # Must be recent
        if right_idx < len(highs) - 15:
            continue

        # Find neckline
        between_troughs = [idx for idx in local_min_idx if left_idx < idx < right_idx]
        if len(between_troughs) < 2:
            continue

        neckline = (lows[between_troughs[0]] + lows[between_troughs[-1]]) / 2
        pattern_height = head_price - neckline
        target = round(neckline - pattern_height, 2)

        confidence = min(0.9, 0.6 + (1 - shoulder_diff) * 0.3)

        patterns.append(
            PatternMatch(
                pattern_type="head_and_shoulders",
                confidence=round(confidence, 2),
                direction="bearish",
                key_points=[
                    (int(left_idx), round(left_price, 2)),
                    (int(head_idx), round(head_price, 2)),
                    (int(right_idx), round(right_price, 2)),
                ],
                target_price=target,
                invalidation_price=round(head_price * 1.01, 2),
                description=f"Head & shoulders, neckline at ${round(neckline, 2)}",
            )
        )

    return patterns


def _detect_inverse_head_and_shoulders(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect inverse head and shoulders (bullish reversal)."""
    highs = df["high"].values
    lows = df["low"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_min_idx) < 3:
        return []

    patterns = []

    for i in range(len(local_min_idx) - 2):
        left_idx = local_min_idx[i]
        head_idx = local_min_idx[i + 1]
        right_idx = local_min_idx[i + 2]

        left_price = lows[left_idx]
        head_price = lows[head_idx]
        right_price = lows[right_idx]

        if head_price >= left_price or head_price >= right_price:
            continue

        shoulder_diff = abs(left_price - right_price) / left_price
        if shoulder_diff > 0.03:
            continue

        if (min(left_price, right_price) - head_price) / head_price < 0.01:
            continue

        if right_idx < len(lows) - 15:
            continue

        between_peaks = [idx for idx in local_max_idx if left_idx < idx < right_idx]
        if len(between_peaks) < 2:
            continue

        neckline = (highs[between_peaks[0]] + highs[between_peaks[-1]]) / 2
        pattern_height = neckline - head_price
        target = round(neckline + pattern_height, 2)

        confidence = min(0.9, 0.6 + (1 - shoulder_diff) * 0.3)

        patterns.append(
            PatternMatch(
                pattern_type="inverse_head_and_shoulders",
                confidence=round(confidence, 2),
                direction="bullish",
                key_points=[
                    (int(left_idx), round(left_price, 2)),
                    (int(head_idx), round(head_price, 2)),
                    (int(right_idx), round(right_price, 2)),
                ],
                target_price=target,
                invalidation_price=round(head_price * 0.99, 2),
                description=f"Inverse H&S, neckline at ${round(neckline, 2)}",
            )
        )

    return patterns


def _detect_ascending_triangle(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect ascending triangle (bullish continuation)."""
    highs = df["high"].values
    lows = df["low"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_max_idx) < 2 or len(local_min_idx) < 2:
        return []

    patterns = []

    # Recent highs and lows
    recent_max = local_max_idx[-4:] if len(local_max_idx) >= 4 else local_max_idx
    recent_min = local_min_idx[-4:] if len(local_min_idx) >= 4 else local_min_idx

    if len(recent_max) < 2 or len(recent_min) < 2:
        return []

    # Check for flat resistance (highs at similar level)
    high_prices = highs[recent_max]
    high_range = high_prices.max() - high_prices.min()
    avg_high = high_prices.mean()

    if avg_high == 0:
        return []

    high_flat = high_range / avg_high < 0.015  # Within 1.5%

    # Check for rising support (lows getting higher)
    low_prices = lows[recent_min]
    lows_rising = all(low_prices[i] <= low_prices[i + 1] for i in range(len(low_prices) - 1))

    if high_flat and lows_rising and recent_max[-1] > len(highs) - 15:
        pattern_height = avg_high - low_prices[0]
        target = round(avg_high + pattern_height, 2)

        patterns.append(
            PatternMatch(
                pattern_type="ascending_triangle",
                confidence=0.65,
                direction="bullish",
                key_points=[
                    (int(recent_min[0]), round(low_prices[0], 2)),
                    (int(recent_max[-1]), round(avg_high, 2)),
                ],
                target_price=target,
                invalidation_price=round(low_prices[-1] * 0.99, 2),
                description=f"Ascending triangle, resistance at ${round(avg_high, 2)}",
            )
        )

    return patterns


def _detect_descending_triangle(df: pd.DataFrame, lookback: int = 5) -> list[PatternMatch]:
    """Detect descending triangle (bearish continuation)."""
    highs = df["high"].values
    lows = df["low"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_max_idx) < 2 or len(local_min_idx) < 2:
        return []

    patterns = []

    recent_max = local_max_idx[-4:] if len(local_max_idx) >= 4 else local_max_idx
    recent_min = local_min_idx[-4:] if len(local_min_idx) >= 4 else local_min_idx

    if len(recent_max) < 2 or len(recent_min) < 2:
        return []

    low_prices = lows[recent_min]
    low_range = low_prices.max() - low_prices.min()
    avg_low = low_prices.mean()

    if avg_low == 0:
        return []

    low_flat = low_range / avg_low < 0.015

    high_prices = highs[recent_max]
    highs_falling = all(high_prices[i] >= high_prices[i + 1] for i in range(len(high_prices) - 1))

    if low_flat and highs_falling and recent_min[-1] > len(lows) - 15:
        pattern_height = high_prices[0] - avg_low
        target = round(avg_low - pattern_height, 2)

        patterns.append(
            PatternMatch(
                pattern_type="descending_triangle",
                confidence=0.65,
                direction="bearish",
                key_points=[
                    (int(recent_max[0]), round(high_prices[0], 2)),
                    (int(recent_min[-1]), round(avg_low, 2)),
                ],
                target_price=target,
                invalidation_price=round(high_prices[-1] * 1.01, 2),
                description=f"Descending triangle, support at ${round(avg_low, 2)}",
            )
        )

    return patterns
