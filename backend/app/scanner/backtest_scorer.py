"""Score tickers by running the actual replay engine (Approach 2).

Instead of a simplified mini-backtest, run the real replay engine for each
ticker over a short window (default 7 days). This guarantees scoring fidelity
matches live trading — same signals, same exits, same risk management.
"""

from __future__ import annotations

import asyncio
import logging
import structlog
from dataclasses import dataclass
from datetime import datetime, timedelta

logger = structlog.get_logger()


@dataclass
class BacktestValidation:
    """Results from a validation backtest for a single ticker."""
    symbol: str
    pnl_pct: float
    win_rate: float
    trade_count: int
    stop_loss_rate: float  # % of trades closed by stop loss
    max_hold_rate: float   # % of trades closed by max hold
    passed: bool


async def _run_single_backtest(
    symbol: str,
    days: int,
    strategy: str,
) -> tuple[str, float, float, int, float, float]:
    """Run a single-ticker backtest and return (symbol, pnl_pct, win_rate, trades, sl_rate, mh_rate)."""
    # Import here to avoid circular imports
    from app.broker.replay_adapter import ReplayBrokerAdapter
    from app.engine.replay_engine import ReplayEngine
    from app.engine.strategies import get_strategy

    end = datetime.now() + timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    # Use strategy's own parameters instead of hardcoded values
    sdef = get_strategy(strategy)
    sl_pct = sdef.stop_loss_pct if sdef else 0.02
    pos_pct = sdef.position_size_pct if sdef else 0.50

    broker = ReplayBrokerAdapter(starting_balance=100_000)
    engine = ReplayEngine(broker)

    # Suppress noisy logs during batch scoring
    prev_level = logging.getLogger().level
    logging.disable(logging.WARNING)
    try:
        await engine.start(
            symbols=[symbol],
            timeframe="1m",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            speed=1.0,
            stop_loss_pct=sl_pct,
            position_size_pct=pos_pct,
            strategy=strategy,
            headless=True,
        )
        if engine._task:
            await engine._task
    finally:
        logging.disable(prev_level)

    r = engine.get_results()
    if not r or r.total_trades == 0:
        return (symbol, 0.0, 0.0, 0, 0.0, 0.0)

    sl_rate = (r.stop_loss_count / r.total_trades * 100) if r.total_trades > 0 else 0.0
    mh_rate = (r.max_hold_count / r.total_trades * 100) if r.total_trades > 0 else 0.0

    return (symbol, r.total_pnl_pct, r.win_rate, r.total_trades, sl_rate, mh_rate)


async def score_via_backtest(
    symbols: list[str],
    days: int = 7,
    strategy: str = "rsi_divergence_pct",
    max_concurrent: int = 4,
) -> list[tuple[str, float, float, int]]:
    """Run real backtests for each symbol and return results for ranking.

    Returns list of (symbol, total_pnl_pct, win_rate, trade_count),
    sorted by pnl_pct descending.
    """
    logger.info("backtest_scorer_start", symbols=len(symbols), days=days)

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _score_one(sym: str) -> tuple[str, float, float, int]:
        async with semaphore:
            result = await _run_single_backtest(sym, days, strategy)
            logger.info(
                "backtest_score",
                symbol=result[0],
                pnl_pct=f"{result[1]:+.2f}%",
                win_rate=f"{result[2]:.1f}%",
                trades=result[3],
            )
            # Return 4-tuple for backwards compat
            return (result[0], result[1], result[2], result[3])

    results = await asyncio.gather(*[_score_one(s) for s in symbols])
    results = sorted(results, key=lambda r: r[1], reverse=True)

    logger.info("backtest_scorer_complete", scored=len(results))
    return list(results)


async def validate_candidates(
    symbols: list[str],
    days: int = 14,
    strategy: str = "rsi_divergence_pct",
    max_concurrent: int = 4,
    min_pnl_pct: float = 0.5,
    min_win_rate: float = 50.0,
    max_sl_rate: float = 40.0,
    min_trades: int = 3,
) -> list[BacktestValidation]:
    """Run validation backtests and filter candidates by quality thresholds.

    Returns list of BacktestValidation results (passing first, sorted by P&L).
    """
    logger.info(
        "validation_start",
        candidates=len(symbols),
        days=days,
        thresholds=dict(
            min_pnl=min_pnl_pct, min_wr=min_win_rate,
            max_sl=max_sl_rate, min_trades=min_trades,
        ),
    )

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _validate_one(sym: str) -> BacktestValidation:
        async with semaphore:
            sym, pnl, wr, trades, sl_rate, mh_rate = await _run_single_backtest(
                sym, days, strategy,
            )
            passed = (
                trades >= min_trades
                and pnl >= min_pnl_pct
                and wr >= min_win_rate
                and sl_rate < max_sl_rate
            )
            logger.info(
                "validation_result",
                symbol=sym,
                pnl_pct=f"{pnl:+.2f}%",
                win_rate=f"{wr:.1f}%",
                sl_rate=f"{sl_rate:.0f}%",
                mh_rate=f"{mh_rate:.0f}%",
                trades=trades,
                passed=passed,
            )
            return BacktestValidation(
                symbol=sym,
                pnl_pct=pnl,
                win_rate=wr,
                trade_count=trades,
                stop_loss_rate=sl_rate,
                max_hold_rate=mh_rate,
                passed=passed,
            )

    results = await asyncio.gather(*[_validate_one(s) for s in symbols])

    # Sort: passing first (by P&L desc), then failing (by P&L desc)
    passing = sorted([r for r in results if r.passed], key=lambda r: r.pnl_pct, reverse=True)
    failing = sorted([r for r in results if not r.passed], key=lambda r: r.pnl_pct, reverse=True)

    logger.info(
        "validation_complete",
        total=len(results),
        passed=len(passing),
        failed=len(failing),
        passed_symbols=[r.symbol for r in passing],
    )

    return passing + failing
