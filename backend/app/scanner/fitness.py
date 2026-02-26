"""Strategy-fitness scanner — two-phase scoring for the refined RSI strategy.

Phase 1: Fast daily pre-filter (price, volume, ATR hard cuts) → ~40 candidates
Phase 2: Mini-backtest on 7d of 1m data.
         For each valid setup (divergence + S/R + RSI band), simulate a trade
         with ATR-based stop/target and check if it would have won or lost.
         Score = signal win rate (quality) only. Setup count doesn't matter.

This approach directly measures P&L potential rather than using proxy metrics.
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
from app.engine.strategies import ScanConfig

logger = structlog.get_logger()


@dataclass
class FitnessScore:
    symbol: str
    total_score: float  # 0-100
    # Metrics
    price: float
    avg_volume: int
    atr_pct: float
    sr_strong_count: int
    # Score components (repurposed for new scoring)
    volume_score: float     # volume floor pts
    price_score: float      # signal win rate pts (primary)
    sr_score: float         # avg pnl per trade pts
    atr_score: float        # atr range pts
    disqualified: bool
    disqualify_reason: str


def _empty_score(symbol: str, disqualify_reason: str, price: float = 0,
                 avg_volume: int = 0, atr_pct: float = 0) -> FitnessScore:
    return FitnessScore(
        symbol=symbol, total_score=0, price=price, avg_volume=avg_volume,
        atr_pct=atr_pct, sr_strong_count=0,
        volume_score=0, price_score=0, sr_score=0, atr_score=0,
        disqualified=True, disqualify_reason=disqualify_reason,
    )


# ---------------------------------------------------------------------------
# Scoring functions
#
# Win rate (50 pts): Probability of each setup being a winner.
#   For each valid setup, simulate entry with 2x ATR stop / 3x ATR target.
#   Score based on win rate: 35% → 0, 60%+ → 50 pts.
# Total P&L (30 pts): Cumulative P&L from all simulated mini-trades.
#   Measures actual profit potential (quality × opportunity).
# ATR range (10 pts): Higher ATR = bigger moves = more profit per trade.
# Volume floor (10 pts): Minimum liquidity for reliable candles.
# ---------------------------------------------------------------------------

def _score_signal_win_rate(win_rate: float, num_setups: int) -> float:
    """0-50 pts. Based on simulated mini-trade win rate.

    Requires at least 3 setups to be meaningful. Win rates below 35% score 0
    (breakeven with 2:3 R:R is ~40%). 60%+ scores max. Linear between.
    """
    if num_setups < 3:
        return 0.0
    if win_rate <= 0.35:
        return 0.0
    if win_rate >= 0.60:
        return 50.0
    return (win_rate - 0.35) / 0.25 * 50


def _score_total_pnl(total_pnl_pct: float, num_setups: int) -> float:
    """0-30 pts. Based on total cumulative P&L from simulated trades.

    Negative or zero → 0 pts. 2%+ total → 30 pts. Linear between.
    Requires at least 3 setups to be meaningful.
    """
    if num_setups < 3:
        return 0.0
    if total_pnl_pct <= 0:
        return 0.0
    if total_pnl_pct >= 2.0:
        return 30.0
    return total_pnl_pct / 2.0 * 30


def _score_volume(avg_volume: float) -> float:
    """0-10 pts. Once above 10M, volume barely matters."""
    if avg_volume < 5_000_000:
        return 0.0
    if avg_volume >= 50_000_000:
        return 10.0
    return (avg_volume - 5_000_000) / (50_000_000 - 5_000_000) * 10


def _score_atr_range(atr_pct: float) -> float:
    """0-10 pts. Peak at 1.5-5.0% daily ATR range."""
    if atr_pct < 0.5:
        return atr_pct / 0.5 * 3
    if atr_pct < 1.5:
        return 3.0 + (atr_pct - 0.5) / 1.0 * 7  # 3 → 10
    if atr_pct <= 5.0:
        return 10.0
    if atr_pct <= 7.0:
        return 10.0 - (atr_pct - 5.0) / 2.0 * 10
    return 0.0


# ---------------------------------------------------------------------------
# Phase 1: Daily pre-filter (hard cuts only)
# ---------------------------------------------------------------------------

async def _phase1_daily_filter(
    universe: list[str],
    top_n: int = 40,
) -> list[tuple[str, float, int, float]]:
    """Fetch daily data, apply hard disqualifiers.

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
                continue

            df.columns = [c.lower() for c in df.columns]

            price = float(df["close"].iloc[-1])
            avg_volume = int(df["volume"].mean())

            atr_series = calc_atr(df, period=14)
            current_atr = float(atr_series.iloc[-1]) if not atr_series.empty and not np.isnan(atr_series.iloc[-1]) else 0
            atr_pct = (current_atr / price * 100) if price > 0 else 0

            if price < 10 or avg_volume < 500_000 or atr_pct > 7:
                continue

            candidates.append((symbol, price, avg_volume, atr_pct))

        except Exception as e:
            logger.debug("phase1_error", symbol=symbol, error=str(e))
            continue

    logger.info("phase1_complete", total=len(universe), passed=len(candidates))
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Phase 2: Mini-backtest on 1m data
# ---------------------------------------------------------------------------

def _simulate_signals_on_1m(df: pd.DataFrame, scan_config: ScanConfig | None = None) -> tuple[int, int, float, int]:
    """Simulate mini-trades for each valid strategy setup on 1m data.

    For each setup (divergence + matching S/R + RSI in band):
    - Entry at current close
    - Stop = atr_stop_mult × ATR(14) against the trade
    - Target = atr_target_mult × ATR(14) in trade direction
    - Walk forward bar-by-bar, see which is hit first
    - If neither hit within max_hold_bars, close at current price

    Returns (num_setups, wins, total_pnl_pct, sr_strong_count).
    """
    if scan_config is None:
        scan_config = ScanConfig()

    if len(df) < 40:
        return 0, 0, 0.0, 0

    closes = df["close"].values.tolist()
    highs = df["high"].values.tolist()
    lows = df["low"].values.tolist()
    rsi_series = calc_rsi(df["close"], period=14)
    rsi_values = rsi_series.values.tolist()
    atr_series = calc_atr(df, period=14)
    atr_values = atr_series.values.tolist()

    # Detect S/R levels
    sr_levels = detect_sr_levels(df)
    sr_strong = [lvl for lvl in sr_levels if lvl.strength >= scan_config.sr_min_strength]
    sr_strong_count = len(sr_strong)

    supports = [lvl for lvl in sr_strong if lvl.level_type == "support"]
    resistances = [lvl for lvl in sr_strong if lvl.level_type == "resistance"]

    num_setups = 0
    wins = 0
    total_pnl_pct = 0.0
    window_size = 20
    step = 5
    cooldown = 0  # skip N bars after a trade

    for i in range(window_size, len(closes) - 1, step):
        if cooldown > 0:
            cooldown -= step
            continue

        # Check RSI divergence
        window_rsi = [v for v in rsi_values[i - window_size:i] if not np.isnan(v)]
        window_prices = closes[i - window_size:i]

        if len(window_rsi) < window_size or len(window_prices) < window_size:
            continue

        div = detect_rsi_divergence(window_rsi, window_prices, lookback=window_size)
        if div is None or div.rsi_spread < scan_config.min_rsi_spread:
            continue

        # Flip signals for contrarian strategies
        direction = div.direction
        if scan_config.flip_signals:
            direction = "bearish" if direction == "bullish" else "bullish"

        current_rsi = window_rsi[-1]
        entry_price = closes[i]

        # ATR at this bar
        atr_val = atr_values[i] if i < len(atr_values) and not np.isnan(atr_values[i]) else 0
        if atr_val <= 0:
            continue

        # RSI band filter
        if direction == "bullish":
            if not (scan_config.rsi_buy_lo <= current_rsi < scan_config.rsi_buy_hi):
                continue
        else:
            if not (scan_config.rsi_sell_lo < current_rsi <= scan_config.rsi_sell_hi):
                continue

        # Direction-matched S/R proximity
        levels_to_check = supports if direction == "bullish" else resistances
        near_sr = False
        for lvl in levels_to_check:
            dist_pct = abs(entry_price - lvl.price) / entry_price * 100
            if dist_pct <= 0.5:
                near_sr = True
                break

        if not near_sr:
            continue

        # Valid setup — simulate the trade
        num_setups += 1
        stop_dist = atr_val * scan_config.atr_stop_mult
        target_dist = atr_val * scan_config.atr_target_mult

        if direction == "bullish":
            stop = entry_price - stop_dist
            target = entry_price + target_dist
        else:
            stop = entry_price + stop_dist
            target = entry_price - target_dist

        # Walk forward bar-by-bar
        exit_price = entry_price
        for j in range(i + 1, min(i + 1 + scan_config.max_hold_bars, len(closes))):
            bar_high = highs[j]
            bar_low = lows[j]

            if direction == "bullish":
                if bar_low <= stop:
                    exit_price = stop
                    break
                if bar_high >= target:
                    exit_price = target
                    break
            else:
                if bar_high >= stop:
                    exit_price = stop
                    break
                if bar_low <= target:
                    exit_price = target
                    break
            exit_price = closes[j]

        # Calculate P&L
        if direction == "bullish":
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        total_pnl_pct += pnl_pct
        if pnl_pct > 0:
            wins += 1

        cooldown = scan_config.cooldown_bars

    return num_setups, wins, total_pnl_pct, sr_strong_count


async def _score_ticker(
    symbol: str,
    price: float,
    avg_volume: int,
    atr_pct: float,
    scan_config: ScanConfig | None = None,
) -> FitnessScore:
    """Fetch 7 days of 1m data, simulate mini-trades, score on quality."""
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

        # Simulate trades on 1m data
        num_setups, wins, total_pnl_pct, sr_strong_count = _simulate_signals_on_1m(df, scan_config)

        win_rate = wins / num_setups if num_setups > 0 else 0
        avg_pnl = total_pnl_pct / num_setups if num_setups > 0 else 0

        # Score components
        wr_s = _score_signal_win_rate(win_rate, num_setups)
        pnl_s = _score_total_pnl(total_pnl_pct, num_setups)
        vol_s = _score_volume(avg_volume)
        atr_s = _score_atr_range(atr_pct)

        total = wr_s + pnl_s + vol_s + atr_s

        logger.info(
            "phase2_minitrades",
            symbol=symbol,
            setups=num_setups,
            wins=wins,
            win_rate=f"{win_rate:.0%}",
            total_pnl=f"{total_pnl_pct:+.2f}%",
            avg_pnl=f"{avg_pnl:+.3f}%",
            score=round(total, 1),
        )

        return FitnessScore(
            symbol=symbol,
            total_score=round(total, 1),
            price=round(price, 2),
            avg_volume=avg_volume,
            atr_pct=round(atr_pct, 2),
            sr_strong_count=sr_strong_count,
            volume_score=round(vol_s, 1),
            price_score=round(wr_s, 1),       # win rate score
            sr_score=round(pnl_s, 1),         # total pnl score
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

async def score_ticker(symbol: str, bars: int = 60, scan_config: ScanConfig | None = None) -> FitnessScore:
    """Score a single ticker through both phases."""
    candidates = await _phase1_daily_filter([symbol], top_n=1)
    if not candidates:
        return _empty_score(symbol, "failed Phase 1 filter")
    sym, price, avg_volume, atr_pct = candidates[0]
    return await _score_ticker(sym, price, avg_volume, atr_pct, scan_config)


async def run_fitness_scan(
    universe: list[str] | None = None,
    top_n: int = 15,
    scan_config: ScanConfig | None = None,
) -> list[FitnessScore]:
    """Two-phase fitness scan: daily filter → 1m mini-backtest."""
    if universe is None:
        from app.scanner.runner import SCAN_UNIVERSE
        symbols = SCAN_UNIVERSE
    else:
        symbols = universe

    logger.info("fitness_scan_start", symbols=len(symbols), top_n=top_n)

    candidates = await _phase1_daily_filter(symbols, top_n=40)
    logger.info("phase1_candidates", count=len(candidates))

    scores: list[FitnessScore] = []
    for sym, price, avg_volume, atr_pct in candidates:
        score = await _score_ticker(sym, price, avg_volume, atr_pct, scan_config)
        scores.append(score)

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
