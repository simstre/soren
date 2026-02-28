"""Score tickers by running the actual replay engine (Approach 2).

Instead of a simplified mini-backtest, run the real replay engine for each
ticker over a short window (default 7 days). This guarantees scoring fidelity
matches live trading — same signals, same exits, same risk management.
"""

from __future__ import annotations

import asyncio
import logging
import structlog
from datetime import datetime, timedelta

logger = structlog.get_logger()


async def _run_single_backtest(
    symbol: str,
    days: int,
    strategy: str,
) -> tuple[str, float, float, int]:
    """Run a single-ticker backtest and return (symbol, pnl_pct, win_rate, trades)."""
    # Import here to avoid circular imports
    from app.broker.replay_adapter import ReplayBrokerAdapter
    from app.engine.replay_engine import ReplayEngine

    end = datetime.now() + timedelta(days=1)
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

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
            stop_loss_pct=0.02,
            position_size_pct=0.50,
            strategy=strategy,
            headless=True,
        )
        if engine._task:
            await engine._task
    finally:
        logging.disable(prev_level)

    r = engine.get_results()
    if not r or r.total_trades == 0:
        return (symbol, 0.0, 0.0, 0)

    return (symbol, r.total_pnl_pct, r.win_rate, r.total_trades)


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
            return result

    results = await asyncio.gather(*[_score_one(s) for s in symbols])
    results = sorted(results, key=lambda r: r[1], reverse=True)

    logger.info("backtest_scorer_complete", scored=len(results))
    return list(results)
