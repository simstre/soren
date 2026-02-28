#!/usr/bin/env python3
"""Compare backtest performance: top 1 vs top 2 vs top 3 tickers from the scanner.

Runs headless backtests with the refined_rsi strategy at 50% position size
over the last 30 days on 1m candles, using the fitness scanner to select tickers.

Usage:
    cd backend && python -m scripts.compare_ticker_counts
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.scanner.fitness import run_fitness_scan

STRATEGY = "refined_rsi"
TIMEFRAME = "1m"
POSITION_SIZE_PCT = 0.50
STOP_LOSS_PCT = 0.02
STARTING_BALANCE = 100_000.0

# Test period: last 29 days (yfinance 1m limit is 30 days)
END_DATE = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")


async def run_backtest(symbols: list[str], label: str) -> dict:
    """Run a single headless backtest and return results."""
    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)

    try:
        await engine.start(
            symbols=symbols,
            timeframe=TIMEFRAME,
            start_date=START_DATE,
            end_date=END_DATE,
            speed=1.0,
            stop_loss_pct=STOP_LOSS_PCT,
            position_size_pct=POSITION_SIZE_PCT,
            strategy=STRATEGY,
            headless=True,
        )

        if engine._task:
            await engine._task

    except Exception as e:
        return {"label": label, "symbols": symbols, "error": str(e)}

    results = engine.get_results()
    if results is None:
        return {"label": label, "symbols": symbols, "error": "no results"}

    # Per-symbol breakdown
    symbol_pnl: dict[str, float] = {}
    symbol_trades: dict[str, int] = {}
    symbol_wins: dict[str, int] = {}
    for t in results.trades:
        if t["status"] != "CLOSED":
            continue
        sym = t["symbol"]
        pnl = t.get("pnl", 0) or 0
        symbol_pnl[sym] = symbol_pnl.get(sym, 0) + pnl
        symbol_trades[sym] = symbol_trades.get(sym, 0) + 1
        if pnl > 0:
            symbol_wins[sym] = symbol_wins.get(sym, 0) + 1

    # Gated symbols
    gated = list(engine._gated_symbols)

    return {
        "label": label,
        "symbols": symbols,
        "error": "",
        "total_trades": results.total_trades,
        "winning_trades": results.winning_trades,
        "losing_trades": results.losing_trades,
        "win_rate": results.win_rate,
        "total_pnl": results.total_pnl,
        "total_pnl_pct": results.total_pnl_pct,
        "max_drawdown_pct": results.max_drawdown_pct,
        "ending_balance": results.ending_balance,
        "symbol_pnl": symbol_pnl,
        "symbol_trades": symbol_trades,
        "symbol_wins": symbol_wins,
        "gated_symbols": gated,
    }


async def main() -> None:
    print("=" * 80)
    print("TICKER COUNT COMPARISON: Top 1 vs Top 2 vs Top 3")
    print(f"Strategy: {STRATEGY}  |  Timeframe: {TIMEFRAME}  |  Position: {POSITION_SIZE_PCT*100:.0f}%")
    print(f"Period: {START_DATE} → {END_DATE}  |  Stop Loss: {STOP_LOSS_PCT*100:.0f}%")
    print("=" * 80)

    # Step 1: Get top tickers from fitness scanner
    print("\n[1/4] Running fitness scanner...")
    scores = await run_fitness_scan(top_n=3)
    if len(scores) < 3:
        print(f"ERROR: Scanner returned only {len(scores)} tickers, need at least 3")
        return

    top_10 = [s.symbol for s in scores]
    print(f"  Top 3: {', '.join(top_10)}")
    for s in scores[:3]:
        print(f"    {s.symbol:6s}: score={s.total_score:5.1f}  price=${s.price:>8.2f}  vol={s.avg_volume:>12,}  atr={s.atr_pct:.2f}%  sr={s.sr_strong_count}  [vol={s.volume_score:.0f} price={s.price_score:.0f} sr={s.sr_score:.0f} atr={s.atr_score:.0f}]")

    # Step 2: Run backtests
    configs = [
        (top_10[:1], "Top 1 only"),
        (top_10[:2], "Top 2"),
        (top_10[:3], "Top 3"),
    ]

    results_list: list[dict] = []
    for i, (symbols, label) in enumerate(configs):
        print(f"\n[{i+2}/4] Backtesting {label}: {', '.join(symbols)} ...")
        result = await run_backtest(symbols, label)
        results_list.append(result)

        if result.get("error"):
            print(f"  ERROR: {result['error']}")
        else:
            print(f"  Trades: {result['total_trades']}  |  Win Rate: {result['win_rate']:.1f}%  |  P&L: {result['total_pnl_pct']:+.2f}%  |  Max DD: {result['max_drawdown_pct']:.2f}%")

    # Step 3: Print comparison
    print("\n" + "=" * 80)
    print("COMPARISON RESULTS")
    print("=" * 80)
    print(f"{'Config':<14} {'Symbols':<20} {'Trades':>7} {'Wins':>6} {'WR%':>6} {'P&L':>10} {'P&L%':>8} {'MaxDD%':>8} {'Gated':>8}")
    print("-" * 80)

    for r in results_list:
        if r.get("error"):
            print(f"{r['label']:<14} {','.join(r['symbols']):<20} {'ERROR: ' + r['error'][:40]}")
            continue
        print(
            f"{r['label']:<14} "
            f"{','.join(r['symbols']):<20} "
            f"{r['total_trades']:>7} "
            f"{r['winning_trades']:>6} "
            f"{r['win_rate']:>5.1f}% "
            f"${r['total_pnl']:>+9,.2f} "
            f"{r['total_pnl_pct']:>+7.2f}% "
            f"{r['max_drawdown_pct']:>7.2f}% "
            f"{len(r.get('gated_symbols', [])):>8}"
        )

    # Step 4: Per-symbol breakdown
    print("\n" + "-" * 80)
    print("PER-SYMBOL BREAKDOWN")
    print("-" * 80)

    for r in results_list:
        if r.get("error"):
            continue
        print(f"\n  {r['label']}:")
        sym_pnl = r.get("symbol_pnl", {})
        sym_trades = r.get("symbol_trades", {})
        sym_wins = r.get("symbol_wins", {})
        gated = r.get("gated_symbols", [])

        for sym in r["symbols"]:
            pnl = sym_pnl.get(sym, 0)
            trades = sym_trades.get(sym, 0)
            wins = sym_wins.get(sym, 0)
            wr = (wins / trades * 100) if trades > 0 else 0
            gate_flag = " [GATED]" if sym in gated else ""
            print(
                f"    {sym:6s}: {trades:>3} trades, "
                f"{wins:>3} wins ({wr:>5.1f}%), "
                f"P&L ${pnl:>+9,.2f}"
                f"{gate_flag}"
            )

    # Best config
    valid = [r for r in results_list if not r.get("error")]
    if valid:
        best = max(valid, key=lambda r: r["total_pnl_pct"])
        print(f"\n{'=' * 80}")
        print(f"BEST CONFIG: {best['label']} ({', '.join(best['symbols'])}) → {best['total_pnl_pct']:+.2f}% P&L")
        print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
