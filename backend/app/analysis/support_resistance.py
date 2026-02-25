"""Support and Resistance level detection."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema

from app.analysis.indicators import atr


@dataclass
class SRLevel:
    price: float
    level_type: str  # "support" or "resistance"
    strength: int  # number of touches
    touch_count: int
    first_seen_idx: int
    last_seen_idx: int


def detect_sr_levels(
    df: pd.DataFrame,
    lookback: int = 5,
    tolerance_atr_mult: float = 0.5,
    min_touches: int = 2,
) -> list[SRLevel]:
    """
    Detect support and resistance levels using local extrema clustering.

    1. Find local minima (support candidates) and maxima (resistance candidates)
    2. Cluster nearby price levels using ATR-based tolerance
    3. Filter by minimum touch count
    """
    if len(df) < lookback * 2:
        return []

    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values

    # Find local maxima and minima
    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    # Calculate ATR for tolerance
    atr_series = atr(df, period=14)
    current_atr = atr_series.iloc[-1] if not atr_series.empty else (highs.max() - lows.min()) * 0.02
    if np.isnan(current_atr):
        current_atr = (highs.max() - lows.min()) * 0.02
    tolerance = current_atr * tolerance_atr_mult

    # Collect all pivot points
    resistance_pivots = [(int(i), float(highs[i])) for i in local_max_idx]
    support_pivots = [(int(i), float(lows[i])) for i in local_min_idx]

    # Cluster support levels
    support_levels = _cluster_levels(support_pivots, tolerance, "support", min_touches)
    resistance_levels = _cluster_levels(resistance_pivots, tolerance, "resistance", min_touches)

    # Filter out levels too close to current price (within 0.2 * ATR)
    current_price = closes[-1]
    all_levels = support_levels + resistance_levels

    # Sort by strength
    all_levels.sort(key=lambda x: x.strength, reverse=True)

    return all_levels


def _cluster_levels(
    pivots: list[tuple[int, float]],
    tolerance: float,
    level_type: str,
    min_touches: int,
) -> list[SRLevel]:
    """Cluster nearby pivot points into S/R levels."""
    if not pivots:
        return []

    # Sort by price
    pivots.sort(key=lambda x: x[1])

    clusters: list[list[tuple[int, float]]] = []
    current_cluster = [pivots[0]]

    for i in range(1, len(pivots)):
        if abs(pivots[i][1] - current_cluster[-1][1]) <= tolerance:
            current_cluster.append(pivots[i])
        else:
            clusters.append(current_cluster)
            current_cluster = [pivots[i]]
    clusters.append(current_cluster)

    levels = []
    for cluster in clusters:
        if len(cluster) >= min_touches:
            prices = [p[1] for p in cluster]
            indices = [p[0] for p in cluster]
            avg_price = round(sum(prices) / len(prices), 2)

            levels.append(
                SRLevel(
                    price=avg_price,
                    level_type=level_type,
                    strength=len(cluster),
                    touch_count=len(cluster),
                    first_seen_idx=min(indices),
                    last_seen_idx=max(indices),
                )
            )

    return levels
