"""Strategy-fitness scanner — two-phase scoring for the refined RSI strategy.

Phase 1: Fast daily pre-filter (price, volume, ATR hard cuts) → ~30 candidates
Phase 2: Score on metrics empirically correlated with backtest P&L:
         - Volume (r=+0.40 with P&L, +0.35 with win rate)
         - Price inverted (r=-0.34 with P&L, lower price → better)
         - 1m S/R strong count (r=+0.36 with win rate, only 1m metric that helps)
         - ATR sweet spot on daily (good range = more trades, less gating)

Scoring weights derived from correlation analysis on 40-ticker backtest:
  Volume:     40 pts  (strongest pre-backtest predictor of win rate)
  Price:      25 pts  (inverted — cheaper stocks win more on 1m)
  1m S/R:     25 pts  (only 1m metric correlated with WR)
  ATR range:  10 pts  (mild preference for mid-range volatility)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import yfinance as yf
import structlog

from app.analysis.indicators import atr as calc_atr
from app.analysis.support_resistance import detect_sr_levels

logger = structlog.get_logger()


@dataclass
class FitnessScore:
    symbol: str
    total_score: float  # 0-100
    # Metrics (kept for display and debugging)
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
# Scoring functions (weights: volume 40, price 25, sr 25, atr 10 = 100)
# ---------------------------------------------------------------------------

def _score_volume(avg_volume: float) -> float:
    """0-40 pts. Linear from 5M → 80M+.

    Volume is the strongest pre-backtest predictor of both win rate and P&L.
    High-volume tickers have tighter spreads and more reliable 1m candles.
    """
    if avg_volume < 5_000_000:
        return 0.0
    if avg_volume >= 80_000_000:
        return 40.0
    return (avg_volume - 5_000_000) / (80_000_000 - 5_000_000) * 40


def _score_price_inverted(price: float) -> float:
    """0-25 pts. Lower price → higher score.

    Empirically, cheaper stocks ($20-150) perform better on 1m refined_rsi
    than expensive ones ($400+). Likely due to more retail flow and larger
    percentage moves per ATR unit.

    $20-50: 25 pts, $50-150: 25→15 pts, $150-400: 15→5, $400+: 5→0
    """
    if price < 20:
        return 0.0  # too cheap, likely low quality
    if price <= 50:
        return 25.0
    if price <= 150:
        return 25.0 - (price - 50) / (150 - 50) * 10  # 25 → 15
    if price <= 400:
        return 15.0 - (price - 150) / (400 - 150) * 10  # 15 → 5
    if price <= 700:
        return 5.0 - (price - 400) / (700 - 400) * 5  # 5 → 0
    return 0.0


def _score_sr_1m(strong_count: int) -> float:
    """0-25 pts. Linear from 0 → 25+ strong S/R levels on 1m data.

    Only 1m metric that correlates with win rate (r=+0.36).
    More strong S/R levels = more valid entry zones for the strategy.
    """
    if strong_count <= 0:
        return 0.0
    if strong_count >= 25:
        return 25.0
    return strong_count / 25 * 25


def _score_atr_range(atr_pct: float) -> float:
    """0-10 pts. Peak at 1.5-3.5% daily ATR range.

    Mid-range volatility produces more trades without excessive gating.
    Too low = no signals. Too high = erratic stops.
    """
    if atr_pct < 0.5:
        return atr_pct / 0.5 * 5  # ramp to 5 at 0.5%
    if atr_pct < 1.5:
        return 5.0 + (atr_pct - 0.5) / 1.0 * 5  # 5 → 10
    if atr_pct <= 3.5:
        return 10.0  # sweet spot
    if atr_pct <= 5.0:
        return 10.0 - (atr_pct - 3.5) / 1.5 * 10  # 10 → 0
    return 0.0


# ---------------------------------------------------------------------------
# Phase 1: Daily pre-filter (hard cuts only, no scoring)
# ---------------------------------------------------------------------------

async def _phase1_daily_filter(
    universe: list[str],
    top_n: int = 30,
) -> list[tuple[str, float, int, float]]:
    """Fetch daily data, apply hard disqualifiers, return passing tickers.

    Hard disqualifiers: price < $10, volume < 500K, ATR% > 5%.
    Returns list of (symbol, price, avg_volume, atr_pct).
    """
    candidates: list[tuple[str, float, int, float]] = []

    for symbol in universe:
        try:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period="70d", interval="1d")

            if df.empty or len(df) < 20:
                logger.debug("phase1_skip", symbol=symbol, reason="insufficient data")
                continue

            df.columns = [c.lower() for c in df.columns]

            price = float(df["close"].iloc[-1])
            avg_volume = int(df["volume"].mean())

            atr_series = calc_atr(df, period=14)
            current_atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else 0
            atr_pct = (current_atr / price * 100) if price > 0 else 0

            # Hard disqualifiers
            if price < 10:
                logger.debug("phase1_dq", symbol=symbol, reason=f"price ${price:.2f} < $10")
                continue
            if avg_volume < 500_000:
                logger.debug("phase1_dq", symbol=symbol, reason=f"volume {avg_volume:,} < 500K")
                continue
            if atr_pct > 5:
                logger.debug("phase1_dq", symbol=symbol, reason=f"ATR% {atr_pct:.1f}% > 5%")
                continue

            candidates.append((symbol, price, avg_volume, atr_pct))

        except Exception as e:
            logger.debug("phase1_error", symbol=symbol, error=str(e))
            continue

    logger.info("phase1_complete", total=len(universe), passed=len(candidates))
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Phase 2: Score on empirically-validated metrics
# ---------------------------------------------------------------------------

async def _score_ticker(
    symbol: str,
    price: float,
    avg_volume: int,
    atr_pct: float,
) -> FitnessScore:
    """Fetch 7 days of 1m data for S/R count, then score."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period="7d", interval="1m")

        if df.empty or len(df) < 100:
            return _empty_score(symbol, "insufficient 1m data", price, avg_volume, atr_pct)

        df.columns = [c.lower() for c in df.columns]

        # S/R quality on 1m (only 1m metric that correlates with win rate)
        sr_levels = detect_sr_levels(df)
        sr_strong = sum(1 for lvl in sr_levels if lvl.strength >= 5)

        # Score components
        vol_s = _score_volume(avg_volume)
        price_s = _score_price_inverted(price)
        sr_s = _score_sr_1m(sr_strong)
        atr_s = _score_atr_range(atr_pct)

        total = vol_s + price_s + sr_s + atr_s

        return FitnessScore(
            symbol=symbol,
            total_score=round(total, 1),
            price=round(price, 2),
            avg_volume=avg_volume,
            atr_pct=round(atr_pct, 2),
            sr_strong_count=sr_strong,
            volume_score=round(vol_s, 1),
            price_score=round(price_s, 1),
            sr_score=round(sr_s, 1),
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
    """Two-phase fitness scan: daily filter → empirical scoring."""
    if universe is None:
        from app.scanner.runner import SCAN_UNIVERSE
        symbols = SCAN_UNIVERSE
    else:
        symbols = universe

    logger.info("fitness_scan_start", symbols=len(symbols), top_n=top_n)

    # Phase 1: daily filter
    candidates = await _phase1_daily_filter(symbols, top_n=40)
    logger.info("phase1_candidates", count=len(candidates))

    # Phase 2: score each candidate
    scores: list[FitnessScore] = []
    for sym, price, avg_volume, atr_pct in candidates:
        score = await _score_ticker(sym, price, avg_volume, atr_pct)
        scores.append(score)
        if not score.disqualified:
            logger.info(
                "fitness_scored",
                symbol=sym,
                total=score.total_score,
                vol_s=score.volume_score,
                price_s=score.price_score,
                sr_s=score.sr_score,
                atr_s=score.atr_score,
                sr_strong=score.sr_strong_count,
            )
        else:
            logger.debug("fitness_disqualified", symbol=sym, reason=score.disqualify_reason)

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
