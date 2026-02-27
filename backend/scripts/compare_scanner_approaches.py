"""Compare 3 scanner approaches by running 29-day backtests with each's top 4 tickers.

Approach 1: Daily-only scoring (no mini-backtest, just daily metrics)
Approach 2: Real 7-day backtest scoring (actual replay engine per ticker)
Approach 3: Fixed mini-backtest (time-of-day + volume + full divergence window)
Baseline:   Hand-picked TSLA+CEG+PLTR+RIVN (known best from v8 optimization)
"""

import asyncio
import sys
import os
import logging

logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from datetime import datetime, timedelta
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.engine.strategies import get_strategy
from app.scanner.fitness import run_fitness_scan


async def run_backtest(symbols: list[str], days: int = 29) -> dict:
    """Run a 29-day backtest and return results dict."""
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=150_000)
    engine = ReplayEngine(broker)
    await engine.start(
        symbols=symbols,
        timeframe="1m",
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        speed=1.0,
        stop_loss_pct=0.02,
        position_size_pct=0.50,
        strategy="rsi_divergence_pct",
        headless=True,
    )
    if engine._task:
        await engine._task

    r = engine.get_results()
    if not r:
        return {"symbols": symbols, "pnl_pct": 0, "win_rate": 0, "trades": 0, "dd_pct": 0}
    return {
        "symbols": symbols,
        "pnl_pct": r.total_pnl_pct,
        "pnl_dollar": r.total_pnl,
        "win_rate": r.win_rate,
        "trades": r.total_trades,
        "dd_pct": r.max_drawdown_pct,
        "winning": r.winning_trades,
        "losing": r.losing_trades,
    }


async def get_scanner_tickers(mode: str, top_n: int = 4) -> list[str]:
    """Run scanner with given mode and return top N ticker symbols."""
    scan_config = get_strategy("rsi_divergence_pct").scan_config
    # Re-enable logging briefly for scanner
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))
    results = await run_fitness_scan(top_n=top_n, scan_config=scan_config, scoring_mode=mode)
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))
    return [r.symbol for r in results]


async def main():
    print("=" * 90)
    print("SCANNER APPROACH COMPARISON — 29-day backtests with v8 (rsi_divergence_pct)")
    print("=" * 90)

    # Step 1: Get ticker lists from each approach
    print("\n--- Phase 1: Scanning tickers ---\n")

    approaches = {}

    print("  [1/4] Approach 1: Daily-only scoring...")
    tickers_daily = await get_scanner_tickers("daily_only", top_n=4)
    approaches["1_daily_only"] = tickers_daily
    print(f"         → {tickers_daily}")

    print("  [2/4] Approach 3: Fixed mini-backtest...")
    tickers_fixed = await get_scanner_tickers("mini_backtest", top_n=4)
    approaches["3_fixed_mini"] = tickers_fixed
    print(f"         → {tickers_fixed}")

    # Approach 2 is slowest — real backtest per ticker
    print("  [3/4] Approach 2: Real backtest scoring (7d per ticker, ~40 candidates)...")
    tickers_real = await get_scanner_tickers("real_backtest", top_n=4)
    approaches["2_real_bt"] = tickers_real
    print(f"         → {tickers_real}")

    # Baseline: hand-picked
    approaches["0_baseline"] = ["TSLA", "CEG", "PLTR", "RIVN"]
    print(f"  [4/4] Baseline: hand-picked → {approaches['0_baseline']}")

    # Step 2: Run 29-day backtests
    print("\n--- Phase 2: Running 29-day backtests ---\n")

    results = {}
    for label, tickers in sorted(approaches.items()):
        if not tickers:
            print(f"  {label}: NO TICKERS — skipping")
            continue
        print(f"  Running {label}: {tickers}...")
        results[label] = await run_backtest(tickers, days=29)
        r = results[label]
        sign = "+" if r["pnl_pct"] >= 0 else ""
        print(f"    → {sign}{r['pnl_pct']:.2f}% | {r['trades']} trades | WR {r['win_rate']:.1f}% | DD {r['dd_pct']:.2f}%")

    # Step 3: Print comparison table
    print("\n" + "=" * 90)
    print(f"{'Approach':<20s} | {'Tickers':<24s} | {'P&L%':>8s} | {'P&L$':>10s} | {'Trades':>6s} | {'WR%':>6s} | {'DD%':>6s}")
    print("-" * 90)

    for label in sorted(results.keys()):
        r = results[label]
        tickers_str = ",".join(r["symbols"])[:24]
        pnl_sign = "+" if r["pnl_pct"] >= 0 else ""
        print(
            f"{label:<20s} | {tickers_str:<24s} | {pnl_sign}{r['pnl_pct']:>7.2f}% | "
            f"${r.get('pnl_dollar', 0):>+9,.0f} | {r['trades']:>6d} | "
            f"{r['win_rate']:>5.1f}% | {r['dd_pct']:>5.2f}%"
        )

    print("=" * 90)

    # Determine winner
    best = max(results.items(), key=lambda x: x[1]["pnl_pct"])
    print(f"\nWINNER: {best[0]} with {best[1]['pnl_pct']:+.2f}% P&L")
    print()


if __name__ == "__main__":
    asyncio.run(main())
