"""A/B test: v8 strategy with vs without daily S/R levels.

Runs the same backtest twice — once with intraday-only S/R,
once with daily S/R merged in — and compares results.
"""
import asyncio, sys, os, logging
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
os.environ["PYTHONUNBUFFERED"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from datetime import datetime, timedelta
from collections import Counter
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine

STARTING_BALANCE = 100_000
TICKERS = ["TSLA", "CEG", "PLTR", "RIVN"]
DAYS = 21
POS_PCT = 0.50
SL_PCT = 0.02

P = lambda *a, **kw: print(*a, **kw, flush=True)


async def run_one(use_daily_sr: bool, daily_sr_years: int = 2):
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=DAYS)

    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    engine = ReplayEngine(broker)

    await engine.start(
        symbols=TICKERS, timeframe="1m",
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        speed=1.0, stop_loss_pct=SL_PCT, position_size_pct=POS_PCT,
        strategy="rsi_divergence_pct", headless=True,
        use_daily_sr=use_daily_sr,
        daily_sr_years=daily_sr_years,
    )
    if engine._task:
        await engine._task

    return engine.get_results()


def print_result(label: str, r):
    if not r:
        P(f"  {label}: FAILED")
        return

    reasons = Counter()
    per_ticker = {}
    for t in r.trades:
        er = t.get("exit_reason") or "open"
        reasons[er] += 1
        sym = t.get("symbol", "?")
        pnl = t.get("pnl") or 0
        pnl_pct = t.get("pnl_pct") or 0
        if sym not in per_ticker:
            per_ticker[sym] = {"pnl": 0.0, "pnl_pct": 0.0, "n": 0, "wins": 0}
        per_ticker[sym]["pnl"] += pnl
        per_ticker[sym]["pnl_pct"] += pnl_pct
        per_ticker[sym]["n"] += 1
        if pnl > 0:
            per_ticker[sym]["wins"] += 1

    sign = "+" if r.total_pnl_pct >= 0 else ""
    reason_str = ", ".join(f"{k}={v}" for k, v in reasons.most_common())

    P(f"\n  {label}")
    P(f"  {'─' * 60}")
    P(f"  P&L:     {sign}{r.total_pnl_pct:.2f}% (${r.total_pnl:+,.0f})")
    P(f"  Trades:  {r.total_trades}  |  WR: {r.win_rate:.1f}%  |  DD: {r.max_drawdown_pct:.2f}%")
    P(f"  Exits:   {reason_str}")
    P(f"  Per ticker:")
    for sym in sorted(per_ticker.keys()):
        d = per_ticker[sym]
        wr = d["wins"] / d["n"] * 100 if d["n"] else 0
        P(f"    {sym:5s}: {d['pnl_pct']:+6.2f}% (${d['pnl']:+,.0f}) | {d['n']} trades | WR {wr:.0f}%")


async def main():
    P("=" * 70)
    P(f"DAILY S/R A/B TEST  —  {', '.join(TICKERS)}")
    P(f"Period: {DAYS}d | SL: {SL_PCT*100:.1f}% | Pos: {POS_PCT*100:.0f}% | Bal: ${STARTING_BALANCE:,}")
    P("=" * 70)

    P("\n[1/2] Running WITHOUT daily S/R (baseline)...")
    r_base = await run_one(use_daily_sr=False)
    print_result("BASELINE (intraday S/R only)", r_base)

    P("\n[2/2] Running WITH daily S/R (2 years)...")
    r_daily = await run_one(use_daily_sr=True, daily_sr_years=2)
    print_result("WITH DAILY S/R (2y)", r_daily)

    # Comparison
    if r_base and r_daily:
        P(f"\n  {'═' * 60}")
        P(f"  COMPARISON")
        P(f"  {'─' * 60}")
        delta_pnl = r_daily.total_pnl_pct - r_base.total_pnl_pct
        delta_trades = r_daily.total_trades - r_base.total_trades
        delta_wr = r_daily.win_rate - r_base.win_rate
        sign = "+" if delta_pnl >= 0 else ""
        P(f"  P&L change:    {sign}{delta_pnl:.2f}%")
        P(f"  Trade change:  {delta_trades:+d}")
        P(f"  WR change:     {delta_wr:+.1f}%")
        if delta_pnl > 0:
            P(f"  Verdict:       Daily S/R IMPROVED results")
        elif delta_pnl < 0:
            P(f"  Verdict:       Daily S/R HURT results")
        else:
            P(f"  Verdict:       No difference")


if __name__ == "__main__":
    asyncio.run(main())
