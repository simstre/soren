"""Shared VWAP Mean Reversion strategy module.

Pre-computes all indicators once over the full series, then provides O(1)
per-bar entry/exit checks. Used by both the replay engine (backtesting)
and the trading engine (live), guaranteeing identical signal logic.

Ported from strategy_lab.py's VWAP revert strategy which achieves +29-47%
over 69 trading days depending on position sizing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.analysis.indicators import rsi as calc_rsi, sma as calc_sma


# ── Parameters ────────────────────────────────────────────────────────

@dataclass
class VWAPRevertParams:
    sigma: float = 1.5          # min deviation from VWAP in σ
    rsi_buy: float = 35.0       # RSI must be < 35 for buy
    rsi_sell: float = 65.0      # RSI must be > 65 for sell
    vol_ratio_min: float = 1.5  # volume / SMA(volume, 20) threshold
    stop_pct: float = 0.02      # 2% stop loss
    target_pct: float = 0.06    # 6% profit target (safety cap, rarely hit)
    max_hold_bars: int = 195    # ~3.25 hours
    cooldown_bars: int = 10
    first_entry_bar: int = 30   # 10:00 AM (bar_of_day index)
    last_entry_bar: int = 330   # 3:00 PM (bar_of_day index)


# ── Indicator Arrays ──────────────────────────────────────────────────

@dataclass
class VWAPRevertIndicators:
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    rsi_14: np.ndarray
    vwap_daily: np.ndarray      # daily-resetting VWAP
    vwap_std: np.ndarray        # rolling std of (close - vwap), 20-bar window
    vol_ratio: np.ndarray       # volume / SMA(volume, 20)
    bar_of_day: np.ndarray      # 0-based minute offset within trading day
    day_boundaries: np.ndarray  # indices where new trading days start
    n_bars: int


# ── Internal helpers ──────────────────────────────────────────────────

def _detect_day_boundaries(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Find indices where a new trading day starts and compute bar-of-day.

    Returns (day_boundary_indices, bar_of_day_array).
    Ported from strategy_lab.py.
    """
    idx = df.index
    if hasattr(idx, "date"):
        dates = pd.Series(idx.date)
    else:
        dates = pd.Series(pd.to_datetime(idx).date)

    day_changes = (dates != dates.shift(1)).values.copy()
    day_changes[0] = True
    boundaries = np.where(day_changes)[0]

    # Compute bar_of_day (0-based offset within each day)
    bar_of_day = np.zeros(len(df), dtype=int)
    for i in range(len(boundaries)):
        start = boundaries[i]
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(df)
        bar_of_day[start:end] = np.arange(end - start)

    return boundaries, bar_of_day


def _compute_daily_vwap(
    df: pd.DataFrame, day_boundaries: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compute daily-resetting VWAP and rolling std of (close - vwap).

    Returns (vwap_array, vwap_std_array).
    Ported from strategy_lab.py.
    """
    n = len(df)
    vwap_arr = np.full(n, np.nan)
    typical = ((df["high"] + df["low"] + df["close"]) / 3).values
    vol = df["volume"].values.astype(float)

    # Process each day separately — reset cumulative sums at each boundary
    boundaries = list(day_boundaries) + [n]
    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        cum_tp_vol = np.cumsum(typical[start:end] * vol[start:end])
        cum_vol = np.cumsum(vol[start:end])
        with np.errstate(divide="ignore", invalid="ignore"):
            day_vwap = np.where(cum_vol > 0, cum_tp_vol / cum_vol, np.nan)
        vwap_arr[start:end] = day_vwap

    # Rolling std of (close - vwap) over 20 bars
    diff = df["close"].values - vwap_arr
    diff_series = pd.Series(diff)
    vwap_std = diff_series.rolling(20).std().values

    return vwap_arr, vwap_std


# ── Public API ────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> VWAPRevertIndicators:
    """Pre-compute all VWAP revert indicator arrays from a DataFrame.

    Works on the full series (replay engine) or a rolling buffer (live engine).
    Uses the same RSI/SMA implementations as the rest of the codebase.
    """
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    volume = df["volume"].values.astype(float)

    rsi_14 = calc_rsi(df["close"], 14).values

    vol_sma20 = calc_sma(df["volume"].astype(float), 20).values
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = np.where(vol_sma20 > 0, volume / vol_sma20, 0.0)

    day_boundaries, bar_of_day = _detect_day_boundaries(df)
    vwap_daily, vwap_std = _compute_daily_vwap(df, day_boundaries)

    return VWAPRevertIndicators(
        close=close,
        high=high,
        low=low,
        rsi_14=rsi_14,
        vwap_daily=vwap_daily,
        vwap_std=vwap_std,
        vol_ratio=vol_ratio,
        bar_of_day=bar_of_day,
        day_boundaries=day_boundaries,
        n_bars=len(df),
    )


def check_entry(
    ind: VWAPRevertIndicators,
    bar: int,
    params: VWAPRevertParams,
) -> tuple[str, float] | None:
    """Check if bar triggers a VWAP revert entry.

    Returns ("BUY", price), ("SELL", price), or None.
    Matches strategy_lab.py strategy_vwap_revert() exactly.
    """
    if bar < 0 or bar >= ind.n_bars:
        return None

    bod = ind.bar_of_day[bar]
    if bod < params.first_entry_bar or bod > params.last_entry_bar:
        return None

    price = ind.close[bar]
    vwap_val = ind.vwap_daily[bar]
    vwap_s = ind.vwap_std[bar]
    if np.isnan(vwap_val) or np.isnan(vwap_s) or vwap_s <= 0:
        return None

    deviation = (price - vwap_val) / vwap_s
    rsi = ind.rsi_14[bar]
    if np.isnan(rsi):
        return None

    if ind.vol_ratio[bar] < params.vol_ratio_min:
        return None

    # Price below VWAP (oversold) -> buy expecting reversion up
    if deviation < -params.sigma and rsi < params.rsi_buy:
        return ("BUY", price)

    # Price above VWAP (overbought) -> sell expecting reversion down
    if deviation > params.sigma and rsi > params.rsi_sell:
        return ("SELL", price)

    return None


def check_exit(
    ind: VWAPRevertIndicators,
    bar: int,
    side: str,
    entry_bar: int,
    params: VWAPRevertParams,
) -> str | None:
    """Check if bar triggers an exit for an open VWAP revert position.

    Returns exit reason string or None.
    Stop loss and profit target are handled by the broker's
    check_stops_and_targets(); this handles max_hold and vwap_target exits.
    """
    if bar < 0 or bar >= ind.n_bars:
        return None

    # Max hold check (bar count, not wall-clock time)
    bars_held = bar - entry_bar
    if bars_held >= params.max_hold_bars:
        return "max_hold"

    # VWAP target: price reverted back to VWAP
    vwap_val = ind.vwap_daily[bar]
    if not np.isnan(vwap_val):
        price = ind.close[bar]
        if side == "BUY" and price >= vwap_val:
            return "vwap_target"
        if side == "SELL" and price <= vwap_val:
            return "vwap_target"

    return None


def compute_stops(
    price: float,
    side: str,
    params: VWAPRevertParams,
) -> tuple[float, float]:
    """Compute (stop_loss, profit_target) for a new VWAP revert position.

    The profit target is the 6% safety cap; the actual VWAP target exit
    is handled by check_exit() returning "vwap_target".
    """
    if side == "BUY":
        stop_loss = round(price * (1 - params.stop_pct), 2)
        profit_target = round(price * (1 + params.target_pct), 2)
    else:
        stop_loss = round(price * (1 + params.stop_pct), 2)
        profit_target = round(price * (1 - params.target_pct), 2)
    return stop_loss, profit_target
