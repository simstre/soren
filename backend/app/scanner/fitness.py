"""Strategy-fitness scanner — two-phase scoring for the refined RSI strategy.

Phase 1: Fast daily pre-filter (price, volume, ATR hard cuts) → ~40 candidates
Phase 2: Score on actual 1m strategy-relevant metrics:
         - Signal density: count RSI divergences + S/R co-occurrences on 7d 1m data.
           This directly measures how many entry opportunities the strategy gets.
           Tickers with more signals = more trades = more profit opportunity.
         - Volume floor: minimum volume for reliable 1m candles.

Validated against 10-ticker backtests:
  INTC scored #1 by proxy metrics but LOST -$1,216.
  AMD scored #12 by proxy metrics but WON +$1,622.
  Signal density correctly predicts: AMD 27 trades, INTC 4 trades (gated).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf
import structlog

from app.analysis.indicators import atr as calc_atr, rsi as calc_rsi
from app.analysis.support_resistance import detect_sr_levels
from app.engine.signal_generator import detect_rsi_divergence

logger = structlog.get_logger()


@dataclass
class FitnessScore:
    symbol: str
    total_score: float  # 0-100
    # Metrics
    price: float
    avg_volume: int
    atr_pct: float
    sr_strong_count: int        # S/R levels with strength >= 5 on 1m
    # Score components
    volume_score: float
    price_score: float
    sr_score: float
    atr_score: float
    disqualified: bool
    disqualify_reason: str


def _empty_score(symbol: str, disqualify_reason: str, price: float = 0,
                 avg_volume: int = 0, atr_pct: float = 0) -> FitnessScore:
    """Return a zeroed-out disqualified score."""
    return FitnessScore(
        symbol=symbol, total_score=0, price=price, avg_volume=avg_volume,
        atr_pct=atr_pct, sr_strong_count=0,
        volume_score=0, price_score=0, sr_score=0, atr_score=0,
        disqualified=True, disqualify_reason=disqualify_reason,
    )


# ---------------------------------------------------------------------------
# Scoring functions
#
# Signal density (75 pts): How many valid strategy setups exist on 1m data.
#   This directly measures whether the strategy can trade the ticker.
#   Uses strict matching: divergence + matching S/R + RSI in correct band.
# Volume (10 pts): Minimum liquidity for reliable 1m candles.
#   Once a ticker has 10M+ volume, more volume doesn't improve performance.
# ATR range (15 pts): Moderate-to-high volatility = bigger moves per trade.
# ---------------------------------------------------------------------------

def _score_signal_density(setup_count: int) -> float:
    """0-75 pts. Based on valid setup count (div + S/R + RSI band + direction match).

    Tickers with more valid setups give the strategy more entry opportunities.
    20+ valid setups in 7 days of 1m data → max score.
    """
    if setup_count <= 0:
        return 0.0
    if setup_count >= 20:
        return 75.0
    return setup_count / 20 * 75


def _score_volume(avg_volume: float) -> float:
    """0-10 pts. Once above 10M, volume barely matters for this strategy.

    Just need enough for reliable 1m candles and reasonable spreads.
    """
    if avg_volume < 5_000_000:
        return 0.0
    if avg_volume >= 50_000_000:
        return 10.0
    return (avg_volume - 5_000_000) / (50_000_000 - 5_000_000) * 10


def _score_atr_range(atr_pct: float) -> float:
    """0-15 pts. Peak at 1.5-5.0% daily ATR range.

    Higher volatility means bigger moves per trade. AMD at 4.9% was top performer.
    """
    if atr_pct < 0.5:
        return atr_pct / 0.5 * 5
    if atr_pct < 1.5:
        return 5.0 + (atr_pct - 0.5) / 1.0 * 10  # 5 → 15
    if atr_pct <= 5.0:
        return 15.0
    if atr_pct <= 7.0:
        return 15.0 - (atr_pct - 5.0) / 2.0 * 15  # 15 → 0
    return 0.0


# ---------------------------------------------------------------------------
# Phase 1: Daily pre-filter (hard cuts only)
# ---------------------------------------------------------------------------

async def _phase1_daily_filter(
    universe: list[str],
    top_n: int = 40,
) -> list[tuple[str, float, int, float]]:
    """Fetch daily data, apply hard disqualifiers, return passing tickers.

    Hard disqualifiers: price < $10, volume < 500K, ATR% > 7%.
    Returns list of (symbol, price, avg_volume, atr_pct).
    """
    candidates: list[tuple[str, float, int, float]] = []

    for symbol in universe:
        try:
            ticker = yf.Ticker(symbol)

            df = pd.DataFrame()
            for _attempt in range(3):
                df = ticker.history(period="70d", interval="1d")
                if not df.empty:
                    break

            if df.empty or len(df) < 20:
                logger.debug("phase1_skip", symbol=symbol, reason="insufficient data")
                continue

            df.columns = [c.lower() for c in df.columns]

            price = float(df["close"].iloc[-1])
            avg_volume = int(df["volume"].mean())

            atr_series = calc_atr(df, period=14)
            current_atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else 0
            atr_pct = (current_atr / price * 100) if price > 0 else 0

            if price < 10:
                continue
            if avg_volume < 500_000:
                continue
            if atr_pct > 7:
                continue

            candidates.append((symbol, price, avg_volume, atr_pct))

        except Exception as e:
            logger.debug("phase1_error", symbol=symbol, error=str(e))
            continue

    logger.info("phase1_complete", total=len(universe), passed=len(candidates))
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Phase 2: Score on actual 1m strategy signals
# ---------------------------------------------------------------------------

def _count_signals_on_1m(df: pd.DataFrame) -> tuple[int, int, int, int, int]:
    """Slide through 1m data counting valid strategy setups and checking signal quality.

    Returns (div_count, setup_count, winning_setups, sr_strong_count, total_windows).
    A 'setup' = divergence + matching S/R + RSI in valid band.
    A 'winning_setup' = setup where price moved in the predicted direction within 30 bars.
    """
    if len(df) < 40:
        return 0, 0, 0, 0, 0

    closes = df["close"].values.tolist()
    rsi_series = calc_rsi(df["close"], period=14)
    rsi_values = rsi_series.values.tolist()

    # Detect S/R levels on the full 1m dataset
    sr_levels = detect_sr_levels(df)
    sr_strong = [lvl for lvl in sr_levels if lvl.strength >= 5]
    sr_strong_count = len(sr_strong)

    # Separate support and resistance for direction matching
    supports = [lvl for lvl in sr_strong if lvl.level_type == "support"]
    resistances = [lvl for lvl in sr_strong if lvl.level_type == "resistance"]

    div_count = 0
    setup_count = 0
    winning_setups = 0
    window_size = 20
    step = 5
    total_windows = 0
    lookahead = 30  # bars to check after signal

    for i in range(window_size, len(closes), step):
        total_windows += 1

        window_rsi = [v for v in rsi_values[i - window_size:i] if not np.isnan(v)]
        window_prices = closes[i - window_size:i]

        if len(window_rsi) < window_size or len(window_prices) < window_size:
            continue

        div = detect_rsi_divergence(window_rsi, window_prices, lookback=window_size)
        if div is None:
            continue
        if div.rsi_spread < 7:
            continue

        current_rsi = window_rsi[-1]
        current_price = closes[i - 1]

        # Must pass RSI band filter (match strategy's bands for 1m)
        if div.direction == "bullish":
            if not (30 <= current_rsi < 42):
                continue
        else:  # bearish
            if not (58 < current_rsi <= 70):
                continue

        div_count += 1

        # Check direction-matched S/R proximity (within 0.5%)
        levels_to_check = supports if div.direction == "bullish" else resistances
        is_setup = False
        for lvl in levels_to_check:
            dist_pct = abs(current_price - lvl.price) / current_price * 100
            if dist_pct <= 0.5:
                is_setup = True
                break

        if not is_setup:
            continue

        setup_count += 1

        # Signal quality check: did price move in predicted direction within 30 bars?
        if i + lookahead <= len(closes):
            future_prices = closes[i:i + lookahead]
            if div.direction == "bullish":
                # Check if price went up by at least 0.1%
                max_future = max(future_prices)
                if max_future > current_price * 1.001:
                    winning_setups += 1
            else:
                # Check if price went down by at least 0.1%
                min_future = min(future_prices)
                if min_future < current_price * 0.999:
                    winning_setups += 1

    return div_count, setup_count, winning_setups, sr_strong_count, total_windows


async def _score_ticker(
    symbol: str,
    price: float,
    avg_volume: int,
    atr_pct: float,
) -> FitnessScore:
    """Fetch 7 days of 1m data, count strategy signals, then score."""
    try:
        ticker = yf.Ticker(symbol)

        df = pd.DataFrame()
        for _attempt in range(3):
            df = ticker.history(period="7d", interval="1m")
            if not df.empty:
                break

        if df.empty or len(df) < 100:
            return _empty_score(symbol, "insufficient 1m data", price, avg_volume, atr_pct)

        df.columns = [c.lower() for c in df.columns]

        # Count actual strategy signals on 1m data
        div_count, setup_count, winning_setups, sr_strong_count, total_windows = _count_signals_on_1m(df)

        # Score components
        signal_s = _score_signal_density(setup_count)
        vol_s = _score_volume(avg_volume)
        atr_s = _score_atr_range(atr_pct)

        total = signal_s + vol_s + atr_s

        win_rate = winning_setups / setup_count * 100 if setup_count > 0 else 0
        logger.info(
            "phase2_signals",
            symbol=symbol,
            div_count=div_count,
            setup_count=setup_count,
            winning=winning_setups,
            win_rate=f"{win_rate:.0f}%",
            sr_strong=sr_strong_count,
            signal_score=round(signal_s, 1),
        )

        return FitnessScore(
            symbol=symbol,
            total_score=round(total, 1),
            price=round(price, 2),
            avg_volume=avg_volume,
            atr_pct=round(atr_pct, 2),
            sr_strong_count=sr_strong_count,
            volume_score=round(vol_s, 1),
            price_score=round(signal_s, 1),  # repurposed: signal density score
            sr_score=0,  # no longer scored separately
            atr_score=round(atr_s, 1),
            disqualified=False,
            disqualify_reason="",
        )

    except Exception as e:
        logger.error("phase2_error", symbol=symbol, error=str(e))
        return _empty_score(symbol, f"error: {str(e)[:100]}", price, avg_volume, atr_pct)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def score_ticker(symbol: str, bars: int = 60) -> FitnessScore:
    """Score a single ticker through both phases. Backward-compatible entry point."""
    candidates = await _phase1_daily_filter([symbol], top_n=1)
    if not candidates:
        return _empty_score(symbol, "failed Phase 1 filter")
    sym, price, avg_volume, atr_pct = candidates[0]
    return await _score_ticker(sym, price, avg_volume, atr_pct)


async def run_fitness_scan(
    universe: list[str] | None = None,
    top_n: int = 15,
) -> list[FitnessScore]:
    """Two-phase fitness scan: daily filter → 1m signal scoring."""
    if universe is None:
        from app.scanner.runner import SCAN_UNIVERSE
        symbols = SCAN_UNIVERSE
    else:
        symbols = universe

    logger.info("fitness_scan_start", symbols=len(symbols), top_n=top_n)

    # Phase 1: daily filter
    candidates = await _phase1_daily_filter(symbols, top_n=40)
    logger.info("phase1_candidates", count=len(candidates))

    # Phase 2: score each candidate on 1m signal density
    scores: list[FitnessScore] = []
    for sym, price, avg_volume, atr_pct in candidates:
        score = await _score_ticker(sym, price, avg_volume, atr_pct)
        scores.append(score)

    # Filter out disqualified, sort by total score descending
    qualified = [s for s in scores if not s.disqualified]
    qualified.sort(key=lambda s: s.total_score, reverse=True)

    logger.info(
        "fitness_scan_complete",
        total=len(symbols),
        phase1_passed=len(candidates),
        qualified=len(qualified),
        returning=min(top_n, len(qualified)),
    )

    return qualified[:top_n]
