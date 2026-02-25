#!/usr/bin/env python3
"""Optimization backtest: fitness scan → multi-timeframe matrix → analysis.

Three-phase orchestrator:
  Phase 1: Run fitness scan to find top 15 tickers
  Phase 2: Backtest 15 tickers × 5 timeframes × 5 position sizes = 375 tests
  Phase 3: Analyze results and find optimal configs for 5% monthly target

Usage:
    cd backend && python -m scripts.optimize_backtest
"""

from __future__ import annotations

import asyncio
import csv
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure the backend package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.scanner.fitness import run_fitness_scan, FitnessScore

STRATEGY = "refined_rsi"
STARTING_BALANCE = 100_000.0
STOP_LOSS_PCT = 0.0  # ATR-only — fixed stops had no effect

POSITION_SIZES = [0.10, 0.15, 0.20, 0.25, 0.30]
POSITION_SIZE_LABELS = ["10%", "15%", "20%", "25%", "30%"]

# Timeframe configs: (timeframe, window_days, start_date, end_date)
# Dates computed dynamically based on yfinance limits
TIMEFRAME_CONFIGS = [
    {"timeframe": "1m",  "window_days": 7,   "label": "1m_7d"},
    {"timeframe": "5m",  "window_days": 30,  "label": "5m_30d"},
    {"timeframe": "15m", "window_days": 90,  "label": "15m_90d"},
    {"timeframe": "1h",  "window_days": 180, "label": "1h_180d"},
    {"timeframe": "1D",  "window_days": 365, "label": "1D_365d"},
]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"

# Target criteria
TARGET_MONTHLY_PNL = 5.0
MAX_DRAWDOWN = 10.0
MIN_WIN_RATE = 40.0
MIN_TRADES = 3


def _compute_dates(window_days: int) -> tuple[str, str]:
    """Compute start/end dates for a given window ending today."""
    end = datetime.now()
    start = end - timedelta(days=window_days)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


async def run_single_backtest(
    ticker: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    position_size_pct: float,
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
            stop_loss_pct=STOP_LOSS_PCT,
            position_size_pct=position_size_pct,
            strategy=STRATEGY,
            headless=True,
        )

        if engine._task:
            await engine._task

    except Exception as e:
        return {
            "error": str(e)[:200],
            "total_trades": 0,
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
            "win_rate": 0.0,
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
        "winning_trades": results.winning_trades,
        "losing_trades": results.losing_trades,
        "max_drawdown_pct": results.max_drawdown_pct,
        "starting_balance": results.starting_balance,
        "ending_balance": results.ending_balance,
    }


# ──────────────────────────────────────────────────────────────────────
# Phase 1: Fitness Scan
# ──────────────────────────────────────────────────────────────────────

async def phase1_fitness_scan() -> list[FitnessScore]:
    """Run fitness scan and return top 15 tickers."""
    print("=" * 80)
    print("PHASE 1: STRATEGY FITNESS SCAN")
    print("=" * 80)

    scores = await run_fitness_scan(top_n=15)

    print(f"\nTop {len(scores)} tickers by fitness score:\n")
    print(f"  {'Rank':<5} {'Symbol':<7} {'Score':>6} {'Price':>8} {'AvgVol':>10} "
          f"{'ATR%':>6} {'S/R':>4} {'VolS':>5} {'PrcS':>5} {'SRS':>4} {'AtrS':>5}")
    print(f"  {'─'*5} {'─'*7} {'─'*6} {'─'*8} {'─'*10} {'─'*6} {'─'*4} {'─'*5} {'─'*5} {'─'*4} {'─'*5}")

    for i, s in enumerate(scores, 1):
        print(f"  {i:<5} {s.symbol:<7} {s.total_score:>6.1f} "
              f"${s.price:>7.2f} {s.avg_volume:>10,} "
              f"{s.atr_pct:>5.2f}% {s.sr_strong_count:>4} {s.volume_score:>5.1f} {s.price_score:>5.1f} {s.sr_score:>4.1f} {s.atr_score:>5.1f}")

    print()
    return scores


# ──────────────────────────────────────────────────────────────────────
# Phase 2: Multi-Timeframe Backtest Matrix
# ──────────────────────────────────────────────────────────────────────

async def phase2_backtest_matrix(
    tickers: list[str],
    csv_path: Path,
) -> list[dict]:
    """Run the full backtest matrix and write results to CSV."""
    print("=" * 80)
    print("PHASE 2: MULTI-TIMEFRAME BACKTEST MATRIX")
    print("=" * 80)

    total_tests = len(tickers) * len(TIMEFRAME_CONFIGS) * len(POSITION_SIZES)
    print(f"\nMatrix: {len(tickers)} tickers × {len(TIMEFRAME_CONFIGS)} timeframes "
          f"× {len(POSITION_SIZES)} position sizes = {total_tests} backtests\n")

    fieldnames = [
        "ticker", "timeframe", "label", "window_days",
        "position_size_label", "position_size_pct",
        "total_trades", "winning_trades", "losing_trades", "win_rate",
        "total_pnl", "total_pnl_pct", "monthly_pnl_pct",
        "max_drawdown_pct", "starting_balance", "ending_balance", "error",
    ]

    all_rows: list[dict] = []
    completed = 0

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for tf_cfg in TIMEFRAME_CONFIGS:
            start_date, end_date = _compute_dates(tf_cfg["window_days"])

            for ticker in tickers:
                for ps_pct, ps_label in zip(POSITION_SIZES, POSITION_SIZE_LABELS):
                    completed += 1
                    print(
                        f"  [{completed:>3}/{total_tests}] "
                        f"{tf_cfg['label']:>8} | {ticker:<6} | PS={ps_label}",
                        end=" ... ",
                        flush=True,
                    )

                    metrics = await run_single_backtest(
                        ticker=ticker,
                        timeframe=tf_cfg["timeframe"],
                        start_date=start_date,
                        end_date=end_date,
                        position_size_pct=ps_pct,
                    )

                    # Normalize to monthly PnL
                    window_days = tf_cfg["window_days"]
                    monthly_pnl_pct = (
                        metrics["total_pnl_pct"] / window_days * 30
                        if window_days > 0
                        else 0
                    )

                    row = {
                        "ticker": ticker,
                        "timeframe": tf_cfg["timeframe"],
                        "label": tf_cfg["label"],
                        "window_days": window_days,
                        "position_size_label": ps_label,
                        "position_size_pct": ps_pct,
                        "monthly_pnl_pct": round(monthly_pnl_pct, 3),
                        **metrics,
                    }
                    writer.writerow(row)
                    f.flush()
                    all_rows.append(row)

                    if metrics.get("error"):
                        print(f"ERROR: {metrics['error'][:60]}")
                    else:
                        print(
                            f"{metrics['total_trades']} trades, "
                            f"PnL={metrics['total_pnl_pct']:+.2f}%, "
                            f"Mo={monthly_pnl_pct:+.2f}%, "
                            f"WR={metrics['win_rate']:.0f}%"
                        )

    print(f"\nCSV saved: {csv_path}")
    return all_rows


# ──────────────────────────────────────────────────────────────────────
# Phase 3: Optimization Analysis
# ──────────────────────────────────────────────────────────────────────

def phase3_analysis(rows: list[dict]) -> None:
    """Analyze backtest results and find optimal configs."""
    print("\n" + "=" * 80)
    print("PHASE 3: OPTIMIZATION ANALYSIS")
    print("=" * 80)

    # Filter out error rows and zero-trade rows for analysis
    valid = [r for r in rows if not r.get("error") and r["total_trades"] > 0]
    errors = [r for r in rows if r.get("error")]
    zero_trades = [r for r in rows if not r.get("error") and r["total_trades"] == 0]

    print(f"\nTotal runs: {len(rows)} | Valid: {len(valid)} | "
          f"Errors: {len(errors)} | Zero-trades: {len(zero_trades)}")

    if not valid:
        print("\nNo valid results to analyze!")
        return

    # ── Section 1: Best configs meeting all criteria ──
    print("\n─── 1. CONFIGS MEETING 5% MONTHLY TARGET ───")
    meeting_criteria = [
        r for r in valid
        if r["monthly_pnl_pct"] >= TARGET_MONTHLY_PNL
        and r["max_drawdown_pct"] < MAX_DRAWDOWN
        and r["win_rate"] > MIN_WIN_RATE
        and r["total_trades"] >= MIN_TRADES
    ]

    if meeting_criteria:
        # Sort by risk-adjusted return (monthly PnL / max drawdown)
        for r in meeting_criteria:
            dd = r["max_drawdown_pct"] if r["max_drawdown_pct"] > 0 else 0.1
            r["risk_adj"] = r["monthly_pnl_pct"] / dd
        meeting_criteria.sort(key=lambda r: r["risk_adj"], reverse=True)

        print(f"\n  Found {len(meeting_criteria)} configs meeting all criteria!\n")
        print(f"  {'Ticker':<7} {'TF':>4} {'PS':>4} {'Mo PnL%':>8} {'DD%':>6} "
              f"{'WR%':>5} {'Trades':>6} {'Risk-Adj':>9}")
        print(f"  {'─'*7} {'─'*4} {'─'*4} {'─'*8} {'─'*6} {'─'*5} {'─'*6} {'─'*9}")
        for r in meeting_criteria[:20]:
            print(f"  {r['ticker']:<7} {r['timeframe']:>4} "
                  f"{r['position_size_label']:>4} "
                  f"{r['monthly_pnl_pct']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
                  f"{r['win_rate']:>4.0f}% {r['total_trades']:>6} "
                  f"{r['risk_adj']:>9.2f}")
    else:
        print("\n  No configs meet all criteria (5% monthly, <10% DD, >40% WR, ≥3 trades)")
        # Show the closest ones
        near_miss = [r for r in valid if r["total_trades"] >= MIN_TRADES]
        if near_miss:
            near_miss.sort(key=lambda r: r["monthly_pnl_pct"], reverse=True)
            print(f"\n  Closest configs (by monthly PnL):\n")
            print(f"  {'Ticker':<7} {'TF':>4} {'PS':>4} {'Mo PnL%':>8} {'DD%':>6} "
                  f"{'WR%':>5} {'Trades':>6}")
            print(f"  {'─'*7} {'─'*4} {'─'*4} {'─'*8} {'─'*6} {'─'*5} {'─'*6}")
            for r in near_miss[:15]:
                print(f"  {r['ticker']:<7} {r['timeframe']:>4} "
                      f"{r['position_size_label']:>4} "
                      f"{r['monthly_pnl_pct']:>+7.2f}% {r['max_drawdown_pct']:>5.1f}% "
                      f"{r['win_rate']:>4.0f}% {r['total_trades']:>6}")

    # ── Section 2: Best timeframe ──
    print("\n─── 2. BEST TIMEFRAME (avg monthly PnL across tickers) ───\n")
    tf_groups: dict[str, list[float]] = {}
    for r in valid:
        tf_groups.setdefault(r["timeframe"], []).append(r["monthly_pnl_pct"])

    tf_summary = []
    for tf, pnls in sorted(tf_groups.items()):
        avg = statistics.mean(pnls)
        med = statistics.median(pnls)
        best = max(pnls)
        worst = min(pnls)
        tf_summary.append((tf, avg, med, best, worst, len(pnls)))

    tf_summary.sort(key=lambda x: x[1], reverse=True)
    print(f"  {'TF':>4} {'Avg Mo%':>8} {'Median':>8} {'Best':>8} {'Worst':>8} {'N':>4}")
    print(f"  {'─'*4} {'─'*8} {'─'*8} {'─'*8} {'─'*8} {'─'*4}")
    for tf, avg, med, best, worst, n in tf_summary:
        print(f"  {tf:>4} {avg:>+7.2f}% {med:>+7.2f}% {best:>+7.2f}% {worst:>+7.2f}% {n:>4}")

    # ── Section 3: Best position size ──
    print("\n─── 3. BEST POSITION SIZE (risk vs reward) ───\n")
    ps_groups: dict[str, list[dict]] = {}
    for r in valid:
        ps_groups.setdefault(r["position_size_label"], []).append(r)

    print(f"  {'PS':>4} {'Avg Mo%':>8} {'Avg DD%':>8} {'Avg WR%':>8} {'Ratio':>7}")
    print(f"  {'─'*4} {'─'*8} {'─'*8} {'─'*8} {'─'*7}")
    for ps_label in POSITION_SIZE_LABELS:
        group = ps_groups.get(ps_label, [])
        if not group:
            continue
        avg_mo = statistics.mean(r["monthly_pnl_pct"] for r in group)
        avg_dd = statistics.mean(r["max_drawdown_pct"] for r in group)
        avg_wr = statistics.mean(r["win_rate"] for r in group)
        ratio = avg_mo / avg_dd if avg_dd > 0 else 0
        print(f"  {ps_label:>4} {avg_mo:>+7.2f}% {avg_dd:>7.2f}% {avg_wr:>7.1f}% {ratio:>7.2f}")

    # ── Section 4: Per-ticker optimal config ──
    print("\n─── 4. PER-TICKER OPTIMAL CONFIG ───\n")
    ticker_groups: dict[str, list[dict]] = {}
    for r in valid:
        ticker_groups.setdefault(r["ticker"], []).append(r)

    print(f"  {'Ticker':<7} {'Best TF':>7} {'Best PS':>7} {'Mo PnL%':>8} {'DD%':>6} "
          f"{'WR%':>5} {'Trades':>6}")
    print(f"  {'─'*7} {'─'*7} {'─'*7} {'─'*8} {'─'*6} {'─'*5} {'─'*6}")
    for ticker in sorted(ticker_groups.keys()):
        group = ticker_groups[ticker]
        best = max(group, key=lambda r: r["monthly_pnl_pct"])
        print(f"  {ticker:<7} {best['timeframe']:>7} "
              f"{best['position_size_label']:>7} "
              f"{best['monthly_pnl_pct']:>+7.2f}% {best['max_drawdown_pct']:>5.1f}% "
              f"{best['win_rate']:>4.0f}% {best['total_trades']:>6}")

    # ── Section 5: Risk warnings ──
    print("\n─── 5. RISK WARNINGS (DD > 15%) ───\n")
    risky = [r for r in valid if r["max_drawdown_pct"] > 15]
    if risky:
        risky.sort(key=lambda r: r["max_drawdown_pct"], reverse=True)
        print(f"  {'Ticker':<7} {'TF':>4} {'PS':>4} {'DD%':>7} {'Mo PnL%':>8}")
        print(f"  {'─'*7} {'─'*4} {'─'*4} {'─'*7} {'─'*8}")
        for r in risky[:15]:
            print(f"  {r['ticker']:<7} {r['timeframe']:>4} "
                  f"{r['position_size_label']:>4} "
                  f"{r['max_drawdown_pct']:>6.1f}% {r['monthly_pnl_pct']:>+7.2f}%")
    else:
        print("  None — all drawdowns under 15%")

    # ── Data quality warnings ──
    if zero_trades:
        print(f"\n─── WARNING: {len(zero_trades)} configs produced 0 trades ───")
        for r in zero_trades[:10]:
            print(f"  {r['label']} | {r['ticker']} | PS={r['position_size_label']}")
        if len(zero_trades) > 10:
            print(f"  ... and {len(zero_trades) - 10} more")

    if errors:
        print(f"\n─── WARNING: {len(errors)} configs had errors ───")
        for r in errors[:10]:
            print(f"  {r['label']} | {r['ticker']} | {r['error'][:80]}")

    # ── Summary ──
    print("\n─── SUMMARY ───")
    all_monthly = [r["monthly_pnl_pct"] for r in valid]
    max_monthly = max(all_monthly) if all_monthly else 0
    avg_monthly = statistics.mean(all_monthly) if all_monthly else 0

    print(f"\n  Max monthly PnL achieved: {max_monthly:+.2f}%")
    print(f"  Avg monthly PnL across all configs: {avg_monthly:+.2f}%")
    print(f"  Configs meeting 5% target: {len(meeting_criteria) if 'meeting_criteria' in dir() else 0}")

    if not meeting_criteria:
        print(f"\n  5% monthly target NOT achievable with single-ticker refined RSI.")
        best_overall = max(valid, key=lambda r: r["monthly_pnl_pct"])
        print(f"  Best single config: {best_overall['ticker']} "
              f"{best_overall['timeframe']} PS={best_overall['position_size_label']} "
              f"→ {best_overall['monthly_pnl_pct']:+.2f}% monthly")
        print(f"\n  Suggestions:")
        print(f"    1. Multi-ticker concurrent execution (portfolio approach)")
        print(f"    2. Combine with additional strategies (momentum, mean reversion)")
        print(f"    3. Increase universe and re-scan for higher-fitness tickers")

    print("\n" + "=" * 80)


# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Phase 1: Fitness scan
    fitness_scores = await phase1_fitness_scan()

    if not fitness_scores:
        print("No tickers passed fitness scan! Aborting.")
        return

    tickers = [s.symbol for s in fitness_scores]

    # Save fitness scores
    fitness_csv = OUTPUT_DIR / f"fitness_scores_{timestamp}.csv"
    with open(fitness_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "rank", "symbol", "total_score",
            "price", "avg_volume", "atr_pct", "sr_strong_count",
            "volume_score", "price_score", "sr_score", "atr_score",
        ])
        writer.writeheader()
        for i, s in enumerate(fitness_scores, 1):
            writer.writerow({
                "rank": i,
                "symbol": s.symbol,
                "total_score": s.total_score,
                "price": s.price,
                "avg_volume": s.avg_volume,
                "atr_pct": s.atr_pct,
                "sr_strong_count": s.sr_strong_count,
                "volume_score": s.volume_score,
                "price_score": s.price_score,
                "sr_score": s.sr_score,
                "atr_score": s.atr_score,
            })
    print(f"Fitness scores saved: {fitness_csv}")

    # Phase 2: Backtest matrix
    backtest_csv = OUTPUT_DIR / f"optimize_results_{timestamp}.csv"
    rows = await phase2_backtest_matrix(tickers, backtest_csv)

    # Phase 3: Analysis
    phase3_analysis(rows)


if __name__ == "__main__":
    asyncio.run(main())
