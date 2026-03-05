#!/usr/bin/env python3
"""Strategy Lab — fast iterative backtester for strategy research.

Pre-computes all indicators once per ticker, then runs O(1)-per-bar
strategy logic. Supports 4 strategy candidates, parameter sweeps,
and train/validate splits.

Usage:
    cd backend
    python -m scripts.strategy_lab --mode=rank
    python -m scripts.strategy_lab --mode=screen
    python -m scripts.strategy_lab --mode=sweep --strategy=v9_rsi
    python -m scripts.strategy_lab --mode=portfolio --strategy=v9_rsi --tickers=TSLA,CEG,PLTR
    python -m scripts.strategy_lab --mode=validate --strategy=v9_rsi --tickers=TSLA,CEG,PLTR
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analysis.indicators import (
    atr as calc_atr,
    bollinger_bands,
    ema as calc_ema,
    rsi as calc_rsi,
    sma as calc_sma,
    vwap as calc_vwap,
)
from app.analysis.support_resistance import detect_sr_levels
from app.engine.candle_cache import load_cached
from app.engine.signal_generator import detect_rsi_divergence

# ── Constants ─────────────────────────────────────────────────────────
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "candle_cache"
OUTPUT_DIR = Path(__file__).resolve().parent / "strategy_lab_results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STARTING_BALANCE = 100_000.0
BARS_PER_DAY = 390  # 6.5 hours × 60 min
DAYS = 90

# Market hours (EST) — bar index within day
MARKET_OPEN_MIN = 0       # 9:30 EST = bar 0
FIRST_30_MIN = 30         # 10:00 EST = bar 30
NO_ENTRY_AFTER = 330      # 3:00 EST = bar 330 (5.5h after open)

STRATEGY_NAMES = ["v9_rsi", "orb", "ema_cross", "vwap_revert", "time_split", "ensemble"]


# ── Data Structures ──────────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    side: str          # "BUY" or "SELL"
    entry_bar: int
    entry_price: float
    stop_loss: float
    profit_target: float
    quantity: int
    max_hold_bars: int
    exit_bar: int | None = None
    exit_price: float | None = None
    exit_reason: str | None = None

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        if self.side == "BUY":
            return (self.exit_price - self.entry_price) * self.quantity
        return (self.entry_price - self.exit_price) * self.quantity

    @property
    def pnl_pct(self) -> float:
        if self.exit_price is None:
            return 0.0
        if self.side == "BUY":
            return (self.exit_price - self.entry_price) / self.entry_price
        return (self.entry_price - self.exit_price) / self.entry_price


@dataclass
class PrecomputedData:
    """All pre-computed indicator arrays for a single ticker."""
    symbol: str
    df: pd.DataFrame
    close: np.ndarray
    high: np.ndarray
    low: np.ndarray
    open_: np.ndarray
    volume: np.ndarray
    rsi_14: np.ndarray
    ema_9: np.ndarray
    ema_21: np.ndarray
    sma_20: np.ndarray
    sma_50: np.ndarray
    atr_14: np.ndarray
    bb_upper: np.ndarray
    bb_mid: np.ndarray
    bb_lower: np.ndarray
    vwap_daily: np.ndarray       # daily-resetting VWAP
    vwap_std: np.ndarray         # rolling std of (close - vwap)
    vol_ratio: np.ndarray        # volume / SMA(volume, 20)
    has_bullish_div: np.ndarray  # bool
    has_bearish_div: np.ndarray  # bool
    div_rsi_spread: np.ndarray   # float
    sr_cache: dict[int, list]    # bar_idx -> list of SRLevel
    day_boundaries: np.ndarray   # indices where a new trading day starts
    bar_of_day: np.ndarray       # minute offset within trading day (0-389)
    n_bars: int


class PortfolioTracker:
    """Synchronous portfolio tracker with shared balance."""

    def __init__(self, balance: float, max_exposure_pct: float = 1.0):
        self.starting_balance = balance
        self.balance = balance
        self.max_exposure_pct = max_exposure_pct
        self.positions: dict[str, Trade] = {}  # symbol -> open trade
        self.closed_trades: list[Trade] = []

    def can_open(self, symbol: str, position_value: float) -> bool:
        if symbol in self.positions:
            return False
        current_exposure = sum(
            t.entry_price * t.quantity for t in self.positions.values()
        )
        if self.balance <= 0:
            return False
        if (current_exposure + position_value) / self.starting_balance > self.max_exposure_pct:
            return False
        return True

    def open_trade(self, trade: Trade) -> bool:
        cost = trade.entry_price * trade.quantity
        if not self.can_open(trade.symbol, cost):
            return False
        self.positions[trade.symbol] = trade
        return True

    def close_trade(self, symbol: str, exit_bar: int, exit_price: float, reason: str):
        if symbol not in self.positions:
            return
        trade = self.positions.pop(symbol)
        trade.exit_bar = exit_bar
        trade.exit_price = exit_price
        trade.exit_reason = reason
        self.balance += trade.pnl
        self.closed_trades.append(trade)

    def check_stops(self, bar_idx: int, highs: dict[str, float], lows: dict[str, float],
                    closes: dict[str, float]):
        """Check stop loss and profit target for all open positions."""
        to_close = []
        for symbol, trade in self.positions.items():
            h = highs.get(symbol)
            l = lows.get(symbol)
            c = closes.get(symbol)
            if h is None or l is None or c is None:
                continue

            # Check max hold
            if bar_idx - trade.entry_bar >= trade.max_hold_bars:
                to_close.append((symbol, bar_idx, c, "max_hold"))
                continue

            if trade.side == "BUY":
                if l <= trade.stop_loss:
                    to_close.append((symbol, bar_idx, trade.stop_loss, "stop_loss"))
                elif h >= trade.profit_target:
                    to_close.append((symbol, bar_idx, trade.profit_target, "target"))
            else:
                if h >= trade.stop_loss:
                    to_close.append((symbol, bar_idx, trade.stop_loss, "stop_loss"))
                elif l <= trade.profit_target:
                    to_close.append((symbol, bar_idx, trade.profit_target, "target"))

        for symbol, bar, price, reason in to_close:
            self.close_trade(symbol, bar, price, reason)

    def equity_at(self, current_prices: dict[str, float]) -> float:
        """Equity = cash balance + unrealized P&L of open positions."""
        unrealized = 0.0
        for sym, trade in self.positions.items():
            price = current_prices.get(sym, trade.entry_price)
            if trade.side == "BUY":
                unrealized += (price - trade.entry_price) * trade.quantity
            else:
                unrealized += (trade.entry_price - price) * trade.quantity
        return self.balance + unrealized

    @property
    def equity(self) -> float:
        # Fallback without current prices (uses entry prices = no unrealized)
        return self.balance + sum(
            t.entry_price * t.quantity for t in self.positions.values()
        )


# ── Pre-computation ──────────────────────────────────────────────────

def _compute_daily_vwap(df: pd.DataFrame, day_boundaries: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Compute daily-resetting VWAP and rolling std of (close - vwap)."""
    n = len(df)
    vwap_arr = np.full(n, np.nan)
    typical = ((df["high"] + df["low"] + df["close"]) / 3).values
    vol = df["volume"].values.astype(float)

    # Process each day separately
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


def _detect_day_boundaries(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Find indices where a new trading day starts and compute bar-of-day."""
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


def _prescan_divergences(
    rsi_arr: np.ndarray, close_arr: np.ndarray, lookback: int = 20,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pre-scan RSI divergences into boolean arrays."""
    n = len(close_arr)
    has_bull = np.zeros(n, dtype=bool)
    has_bear = np.zeros(n, dtype=bool)
    div_spread = np.zeros(n, dtype=float)

    for i in range(lookback, n):
        rsi_window = rsi_arr[i - lookback: i]
        price_window = close_arr[i - lookback: i]

        # Skip if RSI has NaNs
        if np.any(np.isnan(rsi_window)):
            continue

        mid = lookback // 2
        # Bearish: price higher high, RSI lower high
        ph1 = np.max(price_window[:mid])
        ph2 = np.max(price_window[mid:])
        rh1 = np.max(rsi_window[:mid])
        rh2 = np.max(rsi_window[mid:])

        if ph2 > ph1 and rh2 < rh1 - 2:
            has_bear[i] = True
            div_spread[i] = abs(rh1 - rh2)
            continue

        # Bullish: price lower low, RSI higher low
        pl1 = np.min(price_window[:mid])
        pl2 = np.min(price_window[mid:])
        rl1 = np.min(rsi_window[:mid])
        rl2 = np.min(rsi_window[mid:])

        if pl2 < pl1 and rl2 > rl1 + 2:
            has_bull[i] = True
            div_spread[i] = abs(rl2 - rl1)

    return has_bull, has_bear, div_spread


def _precompute_sr(df: pd.DataFrame, interval: int = 390) -> dict[int, list]:
    """Pre-compute S/R levels every `interval` bars."""
    cache = {}
    n = len(df)
    for i in range(interval, n, interval):
        window = df.iloc[max(0, i - interval * 2): i]
        if len(window) >= 20:
            levels = detect_sr_levels(window, lookback=5, min_touches=2)
            cache[i] = levels
    # Also compute at the very end
    if n >= 20:
        window = df.iloc[max(0, n - interval * 2): n]
        levels = detect_sr_levels(window, lookback=5, min_touches=2)
        cache[n] = levels
    return cache


def _get_sr_levels(sr_cache: dict[int, list], bar_idx: int) -> list:
    """Get the most recent S/R levels for a given bar index."""
    best_key = 0
    for key in sr_cache:
        if key <= bar_idx and key > best_key:
            best_key = key
    return sr_cache.get(best_key, [])


def precompute(symbol: str, df: pd.DataFrame) -> PrecomputedData:
    """Pre-compute all indicators for a single ticker."""
    close = df["close"].values.astype(float)
    high = df["high"].values.astype(float)
    low = df["low"].values.astype(float)
    open_ = df["open"].values.astype(float)
    volume = df["volume"].values.astype(float)

    rsi_14 = calc_rsi(df["close"], 14).values
    ema_9 = calc_ema(df["close"], 9).values
    ema_21 = calc_ema(df["close"], 21).values
    sma_20 = calc_sma(df["close"], 20).values
    sma_50 = calc_sma(df["close"], 50).values
    atr_14 = calc_atr(df, 14).values
    bb_upper, bb_mid, bb_lower = bollinger_bands(df["close"], 20, 2.0)

    vol_sma20 = calc_sma(df["volume"].astype(float), 20).values
    with np.errstate(divide="ignore", invalid="ignore"):
        vol_ratio = np.where(vol_sma20 > 0, volume / vol_sma20, 0.0)

    day_boundaries, bar_of_day = _detect_day_boundaries(df)
    vwap_daily, vwap_std = _compute_daily_vwap(df, day_boundaries)

    has_bull, has_bear, div_spread = _prescan_divergences(rsi_14, close)
    sr_cache = _precompute_sr(df, interval=BARS_PER_DAY)

    return PrecomputedData(
        symbol=symbol,
        df=df,
        close=close, high=high, low=low, open_=open_, volume=volume,
        rsi_14=rsi_14,
        ema_9=ema_9, ema_21=ema_21,
        sma_20=sma_20, sma_50=sma_50,
        atr_14=atr_14,
        bb_upper=bb_upper.values, bb_mid=bb_mid.values, bb_lower=bb_lower.values,
        vwap_daily=vwap_daily, vwap_std=vwap_std,
        vol_ratio=vol_ratio,
        has_bullish_div=has_bull, has_bearish_div=has_bear, div_rsi_spread=div_spread,
        sr_cache=sr_cache,
        day_boundaries=day_boundaries,
        bar_of_day=bar_of_day,
        n_bars=len(df),
    )


# ── Opening Range Tracking ───────────────────────────────────────────

@dataclass
class OpeningRange:
    high: float = 0.0
    low: float = float("inf")
    avg_volume: float = 0.0
    valid: bool = False


def _compute_opening_ranges(data: PrecomputedData) -> dict[int, OpeningRange]:
    """Pre-compute opening range (first 30 bars) for each trading day."""
    ranges = {}
    for day_start in data.day_boundaries:
        day_end = day_start + min(30, data.n_bars - day_start)
        if day_end - day_start < 10:
            continue
        or_high = np.max(data.high[day_start:day_end])
        or_low = np.min(data.low[day_start:day_end])
        or_vol = np.mean(data.volume[day_start:day_end])
        if or_high > or_low and or_vol > 0:
            ranges[int(day_start)] = OpeningRange(
                high=or_high, low=or_low, avg_volume=or_vol, valid=True,
            )
    return ranges


def _get_opening_range(or_cache: dict[int, OpeningRange], bar_idx: int,
                       day_boundaries: np.ndarray) -> OpeningRange | None:
    """Get the opening range for the day containing bar_idx."""
    day_start = day_boundaries[np.searchsorted(day_boundaries, bar_idx, side="right") - 1]
    return or_cache.get(int(day_start))


# ── Strategy Functions ───────────────────────────────────────────────
# Each returns (side, entry_price) or None.
# All use pre-computed arrays — O(1) per bar.

@dataclass
class StrategyParams:
    stop_pct: float = 0.02
    target_pct: float = 0.06
    max_hold_bars: int = 390
    # v9_rsi specific
    min_rsi_spread: float = 7.0
    rsi_buy_lo: float = 30.0
    rsi_buy_hi: float = 42.0
    rsi_sell_lo: float = 58.0
    rsi_sell_hi: float = 70.0
    min_vol_ratio: float = 0.5
    # orb specific
    orb_vol_mult: float = 1.5
    orb_target_mult: float = 1.0  # target = breakout + mult * range
    orb_max_hold: int = 195
    # ema_cross specific
    ema_rsi_lo: float = 40.0
    ema_rsi_hi: float = 60.0
    # vwap specific
    vwap_sigma: float = 1.5
    vwap_rsi_buy: float = 35.0
    vwap_rsi_sell: float = 65.0
    vwap_max_hold: int = 195


def strategy_v9_rsi(data: PrecomputedData, bar: int, params: StrategyParams) -> tuple[str, float] | None:
    """v9: RSI divergence + trend alignment (EMA 9>21 for buys)."""
    if data.bar_of_day[bar] < 15 or data.bar_of_day[bar] > NO_ENTRY_AFTER:
        return None

    rsi = data.rsi_14[bar]
    if np.isnan(rsi):
        return None

    price = data.close[bar]
    ema9 = data.ema_9[bar]
    ema21 = data.ema_21[bar]
    if np.isnan(ema9) or np.isnan(ema21):
        return None

    if data.vol_ratio[bar] < params.min_vol_ratio:
        return None

    # Bullish: RSI divergence + RSI in buy zone + EMA9 > EMA21
    if (data.has_bullish_div[bar]
            and data.div_rsi_spread[bar] >= params.min_rsi_spread
            and params.rsi_buy_lo <= rsi < params.rsi_buy_hi
            and ema9 > ema21):
        return ("BUY", price)

    # Bearish: RSI divergence + RSI in sell zone + EMA9 < EMA21
    if (data.has_bearish_div[bar]
            and data.div_rsi_spread[bar] >= params.min_rsi_spread
            and params.rsi_sell_lo < rsi <= params.rsi_sell_hi
            and ema9 < ema21):
        return ("SELL", price)

    return None


def strategy_orb(data: PrecomputedData, bar: int, params: StrategyParams,
                 or_cache: dict[int, OpeningRange],
                 orb_traded_today: set[int]) -> tuple[str, float] | None:
    """Opening Range Breakout: break above/below first-30-min range."""
    bod = data.bar_of_day[bar]
    if bod < FIRST_30_MIN or bod > NO_ENTRY_AFTER:
        return None

    # Get current day start
    day_idx = np.searchsorted(data.day_boundaries, bar, side="right") - 1
    day_start = int(data.day_boundaries[day_idx])

    # One trade per day
    if day_start in orb_traded_today:
        return None

    or_range = or_cache.get(day_start)
    if or_range is None or not or_range.valid:
        return None

    price = data.close[bar]
    rsi = data.rsi_14[bar]
    if np.isnan(rsi):
        return None

    vol_vs_or = data.volume[bar] / or_range.avg_volume if or_range.avg_volume > 0 else 0

    if vol_vs_or < params.orb_vol_mult:
        return None

    # Breakout above
    if price > or_range.high and rsi > 40:
        orb_traded_today.add(day_start)
        return ("BUY", price)

    # Breakdown below
    if price < or_range.low and rsi < 60:
        orb_traded_today.add(day_start)
        return ("SELL", price)

    return None


def strategy_ema_cross(data: PrecomputedData, bar: int, params: StrategyParams) -> tuple[str, float] | None:
    """EMA(9)/EMA(21) crossover + expanding ATR + volume + RSI filter."""
    if bar < 1:
        return None
    if data.bar_of_day[bar] < 15 or data.bar_of_day[bar] > NO_ENTRY_AFTER:
        return None

    ema9 = data.ema_9[bar]
    ema9_prev = data.ema_9[bar - 1]
    ema21 = data.ema_21[bar]
    ema21_prev = data.ema_21[bar - 1]
    if np.isnan(ema9) or np.isnan(ema21) or np.isnan(ema9_prev) or np.isnan(ema21_prev):
        return None

    rsi = data.rsi_14[bar]
    if np.isnan(rsi):
        return None

    # ATR expanding: current ATR > SMA(ATR, 20)
    atr_val = data.atr_14[bar]
    if np.isnan(atr_val):
        return None
    sma_atr_start = max(0, bar - 19)
    atr_window = data.atr_14[sma_atr_start: bar + 1]
    atr_window = atr_window[~np.isnan(atr_window)]
    if len(atr_window) < 5:
        return None
    atr_avg = np.mean(atr_window)
    if atr_val <= atr_avg:
        return None

    if data.vol_ratio[bar] < 1.0:
        return None

    # Bullish cross: EMA9 crosses above EMA21
    if ema9_prev <= ema21_prev and ema9 > ema21 and params.ema_rsi_lo <= rsi <= params.ema_rsi_hi:
        return ("BUY", data.close[bar])

    # Bearish cross: EMA9 crosses below EMA21
    if ema9_prev >= ema21_prev and ema9 < ema21 and params.ema_rsi_lo <= rsi <= params.ema_rsi_hi:
        return ("SELL", data.close[bar])

    return None


def strategy_vwap_revert(data: PrecomputedData, bar: int, params: StrategyParams) -> tuple[str, float] | None:
    """VWAP Mean Reversion: price deviates >1.5σ from VWAP, expect reversion."""
    bod = data.bar_of_day[bar]
    if bod < FIRST_30_MIN or bod > NO_ENTRY_AFTER:
        return None

    price = data.close[bar]
    vwap_val = data.vwap_daily[bar]
    vwap_s = data.vwap_std[bar]
    if np.isnan(vwap_val) or np.isnan(vwap_s) or vwap_s <= 0:
        return None

    deviation = (price - vwap_val) / vwap_s
    rsi = data.rsi_14[bar]
    if np.isnan(rsi):
        return None

    if data.vol_ratio[bar] < 1.5:
        return None

    # Price below VWAP (oversold) -> buy expecting reversion up
    if deviation < -params.vwap_sigma and rsi < params.vwap_rsi_buy:
        return ("BUY", price)

    # Price above VWAP (overbought) -> sell expecting reversion down
    if deviation > params.vwap_sigma and rsi > params.vwap_rsi_sell:
        return ("SELL", price)

    return None


# ── EMA Cross Exit ───────────────────────────────────────────────────

def _check_ema_cross_exit(data: PrecomputedData, bar: int, side: str) -> bool:
    """Exit EMA cross strategy on reverse cross."""
    if bar < 1:
        return False
    ema9 = data.ema_9[bar]
    ema9_prev = data.ema_9[bar - 1]
    ema21 = data.ema_21[bar]
    ema21_prev = data.ema_21[bar - 1]
    if np.isnan(ema9) or np.isnan(ema21) or np.isnan(ema9_prev) or np.isnan(ema21_prev):
        return False

    if side == "BUY" and ema9_prev >= ema21_prev and ema9 < ema21:
        return True
    if side == "SELL" and ema9_prev <= ema21_prev and ema9 > ema21:
        return True
    return False


# ── Composite Strategies ─────────────────────────────────────────────

def strategy_time_split(
    data: PrecomputedData, bar: int, params: StrategyParams,
    or_cache: dict[int, OpeningRange], orb_traded_today: set[int],
) -> tuple[str, float, str] | None:
    """Time-split: ORB in morning (bar 30-150), VWAP revert in afternoon (bar 150-330).

    Returns (side, price, sub_strategy) or None.
    """
    bod = data.bar_of_day[bar]

    # Morning: ORB (10:00-11:30 EST, bar 30-120)
    if 30 <= bod <= 120:
        sig = strategy_orb(data, bar, params, or_cache, orb_traded_today)
        if sig:
            return (sig[0], sig[1], "orb")

    # Afternoon: VWAP revert (12:00-15:00 EST, bar 150-330)
    if 150 <= bod <= 330:
        sig = strategy_vwap_revert(data, bar, params)
        if sig:
            return (sig[0], sig[1], "vwap_revert")

    return None


def strategy_ensemble(
    data: PrecomputedData, bar: int, params: StrategyParams,
    or_cache: dict[int, OpeningRange], orb_traded_today: set[int],
) -> tuple[str, float] | None:
    """Ensemble: enter only when 2+ strategies agree on direction."""
    votes: dict[str, int] = {"BUY": 0, "SELL": 0}
    prices: dict[str, float] = {"BUY": 0.0, "SELL": 0.0}

    for fn in [
        lambda: strategy_v9_rsi(data, bar, params),
        lambda: strategy_orb(data, bar, params, or_cache, orb_traded_today.copy()),
        lambda: strategy_ema_cross(data, bar, params),
        lambda: strategy_vwap_revert(data, bar, params),
    ]:
        sig = fn()
        if sig:
            votes[sig[0]] += 1
            prices[sig[0]] = sig[1]

    # Need 2+ strategies to agree
    for side in ("BUY", "SELL"):
        if votes[side] >= 2:
            return (side, prices[side])

    return None


# ── Core Backtest Engine ─────────────────────────────────────────────

def run_backtest(
    data_map: dict[str, PrecomputedData],
    strategy_name: str,
    params: StrategyParams,
    position_size_pct: float = 0.50,
    max_exposure_pct: float = 1.0,
    start_bar_pct: float = 0.0,    # fraction of data to skip (for train/validate split)
    end_bar_pct: float = 1.0,
) -> dict:
    """Run a single backtest across all tickers with shared portfolio."""
    tracker = PortfolioTracker(STARTING_BALANCE, max_exposure_pct)

    # Determine bar range
    all_n = [d.n_bars for d in data_map.values()]
    if not all_n:
        return {"error": "No data"}
    max_n = max(all_n)
    start_bar = int(max_n * start_bar_pct)
    end_bar = int(max_n * end_bar_pct)

    # Pre-compute ORB ranges if needed
    or_caches: dict[str, dict[int, OpeningRange]] = {}
    orb_traded_today: dict[str, set[int]] = {}
    if strategy_name in ("orb", "time_split", "ensemble"):
        for sym, data in data_map.items():
            or_caches[sym] = _compute_opening_ranges(data)
            orb_traded_today[sym] = set()

    # Cooldown per symbol (bars remaining)
    cooldown: dict[str, int] = {s: 0 for s in data_map}

    equity_curve = []

    for bar in range(start_bar, end_bar):
        # Collect current prices for stop checks
        highs, lows, closes = {}, {}, {}
        for sym, data in data_map.items():
            if bar < data.n_bars:
                highs[sym] = float(data.high[bar])
                lows[sym] = float(data.low[bar])
                closes[sym] = float(data.close[bar])

        # Check stops/targets/max_hold
        tracker.check_stops(bar, highs, lows, closes)

        # Check EMA cross exits for ema_cross strategy
        if strategy_name == "ema_cross":
            for sym in list(tracker.positions.keys()):
                data = data_map.get(sym)
                if data and bar < data.n_bars:
                    trade = tracker.positions[sym]
                    if _check_ema_cross_exit(data, bar, trade.side):
                        tracker.close_trade(sym, bar, data.close[bar], "ema_reverse")

        # Check VWAP target exits for vwap_revert / time_split strategy
        if strategy_name in ("vwap_revert", "time_split"):
            for sym in list(tracker.positions.keys()):
                data = data_map.get(sym)
                if data and bar < data.n_bars:
                    vwap_val = data.vwap_daily[bar]
                    price = data.close[bar]
                    trade = tracker.positions[sym]
                    if not np.isnan(vwap_val):
                        if trade.side == "BUY" and price >= vwap_val:
                            tracker.close_trade(sym, bar, price, "vwap_target")
                        elif trade.side == "SELL" and price <= vwap_val:
                            tracker.close_trade(sym, bar, price, "vwap_target")

        # Generate signals
        for sym, data in data_map.items():
            if bar >= data.n_bars:
                continue
            if bar < 50:  # need warmup
                continue
            if cooldown[sym] > 0:
                cooldown[sym] -= 1
                continue
            if sym in tracker.positions:
                continue

            signal = None
            sub_strategy = strategy_name  # track which sub-strategy fired
            if strategy_name == "v9_rsi":
                signal = strategy_v9_rsi(data, bar, params)
            elif strategy_name == "orb":
                signal = strategy_orb(data, bar, params, or_caches[sym], orb_traded_today[sym])
            elif strategy_name == "ema_cross":
                signal = strategy_ema_cross(data, bar, params)
            elif strategy_name == "vwap_revert":
                signal = strategy_vwap_revert(data, bar, params)
            elif strategy_name == "time_split":
                ts_sig = strategy_time_split(data, bar, params, or_caches[sym], orb_traded_today[sym])
                if ts_sig:
                    signal = (ts_sig[0], ts_sig[1])
                    sub_strategy = ts_sig[2]
            elif strategy_name == "ensemble":
                signal = strategy_ensemble(data, bar, params, or_caches[sym], orb_traded_today[sym])

            if signal is None:
                continue

            side, entry_price = signal
            if entry_price <= 0:
                continue

            # Calculate position size
            alloc = tracker.balance * position_size_pct
            qty = int(alloc / entry_price)
            if qty <= 0:
                continue

            # Compute stops — route by effective sub-strategy
            sl_pct = params.stop_pct
            effective = sub_strategy if strategy_name == "time_split" else strategy_name
            if effective == "orb":
                or_range = _get_opening_range(or_caches[sym], bar, data.day_boundaries)
                if or_range and or_range.valid:
                    range_size = or_range.high - or_range.low
                    if side == "BUY":
                        sl = entry_price - entry_price * sl_pct
                        tp = entry_price + range_size * params.orb_target_mult
                    else:
                        sl = entry_price + entry_price * sl_pct
                        tp = entry_price - range_size * params.orb_target_mult
                    mh = params.orb_max_hold
                else:
                    continue
            elif effective == "vwap_revert":
                vwap_val = data.vwap_daily[bar]
                if np.isnan(vwap_val):
                    continue
                if side == "BUY":
                    sl = entry_price * (1 - sl_pct)
                    tp = vwap_val  # target = VWAP
                else:
                    sl = entry_price * (1 + sl_pct)
                    tp = vwap_val
                mh = params.vwap_max_hold
            else:
                if side == "BUY":
                    sl = entry_price * (1 - sl_pct)
                    tp = entry_price * (1 + params.target_pct)
                else:
                    sl = entry_price * (1 + sl_pct)
                    tp = entry_price * (1 - params.target_pct)
                mh = params.max_hold_bars

            trade = Trade(
                symbol=sym, side=side, entry_bar=bar,
                entry_price=entry_price, stop_loss=sl,
                profit_target=tp, quantity=qty, max_hold_bars=mh,
            )
            opened = tracker.open_trade(trade)
            if not opened:
                continue

            # Cooldown after entry (prevent re-entry same bar cluster)
            cooldown[sym] = 10

        # Track equity periodically (every 100 bars to save memory)
        if bar % 100 == 0:
            equity_curve.append({"bar": bar, "equity": tracker.equity_at(closes)})

    # Force-close any remaining positions at last available price
    for sym in list(tracker.positions.keys()):
        data = data_map.get(sym)
        if data:
            last_bar = min(end_bar - 1, data.n_bars - 1)
            tracker.close_trade(sym, last_bar, data.close[last_bar], "end_of_backtest")

    return _build_results(tracker, equity_curve, data_map, position_size_pct, strategy_name, params)


def _build_results(
    tracker: PortfolioTracker, equity_curve: list, data_map: dict,
    position_size_pct: float, strategy_name: str, params: StrategyParams,
) -> dict:
    """Build results dict from completed backtest."""
    closed = tracker.closed_trades
    total_pnl = sum(t.pnl for t in closed)
    total_pnl_pct = (total_pnl / tracker.starting_balance) * 100
    wins = [t for t in closed if t.pnl > 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0

    # Max drawdown from equity curve
    max_dd = 0.0
    max_dd_pct = 0.0
    if equity_curve:
        peak = equity_curve[0]["equity"]
        for pt in equity_curve:
            eq = pt["equity"]
            if eq > peak:
                peak = eq
            dd = peak - eq
            dd_pct = (dd / peak * 100) if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = dd_pct

    # Per-ticker breakdown
    ticker_breakdown = {}
    for sym in data_map:
        sym_trades = [t for t in closed if t.symbol == sym]
        sym_pnl = sum(t.pnl for t in sym_trades)
        sym_wins = len([t for t in sym_trades if t.pnl > 0])
        ticker_breakdown[sym] = {
            "pnl": round(sym_pnl, 2),
            "pnl_pct": round(sym_pnl / tracker.starting_balance * 100, 2),
            "trades": len(sym_trades),
            "win_rate": round(sym_wins / len(sym_trades) * 100, 1) if sym_trades else 0,
        }

    # Compute trading days
    trading_days = 0
    if data_map:
        first_data = next(iter(data_map.values()))
        trading_days = len(first_data.day_boundaries)

    # Exit reason breakdown
    exit_reasons = {}
    for t in closed:
        r = t.exit_reason or "unknown"
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # Average hold duration (in bars)
    avg_hold = 0
    if closed:
        holds = [(t.exit_bar or 0) - t.entry_bar for t in closed]
        avg_hold = sum(holds) / len(holds)

    return {
        "strategy": strategy_name,
        "params": {
            "stop_pct": params.stop_pct,
            "target_pct": params.target_pct,
            "max_hold_bars": params.max_hold_bars,
        },
        "position_size_pct": position_size_pct,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": len(closed),
        "winning_trades": len(wins),
        "losing_trades": len(closed) - len(wins),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "avg_hold_bars": round(avg_hold, 1),
        "trading_days": trading_days,
        "exit_reasons": exit_reasons,
        "ticker_breakdown": ticker_breakdown,
        "starting_balance": tracker.starting_balance,
        "ending_balance": round(tracker.balance, 2),
    }


# ── Ticker Ranking ───────────────────────────────────────────────────

def rank_tickers(top_n: int = 8) -> list[dict]:
    """Rank all cached tickers by tradability metrics."""
    parquets = sorted(CACHE_DIR.glob("*_1m.parquet"))
    results = []

    for path in parquets:
        symbol = path.stem.replace("_1m", "")
        df = load_cached(symbol, "1m")
        if df is None or len(df) < BARS_PER_DAY * 10:
            continue

        df = _normalize_columns(df)
        close = df["close"].values.astype(float)
        high = df["high"].values.astype(float)
        low = df["low"].values.astype(float)
        volume = df["volume"].values.astype(float)

        # Daily ATR%
        atr_series = calc_atr(df, 14).values
        valid_atr = atr_series[~np.isnan(atr_series)]
        if len(valid_atr) == 0:
            continue
        avg_price = np.mean(close[~np.isnan(close)])
        if avg_price <= 0:
            continue
        daily_atr_pct = (np.mean(valid_atr) / avg_price) * 100 * np.sqrt(BARS_PER_DAY / 14)

        # Approximate daily ATR% from daily ranges
        n_days = len(df) // BARS_PER_DAY
        if n_days < 5:
            continue
        daily_ranges = []
        for d in range(n_days):
            s = d * BARS_PER_DAY
            e = min(s + BARS_PER_DAY, len(df))
            day_high = np.max(high[s:e])
            day_low = np.min(low[s:e])
            day_close = close[e - 1] if e > s else avg_price
            if day_close > 0:
                daily_ranges.append((day_high - day_low) / day_close * 100)
        daily_range_pct = np.mean(daily_ranges) if daily_ranges else 0

        # Dollar volume (daily average)
        dollar_vol = np.mean(close * volume) * BARS_PER_DAY / 1e6  # in millions

        # Trend persistence: autocorrelation of 30-min returns
        returns_30m = pd.Series(close).pct_change(30).dropna().values
        if len(returns_30m) > 60:
            autocorr = np.corrcoef(returns_30m[:-1], returns_30m[1:])[0, 1]
            if np.isnan(autocorr):
                autocorr = 0.0
        else:
            autocorr = 0.0

        # Data coverage: trading days with data
        data_days = n_days
        coverage = min(data_days / (DAYS * 0.7), 1.0)  # ~69 trading days expected

        # Composite score (higher = better)
        score = (
            daily_range_pct * 0.40 +
            min(dollar_vol / 100, 1.0) * 20 * 0.20 +  # cap at $100M/day
            abs(autocorr) * 20 * 0.20 +
            coverage * 20 * 0.20
        )

        results.append({
            "symbol": symbol,
            "daily_range_pct": round(daily_range_pct, 2),
            "dollar_vol_M": round(dollar_vol, 1),
            "autocorr": round(autocorr, 3),
            "data_days": data_days,
            "coverage": round(coverage, 2),
            "score": round(score, 2),
            "bars": len(df),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ── Load Data ────────────────────────────────────────────────────────

def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase column names and drop non-OHLCV columns."""
    df.columns = [c.lower().replace(" ", "_") for c in df.columns]
    # Keep only OHLCV
    keep = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
    return df[keep]


def load_ticker_data(symbols: list[str]) -> dict[str, PrecomputedData]:
    """Load and precompute data for given symbols."""
    data_map = {}
    for sym in symbols:
        df = load_cached(sym, "1m")
        if df is None or df.empty:
            print(f"  SKIP {sym}: no cached data")
            continue
        df = _normalize_columns(df)
        if len(df) < BARS_PER_DAY * 5:
            print(f"  SKIP {sym}: only {len(df)} bars ({len(df)//BARS_PER_DAY} days)")
            continue
        t0 = time.time()
        data_map[sym] = precompute(sym, df)
        elapsed = time.time() - t0
        print(f"  {sym}: {len(df):,} bars, precomputed in {elapsed:.1f}s")
    return data_map


# ── Mode: Screen ─────────────────────────────────────────────────────

def run_screen(tickers: list[str]) -> list[dict]:
    """Screen all 4 strategies on given tickers."""
    data_map = load_ticker_data(tickers)
    if not data_map:
        print("No data loaded!")
        return []

    results = []
    default_params = StrategyParams()

    for strategy_name in STRATEGY_NAMES:
        t0 = time.time()
        result = run_backtest(data_map, strategy_name, default_params)
        elapsed = time.time() - t0

        result["elapsed_s"] = round(elapsed, 1)
        results.append(result)

        print(f"\n  {strategy_name:12s} | "
              f"PnL: ${result['total_pnl']:+10,.2f} ({result['total_pnl_pct']:+6.2f}%) | "
              f"Trades: {result['total_trades']:3d} | "
              f"WR: {result['win_rate']:5.1f}% | "
              f"DD: {result['max_drawdown_pct']:5.2f}% | "
              f"{elapsed:.1f}s")

        # Per-ticker detail
        for sym, tb in result.get("ticker_breakdown", {}).items():
            print(f"    {sym:6s}: ${tb['pnl']:+8,.2f} ({tb['pnl_pct']:+5.2f}%)  "
                  f"T:{tb['trades']:3d}  WR:{tb['win_rate']:4.0f}%")

    # Save results
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"screening_{ts}.csv"
    if results:
        fieldnames = [
            "strategy", "total_pnl", "total_pnl_pct", "total_trades",
            "win_rate", "max_drawdown_pct", "avg_hold_bars", "elapsed_s",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in results:
                writer.writerow(r)
        print(f"\n  Saved to {csv_path}")

    return results


# ── Mode: Sweep ──────────────────────────────────────────────────────

def run_sweep(tickers: list[str], strategy_name: str) -> list[dict]:
    """Parameter sweep for a single strategy."""
    data_map = load_ticker_data(tickers)
    if not data_map:
        print("No data loaded!")
        return []

    # Define sweep grid
    stop_pcts = [0.01, 0.015, 0.02, 0.025, 0.03]
    max_holds = [195, 390, 780]

    if strategy_name == "v9_rsi":
        extra_params = [
            {"min_rsi_spread": 5.0},
            {"min_rsi_spread": 7.0},
            {"min_rsi_spread": 10.0},
        ]
    elif strategy_name == "orb":
        extra_params = [
            {"orb_target_mult": 0.5, "orb_vol_mult": 1.0},
            {"orb_target_mult": 1.0, "orb_vol_mult": 1.5},
            {"orb_target_mult": 1.5, "orb_vol_mult": 2.0},
        ]
    elif strategy_name == "ema_cross":
        extra_params = [
            {"ema_rsi_lo": 35.0, "ema_rsi_hi": 65.0},
            {"ema_rsi_lo": 40.0, "ema_rsi_hi": 60.0},
            {"ema_rsi_lo": 45.0, "ema_rsi_hi": 55.0},
        ]
    elif strategy_name == "vwap_revert":
        extra_params = [
            {"vwap_sigma": 1.0},
            {"vwap_sigma": 1.5},
            {"vwap_sigma": 2.0},
        ]
    else:
        extra_params = [{}]

    results = []
    total_runs = len(stop_pcts) * len(max_holds) * len(extra_params)
    run_num = 0

    print(f"\n  Sweeping {strategy_name}: {total_runs} combinations")
    t_start = time.time()

    for sl in stop_pcts:
        for mh in max_holds:
            for extra in extra_params:
                run_num += 1
                params = StrategyParams(stop_pct=sl, max_hold_bars=mh)
                # Apply strategy-specific params
                for k, v in extra.items():
                    setattr(params, k, v)
                # For ORB, also set orb_max_hold
                if strategy_name == "orb":
                    params.orb_max_hold = mh
                if strategy_name == "vwap_revert":
                    params.vwap_max_hold = mh

                result = run_backtest(data_map, strategy_name, params)
                result["stop_pct"] = sl
                result["max_hold_bars_sweep"] = mh
                result.update(extra)
                results.append(result)

                if run_num % 10 == 0 or run_num == total_runs:
                    elapsed = time.time() - t_start
                    print(f"  [{run_num}/{total_runs}] {elapsed:.0f}s elapsed")

    # Sort by P&L
    results.sort(key=lambda r: r.get("total_pnl_pct", 0), reverse=True)

    # Print top 10
    print(f"\n  {'='*80}")
    print(f"  TOP 10 PARAMETER COMBOS — {strategy_name}")
    print(f"  {'='*80}")
    for i, r in enumerate(results[:10]):
        print(f"  #{i+1}: SL={r.get('stop_pct',0)*100:.1f}%  MH={r.get('max_hold_bars_sweep',0)}  "
              f"PnL={r['total_pnl_pct']:+.2f}%  Trades={r['total_trades']}  "
              f"WR={r['win_rate']:.1f}%  DD={r['max_drawdown_pct']:.2f}%")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"sweep_{strategy_name}_{ts}.csv"
    if results:
        fieldnames = [
            "strategy", "stop_pct", "max_hold_bars_sweep", "total_pnl", "total_pnl_pct",
            "total_trades", "win_rate", "max_drawdown_pct", "avg_hold_bars",
        ]
        # Add strategy-specific columns
        for key in extra_params[0]:
            fieldnames.append(key)

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in results:
                writer.writerow({k: r.get(k, "") for k in fieldnames})
        print(f"\n  Saved to {csv_path}")

    return results


# ── Mode: Portfolio ──────────────────────────────────────────────────

def run_portfolio(tickers: list[str], strategy_name: str, params: StrategyParams | None = None):
    """Run portfolio backtest with best params."""
    data_map = load_ticker_data(tickers)
    if not data_map:
        print("No data loaded!")
        return

    if params is None:
        params = StrategyParams()

    t0 = time.time()
    result = run_backtest(data_map, strategy_name, params)
    elapsed = time.time() - t0

    print(f"\n  {'='*80}")
    print(f"  PORTFOLIO RESULT — {strategy_name}")
    print(f"  {'='*80}")
    print(f"  Tickers: {', '.join(data_map.keys())}")
    print(f"  PnL: ${result['total_pnl']:+,.2f} ({result['total_pnl_pct']:+.2f}%)")
    print(f"  Trades: {result['total_trades']}  |  Win rate: {result['win_rate']:.1f}%")
    print(f"  Max DD: ${result['max_drawdown']:,.2f} ({result['max_drawdown_pct']:.2f}%)")
    print(f"  Avg hold: {result['avg_hold_bars']:.0f} bars ({result['avg_hold_bars']/60:.1f}h)")
    print(f"  Trading days: {result['trading_days']}")
    print(f"  Time: {elapsed:.1f}s")

    print(f"\n  Exit reasons:")
    for reason, count in result.get("exit_reasons", {}).items():
        print(f"    {reason}: {count}")

    print(f"\n  Per-ticker:")
    for sym, tb in result.get("ticker_breakdown", {}).items():
        print(f"    {sym:6s}: ${tb['pnl']:+10,.2f} ({tb['pnl_pct']:+6.2f}%)  "
              f"T:{tb['trades']:3d}  WR:{tb['win_rate']:5.1f}%")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"portfolio_{strategy_name}_{ts}.csv"
    fieldnames = [
        "strategy", "total_pnl", "total_pnl_pct", "total_trades", "win_rate",
        "max_drawdown", "max_drawdown_pct", "avg_hold_bars", "trading_days",
        "starting_balance", "ending_balance",
    ]
    for sym in data_map:
        fieldnames.extend([f"{sym}_pnl", f"{sym}_pnl_pct", f"{sym}_trades", f"{sym}_win_rate"])

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        row = {k: result.get(k, "") for k in fieldnames[:11]}
        for sym in data_map:
            tb = result.get("ticker_breakdown", {}).get(sym, {})
            row[f"{sym}_pnl"] = tb.get("pnl", 0)
            row[f"{sym}_pnl_pct"] = tb.get("pnl_pct", 0)
            row[f"{sym}_trades"] = tb.get("trades", 0)
            row[f"{sym}_win_rate"] = tb.get("win_rate", 0)
        writer.writerow(row)
    print(f"\n  Saved to {csv_path}")

    return result


# ── Mode: Validate ───────────────────────────────────────────────────

def run_validate(tickers: list[str], strategy_name: str, params: StrategyParams | None = None):
    """Train/validate split: first 2/3 train, last 1/3 validate."""
    data_map = load_ticker_data(tickers)
    if not data_map:
        print("No data loaded!")
        return

    if params is None:
        params = StrategyParams()

    train_result = run_backtest(data_map, strategy_name, params,
                                start_bar_pct=0.0, end_bar_pct=0.667)
    val_result = run_backtest(data_map, strategy_name, params,
                              start_bar_pct=0.667, end_bar_pct=1.0)

    print(f"\n  {'='*80}")
    print(f"  TRAIN/VALIDATE — {strategy_name}")
    print(f"  {'='*80}")
    print(f"  {'':12s} {'TRAIN (first 2/3)':>20s}  {'VALIDATE (last 1/3)':>20s}")
    print(f"  {'PnL%':12s} {train_result['total_pnl_pct']:>18.2f}%  {val_result['total_pnl_pct']:>18.2f}%")
    print(f"  {'Trades':12s} {train_result['total_trades']:>19d}  {val_result['total_trades']:>19d}")
    print(f"  {'Win Rate':12s} {train_result['win_rate']:>18.1f}%  {val_result['win_rate']:>18.1f}%")
    print(f"  {'Max DD%':12s} {train_result['max_drawdown_pct']:>18.2f}%  {val_result['max_drawdown_pct']:>18.2f}%")

    robust = val_result["total_pnl_pct"] > 0 and val_result["win_rate"] > 45
    print(f"\n  Robust: {'YES' if robust else 'NO'}")

    # Save
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"validate_{strategy_name}_{ts}.csv"
    fieldnames = ["split", "total_pnl_pct", "total_trades", "win_rate", "max_drawdown_pct"]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow({"split": "train", **{k: train_result[k] for k in fieldnames[1:]}})
        writer.writerow({"split": "validate", **{k: val_result[k] for k in fieldnames[1:]}})
    print(f"  Saved to {csv_path}")

    return {"train": train_result, "validate": val_result, "robust": robust}


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Strategy Lab — fast backtester")
    parser.add_argument("--mode", choices=["rank", "screen", "sweep", "portfolio", "validate"],
                        default="rank", help="Operation mode")
    parser.add_argument("--strategy", choices=STRATEGY_NAMES, default="v9_rsi",
                        help="Strategy to test (for sweep/portfolio/validate)")
    parser.add_argument("--tickers", type=str, default="",
                        help="Comma-separated tickers (auto-ranked if empty)")
    parser.add_argument("--top-n", type=int, default=8,
                        help="Number of tickers to rank/use")
    parser.add_argument("--stop-pct", type=float, default=0.02)
    parser.add_argument("--target-pct", type=float, default=0.06)
    parser.add_argument("--max-hold", type=int, default=390)

    args = parser.parse_args()

    print(f"\n{'='*80}")
    print(f"  STRATEGY LAB — mode={args.mode}")
    print(f"{'='*80}")

    if args.mode == "rank":
        ranked = rank_tickers(top_n=args.top_n)
        print(f"\n  {'Rank':>4s}  {'Symbol':6s}  {'Range%':>7s}  {'$Vol(M)':>8s}  "
              f"{'AutoCorr':>9s}  {'Days':>5s}  {'Cover':>6s}  {'Score':>6s}")
        print(f"  {'-'*60}")
        for i, r in enumerate(ranked):
            print(f"  {i+1:4d}  {r['symbol']:6s}  {r['daily_range_pct']:7.2f}  "
                  f"{r['dollar_vol_M']:8.1f}  {r['autocorr']:9.3f}  "
                  f"{r['data_days']:5d}  {r['coverage']:6.2f}  {r['score']:6.2f}")

        # Extract top symbols for convenience
        top_syms = [r["symbol"] for r in ranked]
        print(f"\n  Top {len(top_syms)}: {','.join(top_syms)}")
        print(f"  Use: --tickers={','.join(top_syms[:6])}")
        return

    # Resolve tickers
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",")]
    else:
        print("  Auto-ranking tickers...")
        ranked = rank_tickers(top_n=args.top_n)
        tickers = [r["symbol"] for r in ranked]
        print(f"  Selected: {', '.join(tickers)}")

    if args.mode == "screen":
        run_screen(tickers)
    elif args.mode == "sweep":
        run_sweep(tickers, args.strategy)
    elif args.mode == "portfolio":
        params = StrategyParams(
            stop_pct=args.stop_pct,
            target_pct=args.target_pct,
            max_hold_bars=args.max_hold,
        )
        run_portfolio(tickers, args.strategy, params)
    elif args.mode == "validate":
        params = StrategyParams(
            stop_pct=args.stop_pct,
            target_pct=args.target_pct,
            max_hold_bars=args.max_hold,
        )
        run_validate(tickers, args.strategy, params)


if __name__ == "__main__":
    main()
