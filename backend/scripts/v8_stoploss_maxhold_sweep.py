"""Parameter sweep: stop_loss × max_hold for v8 strategy.

Finds the optimal combination of stop_loss_pct and max_hold_minutes
on a portfolio of high-volatility tickers (TSLA, CEG, PLTR, RIVN).
"""
import asyncio, sys, os, logging
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from datetime import datetime, timedelta
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.engine import signal_generator as sg

STARTING_BALANCE = 100_000
TICKERS = ["TSLA", "CEG", "PLTR", "RIVN"]
STOP_LOSSES = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04]
MAX_HOLDS = [195, 390, 780]  # half-day, 1 day, 2 days
DAYS = 29
POS_PCT = 0.50


async def run(symbols, strategy, days=DAYS, stop_pct=0.02, pos_pct=POS_PCT):
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    engine = ReplayEngine(broker)
    try:
        await engine.start(
            symbols=symbols, timeframe="1m",
            start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"),
            speed=1.0, stop_loss_pct=stop_pct, position_size_pct=pos_pct,
            strategy=strategy, headless=True,
        )
        if engine._task:
            await engine._task
    except Exception:
        return None

    return engine.get_results()


async def main():
    results = []

    print("=" * 120)
    print(f"V8 STOP LOSS × MAX HOLD SWEEP  —  Portfolio: {', '.join(TICKERS)}")
    print(f"Period: {DAYS}d | Position: {POS_PCT*100:.0f}% | Balance: ${STARTING_BALANCE:,}")
    print("=" * 120)
    header = f"  {'SL%':>5s} | {'MaxHold':>8s} | {'P&L%':>8s} | {'P&L$':>10s} | {'#':>3s} | {'WR%':>5s} | {'DD%':>6s} | Per-ticker"
    print(header)
    print("-" * 120)

    for sl in STOP_LOSSES:
        for mh in MAX_HOLDS:
            # Mutate v8 globals for this run
            sg._V8_MAX_HOLD_MINUTES = mh

            r = await run(TICKERS, "refined_rsi_v8", stop_pct=sl)
            if r:
                row = {
                    "sl": sl, "mh": mh,
                    "pnl_pct": r.total_pnl_pct, "pnl": r.total_pnl,
                    "trades": r.total_trades, "wr": r.win_rate,
                    "dd": r.max_drawdown_pct,
                }
                results.append(row)

                sign = "+" if r.total_pnl_pct >= 0 else ""
                # Per-ticker breakdown from trade records
                per_ticker = {}
                for t in r.trades:
                    sym = t.get("symbol", "?")
                    pnl = t.get("pnl_pct", 0) or 0
                    if sym not in per_ticker:
                        per_ticker[sym] = {"pnl": 0, "n": 0}
                    per_ticker[sym]["pnl"] += pnl
                    per_ticker[sym]["n"] += 1
                detail = " | ".join(
                    f"{s}: {d['pnl']:+.2f}%/{d['n']}t"
                    for s, d in sorted(per_ticker.items())
                )
                print(f"  {sl*100:>4.1f}% | {mh:>6d}m | {sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} | {r.win_rate:>5.1f} | {r.max_drawdown_pct:>5.2f}% | {detail}")
            else:
                print(f"  {sl*100:>4.1f}% | {mh:>6d}m | FAILED")

    # Restore defaults
    sg._V8_MAX_HOLD_MINUTES = 390

    # Rank by P&L%
    results.sort(key=lambda x: x["pnl_pct"], reverse=True)

    print("\n" + "=" * 120)
    print("RANKED RESULTS (by P&L%)")
    print("=" * 120)
    for i, row in enumerate(results):
        sign = "+" if row["pnl_pct"] >= 0 else ""
        marker = " <<<" if i == 0 else ""
        print(f"  {i+1:>2d}. SL={row['sl']*100:.1f}% MH={row['mh']}m | {sign}{row['pnl_pct']:>7.2f}% | ${row['pnl']:>+9,.0f} | {row['trades']:>3d} trades | WR {row['wr']:>5.1f}% | DD {row['dd']:>5.2f}%{marker}")

    # Save results to markdown
    if results:
        best = results[0]
        out_dir = os.path.join(os.path.dirname(__file__), "..", "backtest_results")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "v8_stoploss_maxhold_sweep.md")
        with open(out_path, "w") as f:
            f.write(f"# V8 Stop Loss × Max Hold Sweep\n\n")
            f.write(f"**Date**: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
            f.write(f"**Tickers**: {', '.join(TICKERS)}\n")
            f.write(f"**Period**: {DAYS}d | **Position**: {POS_PCT*100:.0f}% | **Balance**: ${STARTING_BALANCE:,}\n\n")
            f.write(f"## Best Config\n\n")
            f.write(f"- **Stop Loss**: {best['sl']*100:.1f}%\n")
            f.write(f"- **Max Hold**: {best['mh']} minutes\n")
            f.write(f"- **P&L**: {best['pnl_pct']:+.2f}% (${best['pnl']:+,.0f})\n")
            f.write(f"- **Trades**: {best['trades']} | **WR**: {best['wr']:.1f}% | **DD**: {best['dd']:.2f}%\n\n")
            f.write(f"## All Results\n\n")
            f.write(f"| Rank | SL% | MaxHold | P&L% | P&L$ | Trades | WR% | DD% |\n")
            f.write(f"|------|-----|---------|------|------|--------|-----|-----|\n")
            for i, row in enumerate(results):
                f.write(f"| {i+1} | {row['sl']*100:.1f}% | {row['mh']}m | {row['pnl_pct']:+.2f}% | ${row['pnl']:+,.0f} | {row['trades']} | {row['wr']:.1f}% | {row['dd']:.2f}% |\n")
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
