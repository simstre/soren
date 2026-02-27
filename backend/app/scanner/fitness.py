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
# Default weights (v1-v7): WR 50 + P&L 30 + Vol 10 + ATR 10 = 100
# V8 weights:              WR 40 + P&L 30 + Trend 15 + ATR 10 + Vol 5 = 100
#
# Win rate is the strongest predictor of actual backtest P&L.
# ATR% is a weak predictor (already filtered ≥2.5% in phase 1).
# ---------------------------------------------------------------------------

def _score_signal_win_rate(win_rate: float, num_setups: int, min_setups: int = 3,
                           gated: bool = False) -> float:
    """0-50 pts. Based on simulated mini-trade win rate.

    When gated=True (v8), WR < 50% is already filtered out, so remap:
    50% → 0, 75%+ → 50. Otherwise original scale: 35% → 0, 60%+ → 50.
    """
    if num_setups < min_setups:
        return 0.0
    if gated:
        if win_rate <= 0.50:
            return 0.0
        if win_rate >= 0.75:
            return 50.0
        return (win_rate - 0.50) / 0.25 * 50
    else:
        if win_rate <= 0.35:
            return 0.0
        if win_rate >= 0.60:
            return 50.0
        return (win_rate - 0.35) / 0.25 * 50


def _score_total_pnl(total_pnl_pct: float, num_setups: int, min_setups: int = 3) -> float:
    """0-30 pts. Based on total cumulative P&L from simulated trades.

    Negative or zero → 0 pts. 2%+ total → 30 pts. Linear between.
    Requires at least min_setups to be meaningful.
    """
    if num_setups < min_setups:
        return 0.0
    if total_pnl_pct <= 0:
        return 0.0
    if total_pnl_pct >= 2.0:
        return 30.0
    return total_pnl_pct / 2.0 * 30


def _score_volume(avg_volume: float, max_pts: float = 10) -> float:
    """0-max_pts. Once above 10M, volume barely matters."""
    if avg_volume < 1_000_000:
        return 0.0
    if avg_volume >= 50_000_000:
        return max_pts
    return (avg_volume - 1_000_000) / (50_000_000 - 1_000_000) * max_pts


def _score_daily_trend(daily_returns: pd.Series) -> float:
    """0-10 pts. Measures how often intraday moves continue next day.

    Computed as fraction of consecutive same-sign daily returns.
    Higher = more trending behavior = better for v8's 1-day holds.
    """
    if len(daily_returns) < 10:
        return 0.0
    signs = daily_returns.dropna().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    same_sign = sum(1 for i in range(1, len(signs)) if signs.iloc[i] == signs.iloc[i-1] and signs.iloc[i] != 0)
    total = sum(1 for i in range(1, len(signs)) if signs.iloc[i] != 0 and signs.iloc[i-1] != 0)
    if total < 5:
        return 0.0
    ratio = same_sign / total  # 0.0 to 1.0
    # 40% same-sign is random. 55%+ is strongly trending.
    if ratio <= 0.40:
        return 0.0
    if ratio >= 0.55:
        return 10.0
    return (ratio - 0.40) / 0.15 * 10


def _score_atr_range(atr_pct: float, no_upper_penalty: bool = False) -> float:
    """0-10 pts. Peak at 1.5-5.0% daily ATR range.

    When no_upper_penalty=True (v8), don't penalize ATR above 5% —
    higher volatility is strictly better for pct-based strategies.
    """
    if atr_pct < 0.5:
        return atr_pct / 0.5 * 3
    if atr_pct < 1.5:
        return 3.0 + (atr_pct - 0.5) / 1.0 * 7  # 3 → 10
    if atr_pct <= 5.0:
        return 10.0
    if no_upper_penalty:
        return 10.0  # v8: no penalty for high ATR
    if atr_pct <= 7.0:
        return 10.0 - (atr_pct - 5.0) / 2.0 * 10
    return 0.0


# ---------------------------------------------------------------------------
# Phase 1: Daily pre-filter (hard cuts only)
# ---------------------------------------------------------------------------

async def _phase1_daily_filter(
    universe: list[str],
    top_n: int = 40,
    scan_config: ScanConfig | None = None,
) -> list[tuple[str, float, int, float, pd.Series | None]]:
    """Batch-download daily data for full universe, apply hard disqualifiers.

    Hard disqualifiers: price < $10, volume < 500K, ATR% > max.
    For pct-based strategies (v8+): also require ATR% >= 2.5%.
    Returns list of (symbol, price, avg_volume, atr_pct, daily_returns).
    """
    min_atr_pct = 2.5 if (scan_config and scan_config.use_pct_stops) else 0
    max_atr_pct = 12 if (scan_config and scan_config.use_pct_stops) else 7

    candidates: list[tuple[str, float, int, float, pd.Series | None]] = []

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

            # Hard disqualifiers: price < $10, volume < 500K
            if price < 10 or avg_volume < 500_000:
                continue
            if atr_pct > max_atr_pct or atr_pct < min_atr_pct:
                continue

            daily_returns = df["close"].pct_change()
            candidates.append((symbol, price, avg_volume, atr_pct, daily_returns))

        except Exception as e:
            logger.debug("phase1_error", symbol=symbol, error=str(e))
            continue

    # Sort by ATR% descending so high-volatility tickers (best for v8)
    # don't get truncated when the list exceeds top_n.
    candidates.sort(key=lambda c: c[3], reverse=True)
    logger.info("phase1_complete", total=len(universe), passed=len(candidates))
    return candidates[:top_n]


# ---------------------------------------------------------------------------
# Phase 2: Mini-backtest on 1m data
# ---------------------------------------------------------------------------

def _simulate_signals_on_1m(df: pd.DataFrame, scan_config: ScanConfig | None = None) -> tuple[int, int, float, int]:
    """Simulate mini-trades for each valid strategy setup on 1m data.

    Matches the real replay engine as closely as possible:
    - Pass full RSI/price history to divergence detector (not just 20 bars)
    - Time-of-day filter: skip 9:00-9:45 AM and after 3:00 PM
    - Volume confirmation: skip bars with volume < 50% of 20-bar avg
    - Cooldown applied after trades (matching scan_config.cooldown_bars)

    Returns (num_setups, wins, total_pnl_pct, sr_strong_count).
    """
    if scan_config is None:
        scan_config = ScanConfig()

    if len(df) < 40:
        return 0, 0, 0.0, 0

    closes = df["close"].values.tolist()
    highs = df["high"].values.tolist()
    lows = df["low"].values.tolist()
    volumes = df["volume"].values.tolist() if "volume" in df.columns else []
    rsi_series = calc_rsi(df["close"], period=14)
    rsi_values = rsi_series.values.tolist()
    atr_series = calc_atr(df, period=14)
    atr_values = atr_series.values.tolist()

    # Extract timestamps for time-of-day filter
    has_timestamps = hasattr(df.index, 'hour')
    timestamps = df.index if has_timestamps else None

    # Detect S/R levels
    sr_levels = detect_sr_levels(df)
    sr_strong = [lvl for lvl in sr_levels if lvl.strength >= scan_config.sr_min_strength]
    sr_strong_count = len(sr_strong)

    supports = [lvl for lvl in sr_strong if lvl.level_type == "support"]
    resistances = [lvl for lvl in sr_strong if lvl.level_type == "resistance"]

    num_setups = 0
    wins = 0
    total_pnl_pct = 0.0
    min_bars = 20
    cooldown = 0  # skip N bars after a trade

    for i in range(min_bars, len(closes) - 1):
        if cooldown > 0:
            cooldown -= 1
            continue

        # Time-of-day filter (match real engine: skip 9:00-9:45 and after 3 PM)
        if timestamps is not None:
            ts = timestamps[i]
            hour = ts.hour
            minute = getattr(ts, 'minute', 0)
            if hour == 9 and minute < 45:
                continue
            if hour >= 15:
                continue

        # Volume confirmation (match real engine: >= 50% of 20-bar avg)
        if volumes and i >= 20:
            cur_vol = volumes[i]
            avg_vol = sum(volumes[i-20:i]) / 20
            if avg_vol > 0 and cur_vol < avg_vol * 0.5:
                continue

        # RSI divergence — pass full history up to current bar (match real engine)
        full_rsi = [v for v in rsi_values[:i] if not np.isnan(v)]
        full_prices = closes[:i]

        if len(full_rsi) < 20:
            continue

        div = detect_rsi_divergence(full_rsi, full_prices, lookback=20)
        if div is None or div.rsi_spread < scan_config.min_rsi_spread:
            continue

        # Flip signals for contrarian strategies
        direction = div.direction
        if scan_config.flip_signals:
            direction = "bearish" if direction == "bullish" else "bullish"

        current_rsi = full_rsi[-1]
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
        if scan_config.use_pct_stops:
            stop_dist = entry_price * scan_config.stop_pct
            target_dist = entry_price * scan_config.target_pct
        else:
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
    daily_returns: pd.Series | None = None,
) -> FitnessScore:
    """Fetch 1m data and simulate mini-trades to score quality.

    For pct-based strategies (v8+), fetches max available 1m data (~30 days)
    since longer hold periods need more data for statistical significance.
    """
    try:
        ticker = yf.Ticker(symbol)

        # For pct-based strategies (v8+), fetch multiple 7-day chunks
        # to get ~28 days of 1m data (Yahoo limits 1m to 8 days/request).
        use_extended = scan_config is not None and scan_config.use_pct_stops

        df = pd.DataFrame()
        if use_extended:
            from datetime import datetime, timedelta
            end = datetime.now()
            chunks = []
            for chunk_i in range(4):  # 4 chunks × 7 days = 28 days
                chunk_end = end - timedelta(days=chunk_i * 7)
                chunk_start = chunk_end - timedelta(days=7)
                for _attempt in range(2):
                    chunk_df = ticker.history(
                        start=chunk_start.strftime("%Y-%m-%d"),
                        end=chunk_end.strftime("%Y-%m-%d"),
                        interval="1m",
                    )
                    if not chunk_df.empty:
                        chunks.append(chunk_df)
                        break
            if chunks:
                df = pd.concat(chunks).sort_index()
                df = df[~df.index.duplicated(keep='first')]
        else:
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

        # For pct-based strategies, lower the min setups requirement
        # since longer holds (390 bars) produce fewer trades.
        min_setups_for_scoring = 2 if (scan_config and scan_config.use_pct_stops) else 3

        # Score components
        is_pct = scan_config and scan_config.use_pct_stops

        if is_pct:
            # V8 weights: WR 40 + P&L 30 + Trend 15 + ATR 10 + Vol 5 = 100
            wr_s = _score_signal_win_rate(win_rate, num_setups, min_setups=min_setups_for_scoring, gated=True)
            pnl_s = _score_total_pnl(total_pnl_pct, num_setups, min_setups=min_setups_for_scoring)
            vol_s = _score_volume(avg_volume, max_pts=5)
            atr_s = _score_atr_range(atr_pct, no_upper_penalty=True)
            trend_s = _score_daily_trend(daily_returns) if daily_returns is not None else 0
            wr_s = wr_s * (40/50)       # 50 → 40
            trend_s = trend_s * 1.5      # 10 → 15
            # atr_s stays at 10, pnl_s stays at 30, vol_s is already 5
            total = wr_s + pnl_s + vol_s + atr_s + trend_s
        else:
            wr_s = _score_signal_win_rate(win_rate, num_setups, min_setups=min_setups_for_scoring)
            pnl_s = _score_total_pnl(total_pnl_pct, num_setups, min_setups=min_setups_for_scoring)
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
# Approach 1: Daily-metrics-only scoring (skip mini-backtest)
# ---------------------------------------------------------------------------

def _score_daily_metrics_only(
    price: float, avg_volume: float, atr_pct: float, daily_returns: pd.Series | None,
) -> float:
    """Score ticker for v8 using ONLY daily metrics — no mini-backtest.

    Targets tickers with: high ATR (4-7%), trending behavior, steady volume,
    positive return asymmetry (up days > down days in magnitude).
    Total: ATR 25 + Volatility 20 + Trend 20 + Volume 15 + Asymmetry 10 + Price 10 = 100
    """
    if daily_returns is None or len(daily_returns.dropna()) < 10:
        return 0.0

    # ATR Range (0-25): peak at 4-7%, no penalty above for v8
    if atr_pct < 3.0:
        atr_s = (atr_pct / 3.0) * 12
    elif atr_pct <= 4.0:
        atr_s = 12 + (atr_pct - 3.0) * 4
    elif atr_pct <= 7.0:
        atr_s = 16 + (atr_pct - 4.0) / 3.0 * 9
    else:
        atr_s = 25.0
    atr_s = max(0, min(25, atr_s))

    # Daily volatility (0-20): std dev of returns, 1-3% is good
    daily_vol = daily_returns.std() * 100
    if daily_vol < 0.5:
        vol_s = (daily_vol / 0.5) * 10
    elif daily_vol <= 3.0:
        vol_s = 10 + (daily_vol - 0.5) / 2.5 * 10
    else:
        vol_s = 20.0
    vol_s = max(0, min(20, vol_s))

    # Trend persistence (0-20): reuse existing function, scale 10→20
    trend_s = _score_daily_trend(daily_returns) * 2.0
    trend_s = max(0, min(20, trend_s))

    # Volume (0-15): steady liquidity
    if avg_volume < 5_000_000:
        liq_s = (avg_volume / 5_000_000) * 5
    elif avg_volume <= 50_000_000:
        liq_s = 5 + (avg_volume - 5_000_000) / 45_000_000 * 10
    else:
        liq_s = 15.0
    liq_s = max(0, min(15, liq_s))

    # Return asymmetry (0-10): larger up moves than down moves
    returns_clean = daily_returns.dropna()
    up_ret = returns_clean[returns_clean > 0]
    down_ret = returns_clean[returns_clean < 0]
    if len(up_ret) > 0 and len(down_ret) > 0:
        ratio = up_ret.mean() / (abs(down_ret.mean()) + 1e-6)
        asym_s = min(10, 5 + (ratio - 1.0) * 5)
    else:
        asym_s = 5.0
    asym_s = max(0, min(10, asym_s))

    # Price (0-10): bonus for >= $50
    price_s = min(10, (price / 50) * 10) if price < 50 else 10.0

    return atr_s + vol_s + trend_s + liq_s + asym_s + price_s


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def score_ticker(symbol: str, bars: int = 60, scan_config: ScanConfig | None = None) -> FitnessScore:
    """Score a single ticker through both phases."""
    candidates = await _phase1_daily_filter([symbol], top_n=1, scan_config=scan_config)
    if not candidates:
        return _empty_score(symbol, "failed Phase 1 filter")
    sym, price, avg_volume, atr_pct, daily_returns = candidates[0]
    return await _score_ticker(sym, price, avg_volume, atr_pct, scan_config, daily_returns)


async def run_fitness_scan(
    universe: list[str] | None = None,
    top_n: int = 15,
    scan_config: ScanConfig | None = None,
    scoring_mode: str = "mini_backtest",
) -> list[FitnessScore]:
    """Fitness scan with selectable scoring mode.

    scoring_mode:
      "mini_backtest" — phase 2 mini-backtest (fixed: time-of-day, volume, full divergence window)
      "daily_only"    — phase 1 daily metrics only (no 1m data fetch)
      "real_backtest"  — use actual replay engine for scoring (slow but accurate)
    """
    if universe is None:
        from app.scanner.runner import SCAN_UNIVERSE
        symbols = SCAN_UNIVERSE
    else:
        symbols = universe

    # Auto-select real_backtest for v8 (pct-based) strategies unless overridden
    is_pct = scan_config and scan_config.use_pct_stops
    if scoring_mode == "mini_backtest" and is_pct:
        scoring_mode = "real_backtest"

    logger.info("fitness_scan_start", symbols=len(symbols), top_n=top_n, mode=scoring_mode)

    candidates = await _phase1_daily_filter(symbols, top_n=40, scan_config=scan_config)
    logger.info("phase1_candidates", count=len(candidates))

    scores: list[FitnessScore] = []

    if scoring_mode == "daily_only":
        for sym, price, avg_volume, atr_pct, daily_returns in candidates:
            total = _score_daily_metrics_only(price, avg_volume, atr_pct, daily_returns)
            scores.append(FitnessScore(
                symbol=sym, total_score=round(total, 1), price=round(price, 2),
                avg_volume=avg_volume, atr_pct=round(atr_pct, 2), sr_strong_count=0,
                volume_score=0, price_score=0, sr_score=0, atr_score=0,
                disqualified=False, disqualify_reason="",
            ))
            logger.info("daily_score", symbol=sym, score=round(total, 1))

    elif scoring_mode == "real_backtest":
        from app.scanner.backtest_scorer import score_via_backtest
        bt_results = await score_via_backtest(
            [c[0] for c in candidates], days=7, strategy="rsi_divergence_pct",
        )
        cand_map = {c[0]: c for c in candidates}
        for sym, pnl_pct, wr, trades in bt_results:
            _, price, avg_volume, atr_pct, _ = cand_map[sym]
            # Score: 60% P&L + 40% WR (both proven predictors from real engine)
            pnl_s = min(60, max(0, pnl_pct / 5.0 * 60))  # 5%+ → 60 pts
            wr_s = max(0, (wr - 40) / 30 * 40)  # 40%→0, 70%+→40 pts
            total = pnl_s + wr_s
            scores.append(FitnessScore(
                symbol=sym, total_score=round(total, 1), price=round(price, 2),
                avg_volume=avg_volume, atr_pct=round(atr_pct, 2), sr_strong_count=trades,
                volume_score=0, price_score=round(wr_s, 1), sr_score=round(pnl_s, 1),
                atr_score=0, disqualified=False, disqualify_reason="",
            ))

    else:  # "mini_backtest" (default)
        for sym, price, avg_volume, atr_pct, daily_returns in candidates:
            score = await _score_ticker(sym, price, avg_volume, atr_pct, scan_config, daily_returns)
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
