"""Sweep max_hold_bars for rsi_divergence_pct strategy.

Tests: 180, 240, 300, 390, 720 minute hold limits.
"""
import asyncio, sys, os, logging
logging.disable(logging.WARNING)
os.environ["LOG_LEVEL"] = "ERROR"
os.environ["PYTHONUNBUFFERED"] = "1"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import structlog
structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR))

from datetime import datetime, timedelta
from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine
from app.engine.strategies import get_strategy

STARTING_BALANCE = 100_000
TICKERS = ["TSLA", "CEG", "PLTR", "RIVN"]
MAX_HOLDS = [180, 240, 300, 390, 720]
DAYS = 21
POS_PCT = 0.50
SL_PCT = 0.02

P = lambda *a, **kw: print(*a, **kw, flush=True)


async def run_one(max_hold_bars: int):
    end = datetime.now()
    while end.weekday() >= 5:
        end -= timedelta(days=1)
    start = end - timedelta(days=DAYS)

    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    engine = ReplayEngine(broker)

    # Override max_hold_bars on the strategy's scan_config
    sdef = get_strategy("rsi_divergence_pct")
    original_mh = sdef.scan_config.max_hold_bars
    sdef.scan_config.max_hold_bars = max_hold_bars

    try:
        await engine.start(
            symbols=TICKERS, timeframe="1m",
            start_date=start.strftime("%Y-%m-%d"),
            end_date=end.strftime("%Y-%m-%d"),
            speed=1.0, stop_loss_pct=SL_PCT, position_size_pct=POS_PCT,
            strategy="rsi_divergence_pct", headless=True,
        )
        if engine._task:
            await engine._task
    except Exception as e:
        P(f"  ERROR: {e}")
        return None
    finally:
        sdef.scan_config.max_hold_bars = original_mh

    return engine.get_results()


async def main():
    P("=" * 110)
    P(f"MAX HOLD SWEEP  —  Portfolio: {', '.join(TICKERS)}")
    P(f"Period: {DAYS}d | SL: {SL_PCT*100:.1f}% | Position: {POS_PCT*100:.0f}% | Balance: ${STARTING_BALANCE:,}")
    P("=" * 110)
    P(f"  {'MaxHold':>8s} | {'P&L%':>8s} | {'P&L$':>10s} | {'#':>3s} | {'WR%':>5s} | {'DD%':>6s} | Exit Reasons")
    P("-" * 110)

    results = []
    for i, mh in enumerate(MAX_HOLDS):
        P(f"  [{i+1}/{len(MAX_HOLDS)}] Running max_hold={mh}m ...")
        r = await run_one(mh)
        if not r:
            P(f"  {'':>8s} | FAILED")
            continue

        # Tally exit reasons
        from collections import Counter
        reasons = Counter()
        for t in r.trades:
            er = t.get("exit_reason") or "open"
            reasons[er] += 1
        reason_str = ", ".join(f"{k}={v}" for k, v in reasons.most_common())

        row = {
            "mh": mh, "pnl_pct": r.total_pnl_pct, "pnl": r.total_pnl,
            "trades": r.total_trades, "wr": r.win_rate, "dd": r.max_drawdown_pct,
            "reasons": reason_str,
        }
        results.append(row)
        sign = "+" if r.total_pnl_pct >= 0 else ""
        P(f"  {mh:>6d}m | {sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} | {r.win_rate:>5.1f} | {r.max_drawdown_pct:>5.2f}% | {reason_str}")

    # Ranked summary
    results.sort(key=lambda x: x["pnl_pct"], reverse=True)
    P("\n" + "=" * 110)
    P("RANKED (by P&L%)")
    P("=" * 110)
    for i, row in enumerate(results):
        sign = "+" if row["pnl_pct"] >= 0 else ""
        marker = " <<<" if i == 0 else ""
        P(f"  {i+1}. MH={row['mh']}m | {sign}{row['pnl_pct']:>7.2f}% | ${row['pnl']:>+9,.0f} | {row['trades']} trades | WR {row['wr']:.1f}% | DD {row['dd']:.2f}% | {row['reasons']}{marker}")


if __name__ == "__main__":
    asyncio.run(main())
