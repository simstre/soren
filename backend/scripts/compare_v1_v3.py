#!/usr/bin/env python3
"""Compare refined_rsi (v1) vs refined_rsi_v3 across multiple ticker sets."""

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
        print(f"  {label:45s} ERROR: {r['error']}")
        return
    print(f"  {label:45s} {r['trades']:>3}T  {r['wr']:>5.1f}%WR  ${r['pnl']:>+9,.2f}  {r['pnl_pct']:>+6.2f}%  DD:{r['dd']:>5.2f}%  L:{r['long_wr']:.0f}% S:{r['short_wr']:.0f}%  L:${r['long_pnl']:>+,.0f} S:${r['short_pnl']:>+,.0f}  G:{len(r.get('gated',[]))}")


async def main():
    print("=" * 110)
    print("V1 vs V3 COMPARISON — 29 days, 1m, 50% position, 2% SL")
    print("=" * 110)

    # Individual top performers
    for sym in ["AMD", "NVDA", "PFE", "LLY", "QQQ", "XOM", "CVX", "TSLA", "AAPL", "GOOGL"]:
        r1 = await bt([sym], "refined_rsi")
        r3 = await bt([sym], "refined_rsi_v3")
        pr(f"v1 {sym}", r1)
        pr(f"v3 {sym}", r3)
        print()

    # Portfolio combinations
    combos = [
        ["AMD", "NVDA", "PFE"],
        ["AMD", "NVDA", "PFE", "LLY", "XOM"],
        ["NVDA", "PFE", "AAPL"],
        ["AMD", "QQQ", "CVX"],
    ]
    for combo in combos:
        label = ",".join(combo)
        r1 = await bt(combo, "refined_rsi")
        r3 = await bt(combo, "refined_rsi_v3")
        pr(f"v1 {label}", r1)
        pr(f"v3 {label}", r3)
        print()

    # Also test with different position sizes for v3
    print("\n" + "=" * 110)
    print("V3 POSITION SIZE SWEEP — AMD,NVDA,PFE")
    print("=" * 110)
    for pos in [0.25, 0.50, 0.75, 1.00]:
        r = await bt(["AMD", "NVDA", "PFE"], "refined_rsi_v3", pos=pos)
        pr(f"v3 {pos*100:.0f}%", r)


if __name__ == "__main__":
    asyncio.run(main())
