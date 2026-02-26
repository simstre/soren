#!/usr/bin/env python3
"""Analyze optimal portfolio size and position sizing for v1 strategy.

Phase 1: Run each portfolio config at 10% position size to observe
         max concurrent positions without capital constraints.
Phase 2: Re-run with optimal position size (100% / max_concurrent).
"""

from __future__ import annotations
import asyncio, sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
logging.disable(logging.DEBUG)
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.INFO))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

BALANCE = 100_000.0
END = datetime.now().strftime("%Y-%m-%d")
START = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")
STRATEGY = "refined_rsi"

# Tickers ranked by v1 individual P&L
TOP_10 = ["AMD", "NVDA", "PFE", "LLY", "QQQ", "XOM", "CVX", "TSLA", "AAPL", "GOOGL"]

CONFIGS = [
    ("Top 3", TOP_10[:3]),
    ("Top 5", TOP_10[:5]),
    ("Top 7", TOP_10[:7]),
    ("Top 10", TOP_10[:10]),
]


async def run_backtest(symbols, pos_pct, sl=0.02):
    broker = ReplayBrokerAdapter(starting_balance=BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)
    try:
        await engine.start(
            symbols=symbols, timeframe="1m", start_date=START,
            end_date=END, speed=1.0, stop_loss_pct=sl,
            position_size_pct=pos_pct, strategy=STRATEGY, headless=True,
        )
        if engine._task:
            await engine._task
    except Exception as e:
        return None, {"error": str(e)}

    r = engine.get_results()
    if not r:
        return engine, {"error": "no results"}

    return engine, {
        "trades": r.total_trades,
        "wr": r.win_rate,
        "pnl": r.total_pnl,
        "pnl_pct": r.total_pnl_pct,
        "dd": r.max_drawdown_pct,
        "long_wr": r.long_win_rate,
        "short_wr": r.short_win_rate,
        "gated": list(engine._gated_symbols),
    }


def analyze_concurrent_positions(engine) -> dict:
    """Analyze trade records to find max concurrent open positions."""
    if not engine or not hasattr(engine, '_trade_records'):
        return {"max_concurrent": 0, "avg_concurrent": 0, "p95_concurrent": 0}

    events = []
    for trade in engine._trade_records:
        entry_time = trade.get("entry_time")
        exit_time = trade.get("exit_time")
        if entry_time:
            events.append((entry_time, +1))
        if exit_time:
            events.append((exit_time, -1))

    if not events:
        return {"max_concurrent": 0, "avg_concurrent": 0, "p95_concurrent": 0}

    events.sort(key=lambda x: (x[0], x[1]))  # closes before opens at same time

    concurrent = 0
    max_concurrent = 0
    samples = []

    for _, delta in events:
        concurrent += delta
        concurrent = max(0, concurrent)
        max_concurrent = max(max_concurrent, concurrent)
        samples.append(concurrent)

    avg_concurrent = sum(samples) / len(samples) if samples else 0

    sorted_samples = sorted(samples)
    p95_idx = int(len(sorted_samples) * 0.95)
    p95_concurrent = sorted_samples[p95_idx] if sorted_samples else 0

    return {
        "max_concurrent": max_concurrent,
        "avg_concurrent": round(avg_concurrent, 1),
        "p95_concurrent": p95_concurrent,
    }


def pr(label, r, conc=None):
    if r.get("error"):
        print(f"  {label:45s} ERROR: {r['error']}")
        return
    conc_str = ""
    if conc:
        conc_str = f"  Max:{conc['max_concurrent']} Avg:{conc['avg_concurrent']} P95:{conc['p95_concurrent']}"
    gated = r.get("gated", [])
    print(
        f"  {label:45s} {r['trades']:>3}T  {r['wr']:>5.1f}%WR  "
        f"${r['pnl']:>+9,.2f}  {r['pnl_pct']:>+6.2f}%  DD:{r['dd']:>5.2f}%  "
        f"G:{len(gated)}{conc_str}"
    )
    if gated:
        print(f"  {'':45s} Gated: {', '.join(gated)}")


async def main():
    print("=" * 120)
    print(f"PORTFOLIO SIZING ANALYSIS — v1, 29 days, 1m, $100K")
    print(f"Tickers ranked by individual v1 P&L: {', '.join(TOP_10)}")
    print("=" * 120)

    # ===== Phase 1: Observe max concurrent positions at 10% size =====
    print(f"\n{'─' * 120}")
    print("PHASE 1: Observe concurrent positions (10% position size — no capital constraint)")
    print(f"{'─' * 120}")

    concurrent_data = {}
    for label, symbols in CONFIGS:
        engine, r = await run_backtest(symbols, pos_pct=0.10)
        conc = analyze_concurrent_positions(engine)
        concurrent_data[label] = conc
        pr(f"[10%] {label} ({','.join(symbols)})", r, conc)

    # ===== Calculate optimal position sizes =====
    print(f"\n{'─' * 120}")
    print("OPTIMAL POSITION SIZES (target: ~100% capital utilization)")
    print(f"{'─' * 120}")

    optimal_sizes = {}
    for label, symbols in CONFIGS:
        conc = concurrent_data[label]
        max_c = conc["max_concurrent"]
        p95_c = conc["p95_concurrent"]

        if max_c > 0:
            # Use P95 to avoid being too conservative (max might be a rare spike)
            # But cap at max to avoid over-leveraging
            target_concurrent = max(p95_c, 1)
            optimal_pct = round(1.0 / target_concurrent, 2)
            # Also show the conservative (max-based) sizing
            conservative_pct = round(1.0 / max_c, 2)
        else:
            optimal_pct = 0.50
            conservative_pct = 0.50

        optimal_sizes[label] = optimal_pct
        print(
            f"  {label:20s}  Max concurrent: {max_c}  P95: {p95_c}  "
            f"Optimal: {optimal_pct:.0%} (P95-based)  "
            f"Conservative: {conservative_pct:.0%} (max-based)"
        )

    # ===== Phase 2: Re-run with optimal sizing =====
    print(f"\n{'─' * 120}")
    print("PHASE 2: Run with optimal position sizes")
    print(f"{'─' * 120}")

    for label, symbols in CONFIGS:
        opt_pct = optimal_sizes[label]
        engine, r = await run_backtest(symbols, pos_pct=opt_pct)
        conc = analyze_concurrent_positions(engine)
        pr(f"[{opt_pct:.0%}] {label} ({','.join(symbols)})", r, conc)

    # ===== Phase 3: Also run all at 50% for baseline comparison =====
    print(f"\n{'─' * 120}")
    print("PHASE 3: Baseline comparison at 50% position size")
    print(f"{'─' * 120}")

    for label, symbols in CONFIGS:
        engine, r = await run_backtest(symbols, pos_pct=0.50)
        conc = analyze_concurrent_positions(engine)
        pr(f"[50%] {label} ({','.join(symbols)})", r, conc)


if __name__ == "__main__":
    asyncio.run(main())
