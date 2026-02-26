"""Parameter sweep for v8 strategy optimization.

Tests different target percentages, holding periods, and entry filters.
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

async def run(symbols, strategy, days=29, stop_pct=0.02, pos_pct=0.50):
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
    except Exception as e:
        return None

    return engine.get_results()


async def main():
    # Test tickers — mix of high and medium volatility
    tickers = ["CEG", "PLTR", "TSLA", "NVDA", "SOFI", "AMZN", "AAPL", "META"]

    # First: baseline with v1 and v5 for comparison
    print("=" * 120)
    print("BASELINE: v1 and v5 strategies")
    print("=" * 120)
    print(f"  {'Strategy':>20s} | {'Ticker':>6s} | {'P&L%':>8s} | {'P&L$':>10s} | {'#':>3s} | {'WR%':>5s} | {'DD%':>5s}")
    print("-" * 80)

    baselines = {}
    for ticker in tickers:
        for strat in ["refined_rsi"]:
            r = await run([ticker], strat)
            if r:
                pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
                print(f"  {strat:>20s} | {ticker:>6s} | {pnl_sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} | {r.win_rate:>5.1f} | {r.max_drawdown_pct:>5.2f}")
                baselines[(strat, ticker)] = r.total_pnl_pct

    # Now: sweep v8 parameters
    print("\n" + "=" * 120)
    print("V8 PARAMETER SWEEP")
    print("=" * 120)

    # Parameters to sweep
    target_pcts = [0.02, 0.03, 0.04, 0.06]
    max_hold_options = [390, 780, 1560]  # 1 day, 2 days, 4 days
    breakeven_options = [0.01, 0.015, 0.02]

    best_configs = []

    for target_pct in target_pcts:
        for max_hold in max_hold_options:
            # Set v8 parameters
            sg._V8_TARGET_PCT = target_pct
            sg._V8_MAX_HOLD_MINUTES = max_hold

            config_str = f"T={target_pct*100:.0f}% H={max_hold}m"
            total_pnl_all = 0
            total_trades_all = 0
            ticker_results = {}

            for ticker in tickers:
                r = await run([ticker], "refined_rsi_v8")
                if r:
                    total_pnl_all += r.total_pnl_pct
                    total_trades_all += r.total_trades
                    ticker_results[ticker] = r

            avg_pnl = total_pnl_all / len(tickers) if tickers else 0
            avg_trades = total_trades_all / len(tickers) if tickers else 0

            best_configs.append({
                "config": config_str,
                "target_pct": target_pct,
                "max_hold": max_hold,
                "avg_pnl": avg_pnl,
                "avg_trades": avg_trades,
                "ticker_results": ticker_results,
            })

            pnl_sign = "+" if avg_pnl >= 0 else ""
            detail = " | ".join(
                f"{t}: {r.total_pnl_pct:+.2f}%/{r.total_trades}t"
                for t, r in sorted(ticker_results.items())
            )
            print(f"  {config_str:>20s} | avg {pnl_sign}{avg_pnl:>7.2f}% | avg {avg_trades:>5.1f} trades | {detail}")

    # Sort by avg P&L
    best_configs.sort(key=lambda x: x["avg_pnl"], reverse=True)

    print("\n" + "=" * 120)
    print("TOP 5 CONFIGURATIONS")
    print("=" * 120)
    for i, c in enumerate(best_configs[:5]):
        pnl_sign = "+" if c["avg_pnl"] >= 0 else ""
        print(f"  {i+1}. {c['config']:>20s} | avg {pnl_sign}{c['avg_pnl']:>7.2f}% | avg {c['avg_trades']:>5.1f} trades")

    # Best config per-ticker breakdown
    if best_configs:
        best = best_configs[0]
        print(f"\n  Best config: {best['config']}")
        for ticker, r in sorted(best["ticker_results"].items()):
            exits = {}
            for t in r.trades:
                exits[t.get("exit_reason", "?")] = exits.get(t.get("exit_reason", "?"), 0) + 1
            exit_str = " ".join(f"{k}={v}" for k, v in sorted(exits.items()))
            pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
            print(f"    {ticker:>6s} | {pnl_sign}{r.total_pnl_pct:>7.2f}% | {r.total_trades:>3d} trades | WR {r.win_rate:>5.1f}% | {exit_str}")

    # Restore defaults
    sg._V8_TARGET_PCT = 0.04
    sg._V8_MAX_HOLD_MINUTES = 1560

    # Now test multi-ticker portfolios with the best v8 config
    print("\n" + "=" * 120)
    print("MULTI-TICKER PORTFOLIOS (using best v8 config)")
    print("=" * 120)

    if best_configs:
        best = best_configs[0]
        sg._V8_TARGET_PCT = best["target_pct"]
        sg._V8_MAX_HOLD_MINUTES = best["max_hold"]

        # Test different portfolio combinations
        portfolios = [
            ("Top2 by v8", ["CEG", sorted(best["ticker_results"].items(), key=lambda x: x[1].total_pnl_pct, reverse=True)[1][0]]),
            ("Top3 by v8", [t for t, _ in sorted(best["ticker_results"].items(), key=lambda x: x[1].total_pnl_pct, reverse=True)[:3]]),
            ("High ATR", ["CEG", "PLTR", "SOFI"]),
            ("Tech Mega", ["NVDA", "TSLA", "META"]),
            ("CEG only", ["CEG"]),
        ]

        for name, symbols in portfolios:
            r = await run(symbols, "refined_rsi_v8")
            if r:
                pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
                exits = {}
                for t in r.trades:
                    exits[t.get("exit_reason", "?")] = exits.get(t.get("exit_reason", "?"), 0) + 1
                exit_str = " ".join(f"{k}={v}" for k, v in sorted(exits.items()))
                print(f"  {name:>20s} {str(symbols):>30s} | {pnl_sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} trades | WR {r.win_rate:>5.1f}% | {exit_str}")

    # Also compare with best v1/v5 multi-ticker
    for name, symbols in [("v1 High ATR", ["CEG", "PLTR", "SOFI"]), ("v5 High ATR", ["CEG", "PLTR", "SOFI"])]:
        strat = "refined_rsi"
        r = await run(symbols, strat)
        if r:
            pnl_sign = "+" if r.total_pnl_pct >= 0 else ""
            print(f"  {name:>20s} {str(symbols):>30s} | {pnl_sign}{r.total_pnl_pct:>7.2f}% | ${r.total_pnl:>+9,.0f} | {r.total_trades:>3d} trades | WR {r.win_rate:>5.1f}%")


if __name__ == "__main__":
    asyncio.run(main())
