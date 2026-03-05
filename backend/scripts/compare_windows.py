"""Compare v8 backtest results across date windows to diagnose P&L drop."""
import asyncio, sys, os, logging
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine


async def run(symbols, start_date, end_date, label):
    broker = ReplayBrokerAdapter(starting_balance=100_000)
    engine = ReplayEngine(broker)
    await engine.start(
        symbols=symbols, timeframe="1m",
        start_date=start_date, end_date=end_date,
        speed=1.0,
        strategy="rsi_divergence_pct", headless=True, instant=True,
    )
    if engine._task:
        await engine._task

    r = engine.get_results()
    if not r:
        print(f"  {label} | NO RESULTS")
        return

    exits = {}
    for t in r.trades:
        reason = t.get("exit_reason", "unknown")
        exits[reason] = exits.get(reason, 0) + 1

    pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
    exit_str = " ".join(f"{k}={v}" for k, v in sorted(exits.items()))
    print(f"  {label}")
    print(f"    Symbols: {','.join(symbols)}")
    print(f"    Dates:   {start_date} -> {end_date}")
    print(f"    P&L:     {pnl_sign}{r.total_pnl_pct:.2f}% (${r.total_pnl:+,.0f})")
    print(f"    Trades:  {r.total_trades} | WR: {r.win_rate:.1f}% | DD: {r.max_drawdown_pct:.2f}%")
    print(f"    Exits:   {exit_str}")

    # Gated symbols
    if engine._gated_symbols:
        print(f"    GATED:   {', '.join(engine._gated_symbols)}")

    print()

    # Print individual trades
    print(f"    {'Side':>5s} | {'Symbol':>6s} | {'Entry':>9s} | {'Exit':>9s} | {'P&L':>9s} | {'P&L%':>7s} | Entry -> Exit | Reason")
    print("    " + "-" * 110)
    for t in r.trades:
        pnl = t.get("pnl", 0) or 0
        pnl_pct = t.get("pnl_pct", 0) or 0
        entry_t = t.get("entry_time", "")[:16] if t.get("entry_time") else ""
        exit_t = t.get("exit_time", "")[:16] if t.get("exit_time") else ""
        exit_r = t.get("exit_reason", "open")
        sym = t.get("symbol", "")
        print(f"    {t['side']:>5s} | {sym:>6s} | ${t['entry_price']:>8.2f} | ${(t.get('exit_price') or 0):>8.2f} | ${pnl:>+8,.0f} | {pnl_pct:>+6.2f}% | {entry_t} -> {exit_t} | {exit_r}")
    print()


async def main():
    tickers = ["TSLA", "CEG", "PLTR", "RIVN"]

    # Run 1: Original window (same as v8 optimization on Feb 26)
    print("=" * 120)
    print("RUN 1: Original window (Jan 28 - Feb 26) — same as v8 optimization results")
    print("=" * 120)
    await run(tickers, "2026-01-28", "2026-02-26", "OLD WINDOW")

    # Run 2: Current window (Jan 28 - Feb 27) — what 1M gives from today
    print("=" * 120)
    print("RUN 2: Current window (Jan 28 - Feb 27) — +1 day")
    print("=" * 120)
    await run(tickers, "2026-01-28", "2026-02-27", "NEW WINDOW (+1 day)")


if __name__ == "__main__":
    asyncio.run(main())
