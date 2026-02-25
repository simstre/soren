#!/usr/bin/env python3
"""Parameter sweep — test individual changes from v1 baseline to find what actually helps."""

from __future__ import annotations
import asyncio, sys, json
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

BALANCE = 100_000.0
END = datetime.now().strftime("%Y-%m-%d")
START = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
SYMBOLS = ["AMD", "NVDA", "PFE"]


async def bt(symbols, strategy, pos=0.50, sl=0.02):
    broker = ReplayBrokerAdapter(starting_balance=BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)
    try:
        await engine.start(symbols=symbols, timeframe="1m", start_date=START,
                           end_date=END, speed=1.0, stop_loss_pct=sl,
                           position_size_pct=pos, strategy=strategy, headless=True)
        if engine._task:
            await engine._task
    except Exception as e:
        return {"error": str(e)}
    r = engine.get_results()
    if not r:
        return {"error": "no results"}
    return {
        "trades": r.total_trades, "wr": r.win_rate, "pnl": r.total_pnl,
        "pnl_pct": r.total_pnl_pct, "dd": r.max_drawdown_pct,
        "long_wr": r.long_win_rate, "short_wr": r.short_win_rate,
        "long_pnl": r.long_pnl, "short_pnl": r.short_pnl,
        "gated": list(engine._gated_symbols),
        "trade_records": engine._trade_records,
    }


def pr(label, r):
    if r.get("error"):
        print(f"  {label:50s} ERROR: {r['error']}")
        return
    print(f"  {label:50s} {r['trades']:>3}T  {r['wr']:>5.1f}%WR  ${r['pnl']:>+9,.2f}  {r['pnl_pct']:>+6.2f}%  DD:{r['dd']:>5.2f}%  L:{r['long_wr']:.0f}% S:{r['short_wr']:.0f}%  L:${r['long_pnl']:>+,.0f} S:${r['short_pnl']:>+,.0f}  G:{len(r.get('gated',[]))}")


async def main():
    print("=" * 120)
    print(f"PARAMETER SWEEP — {','.join(SYMBOLS)}, 29 days, 1m, 50% pos, 2% SL")
    print("=" * 120)

    # Baseline: v1
    print("\n--- BASELINE ---")
    r = await bt(SYMBOLS, "refined_rsi")
    pr("v1 baseline", r)
    baseline_trades = r.get("trade_records", [])

    # Analyze v1 trade patterns
    print("\n--- V1 TRADE ANALYSIS ---")
    if baseline_trades:
        wins = [t for t in baseline_trades if t.get("pnl", 0) > 0]
        losses = [t for t in baseline_trades if t.get("pnl", 0) <= 0]
        print(f"  Total: {len(baseline_trades)}, Wins: {len(wins)}, Losses: {len(losses)}")

        # Analyze by hour of day
        hour_stats = {}
        for t in baseline_trades:
            entry = t.get("entry_time", "")
            if entry:
                try:
                    h = datetime.fromisoformat(entry.replace("Z", "+00:00")).hour
                except:
                    try:
                        h = int(entry.split("T")[1].split(":")[0])
                    except:
                        continue
                if h not in hour_stats:
                    hour_stats[h] = {"wins": 0, "losses": 0, "pnl": 0.0}
                if t.get("pnl", 0) > 0:
                    hour_stats[h]["wins"] += 1
                else:
                    hour_stats[h]["losses"] += 1
                hour_stats[h]["pnl"] += t.get("pnl", 0)

        print("  By hour:")
        for h in sorted(hour_stats):
            s = hour_stats[h]
            total = s["wins"] + s["losses"]
            wr = s["wins"] / total * 100 if total else 0
            print(f"    {h:02d}:00  {total:>3}T  {wr:>5.1f}%WR  ${s['pnl']:>+9,.2f}")

        # Analyze by exit type
        exit_stats = {}
        for t in baseline_trades:
            exit_type = t.get("exit_reason", "unknown")
            if exit_type not in exit_stats:
                exit_stats[exit_type] = {"count": 0, "pnl": 0.0, "wins": 0}
            exit_stats[exit_type]["count"] += 1
            exit_stats[exit_type]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                exit_stats[exit_type]["wins"] += 1

        print("  By exit type:")
        for et, s in sorted(exit_stats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = s["wins"] / s["count"] * 100 if s["count"] else 0
            print(f"    {et:30s} {s['count']:>3}T  {wr:>5.1f}%WR  ${s['pnl']:>+9,.2f}")

        # Analyze by setup grade
        grade_stats = {}
        for t in baseline_trades:
            reason = t.get("reason", "")
            grade = "?"
            if reason and reason.startswith("[") and len(reason) > 2:
                grade = reason[1]
            if grade not in grade_stats:
                grade_stats[grade] = {"count": 0, "pnl": 0.0, "wins": 0}
            grade_stats[grade]["count"] += 1
            grade_stats[grade]["pnl"] += t.get("pnl", 0)
            if t.get("pnl", 0) > 0:
                grade_stats[grade]["wins"] += 1

        print("  By grade:")
        for g in sorted(grade_stats):
            s = grade_stats[g]
            wr = s["wins"] / s["count"] * 100 if s["count"] else 0
            print(f"    {g:5s} {s['count']:>3}T  {wr:>5.1f}%WR  ${s['pnl']:>+9,.2f}")

        # Analyze by side
        for side in ["BUY", "SELL"]:
            side_trades = [t for t in baseline_trades if t.get("side") == side]
            if side_trades:
                side_wins = [t for t in side_trades if t.get("pnl", 0) > 0]
                side_pnl = sum(t.get("pnl", 0) for t in side_trades)
                avg_win = sum(t.get("pnl", 0) for t in side_wins) / len(side_wins) if side_wins else 0
                avg_loss = sum(t.get("pnl", 0) for t in side_trades if t.get("pnl", 0) <= 0) / max(1, len(side_trades) - len(side_wins))
                print(f"  {side:4s}: {len(side_trades)}T, {len(side_wins)}W, WR={len(side_wins)/len(side_trades)*100:.1f}%, PnL=${side_pnl:+,.2f}, AvgWin=${avg_win:+,.2f}, AvgLoss=${avg_loss:+,.2f}")

        # Analyze win/loss size distributions
        if wins:
            avg_win_size = sum(t.get("pnl", 0) for t in wins) / len(wins)
            max_win = max(t.get("pnl", 0) for t in wins)
            print(f"  Avg win: ${avg_win_size:+,.2f}, Max win: ${max_win:+,.2f}")
        if losses:
            avg_loss_size = sum(t.get("pnl", 0) for t in losses) / len(losses)
            max_loss = min(t.get("pnl", 0) for t in losses)
            print(f"  Avg loss: ${avg_loss_size:+,.2f}, Max loss: ${max_loss:+,.2f}")

    # Test variants
    print("\n--- STOP LOSS SWEEP (v1 base) ---")
    for sl in [0.005, 0.01, 0.015, 0.02, 0.03]:
        r = await bt(SYMBOLS, "refined_rsi", sl=sl)
        pr(f"SL={sl*100:.1f}%", r)

    # Test v1 on broader ticker universe
    print("\n--- V1 ACROSS TICKERS (individual) ---")
    tickers = ["AMD", "NVDA", "PFE", "LLY", "QQQ", "XOM", "CVX", "TSLA", "AAPL", "GOOGL",
               "MSFT", "AMZN", "META", "JPM", "BAC", "GS", "UNH", "JNJ"]
    for sym in tickers:
        r = await bt([sym], "refined_rsi")
        pr(f"v1 {sym}", r)

    # Save results for analysis
    print("\n--- DONE ---")


if __name__ == "__main__":
    asyncio.run(main())
