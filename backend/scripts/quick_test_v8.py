"""Quick test of v8 strategy on a few tickers."""
import asyncio, sys, os, logging
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from datetime import datetime, timedelta
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

async def run(symbols, strategy, days=29):
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=days)

    broker = ReplayBrokerAdapter(starting_balance=100_000)
    engine = ReplayEngine(broker)
    await engine.start(
        symbols=symbols, timeframe="1m",
        start_date=start.strftime("%Y-%m-%d"), end_date=end.strftime("%Y-%m-%d"),
        speed=1.0, stop_loss_pct=0.02, position_size_pct=0.50,
        strategy=strategy, headless=True,
    )
    if engine._task:
        await engine._task

    r = engine.get_results()
    if not r:
        print(f"  {strategy:>20s} | {','.join(symbols):>8s} | NO RESULTS")
        return

    exits = {}
    for t in r.trades:
        reason = t.get("exit_reason", "unknown")
        exits[reason] = exits.get(reason, 0) + 1

    pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
    exit_str = " ".join(f"{k}={v}" for k, v in sorted(exits.items()))
    print(f"  {strategy:>20s} | {','.join(symbols):>8s} | {pnl_sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} trades | WR {r.win_rate:>5.1f}% | DD {r.max_drawdown_pct:>5.2f}% | {exit_str}")

    # Print individual trades for v8
    if strategy == "refined_rsi_v8" and r.trades:
        print(f"\n    {'Side':>5s} | {'Entry$':>9s} | {'Exit$':>9s} | {'P&L$':>9s} | {'P&L%':>7s} | Entry Time → Exit Time | Exit Reason")
        print("    " + "-" * 100)
        for t in r.trades:
            pnl = t.get("pnl", 0) or 0
            pnl_pct = t.get("pnl_pct", 0) or 0
            entry_t = t.get("entry_time", "")[:16] if t.get("entry_time") else ""
            exit_t = t.get("exit_time", "")[:16] if t.get("exit_time") else ""
            exit_r = t.get("exit_reason", "open")
            print(f"    {t['side']:>5s} | ${t['entry_price']:>8.2f} | ${(t.get('exit_price') or 0):>8.2f} | ${pnl:>+8,.0f} | {pnl_pct:>+6.2f}% | {entry_t} → {exit_t} | {exit_r}")
        print()

async def main():
    tickers = ["PLTR", "TSLA", "NVDA", "CEG"]
    strategies = ["refined_rsi", "refined_rsi_v5", "refined_rsi_v8"]

    print(f"{'Strategy':>20s} | {'Ticker':>8s} | {'P&L%':>9s} | {'P&L$':>11s} | {'Trades':>6s} | {'WR%':>6s} | {'DD%':>6s} | Exit Reasons")
    print("-" * 120)

    for ticker in tickers:
        for strategy in strategies:
            await run([ticker], strategy)
        print()

if __name__ == "__main__":
    asyncio.run(main())
