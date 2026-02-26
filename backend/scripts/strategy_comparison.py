"""Compare all strategies with target parameters: 1m, 29d, 50% position, 2% SL.

Runs headless backtests across all refined_rsi variants and outputs a comparison table.
"""

import asyncio
import sys
import os
import logging

# Suppress all logging below ERROR
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import structlog
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR),
)

from datetime import datetime, timedelta
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

STARTING_BALANCE = 100_000
POSITION_SIZE_PCT = 0.50  # 50%
STOP_LOSS_PCT = 0.02       # 2%
TIMEFRAME = "1m"

# Strategies to test
STRATEGIES = [
    "refined_rsi",       # v1
    "refined_rsi_v3",    # v3
    "refined_rsi_v5",    # v5
    "refined_rsi_v7",    # v7 contrarian
    "refined_rsi_v8",    # v8 pct-based (NEW)
]

# Focused tickers — sorted by avg daily range (high ATR first)
TICKERS = ["SMR", "PLTR", "SOFI", "CEG", "TSLA", "NVDA", "GOOGL", "AMZN", "META", "AAPL", "MSFT"]


async def run_backtest(symbols, strategy, days=29):
    """Run a single backtest and return results dict."""
    end_date = datetime.now()
    while end_date.weekday() >= 5:
        end_date -= timedelta(days=1)
    start_date = end_date - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    engine = ReplayEngine(broker)

    try:
        await engine.start(
            symbols=symbols,
            timeframe=TIMEFRAME,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            speed=1.0,
            stop_loss_pct=STOP_LOSS_PCT,
            position_size_pct=POSITION_SIZE_PCT,
            strategy=strategy,
            headless=True,
        )

        if engine._task:
            await engine._task
    except Exception as e:
        return {"error": str(e), "strategy": strategy, "symbols": symbols}

    results = engine.get_results()
    if not results:
        return {"error": "no results", "strategy": strategy, "symbols": symbols}

    # Also collect per-trade data for analysis
    trades = results.trades
    exit_reasons = {}
    for t in trades:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "strategy": strategy,
        "symbols": symbols,
        "total_trades": results.total_trades,
        "winning_trades": results.winning_trades,
        "losing_trades": results.losing_trades,
        "win_rate": results.win_rate,
        "total_pnl": results.total_pnl,
        "total_pnl_pct": results.total_pnl_pct,
        "long_win_rate": results.long_win_rate,
        "short_win_rate": results.short_win_rate,
        "long_pnl": results.long_pnl,
        "short_pnl": results.short_pnl,
        "max_drawdown": results.max_drawdown,
        "max_drawdown_pct": results.max_drawdown_pct,
        "starting_balance": results.starting_balance,
        "ending_balance": results.ending_balance,
        "exit_reasons": exit_reasons,
        "avg_pnl_per_trade": results.total_pnl / results.total_trades if results.total_trades > 0 else 0,
    }


async def main():
    print("=" * 110)
    print(f"STRATEGY COMPARISON — {TIMEFRAME} candles, 29 days, {POSITION_SIZE_PCT*100:.0f}% position, {STOP_LOSS_PCT*100:.0f}% SL")
    print(f"Starting balance: ${STARTING_BALANCE:,.0f}")
    print("=" * 110)

    all_results = []

    # Single-ticker backtests
    print(f"\n{'Strategy':>20s} | {'Ticker':>8s} | {'P&L%':>9s} | {'P&L$':>11s} | {'Trades':>6s} | {'WR%':>6s} | {'DD%':>6s} | {'$/Trade':>9s} | Exit Reasons")
    print("-" * 130)

    for strategy in STRATEGIES:
        for ticker in TICKERS:
            result = await run_backtest([ticker], strategy)
            all_results.append(result)

            if "error" in result:
                print(f"  {strategy:>20s} | {ticker:>8s} | {'ERROR':>9s} | {result['error'][:40]}")
            else:
                p = result
                pnl_sign = "+" if p["total_pnl_pct"] >= 0 else ""
                exits = " ".join(f"{k}={v}" for k, v in sorted(p["exit_reasons"].items()))
                print(f"  {strategy:>20s} | {ticker:>8s} | {pnl_sign}{p['total_pnl_pct']:>7.2f}% | ${p['total_pnl']:>+9,.0f} | {p['total_trades']:>6d} | {p['win_rate']:>5.1f}% | {p['max_drawdown_pct']:>5.2f}% | ${p['avg_pnl_per_trade']:>+8,.0f} | {exits}")
        print()

    # Summary tables
    valid = [r for r in all_results if "error" not in r]

    print("\n" + "=" * 110)
    print("TOP 15 BY P&L%")
    print("=" * 110)
    valid.sort(key=lambda x: x["total_pnl_pct"], reverse=True)
    for i, r in enumerate(valid[:15]):
        sym = ",".join(r["symbols"])
        pnl_sign = "+" if r["total_pnl_pct"] >= 0 else ""
        print(f"  {i+1:2d}. {r['strategy']:>20s} | {sym:>8s} | {pnl_sign}{r['total_pnl_pct']:>7.2f}% | ${r['total_pnl']:>+9,.0f} | {r['total_trades']:>3d} trades | WR {r['win_rate']:>5.1f}%")

    print("\n" + "=" * 110)
    print("AVERAGE P&L% PER STRATEGY")
    print("=" * 110)
    for strategy in STRATEGIES:
        strat = [r for r in valid if r["strategy"] == strategy]
        if strat:
            avg_pnl = sum(r["total_pnl_pct"] for r in strat) / len(strat)
            avg_trades = sum(r["total_trades"] for r in strat) / len(strat)
            avg_wr = sum(r["win_rate"] for r in strat) / len(strat)
            best = max(strat, key=lambda x: x["total_pnl_pct"])
            pnl_sign = "+" if avg_pnl >= 0 else ""
            print(f"  {strategy:>20s} | avg {pnl_sign}{avg_pnl:>7.2f}% | avg {avg_trades:>5.1f} trades | avg WR {avg_wr:>5.1f}% | best: {','.join(best['symbols'])} {best['total_pnl_pct']:+.2f}%")

    print("\n" + "=" * 110)
    print("PER-TICKER AVERAGE (across strategies)")
    print("=" * 110)
    ticker_stats = {}
    for r in valid:
        if len(r["symbols"]) == 1:
            t = r["symbols"][0]
            if t not in ticker_stats:
                ticker_stats[t] = []
            ticker_stats[t].append(r)

    sorted_t = sorted(ticker_stats.items(), key=lambda x: sum(r["total_pnl_pct"] for r in x[1])/len(x[1]), reverse=True)
    for ticker, runs in sorted_t:
        avg_pnl = sum(r["total_pnl_pct"] for r in runs) / len(runs)
        avg_trades = sum(r["total_trades"] for r in runs) / len(runs)
        best_strat = max(runs, key=lambda x: x["total_pnl_pct"])
        pnl_sign = "+" if avg_pnl >= 0 else ""
        print(f"  {ticker:>8s} | avg {pnl_sign}{avg_pnl:>7.2f}% | avg {avg_trades:>5.1f} trades | best strat: {best_strat['strategy']} ({best_strat['total_pnl_pct']:+.2f}%)")

    # Exit reason analysis
    print("\n" + "=" * 110)
    print("EXIT REASON BREAKDOWN PER STRATEGY")
    print("=" * 110)
    for strategy in STRATEGIES:
        strat = [r for r in valid if r["strategy"] == strategy]
        combined_exits = {}
        for r in strat:
            for k, v in r.get("exit_reasons", {}).items():
                combined_exits[k] = combined_exits.get(k, 0) + v
        total = sum(combined_exits.values())
        if total > 0:
            breakdown = " | ".join(f"{k}: {v} ({v/total*100:.0f}%)" for k, v in sorted(combined_exits.items(), key=lambda x: -x[1]))
            print(f"  {strategy:>20s} | {breakdown}")

    # Long vs Short
    print("\n" + "=" * 110)
    print("LONG vs SHORT PER STRATEGY (avg across tickers)")
    print("=" * 110)
    for strategy in STRATEGIES:
        strat = [r for r in valid if r["strategy"] == strategy]
        if strat:
            avg_long = sum(r["long_pnl"] for r in strat) / len(strat)
            avg_short = sum(r["short_pnl"] for r in strat) / len(strat)
            avg_lwr = sum(r["long_win_rate"] for r in strat) / len(strat)
            avg_swr = sum(r["short_win_rate"] for r in strat) / len(strat)
            print(f"  {strategy:>20s} | LONG: ${avg_long:>+8,.0f} WR {avg_lwr:>5.1f}% | SHORT: ${avg_short:>+8,.0f} WR {avg_swr:>5.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
