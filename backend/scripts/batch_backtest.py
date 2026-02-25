#!/usr/bin/env python3
"""Batch backtest: run the Refined RSI strategy across tickers, timeframes, and stop-loss values.

Test matrix (105 runs):
  - 1h bull  (Sept 1-30, 2025):  7 tickers x 5 stop losses = 35
  - 1h bear  (Jan 1-31, 2026):   7 tickers x 5 stop losses = 35
  - 1m recent (last 30 days):    7 tickers x 5 stop losses = 35

Usage:
    cd backend && python -m scripts.batch_backtest
"""

from __future__ import annotations

import asyncio
import csv
import sys
import os
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

TICKERS = ["PLTR", "TSLA", "CEG", "SMR", "NVDA", "AMZN", "SOFI"]

STOP_LOSSES = [0.0, 0.02, 0.03, 0.05, 0.10]
STOP_LOSS_LABELS = ["0%", "2%", "3%", "5%", "10%"]

POSITION_SIZE_PCT = 0.05
STRATEGY = "refined_rsi"
STARTING_BALANCE = 100_000.0

# Test periods
PERIODS: list[dict] = [
    {
        "label": "1h_bull_sep2025",
        "timeframe": "1h",
        "start": "2025-09-01",
        "end": "2025-10-01",
        "regime": "bull",
    },
    {
        "label": "1h_bear_jan2026",
        "timeframe": "1h",
        "start": "2026-01-01",
        "end": "2026-02-01",
        "regime": "bear",
    },
    {
        "label": "1m_recent_30d",
        "timeframe": "1m",
        "start": (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d"),
        "end": datetime.now().strftime("%Y-%m-%d"),
        "regime": "recent",
    },
]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"


async def run_single_backtest(
    ticker: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    stop_loss_pct: float,
) -> dict:
    """Run one backtest and return the result metrics."""
    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    await broker.connect()
    engine = ReplayEngine(broker)

    try:
        await engine.start(
            symbol=ticker,
            timeframe=timeframe,
            start_date=start_date,
            end_date=end_date,
            speed=1.0,
            stop_loss_pct=stop_loss_pct,
            position_size_pct=POSITION_SIZE_PCT,
            strategy=STRATEGY,
            headless=True,
        )

        # Wait for the engine task to finish
        if engine._task:
            await engine._task

    except Exception as e:
        return {
            "error": str(e),
            "total_trades": 0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "win_rate": 0.0,
            "long_win_rate": 0.0,
            "short_win_rate": 0.0,
            "long_pnl": 0.0,
            "short_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "max_drawdown_pct": 0.0,
            "starting_balance": STARTING_BALANCE,
            "ending_balance": STARTING_BALANCE,
        }

    results = engine.get_results()
    if results is None:
        return {
            "error": "no results",
            "total_trades": 0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "win_rate": 0.0,
            "long_win_rate": 0.0,
            "short_win_rate": 0.0,
            "long_pnl": 0.0,
            "short_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "max_drawdown_pct": 0.0,
            "starting_balance": STARTING_BALANCE,
            "ending_balance": STARTING_BALANCE,
        }

    return {
        "error": "",
        "total_trades": results.total_trades,
        "total_pnl": results.total_pnl,
        "total_pnl_pct": results.total_pnl_pct,
        "win_rate": results.win_rate,
        "long_win_rate": results.long_win_rate,
        "short_win_rate": results.short_win_rate,
        "long_pnl": results.long_pnl,
        "short_pnl": results.short_pnl,
        "winning_trades": results.winning_trades,
        "losing_trades": results.losing_trades,
        "max_drawdown_pct": results.max_drawdown_pct,
        "starting_balance": results.starting_balance,
        "ending_balance": results.ending_balance,
    }


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"batch_results_{timestamp}.csv"

    fieldnames = [
        "period", "regime", "timeframe", "ticker", "stop_loss_label", "stop_loss_pct",
        "total_trades", "winning_trades", "losing_trades", "win_rate",
        "total_pnl", "total_pnl_pct",
        "long_win_rate", "short_win_rate", "long_pnl", "short_pnl",
        "max_drawdown_pct", "starting_balance", "ending_balance", "error",
    ]

    total_tests = len(PERIODS) * len(TICKERS) * len(STOP_LOSSES)
    completed = 0

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for period in PERIODS:
            for ticker in TICKERS:
                for sl_pct, sl_label in zip(STOP_LOSSES, STOP_LOSS_LABELS):
                    completed += 1
                    print(
                        f"[{completed}/{total_tests}] "
                        f"{period['label']} | {ticker} | SL={sl_label}",
                        end=" ... ",
                        flush=True,
                    )

                    metrics = await run_single_backtest(
                        ticker=ticker,
                        timeframe=period["timeframe"],
                        start_date=period["start"],
                        end_date=period["end"],
                        stop_loss_pct=sl_pct,
                    )

                    row = {
                        "period": period["label"],
                        "regime": period["regime"],
                        "timeframe": period["timeframe"],
                        "ticker": ticker,
                        "stop_loss_label": sl_label,
                        "stop_loss_pct": sl_pct,
                        **metrics,
                    }
                    writer.writerow(row)
                    f.flush()

                    status = (
                        f"{metrics['total_trades']} trades, "
                        f"PnL={metrics['total_pnl_pct']:.1f}%, "
                        f"WR={metrics['win_rate']:.0f}%"
                    )
                    if metrics.get("error"):
                        status = f"ERROR: {metrics['error'][:60]}"
                    print(status)

    print(f"\nResults saved to: {csv_path}")
    print_summary(csv_path)


def print_summary(csv_path: Path) -> None:
    """Print analysis summary from the CSV results."""
    import statistics

    rows: list[dict] = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["total_trades"] = int(row["total_trades"])
            row["total_pnl"] = float(row["total_pnl"])
            row["total_pnl_pct"] = float(row["total_pnl_pct"])
            row["win_rate"] = float(row["win_rate"])
            row["max_drawdown_pct"] = float(row["max_drawdown_pct"])
            row["stop_loss_pct"] = float(row["stop_loss_pct"])
            rows.append(row)

    if not rows:
        print("No results to analyze.")
        return

    print("\n" + "=" * 80)
    print("BATCH BACKTEST ANALYSIS")
    print("=" * 80)

    # --- 1. Optimal stop loss by ticker ---
    print("\n--- Optimal Stop Loss by Ticker ---")
    for ticker in TICKERS:
        ticker_rows = [r for r in rows if r["ticker"] == ticker]
        if not ticker_rows:
            continue
        best = max(ticker_rows, key=lambda r: r["total_pnl_pct"])
        avg_trades = statistics.mean(r["total_trades"] for r in ticker_rows)
        print(
            f"  {ticker:6s}: best SL = {best['stop_loss_label']:>3s}  "
            f"PnL={best['total_pnl_pct']:+6.1f}%  "
            f"WR={best['win_rate']:.0f}%  "
            f"DD={best['max_drawdown_pct']:.1f}%  "
            f"avg trades={avg_trades:.0f}"
        )

    # --- 2. Optimal stop loss by regime ---
    print("\n--- Optimal Stop Loss by Market Regime ---")
    for regime in ["bull", "bear", "recent"]:
        regime_rows = [r for r in rows if r["regime"] == regime]
        if not regime_rows:
            continue
        # Group by stop loss
        for sl_label in STOP_LOSS_LABELS:
            sl_rows = [r for r in regime_rows if r["stop_loss_label"] == sl_label]
            if not sl_rows:
                continue
            avg_pnl = statistics.mean(r["total_pnl_pct"] for r in sl_rows)
            avg_wr = statistics.mean(r["win_rate"] for r in sl_rows)
            avg_dd = statistics.mean(r["max_drawdown_pct"] for r in sl_rows)
            avg_trades = statistics.mean(r["total_trades"] for r in sl_rows)
            print(
                f"  {regime:7s} SL={sl_label:>3s}: "
                f"avg PnL={avg_pnl:+5.1f}%  "
                f"WR={avg_wr:.0f}%  "
                f"DD={avg_dd:.1f}%  "
                f"trades={avg_trades:.0f}"
            )

    # --- 3. 1m viability ---
    print("\n--- 1m Timeframe Viability ---")
    m1_rows = [r for r in rows if r["timeframe"] == "1m"]
    h1_rows = [r for r in rows if r["timeframe"] == "1h"]
    if m1_rows:
        avg_pnl_1m = statistics.mean(r["total_pnl_pct"] for r in m1_rows)
        avg_wr_1m = statistics.mean(r["win_rate"] for r in m1_rows)
        avg_trades_1m = statistics.mean(r["total_trades"] for r in m1_rows)
        print(f"  1m avg: PnL={avg_pnl_1m:+5.1f}%  WR={avg_wr_1m:.0f}%  trades={avg_trades_1m:.0f}")
    if h1_rows:
        avg_pnl_1h = statistics.mean(r["total_pnl_pct"] for r in h1_rows)
        avg_wr_1h = statistics.mean(r["win_rate"] for r in h1_rows)
        avg_trades_1h = statistics.mean(r["total_trades"] for r in h1_rows)
        print(f"  1h avg: PnL={avg_pnl_1h:+5.1f}%  WR={avg_wr_1h:.0f}%  trades={avg_trades_1h:.0f}")

    # --- 4. Per-ticker anomalies ---
    print("\n--- Per-Ticker Anomalies (consistently failing) ---")
    for ticker in TICKERS:
        ticker_rows = [r for r in rows if r["ticker"] == ticker and r["total_trades"] > 0]
        if not ticker_rows:
            print(f"  {ticker:6s}: NO TRADES across all configurations")
            continue
        losing_configs = [r for r in ticker_rows if r["total_pnl_pct"] < 0]
        pct_losing = len(losing_configs) / len(ticker_rows) * 100
        avg_pnl = statistics.mean(r["total_pnl_pct"] for r in ticker_rows)
        if pct_losing >= 80:
            print(
                f"  {ticker:6s}: POOR — {pct_losing:.0f}% configs lose money, "
                f"avg PnL={avg_pnl:+.1f}%"
            )
        elif pct_losing >= 50:
            print(
                f"  {ticker:6s}: MIXED — {pct_losing:.0f}% configs lose money, "
                f"avg PnL={avg_pnl:+.1f}%"
            )
        else:
            print(
                f"  {ticker:6s}: OK — {100-pct_losing:.0f}% configs profitable, "
                f"avg PnL={avg_pnl:+.1f}%"
            )

    # --- 5. Zero-trade check (data quality) ---
    zero_trade_rows = [r for r in rows if r["total_trades"] == 0 and not r.get("error")]
    if zero_trade_rows:
        print(f"\n--- WARNING: {len(zero_trade_rows)} configs produced 0 trades ---")
        for r in zero_trade_rows[:10]:
            print(f"  {r['period']} | {r['ticker']} | SL={r['stop_loss_label']}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
