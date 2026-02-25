#!/usr/bin/env python3
"""Strategy research: systematic backtesting across parameters to find optimal setup.

Studies:
1. Which tickers perform best with the current strategy?
2. What RSI band widths produce best results?
3. What S/R strength thresholds matter?
4. What ATR stop/target multipliers work best?
5. Do fewer, higher-quality trades outperform many trades?
6. Long-only vs short-only vs both?

Usage:
    cd backend && python -m scripts.strategy_research
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

STARTING_BALANCE = 100_000.0
END_DATE = datetime.now().strftime("%Y-%m-%d")
START_DATE_29 = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
START_DATE_7 = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

# Tickers to test individually
TEST_TICKERS = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "AMD", "NFLX", "CRM", "INTC", "QCOM",
    "JPM", "BAC", "GS", "V", "MA",
    "UNH", "JNJ", "PFE", "LLY", "ABBV",
    "XOM", "CVX", "COP",
    "SPY", "QQQ",
]


async def run_backtest(
    symbols: list[str],
    timeframe: str = "1m",
    start_date: str = "",
    end_date: str = "",
    strategy: str = "refined_rsi",
    position_size_pct: float = 0.50,
    stop_loss_pct: float = 0.02,
) -> dict:
    """Run a single headless backtest and return structured results."""
    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)

    try:
        await engine.start(
            symbols=symbols,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            speed=1.0,
            stop_loss_pct=stop_loss_pct,
            position_size_pct=position_size_pct,
            strategy=strategy,
            headless=True,
        )
        if engine._task:
            await engine._task
    except Exception as e:
        return {"error": str(e), "symbols": symbols}

    results = engine.get_results()
    if results is None:
        return {"error": "no results", "symbols": symbols}

    # Per-trade breakdown
    trades = []
    for t in results.trades:
        if t["status"] != "CLOSED":
            continue
        trades.append({
            "symbol": t["symbol"],
            "side": t["side"],
            "pnl": t.get("pnl", 0) or 0,
            "pnl_pct": t.get("pnl_pct", 0) or 0,
            "reason": t.get("reason", ""),
            "entry_price": t.get("entry_price", 0),
            "exit_price": t.get("exit_price", 0),
        })

    gated = list(engine._gated_symbols)

    return {
        "error": "",
        "symbols": symbols,
        "strategy": strategy,
        "timeframe": timeframe,
        "position_size_pct": position_size_pct,
        "stop_loss_pct": stop_loss_pct,
        "total_trades": results.total_trades,
        "winning_trades": results.winning_trades,
        "losing_trades": results.losing_trades,
        "win_rate": results.win_rate,
        "total_pnl": results.total_pnl,
        "total_pnl_pct": results.total_pnl_pct,
        "max_drawdown_pct": results.max_drawdown_pct,
        "ending_balance": results.ending_balance,
        "long_win_rate": results.long_win_rate,
        "short_win_rate": results.short_win_rate,
        "long_pnl": results.long_pnl,
        "short_pnl": results.short_pnl,
        "gated_symbols": gated,
        "trades": trades,
    }


def print_result(label: str, r: dict) -> None:
    if r.get("error"):
        print(f"  {label:40s} ERROR: {r['error']}")
        return
    pnl_str = f"${r['total_pnl']:>+9,.2f}"
    pnl_pct = f"{r['total_pnl_pct']:>+7.2f}%"
    wr = f"{r['win_rate']:>5.1f}%"
    trades = f"{r['total_trades']:>3}"
    dd = f"{r['max_drawdown_pct']:>5.2f}%"
    long_wr = f"L:{r['long_win_rate']:.0f}%"
    short_wr = f"S:{r['short_win_rate']:.0f}%"
    long_pnl = f"L:${r['long_pnl']:>+,.0f}"
    short_pnl = f"S:${r['short_pnl']:>+,.0f}"
    gated = f"G:{len(r.get('gated_symbols', []))}"
    print(f"  {label:40s} {trades} trades  {wr} WR  {pnl_str}  {pnl_pct}  DD:{dd}  {long_wr} {short_wr}  {long_pnl} {short_pnl}  {gated}")


async def study_1_per_ticker():
    """Study 1: Which tickers perform best individually?"""
    print("\n" + "=" * 100)
    print("STUDY 1: Per-Ticker Performance (refined_rsi, 1m, 29 days, 50% position)")
    print("=" * 100)

    results = []
    for ticker in TEST_TICKERS:
        print(f"  Testing {ticker}...", end="", flush=True)
        r = await run_backtest(
            [ticker], "1m", START_DATE_29, END_DATE,
            strategy="refined_rsi", position_size_pct=0.50, stop_loss_pct=0.02,
        )
        r["ticker"] = ticker
        results.append(r)
        if r.get("error"):
            print(f" ERROR: {r['error']}")
        else:
            print(f" {r['total_trades']} trades, {r['win_rate']:.1f}% WR, {r['total_pnl_pct']:+.2f}%")

    # Sort by P&L
    valid = [r for r in results if not r.get("error")]
    valid.sort(key=lambda r: r["total_pnl_pct"], reverse=True)

    print(f"\n{'Ticker':>8}  {'Trades':>6}  {'WR%':>6}  {'P&L':>10}  {'P&L%':>8}  {'MaxDD%':>8}  {'LongWR':>7}  {'ShortWR':>8}  {'LongPnL':>10}  {'ShortPnL':>10}")
    print("-" * 100)
    for r in valid:
        print(
            f"{r['ticker']:>8}  "
            f"{r['total_trades']:>6}  "
            f"{r['win_rate']:>5.1f}%  "
            f"${r['total_pnl']:>+9,.2f}  "
            f"{r['total_pnl_pct']:>+7.2f}%  "
            f"{r['max_drawdown_pct']:>7.2f}%  "
            f"{r['long_win_rate']:>5.0f}%  "
            f"{r['short_win_rate']:>6.0f}%  "
            f"${r['long_pnl']:>+9,.2f}  "
            f"${r['short_pnl']:>+9,.2f}"
        )

    # Analyze: long vs short across all tickers
    all_long_pnl = sum(r.get("long_pnl", 0) for r in valid)
    all_short_pnl = sum(r.get("short_pnl", 0) for r in valid)
    print(f"\nAggregate Long P&L: ${all_long_pnl:+,.2f}")
    print(f"Aggregate Short P&L: ${all_short_pnl:+,.2f}")

    return results


async def study_2_grade_analysis(study1_results: list[dict]):
    """Study 2: Analyze trade quality by setup grade."""
    print("\n" + "=" * 100)
    print("STUDY 2: Setup Grade Analysis (A-E)")
    print("=" * 100)

    grade_stats: dict[str, dict] = {}  # grade -> {wins, losses, total_pnl}

    for r in study1_results:
        if r.get("error"):
            continue
        for trade in r.get("trades", []):
            reason = trade.get("reason", "")
            # Parse grade from [X] prefix
            grade = "?"
            if reason.startswith("[") and len(reason) > 2 and reason[2] == "]":
                grade = reason[1]

            if grade not in grade_stats:
                grade_stats[grade] = {"wins": 0, "losses": 0, "total_pnl": 0, "count": 0, "pnls": []}

            stats = grade_stats[grade]
            stats["count"] += 1
            pnl = trade.get("pnl", 0)
            stats["total_pnl"] += pnl
            stats["pnls"].append(pnl)
            if pnl > 0:
                stats["wins"] += 1
            else:
                stats["losses"] += 1

    print(f"\n{'Grade':>6}  {'Trades':>7}  {'Wins':>5}  {'Losses':>7}  {'WR%':>6}  {'Total PnL':>12}  {'Avg PnL':>10}  {'Avg Win':>10}  {'Avg Loss':>10}")
    print("-" * 95)
    for grade in sorted(grade_stats.keys()):
        s = grade_stats[grade]
        wr = (s["wins"] / s["count"] * 100) if s["count"] > 0 else 0
        avg_pnl = s["total_pnl"] / s["count"] if s["count"] > 0 else 0
        wins_list = [p for p in s["pnls"] if p > 0]
        losses_list = [p for p in s["pnls"] if p <= 0]
        avg_win = sum(wins_list) / len(wins_list) if wins_list else 0
        avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0
        print(
            f"{grade:>6}  "
            f"{s['count']:>7}  "
            f"{s['wins']:>5}  "
            f"{s['losses']:>7}  "
            f"{wr:>5.1f}%  "
            f"${s['total_pnl']:>+11,.2f}  "
            f"${avg_pnl:>+9,.2f}  "
            f"${avg_win:>+9,.2f}  "
            f"${avg_loss:>+9,.2f}"
        )

    return grade_stats


async def study_3_stop_loss_variations():
    """Study 3: Test different stop loss percentages on top performers."""
    print("\n" + "=" * 100)
    print("STUDY 3: Stop Loss Variations (top 5 tickers)")
    print("=" * 100)

    # Use a few known good performers
    tickers = ["NVDA", "PFE", "AAPL", "GOOGL", "UNH"]
    stop_losses = [0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0]

    for sl in stop_losses:
        label = f"SL={sl*100:.1f}%" if sl > 0 else "SL=None"
        r = await run_backtest(
            tickers, "1m", START_DATE_29, END_DATE,
            strategy="refined_rsi", position_size_pct=0.50, stop_loss_pct=sl,
        )
        print_result(f"{label} ({','.join(tickers)})", r)


async def study_4_position_size_variations():
    """Study 4: Test position size impact."""
    print("\n" + "=" * 100)
    print("STUDY 4: Position Size Variations")
    print("=" * 100)

    tickers = ["NVDA", "PFE", "AAPL"]
    sizes = [0.10, 0.25, 0.33, 0.50, 0.75, 1.00]

    for size in sizes:
        label = f"Position={size*100:.0f}%"
        r = await run_backtest(
            tickers, "1m", START_DATE_29, END_DATE,
            strategy="refined_rsi", position_size_pct=size, stop_loss_pct=0.02,
        )
        print_result(f"{label} ({','.join(tickers)})", r)


async def study_5_v1_vs_v2():
    """Study 5: Compare v1 and v2 strategies."""
    print("\n" + "=" * 100)
    print("STUDY 5: refined_rsi (v1) vs refined_rsi_v2 (v2)")
    print("=" * 100)

    tickers_sets = [
        ["NVDA"], ["PFE"], ["AAPL"], ["GOOGL"], ["UNH"],
        ["NVDA", "PFE", "AAPL"],
    ]

    for tickers in tickers_sets:
        label = ",".join(tickers)
        r1 = await run_backtest(
            tickers, "1m", START_DATE_29, END_DATE,
            strategy="refined_rsi", position_size_pct=0.50, stop_loss_pct=0.02,
        )
        r2 = await run_backtest(
            tickers, "1m", START_DATE_29, END_DATE,
            strategy="refined_rsi_v2", position_size_pct=0.50, stop_loss_pct=0.02,
        )
        print_result(f"v1  {label}", r1)
        print_result(f"v2  {label}", r2)
        print()


async def study_6_long_only_analysis(study1_results: list[dict]):
    """Study 6: Analyze if long-only or short-only would be better."""
    print("\n" + "=" * 100)
    print("STUDY 6: Long vs Short Trade Analysis")
    print("=" * 100)

    long_trades = []
    short_trades = []

    for r in study1_results:
        if r.get("error"):
            continue
        for trade in r.get("trades", []):
            if trade["side"] == "BUY":
                long_trades.append(trade)
            else:
                short_trades.append(trade)

    for label, trades in [("LONG", long_trades), ("SHORT", short_trades)]:
        if not trades:
            print(f"  {label}: No trades")
            continue
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in trades)
        wr = len(wins) / len(trades) * 100 if trades else 0
        avg_pnl = total_pnl / len(trades)
        avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
        print(
            f"  {label:6s}: {len(trades):>4} trades, "
            f"{len(wins)} wins ({wr:.1f}%), "
            f"Total P&L: ${total_pnl:>+,.2f}, "
            f"Avg: ${avg_pnl:>+,.2f}, "
            f"Avg Win: ${avg_win:>+,.2f}, "
            f"Avg Loss: ${avg_loss:>+,.2f}"
        )


async def main():
    print("=" * 100)
    print("STRATEGY RESEARCH — Systematic Parameter Optimization")
    print(f"Period: {START_DATE_29} → {END_DATE} (29 days, 1m candles)")
    print(f"Starting Balance: ${STARTING_BALANCE:,.0f}")
    print("=" * 100)

    # Study 1: Per-ticker performance
    study1_results = await study_1_per_ticker()

    # Study 2: Grade analysis
    await study_2_grade_analysis(study1_results)

    # Study 6: Long vs Short (uses study 1 data)
    await study_6_long_only_analysis(study1_results)

    # Study 3: Stop loss variations
    await study_3_stop_loss_variations()

    # Study 4: Position size
    await study_4_position_size_variations()

    # Study 5: v1 vs v2
    await study_5_v1_vs_v2()

    # Save all results
    output_path = Path("backtest_results/strategy_research.json")
    output_path.parent.mkdir(exist_ok=True)
    with open(output_path, "w") as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "study1_per_ticker": study1_results,
        }, f, indent=2, default=str)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    asyncio.run(main())
