#!/usr/bin/env python3
"""Compare fitness vs percentile scan tactics via 30-day v8 backtest.

Pipeline:
  1. Run fitness scanner → top 4 tickers
  2. Run percentile scanner → top 4 tickers
  3. Run baseline (TSLA/CEG/PLTR/RIVN)
  4. 30-day v8 backtest for each portfolio
  5. Print comparison table

Usage:
    cd backend && .venv/bin/python -m scripts.compare_scan_tactics
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

BASELINE = ["TSLA", "CEG", "PLTR", "RIVN"]
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"


async def run_backtest(symbols: list[str], days: int = 30, label: str = "") -> dict:
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=150_000)
    engine = ReplayEngine(broker)

    try:
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
    except Exception as e:
        print(f"    ERROR in backtest {label}: {e}")
        return {"symbols": symbols, "label": label, "pnl_pct": 0, "pnl_dollar": 0,
                "win_rate": 0, "trades": 0, "dd_pct": 0, "winning": 0, "losing": 0}

    r = engine.get_results()
    if not r:
        return {"symbols": symbols, "label": label, "pnl_pct": 0, "pnl_dollar": 0,
                "win_rate": 0, "trades": 0, "dd_pct": 0, "winning": 0, "losing": 0}
    return {
        "symbols": symbols,
        "label": label,
        "pnl_pct": r.total_pnl_pct,
        "pnl_dollar": r.total_pnl,
        "win_rate": r.win_rate,
        "trades": r.total_trades,
        "dd_pct": r.max_drawdown_pct,
        "winning": r.winning_trades,
        "losing": r.losing_trades,
    }


async def main():
    print("=" * 70)
    print("SCAN TACTIC COMPARISON — Fitness vs Percentile vs Baseline")
    print(f"Strategy: rsi_divergence_pct | Backtest: 30 days | Balance: $150K")
    print("=" * 70)

    # ── Step 1: Run fitness scanner ──
    print("\n[1/4] Running FITNESS scanner (full universe, batch download)...")
    t0 = time.time()
    from app.scanner.fitness import run_fitness_scan
    from app.engine.strategies import STRATEGY_MAP
    strat = STRATEGY_MAP.get("rsi_divergence_pct")
    scan_config = strat.scan_config if strat else None
    fitness_results = await run_fitness_scan(top_n=4, scan_config=scan_config)
    fitness_tickers = [r.symbol for r in fitness_results]
    print(f"  Fitness picks: {fitness_tickers} ({time.time() - t0:.0f}s)")
    for r in fitness_results:
        print(f"    {r.symbol}: score={r.total_score:.1f} price=${r.price:.2f} atr={r.atr_pct:.2f}%")

    # ── Step 2: Run percentile scanner ──
    print("\n[2/4] Running PERCENTILE scanner (full universe, 30 metrics)...")
    t0 = time.time()
    from app.scanner.percentile_scanner import run_percentile_scan
    percentile_results = await run_percentile_scan(top_n=4)
    percentile_tickers = [r.symbol for r in percentile_results]
    print(f"  Percentile picks: {percentile_tickers} ({time.time() - t0:.0f}s)")
    for r in percentile_results:
        print(f"    {r.symbol}: score={r.total_score:.1f} price=${r.price:.2f} atr={r.atr_pct:.2f}%")

    # ── Step 3: Run backtests ──
    portfolios = {
        "Baseline": BASELINE,
        "Fitness": fitness_tickers,
        "Percentile": percentile_tickers,
    }

    results = {}
    step = 3
    for label, syms in portfolios.items():
        if not syms:
            print(f"\n[{step}/4] {label}: NO TICKERS — skipping")
            continue
        print(f"\n[{step}/4] Backtesting {label}: {syms}...")
        t0 = time.time()
        r = await run_backtest(syms, days=30, label=label)
        elapsed = time.time() - t0
        results[label] = r
        print(f"  → {r['pnl_pct']:+.2f}% (${r['pnl_dollar']:+,.0f}) | "
              f"{r['trades']} trades | WR {r['win_rate']:.1f}% | "
              f"DD {r['dd_pct']:.2f}% | {elapsed:.0f}s")
        step += 1

    # ── Step 4: Comparison table ──
    print("\n" + "=" * 70)
    print("RESULTS COMPARISON")
    print("=" * 70)
    print(f"\n{'Tactic':<15} {'Tickers':<30} {'P&L%':>8} {'P&L$':>12} {'Trades':>7} {'WR%':>6} {'DD%':>7}")
    print("-" * 85)
    baseline_pnl = results.get("Baseline", {}).get("pnl_pct", 0)
    for label, r in results.items():
        syms_str = ",".join(r["symbols"])
        delta = f" ({r['pnl_pct'] - baseline_pnl:+.2f}pp)" if label != "Baseline" else ""
        print(f"{label:<15} {syms_str:<30} {r['pnl_pct']:>+7.2f}% ${r['pnl_dollar']:>+10,.0f} "
              f"{r['trades']:>7} {r['win_rate']:>5.1f}% {r['dd_pct']:>6.2f}%{delta}")

    # ── Save results ──
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"scan_tactic_comparison_{ts}.md"
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(out_path, "w") as f:
        f.write(f"# Scan Tactic Comparison — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(f"Strategy: rsi_divergence_pct | Backtest: 30 days | Balance: $150K | Position: 50%\n\n")
        f.write("## Scanner Picks\n\n")
        f.write(f"- **Fitness**: {fitness_tickers}\n")
        f.write(f"- **Percentile**: {percentile_tickers}\n")
        f.write(f"- **Baseline**: {BASELINE}\n\n")
        f.write("## Backtest Results\n\n")
        f.write(f"| Tactic | Tickers | P&L% | P&L$ | Trades | WR% | DD% | vs Baseline |\n")
        f.write(f"|--------|---------|------|------|--------|-----|-----|-------------|\n")
        for label, r in results.items():
            syms_str = ",".join(r["symbols"])
            vs = f"{r['pnl_pct'] - baseline_pnl:+.2f}pp" if label != "Baseline" else "—"
            f.write(f"| {label} | {syms_str} | {r['pnl_pct']:+.2f}% | "
                    f"${r['pnl_dollar']:+,.0f} | {r['trades']} | "
                    f"{r['win_rate']:.1f}% | {r['dd_pct']:.2f}% | {vs} |\n")

    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
