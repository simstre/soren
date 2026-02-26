#!/usr/bin/env python3
"""Contrarian backtest — test v6 (flipped v1 signals) across stop/target combos."""

from __future__ import annotations
import asyncio, sys, json
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

BALANCE = 100_000.0
END = datetime.now().strftime("%Y-%m-%d")
START = (datetime.now() - timedelta(days=21)).strftime("%Y-%m-%d")
SYMBOLS = ["AAPL", "PFE", "CRM", "ABT", "JNJ", "INTC", "KLAC", "WFC", "CDNS", "ABBV"]

# Test the combos that performed WORST for v1 (biggest losses) to see if contrarian profits
COMBOS = [
    # (stop_mult, target_mult, label)
    (2.0, 5.0, "v1 worst: -$666"),
    (2.0, 4.0, "v1: -$507"),
    (2.0, 3.0, "v1: -$480"),
    (2.0, 2.0, "v1 best: +$177"),
    (1.5, 5.0, "v1: -$289"),
    (1.5, 2.0, "v1: +$16"),
    (1.0, 5.0, "v1: -$182"),
    (0.5, 2.0, "v1: -$29"),
]

POS_SIZE = 0.10
STOP_LOSS_PCT = 0.02


async def bt(symbols, strategy, stop_mult, target_mult):
    broker = ReplayBrokerAdapter(starting_balance=BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)
    try:
        await engine.start(
            symbols=symbols, timeframe="1m", start_date=START,
            end_date=END, speed=1.0, stop_loss_pct=STOP_LOSS_PCT,
            position_size_pct=POS_SIZE, strategy=strategy, headless=True,
        )
        if engine._tf_params:
            engine._tf_params = replace(
                engine._tf_params,
                atr_stop_mult=stop_mult,
                atr_target_mult=target_mult,
            )
        if engine._task:
            await engine._task
    except Exception as e:
        return {"error": str(e)}

    r = engine.get_results()
    if not r:
        return {"error": "no results"}

    exit_reasons = {}
    for t in engine._trade_records:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "trades": r.total_trades, "wr": r.win_rate,
        "pnl": r.total_pnl, "pnl_pct": r.total_pnl_pct,
        "dd": r.max_drawdown_pct,
        "long_wr": r.long_win_rate, "short_wr": r.short_win_rate,
        "long_pnl": r.long_pnl, "short_pnl": r.short_pnl,
        "exit_reasons": exit_reasons,
        "gated": len(engine._gated_symbols),
    }


async def main():
    print("=" * 130)
    print(f"CONTRARIAN BACKTEST — v6 vs v1, {','.join(SYMBOLS)}, {START} to {END}, 1m, {POS_SIZE*100:.0f}% pos")
    print("=" * 130)

    all_results = []

    for stop_m, target_m, note in COMBOS:
        # Run v1 (original)
        print(f"\n  [{note}] Stop={stop_m:.1f}x Target={target_m:.1f}x")
        print(f"    v1 (original) ...", end=" ", flush=True)
        r1 = await bt(SYMBOLS, "refined_rsi", stop_m, target_m)
        if r1.get("error"):
            print(f"ERROR: {r1['error']}")
        else:
            exits = r1["exit_reasons"]
            exit_str = "  ".join(f"{k}:{v}" for k, v in sorted(exits.items()))
            print(f"{r1['trades']:>3}T  {r1['wr']:>5.1f}%WR  ${r1['pnl']:>+9,.2f}  DD:{r1['dd']:.2f}%  L:{r1['long_wr']:.0f}% S:{r1['short_wr']:.0f}%  G:{r1['gated']}  {exit_str}")

        # Run v6 (contrarian)
        print(f"    v6 (contrarian) ...", end=" ", flush=True)
        r6 = await bt(SYMBOLS, "refined_rsi_v6", stop_m, target_m)
        if r6.get("error"):
            print(f"ERROR: {r6['error']}")
        else:
            exits = r6["exit_reasons"]
            exit_str = "  ".join(f"{k}:{v}" for k, v in sorted(exits.items()))
            print(f"{r6['trades']:>3}T  {r6['wr']:>5.1f}%WR  ${r6['pnl']:>+9,.2f}  DD:{r6['dd']:.2f}%  L:{r6['long_wr']:.0f}% S:{r6['short_wr']:.0f}%  G:{r6['gated']}  {exit_str}")

        diff = (r6.get("pnl", 0) or 0) - (r1.get("pnl", 0) or 0)
        print(f"    Delta: ${diff:>+,.2f}")

        all_results.append({
            "stop_mult": stop_m, "target_mult": target_m, "note": note,
            "v1": r1, "v6": r6,
        })

    # Summary
    print("\n" + "=" * 130)
    print("SUMMARY")
    print("=" * 130)
    print(f"  {'Stop':>5s}  {'Tgt':>4s}  {'v1 Trades':>9s}  {'v1 WR':>6s}  {'v1 PnL':>10s}  |  {'v6 Trades':>9s}  {'v6 WR':>6s}  {'v6 PnL':>10s}  {'Delta':>10s}")
    print(f"  {'-'*5}  {'-'*4}  {'-'*9}  {'-'*6}  {'-'*10}  |  {'-'*9}  {'-'*6}  {'-'*10}  {'-'*10}")
    for row in all_results:
        r1 = row["v1"]
        r6 = row["v6"]
        if r1.get("error") or r6.get("error"):
            continue
        diff = r6["pnl"] - r1["pnl"]
        print(
            f"  {row['stop_mult']:>4.1f}x  {row['target_mult']:>3.1f}x  "
            f"{r1['trades']:>9d}  {r1['wr']:>5.1f}%  ${r1['pnl']:>+9,.2f}  |  "
            f"{r6['trades']:>9d}  {r6['wr']:>5.1f}%  ${r6['pnl']:>+9,.2f}  ${diff:>+9,.2f}"
        )

    # Save
    out_path = Path(__file__).resolve().parent.parent / "backtest_results" / f"contrarian_v6_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
