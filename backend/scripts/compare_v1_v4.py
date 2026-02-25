#!/usr/bin/env python3
"""Compare refined_rsi (v1) vs refined_rsi_v4 across tickers."""

from __future__ import annotations
import asyncio, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

BALANCE = 100_000.0
END = datetime.now().strftime("%Y-%m-%d")
START = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")


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
    }


def pr(label, r):
    if r.get("error"):
        print(f"  {label:50s} ERROR: {r['error']}")
        return
    print(f"  {label:50s} {r['trades']:>3}T  {r['wr']:>5.1f}%WR  ${r['pnl']:>+9,.2f}  {r['pnl_pct']:>+6.2f}%  DD:{r['dd']:>5.2f}%  L:{r['long_wr']:.0f}% S:{r['short_wr']:.0f}%  L:${r['long_pnl']:>+,.0f} S:${r['short_pnl']:>+,.0f}  G:{len(r.get('gated',[]))}")


async def main():
    print("=" * 120)
    print(f"V1 vs V4 COMPARISON — 29 days, 1m, 50% position, 2% SL")
    print(f"V4 changes: skip 2pm hour, sell RSI band 56-70 (was 58-70), ATR target 3.5x (was 3.0x)")
    print("=" * 120)

    # Individual tickers
    all_v1_pnl = 0
    all_v4_pnl = 0
    all_v1_trades = 0
    all_v4_trades = 0
    for sym in ["AMD", "NVDA", "PFE", "LLY", "QQQ", "XOM", "CVX", "TSLA", "AAPL", "GOOGL"]:
        r1 = await bt([sym], "refined_rsi")
        r4 = await bt([sym], "refined_rsi_v4")
        pr(f"v1 {sym}", r1)
        pr(f"v4 {sym}", r4)
        diff = (r4.get("pnl", 0) - r1.get("pnl", 0))
        diff_t = (r4.get("trades", 0) - r1.get("trades", 0))
        print(f"  {'  Δ':50s} {diff_t:>+3}T            ${diff:>+9,.2f}")
        print()
        all_v1_pnl += r1.get("pnl", 0)
        all_v4_pnl += r4.get("pnl", 0)
        all_v1_trades += r1.get("trades", 0)
        all_v4_trades += r4.get("trades", 0)

    print(f"  {'TOTAL (individual)':50s}")
    print(f"  {'  v1':50s} {all_v1_trades:>3}T          ${all_v1_pnl:>+9,.2f}")
    print(f"  {'  v4':50s} {all_v4_trades:>3}T          ${all_v4_pnl:>+9,.2f}")
    print(f"  {'  Δ':50s} {all_v4_trades-all_v1_trades:>+3}T          ${all_v4_pnl-all_v1_pnl:>+9,.2f}")

    # Portfolio combos
    print("\n" + "=" * 120)
    print("PORTFOLIO COMBINATIONS")
    print("=" * 120)
    combos = [
        ["AMD", "NVDA", "PFE"],
        ["AMD", "NVDA", "PFE", "LLY", "XOM"],
        ["AMD", "QQQ", "CVX"],
        ["NVDA", "PFE", "AAPL"],
    ]
    for combo in combos:
        label = ",".join(combo)
        r1 = await bt(combo, "refined_rsi")
        r4 = await bt(combo, "refined_rsi_v4")
        pr(f"v1 {label}", r1)
        pr(f"v4 {label}", r4)
        diff = (r4.get("pnl", 0) - r1.get("pnl", 0))
        diff_t = (r4.get("trades", 0) - r1.get("trades", 0))
        print(f"  {'  Δ':50s} {diff_t:>+3}T            ${diff:>+9,.2f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
