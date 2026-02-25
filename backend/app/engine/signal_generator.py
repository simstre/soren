"""Converts analysis results into actionable trading signals.

Entry rule: RSI divergence (from lower timeframe) + S/R level proximity.
Both must be present — no single-factor entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import pandas as pd
import structlog

from app.analysis.pipeline import AnalysisResult
from app.analysis.indicators import atr as calc_atr, bollinger_bands, vwap as calc_vwap, sma

if TYPE_CHECKING:
    from app.engine.divergence_tracker import DivergenceSignal

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Timeframe-adaptive parameters for the Refined RSI strategy
# ---------------------------------------------------------------------------

_MINUTES_PER_BAR = {
    "1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": 1440,
}


@dataclass(frozen=True)
class TimeframeParams:
    """All timeframe-dependent values used by the Refined RSI strategy."""
    timeframe: str
    cooldown_bars: int        # round(30 / minutes_per_bar)
    max_hold_minutes: int     # 120 for intraday, 7200 for daily
    rsi_buy_lo: float         # 30 (all)
    rsi_buy_hi: float         # 42 (1m) or 45 (others)
    rsi_sell_lo: float        # 58 (1m) or 55 (others)
    rsi_sell_hi: float        # 70 (all)
    min_rsi_spread: float     # 7 (1m) or 5 (others)
    atr_stop_mult: float      # 2.0 (all)
    atr_target_mult: float    # 3.0 (all)
    use_time_filters: bool    # True for intraday, False for 1D


def get_timeframe_params(timeframe: str) -> TimeframeParams:
    """Factory: compute all adaptive parameters from a timeframe string."""
    mpb = _MINUTES_PER_BAR.get(timeframe, 5)
    is_daily = timeframe == "1D"
    is_1m = timeframe == "1m"

    cooldown = max(1, round(30 / mpb))
    max_hold = 7200 if is_daily else 120

    rsi_buy_hi = 42.0 if is_1m else 45.0
    rsi_sell_lo = 58.0 if is_1m else 55.0
    min_rsi_spread = 7.0 if is_1m else 5.0

    return TimeframeParams(
        timeframe=timeframe,
        cooldown_bars=cooldown,
        max_hold_minutes=max_hold,
        rsi_buy_lo=30.0,
        rsi_buy_hi=rsi_buy_hi,
        rsi_sell_lo=rsi_sell_lo,
        rsi_sell_hi=70.0,
        min_rsi_spread=min_rsi_spread,
        atr_stop_mult=2.0,
        atr_target_mult=3.0,
        use_time_filters=not is_daily,
    )


@dataclass
class TradingSignal:
    symbol: str
    signal_type: str  # "BUY" or "SELL"
    entry_price: float
    stop_loss: float
    profit_target: float
    reason: str
    pattern_type: str | None
    confidence: float
    created_at: datetime
    has_rsi_divergence: bool = False
    sr_strength: float = 0.0
    divergence_timeframe: str | None = None


# ---------------------------------------------------------------------------
# RSI divergence detection (used by DivergenceTracker on lower-TF data)
# ---------------------------------------------------------------------------

@dataclass
class RSIDivergence:
    direction: str  # "bullish" or "bearish"
    rsi_current: float
    description: str
    rsi_spread: float = 0.0  # absolute RSI spread between divergence points


def detect_rsi_divergence(
    rsi_values: list[float],
    prices: list[float],
    lookback: int = 20,
) -> RSIDivergence | None:
    """Detect RSI divergence in the most recent `lookback` bars.

    Bullish divergence: price makes lower low but RSI makes higher low
    Bearish divergence: price makes higher high but RSI makes lower high
    """
    if len(rsi_values) < lookback or len(prices) < lookback:
        return None

    recent_rsi = rsi_values[-lookback:]
    recent_prices = prices[-lookback:]

    mid = lookback // 2
    first_half_prices = recent_prices[:mid]
    second_half_prices = recent_prices[mid:]
    first_half_rsi = recent_rsi[:mid]
    second_half_rsi = recent_rsi[mid:]

    current_rsi = rsi_values[-1]

    # Bearish divergence: price higher high, RSI lower high
    price_high_1 = max(first_half_prices)
    price_high_2 = max(second_half_prices)
    rsi_high_1 = max(first_half_rsi)
    rsi_high_2 = max(second_half_rsi)

    if price_high_2 > price_high_1 and rsi_high_2 < rsi_high_1 - 2:
        spread = abs(rsi_high_1 - rsi_high_2)
        return RSIDivergence(
            direction="bearish",
            rsi_current=current_rsi,
            description=f"Bearish RSI div (RSI {rsi_high_2:.0f} vs {rsi_high_1:.0f})",
            rsi_spread=spread,
        )

    # Bullish divergence: price lower low, RSI higher low
    price_low_1 = min(first_half_prices)
    price_low_2 = min(second_half_prices)
    rsi_low_1 = min(first_half_rsi)
    rsi_low_2 = min(second_half_rsi)

    if price_low_2 < price_low_1 and rsi_low_2 > rsi_low_1 + 2:
        spread = abs(rsi_low_2 - rsi_low_1)
        return RSIDivergence(
            direction="bullish",
            rsi_current=current_rsi,
            description=f"Bullish RSI div (RSI {rsi_low_2:.0f} vs {rsi_low_1:.0f})",
            rsi_spread=spread,
        )

    return None


def check_reversal_signal(
    analysis: AnalysisResult,
    current_side: str,
    prices: list[float],
) -> bool:
    """Check if there's a confirmed reversal against the current position.

    Requires RSI divergence opposite to position direction, or RSI extreme
    zone with declining momentum.
    """
    rsi_val = analysis.rsi_current

    divergence = detect_rsi_divergence(analysis.rsi_values, prices)
    if divergence:
        if current_side == "BUY" and divergence.direction == "bearish":
            return True
        if current_side == "SELL" and divergence.direction == "bullish":
            return True

    # RSI extreme zones with momentum shift
    if current_side == "BUY" and rsi_val > 75:
        if len(analysis.rsi_values) >= 3:
            r = analysis.rsi_values
            if r[-1] < r[-2] < r[-3]:
                return True

    if current_side == "SELL" and rsi_val < 25:
        if len(analysis.rsi_values) >= 3:
            r = analysis.rsi_values
            if r[-1] > r[-2] > r[-3]:
                return True

    return False


# ---------------------------------------------------------------------------
# S/R proximity detection
# ---------------------------------------------------------------------------

@dataclass
class _SRFactor:
    """An S/R level near the current price."""
    side: str          # "BUY" or "SELL"
    confidence: float
    reason: str
    target: float
    stop: float
    sr_strength: float


def _detect_sr_factors(
    analysis: AnalysisResult, price: float,
) -> list[_SRFactor]:
    """Detect S/R level proximity factors."""
    factors: list[_SRFactor] = []

    for level in analysis.sr_levels:
        distance_pct = abs(price - level.price) / price * 100
        if distance_pct > 0.5:
            continue

        base_conf = min(0.5, 0.3 + level.strength * 0.05)

        if level.level_type == "support" and price >= level.price:
            resistances = sorted(
                [lv for lv in analysis.sr_levels if lv.level_type == "resistance" and lv.price > price],
                key=lambda lv: lv.price,
            )
            target = resistances[0].price if resistances else round(price * 1.02, 2)
            stop = round(level.price * 0.985, 2)

            factors.append(_SRFactor(
                side="BUY",
                confidence=base_conf,
                reason=f"Support ${level.price:.2f} ({level.touch_count}T, str {level.strength:.1f})",
                target=target,
                stop=stop,
                sr_strength=level.strength,
            ))

        elif level.level_type == "resistance" and price <= level.price:
            supports = sorted(
                [lv for lv in analysis.sr_levels if lv.level_type == "support" and lv.price < price],
                key=lambda lv: lv.price,
                reverse=True,
            )
            target = supports[0].price if supports else round(price * 0.98, 2)
            stop = round(level.price * 1.015, 2)

            factors.append(_SRFactor(
                side="SELL",
                confidence=base_conf,
                reason=f"Resistance ${level.price:.2f} ({level.touch_count}T, str {level.strength:.1f})",
                target=target,
                stop=stop,
                sr_strength=level.strength,
            ))

    return factors


# ---------------------------------------------------------------------------
# Signal generation — requires S/R + RSI divergence
# ---------------------------------------------------------------------------

def generate_signals(
    analysis: AnalysisResult,
    stop_loss_pct: float | None = None,
    best_divergence: DivergenceSignal | None = None,
) -> list[TradingSignal]:
    """Generate trading signals requiring S/R proximity + RSI divergence.

    Both conditions must be present:
    1. Price is at/near a support or resistance level
    2. RSI divergence detected on a lower timeframe (passed in from engine)

    No divergence or no S/R → no trade.
    """
    price = analysis.current_price
    if price <= 0:
        return []

    # Must have divergence from lower timeframe
    if best_divergence is None:
        return []

    rsi_val = analysis.rsi_current

    # Hard RSI filter: don't buy overbought, don't sell oversold
    allow_buy = rsi_val < 70
    allow_sell = rsi_val > 30

    # Detect S/R proximity
    sr_factors = _detect_sr_factors(analysis, price)
    if not sr_factors:
        return []

    # Map divergence direction to trade side
    div_side = "BUY" if best_divergence.direction == "bullish" else "SELL"

    # Find S/R factors that agree with the divergence direction
    agreeing = [f for f in sr_factors if f.side == div_side]
    if not agreeing:
        return []

    if div_side == "BUY" and not allow_buy:
        return []
    if div_side == "SELL" and not allow_sell:
        return []

    # Use the strongest agreeing S/R factor
    best_sr = max(agreeing, key=lambda f: f.sr_strength)

    # Confidence = S/R confidence + divergence weight
    confidence = min(0.95, best_sr.confidence + best_divergence.weight * 0.4)

    # Apply stop loss override
    if stop_loss_pct is not None:
        if stop_loss_pct == 0:
            stop = 0  # No stop loss
        elif div_side == "BUY":
            stop = round(price * (1 - stop_loss_pct), 2)
        else:
            stop = round(price * (1 + stop_loss_pct), 2)
    else:
        stop = best_sr.stop

    reason = f"{best_sr.reason} + {best_divergence.description} ({best_divergence.timeframe})"

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=div_side,
            entry_price=price,
            stop_loss=stop,
            profit_target=best_sr.target,
            reason=reason,
            pattern_type=None,
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
            has_rsi_divergence=True,
            sr_strength=best_sr.sr_strength,
            divergence_timeframe=best_divergence.timeframe,
        )
    ]


# ---------------------------------------------------------------------------
# Mean Reversion strategy
# ---------------------------------------------------------------------------

def generate_signals_mean_reversion(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    stop_loss_pct: float | None = None,
) -> list[TradingSignal]:
    """Entry: price touches Bollinger Band + RSI extreme (<30 buy, >70 sell)."""
    price = analysis.current_price
    if price <= 0 or len(df) < 20:
        return []

    upper, middle, lower = bollinger_bands(df["close"], period=20, std_dev=2.0)
    rsi_val = analysis.rsi_current

    bb_upper = upper.iloc[-1]
    bb_lower = lower.iloc[-1]
    bb_mid = middle.iloc[-1]

    if pd.isna(bb_upper) or pd.isna(bb_lower):
        return []

    signal_type = None
    reason = ""

    # Buy: price at or below lower band + RSI < 30
    if price <= bb_lower and rsi_val < 30:
        signal_type = "BUY"
        reason = f"Mean reversion BUY: price ${price:.2f} <= BB lower ${bb_lower:.2f}, RSI {rsi_val:.0f}"
    # Sell: price at or above upper band + RSI > 70
    elif price >= bb_upper and rsi_val > 70:
        signal_type = "SELL"
        reason = f"Mean reversion SELL: price ${price:.2f} >= BB upper ${bb_upper:.2f}, RSI {rsi_val:.0f}"

    if signal_type is None:
        return []

    if stop_loss_pct is not None:
        if stop_loss_pct == 0:
            stop = 0  # No stop loss
        elif signal_type == "BUY":
            stop = round(price * (1 - stop_loss_pct), 2)
        else:
            stop = round(price * (1 + stop_loss_pct), 2)
    else:
        stop = round(price * (0.98 if signal_type == "BUY" else 1.02), 2)

    target = round(bb_mid, 2)
    confidence = min(0.85, 0.5 + abs(rsi_val - 50) / 100)

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=signal_type,
            entry_price=price,
            stop_loss=stop,
            profit_target=target,
            reason=reason,
            pattern_type="mean_reversion",
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )
    ]


def check_exit_mean_reversion(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    current_side: str,
) -> bool:
    """Exit when price crosses the SMA(20) midline."""
    if len(df) < 20:
        return False
    mid = sma(df["close"], 20).iloc[-1]
    if pd.isna(mid):
        return False
    price = analysis.current_price
    if current_side == "BUY" and price >= mid:
        return True
    if current_side == "SELL" and price <= mid:
        return True
    return False


# ---------------------------------------------------------------------------
# Momentum Breakout strategy
# ---------------------------------------------------------------------------

def generate_signals_momentum_breakout(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    stop_loss_pct: float | None = None,
) -> list[TradingSignal]:
    """Entry: price breaks S/R level with volume surge (>1.5x 20-bar avg)."""
    price = analysis.current_price
    if price <= 0 or len(df) < 20:
        return []

    vol = df["volume"]
    avg_vol = vol.rolling(20).mean().iloc[-1]
    cur_vol = vol.iloc[-1]

    if pd.isna(avg_vol) or avg_vol == 0 or cur_vol < avg_vol * 1.5:
        return []

    signal_type = None
    reason = ""
    target = price
    sr_strength = 0.0

    for level in analysis.sr_levels:
        dist_pct = abs(price - level.price) / price * 100
        if dist_pct > 0.5:
            continue

        if level.level_type == "resistance" and price >= level.price:
            # Breakout above resistance → BUY
            resistances_above = sorted(
                [lv for lv in analysis.sr_levels if lv.level_type == "resistance" and lv.price > price],
                key=lambda lv: lv.price,
            )
            target = resistances_above[0].price if resistances_above else round(price * 1.03, 2)
            signal_type = "BUY"
            sr_strength = level.strength
            reason = (
                f"Momentum breakout BUY: broke resistance ${level.price:.2f}, "
                f"vol {cur_vol/avg_vol:.1f}x avg"
            )
            break

        elif level.level_type == "support" and price <= level.price:
            # Breakdown below support → SELL
            supports_below = sorted(
                [lv for lv in analysis.sr_levels if lv.level_type == "support" and lv.price < price],
                key=lambda lv: lv.price,
                reverse=True,
            )
            target = supports_below[0].price if supports_below else round(price * 0.97, 2)
            signal_type = "SELL"
            sr_strength = level.strength
            reason = (
                f"Momentum breakout SELL: broke support ${level.price:.2f}, "
                f"vol {cur_vol/avg_vol:.1f}x avg"
            )
            break

    if signal_type is None:
        return []

    if stop_loss_pct is not None:
        if stop_loss_pct == 0:
            stop = 0  # No stop loss
        elif signal_type == "BUY":
            stop = round(price * (1 - stop_loss_pct), 2)
        else:
            stop = round(price * (1 + stop_loss_pct), 2)
    else:
        stop = round(price * (0.985 if signal_type == "BUY" else 1.015), 2)

    confidence = min(0.9, 0.4 + sr_strength * 0.1)

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=signal_type,
            entry_price=price,
            stop_loss=stop,
            profit_target=target,
            reason=reason,
            pattern_type="momentum_breakout",
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
            sr_strength=sr_strength,
        )
    ]


def check_exit_momentum(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    current_side: str,
) -> bool:
    """Exit on reversal candle (engulfing pattern against position)."""
    if len(df) < 2:
        return False
    prev = df.iloc[-2]
    curr = df.iloc[-1]

    if current_side == "BUY":
        # Bearish engulfing
        if curr["close"] < curr["open"] and curr["close"] < prev["low"]:
            return True
    elif current_side == "SELL":
        # Bullish engulfing
        if curr["close"] > curr["open"] and curr["close"] > prev["high"]:
            return True
    return False


# ---------------------------------------------------------------------------
# VWAP Bounce strategy
# ---------------------------------------------------------------------------

def generate_signals_vwap_bounce(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    stop_loss_pct: float | None = None,
) -> list[TradingSignal]:
    """Entry: price near VWAP + RSI confirmation (>40 buy, <60 sell)."""
    price = analysis.current_price
    if price <= 0 or len(df) < 20:
        return []

    vwap_series = calc_vwap(df)
    vwap_val = vwap_series.iloc[-1]

    if pd.isna(vwap_val) or vwap_val <= 0:
        return []

    dist_pct = abs(price - vwap_val) / vwap_val * 100
    if dist_pct > 0.5:
        return []

    rsi_val = analysis.rsi_current
    signal_type = None
    reason = ""

    if price >= vwap_val and rsi_val > 40:
        signal_type = "BUY"
        reason = f"VWAP bounce BUY: price ${price:.2f} near VWAP ${vwap_val:.2f}, RSI {rsi_val:.0f}"
    elif price <= vwap_val and rsi_val < 60:
        signal_type = "SELL"
        reason = f"VWAP bounce SELL: price ${price:.2f} near VWAP ${vwap_val:.2f}, RSI {rsi_val:.0f}"

    if signal_type is None:
        return []

    # Target: nearest S/R level or 1.5% move
    if signal_type == "BUY":
        resistances = sorted(
            [lv for lv in analysis.sr_levels if lv.level_type == "resistance" and lv.price > price],
            key=lambda lv: lv.price,
        )
        target = resistances[0].price if resistances else round(price * 1.015, 2)
    else:
        supports = sorted(
            [lv for lv in analysis.sr_levels if lv.level_type == "support" and lv.price < price],
            key=lambda lv: lv.price,
            reverse=True,
        )
        target = supports[0].price if supports else round(price * 0.985, 2)

    if stop_loss_pct is not None:
        if stop_loss_pct == 0:
            stop = 0  # No stop loss
        elif signal_type == "BUY":
            stop = round(price * (1 - stop_loss_pct), 2)
        else:
            stop = round(price * (1 + stop_loss_pct), 2)
    else:
        stop = round(price * (0.99 if signal_type == "BUY" else 1.01), 2)

    confidence = min(0.8, 0.45 + (1 - dist_pct / 0.5) * 0.2)

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=signal_type,
            entry_price=price,
            stop_loss=stop,
            profit_target=target,
            reason=reason,
            pattern_type="vwap_bounce",
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
        )
    ]


def check_exit_vwap(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    current_side: str,
) -> bool:
    """Exit when price hits VWAP deviation extreme (>2 std devs from VWAP)."""
    if len(df) < 20:
        return False

    vwap_series = calc_vwap(df)
    vwap_val = vwap_series.iloc[-1]
    if pd.isna(vwap_val) or vwap_val <= 0:
        return False

    price = analysis.current_price
    dev = (df["close"] - vwap_series).std()
    if pd.isna(dev) or dev == 0:
        return False

    z = (price - vwap_val) / dev

    if current_side == "BUY" and z > 2.0:
        return True
    if current_side == "SELL" and z < -2.0:
        return True
    return False


# ---------------------------------------------------------------------------
# Exhaustion Reversal strategy
# ---------------------------------------------------------------------------

def generate_signals_exhaustion_reversal(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    stop_loss_pct: float | None = None,
) -> list[TradingSignal]:
    """Fade exhaustion moves: ATR spike + volume climax + RSI extreme + S/R proximity.

    All four conditions must fire simultaneously — this keeps false signals
    low on noisy 1m charts.
    """
    price = analysis.current_price
    if price <= 0 or len(df) < 20:
        return []

    # --- Factor 1: ATR expansion (current bar's true range vs ATR-14) ---
    atr_series = calc_atr(df, period=14)
    atr_val = atr_series.iloc[-1]
    if pd.isna(atr_val) or atr_val <= 0:
        return []

    last = df.iloc[-1]
    bar_range = float(last["high"]) - float(last["low"])
    if bar_range < atr_val * 1.5:
        return []

    # --- Factor 2: Volume climax (>2x 20-bar average) ---
    vol = df["volume"]
    avg_vol = vol.rolling(20).mean().iloc[-1]
    cur_vol = vol.iloc[-1]
    if pd.isna(avg_vol) or avg_vol == 0 or cur_vol < avg_vol * 2:
        return []

    # --- Factor 3: RSI extreme ---
    rsi_val = analysis.rsi_current
    signal_type = None

    if rsi_val < 25:
        signal_type = "BUY"
    elif rsi_val > 75:
        signal_type = "SELL"
    else:
        return []

    # --- Factor 4: S/R proximity ---
    sr_factors = _detect_sr_factors(analysis, price)
    agreeing = [f for f in sr_factors if f.side == signal_type]
    if not agreeing:
        return []

    best_sr = max(agreeing, key=lambda f: f.sr_strength)

    # Confidence scales with how extreme each factor is
    vol_ratio = min(cur_vol / avg_vol, 5.0)
    atr_ratio = min(bar_range / atr_val, 4.0)
    rsi_extremity = abs(rsi_val - 50) / 50  # 0→1
    confidence = min(0.95, 0.3 + rsi_extremity * 0.25 + (vol_ratio - 2) / 6 * 0.2 + (atr_ratio - 1.5) / 5 * 0.2)

    if stop_loss_pct is not None:
        if stop_loss_pct == 0:
            stop = 0  # No stop loss
        elif signal_type == "BUY":
            stop = round(price * (1 - stop_loss_pct), 2)
        else:
            stop = round(price * (1 + stop_loss_pct), 2)
    else:
        stop = round(price * (0.985 if signal_type == "BUY" else 1.015), 2)

    reason = (
        f"Exhaustion reversal {signal_type}: "
        f"ATR {atr_ratio:.1f}x, vol {vol_ratio:.1f}x avg, "
        f"RSI {rsi_val:.0f}, {best_sr.reason}"
    )

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=signal_type,
            entry_price=price,
            stop_loss=stop,
            profit_target=best_sr.target,
            reason=reason,
            pattern_type="exhaustion_reversal",
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
            sr_strength=best_sr.sr_strength,
        )
    ]


def check_exit_exhaustion(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    current_side: str,
) -> bool:
    """Exit when RSI crosses 50 (mean reversion done) or ATR contracts."""
    rsi_val = analysis.rsi_current

    # RSI crossing 50 = exhaustion move has reverted
    if current_side == "BUY" and rsi_val >= 50:
        return True
    if current_side == "SELL" and rsi_val <= 50:
        return True

    # Volatility normalization — ATR contracting back below average
    if len(df) >= 20:
        atr_series = calc_atr(df, period=14)
        atr_val = atr_series.iloc[-1]
        atr_avg = atr_series.rolling(20).mean().iloc[-1]
        if not pd.isna(atr_val) and not pd.isna(atr_avg) and atr_avg > 0:
            if atr_val < atr_avg:
                return True

    return False


# ---------------------------------------------------------------------------
# Refined RSI Divergence strategy (data-driven optimization)
# ---------------------------------------------------------------------------

def generate_signals_refined_rsi(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    stop_loss_pct: float | None = None,
    best_divergence: "DivergenceSignal | None" = None,
    current_timestamp: "datetime | None" = None,
    tf_params: "TimeframeParams | None" = None,
) -> list[TradingSignal]:
    """Market-direction-agnostic RSI divergence + S/R strategy.

    Timeframe-adaptive: all thresholds scale via ``tf_params``.
    Defaults to 5m params when ``tf_params`` is None (backward compat).
    """
    if tf_params is None:
        tf_params = get_timeframe_params("5m")

    price = analysis.current_price
    if price <= 0 or len(df) < 20:
        return []

    # Must have divergence
    if best_divergence is None:
        return []

    # --- Filter 1: Minimum divergence strength ---
    if best_divergence.rsi_spread < tf_params.min_rsi_spread:
        return []

    # --- Filter 2: No late-day entries (intraday only) ---
    if tf_params.use_time_filters and current_timestamp is not None:
        hour = current_timestamp.hour if hasattr(current_timestamp, "hour") else 12
        minute = current_timestamp.minute if hasattr(current_timestamp, "minute") else 0
        if hour == 15 and minute >= 30:
            return []

    rsi_val = analysis.rsi_current

    # --- Filter 3: Adaptive RSI band ---
    allow_buy = tf_params.rsi_buy_lo <= rsi_val < tf_params.rsi_buy_hi
    allow_sell = tf_params.rsi_sell_lo < rsi_val <= tf_params.rsi_sell_hi

    # --- Filter 4: S/R proximity with minimum strength >= 5 ---
    sr_factors = _detect_sr_factors(analysis, price)
    sr_factors = [f for f in sr_factors if f.sr_strength >= 5]
    if not sr_factors:
        return []

    # Map divergence direction to trade side
    div_side = "BUY" if best_divergence.direction == "bullish" else "SELL"

    agreeing = [f for f in sr_factors if f.side == div_side]
    if not agreeing:
        return []

    if div_side == "BUY" and not allow_buy:
        return []
    if div_side == "SELL" and not allow_sell:
        return []

    best_sr = max(agreeing, key=lambda f: f.sr_strength)

    confidence = min(0.95, best_sr.confidence + best_divergence.weight * 0.4)

    # --- ATR-based stop loss ---
    atr_series = calc_atr(df, period=14)
    atr_val = atr_series.iloc[-1]

    if stop_loss_pct is not None and stop_loss_pct > 0:
        atr_stop_dist = atr_val * tf_params.atr_stop_mult if not pd.isna(atr_val) else price * stop_loss_pct
        fixed_stop_dist = price * stop_loss_pct
        stop_dist = min(atr_stop_dist, fixed_stop_dist)
    elif not pd.isna(atr_val):
        stop_dist = atr_val * tf_params.atr_stop_mult
    else:
        stop_dist = price * 0.02

    if div_side == "BUY":
        stop = round(price - stop_dist, 2)
    else:
        stop = round(price + stop_dist, 2)

    # --- ATR-based profit target ---
    if not pd.isna(atr_val):
        target_dist = atr_val * tf_params.atr_target_mult
    else:
        target_dist = price * 0.01

    if div_side == "BUY":
        target = round(price + target_dist, 2)
    else:
        target = round(price - target_dist, 2)

    reason = (
        f"{best_sr.reason} + {best_divergence.description} ({best_divergence.timeframe}) "
        f"[spread={best_divergence.rsi_spread:.0f}] [refined-v9-{tf_params.timeframe}]"
    )

    return [
        TradingSignal(
            symbol=analysis.symbol,
            signal_type=div_side,
            entry_price=price,
            stop_loss=stop,
            profit_target=target,
            reason=reason,
            pattern_type="refined_rsi",
            confidence=confidence,
            created_at=datetime.now(timezone.utc),
            has_rsi_divergence=True,
            sr_strength=best_sr.sr_strength,
            divergence_timeframe=best_divergence.timeframe,
        )
    ]


def check_exit_refined_rsi(
    analysis: AnalysisResult,
    df: pd.DataFrame,
    current_side: str,
    entry_time: datetime | None = None,
    current_time: datetime | None = None,
    tf_params: "TimeframeParams | None" = None,
) -> bool:
    """Exit logic for refined RSI strategy.

    Timeframe-adaptive via ``tf_params``.
    1. Max hold (tf_params.max_hold_minutes)
    2. Force exit before close (15:50) — intraday only
    3. RSI reversal detection (momentum shift)
    """
    if tf_params is None:
        tf_params = get_timeframe_params("5m")

    if entry_time is not None and current_time is not None:
        held_minutes = (current_time - entry_time).total_seconds() / 60

        if held_minutes >= tf_params.max_hold_minutes:
            return True

        # Force exit before market close (intraday only)
        if tf_params.use_time_filters:
            hour = current_time.hour if hasattr(current_time, "hour") else 12
            minute = current_time.minute if hasattr(current_time, "minute") else 0
            if hour == 15 and minute >= 50:
                return True

    # RSI reversal: momentum fading against position
    rsi_val = analysis.rsi_current

    if current_side == "BUY" and rsi_val > 65:
        if len(analysis.rsi_values) >= 3:
            r = analysis.rsi_values
            if r[-1] < r[-2] < r[-3]:
                return True

    if current_side == "SELL" and rsi_val < 35:
        if len(analysis.rsi_values) >= 3:
            r = analysis.rsi_values
            if r[-1] > r[-2] > r[-3]:
                return True

    return False
