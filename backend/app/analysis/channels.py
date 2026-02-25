"""Channel detection using linear regression on swing highs/lows."""

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import argrelextrema


@dataclass
class Channel:
    upper_slope: float
    upper_intercept: float
    lower_slope: float
    lower_intercept: float
    direction: str  # "ascending", "descending", "horizontal"
    start_idx: int
    end_idx: int
    confidence: float  # R-squared of the fit


def detect_channels(
    df: pd.DataFrame,
    lookback: int = 5,
    min_points: int = 3,
    min_r_squared: float = 0.6,
) -> list[Channel]:
    """
    Detect price channels by fitting linear regression to swing highs and lows.
    """
    if len(df) < lookback * 3:
        return []

    highs = df["high"].values
    lows = df["low"].values

    local_max_idx = argrelextrema(highs, np.greater_equal, order=lookback)[0]
    local_min_idx = argrelextrema(lows, np.less_equal, order=lookback)[0]

    if len(local_max_idx) < min_points or len(local_min_idx) < min_points:
        return []

    channels = []

    # Try to fit a channel using recent swing points
    # Use the last N swing highs/lows
    recent_highs = local_max_idx[-min_points * 2 :]
    recent_lows = local_min_idx[-min_points * 2 :]

    if len(recent_highs) >= min_points and len(recent_lows) >= min_points:
        # Fit upper trendline (resistance)
        upper_x = recent_highs.astype(float)
        upper_y = highs[recent_highs]
        upper_slope, upper_intercept, upper_r2 = _linear_fit(upper_x, upper_y)

        # Fit lower trendline (support)
        lower_x = recent_lows.astype(float)
        lower_y = lows[recent_lows]
        lower_slope, lower_intercept, lower_r2 = _linear_fit(lower_x, lower_y)

        avg_r2 = (upper_r2 + lower_r2) / 2

        if avg_r2 >= min_r_squared:
            # Determine channel direction
            avg_slope = (upper_slope + lower_slope) / 2
            price_range = highs.max() - lows.min()
            slope_threshold = price_range * 0.0001  # relative threshold

            if avg_slope > slope_threshold:
                direction = "ascending"
            elif avg_slope < -slope_threshold:
                direction = "descending"
            else:
                direction = "horizontal"

            channels.append(
                Channel(
                    upper_slope=round(upper_slope, 6),
                    upper_intercept=round(upper_intercept, 2),
                    lower_slope=round(lower_slope, 6),
                    lower_intercept=round(lower_intercept, 2),
                    direction=direction,
                    start_idx=int(min(recent_highs[0], recent_lows[0])),
                    end_idx=int(len(df) - 1),
                    confidence=round(avg_r2, 3),
                )
            )

    return channels


def _linear_fit(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
    """Fit a line and return slope, intercept, and R-squared."""
    if len(x) < 2:
        return 0.0, 0.0, 0.0

    coeffs = np.polyfit(x, y, 1)
    slope, intercept = coeffs[0], coeffs[1]

    y_pred = slope * x + intercept
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)

    r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return float(slope), float(intercept), max(0.0, float(r_squared))


def get_channel_prices(channel: Channel, idx: int) -> tuple[float, float]:
    """Get the upper and lower channel prices at a given index."""
    upper = channel.upper_slope * idx + channel.upper_intercept
    lower = channel.lower_slope * idx + channel.lower_intercept
    return round(upper, 2), round(lower, 2)
