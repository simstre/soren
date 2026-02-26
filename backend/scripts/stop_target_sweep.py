#!/usr/bin/env python3
"""Stop/target parameter sweep — test tighter stops + wider targets on v1 baseline."""

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

STOP_MULTS  = [0.5, 0.75, 1.0, 1.5, 2.0]
TARGET_MULTS = [2.0, 3.0, 4.0, 5.0]

POS_SIZE = 0.10  # 10% position
STOP_LOSS_PCT = 0.02  # 2% fixed stop (ATR stop is min'd against this)


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
        # Patch tf_params AFTER start() sets them but BEFORE the loop task runs
        if engine._tf_params:
            engine._tf_params = replace(
                engine._tf_params,
                atr_stop_mult=stop_mult,
                atr_target_mult=target_mult,
            )
        if engine._task:
            await engine._task
    except Exception as e:
        return {"error": str(e), "stop_mult": stop_mult, "target_mult": target_mult}

    r = engine.get_results()
    if not r:
        return {"error": "no results", "stop_mult": stop_mult, "target_mult": target_mult}

    # Count exit reasons
    exit_reasons = {}
    for t in engine._trade_records:
        reason = t.get("exit_reason", "unknown")
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

    return {
        "stop_mult": stop_mult,
        "target_mult": target_mult,
        "trades": r.total_trades,
        "wr": r.win_rate,
        "pnl": r.total_pnl,
        "pnl_pct": r.total_pnl_pct,
        "dd": r.max_drawdown_pct,
        "long_wr": r.long_win_rate,
        "short_wr": r.short_win_rate,
        "long_pnl": r.long_pnl,
        "short_pnl": r.short_pnl,
        "exit_reasons": exit_reasons,
        "gated": len(engine._gated_symbols),
    }


async def main():
    print("=" * 130)
    print(f"STOP/TARGET SWEEP — v1, {','.join(SYMBOLS)}, {START} to {END}, 1m, {POS_SIZE*100:.0f}% pos, {STOP_LOSS_PCT*100:.0f}% SL")
    print("=" * 130)

    all_results = []

    for stop_m in STOP_MULTS:
        for target_m in TARGET_MULTS:
            label = f"Stop={stop_m:.2f}x Target={target_m:.1f}x"
            print(f"\n  Running {label} ...", end=" ", flush=True)
            r = await bt(SYMBOLS, "refined_rsi", stop_m, target_m)
            all_results.append(r)

            if r.get("error"):
                print(f"ERROR: {r['error']}")
            else:
                exits = r["exit_reasons"]
                exit_str = "  ".join(f"{k}:{v}" for k, v in sorted(exits.items()))
                print(
                    f"{r['trades']:>3}T  {r['wr']:>5.1f}%WR  "
                    f"${r['pnl']:>+9,.2f} ({r['pnl_pct']:>+.2f}%)  "
                    f"DD:{r['dd']:>5.2f}%  "
                    f"L:{r['long_wr']:.0f}% S:{r['short_wr']:.0f}%  "
                    f"Gated:{r['gated']}  "
                    f"Exits: {exit_str}"
                )

    # Summary table sorted by PnL
    print("\n" + "=" * 130)
    print("SUMMARY (sorted by P&L)")
    print("=" * 130)
    print(f"  {'Stop':>6s}  {'Target':>7s}  {'Trades':>6s}  {'WR%':>6s}  {'PnL':>12s}  {'PnL%':>7s}  {'DD%':>6s}  {'SL Exits':>8s}  {'PT Exits':>8s}  {'Rev':>4s}  {'MaxH':>5s}  {'EOD':>4s}")
    print(f"  {'-'*6}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*12}  {'-'*7}  {'-'*6}  {'-'*8}  {'-'*8}  {'-'*4}  {'-'*5}  {'-'*4}")

    for r in sorted(all_results, key=lambda x: x.get("pnl", -999999), reverse=True):
        if r.get("error"):
            print(f"  {r['stop_mult']:>5.2f}x  {r['target_mult']:>6.1f}x  ERROR: {r['error']}")
            continue
        exits = r.get("exit_reasons", {})
        print(
            f"  {r['stop_mult']:>5.2f}x  {r['target_mult']:>6.1f}x  "
            f"{r['trades']:>6d}  {r['wr']:>5.1f}%  "
            f"${r['pnl']:>+10,.2f}  {r['pnl_pct']:>+6.2f}%  {r['dd']:>5.2f}%  "
            f"{exits.get('stop_loss', 0):>8d}  {exits.get('profit_target', 0):>8d}  "
            f"{exits.get('reversal', 0):>4d}  {exits.get('max_hold', 0):>5d}  "
            f"{exits.get('eod_close', 0):>4d}"
        )

    # Save results
    out_path = Path(__file__).resolve().parent.parent / "backtest_results" / f"stop_target_sweep_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Strip non-serializable data
    save_data = []
    for r in all_results:
        save_data.append({k: v for k, v in r.items()})
    with open(out_path, "w") as f:
        json.dump(save_data, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
