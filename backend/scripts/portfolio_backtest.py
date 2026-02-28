#!/usr/bin/env python3
"""Portfolio backtest: run multi-ticker concurrent backtest sharing one account.

Iterates bar-by-bar across all tickers simultaneously using a single
ReplayBrokerAdapter (shared balance/positions). Enforces total exposure cap.

Test matrix:
  - Tickers: Top 5 from fitness scan (UNH, AAPL, GOOGL, MSFT, TSLA)
  - Position sizes per ticker: 25%, 33%, 50%, 100%
  - Timeframe: 1m, 7-day window
  - Total exposure cap: 100%

Usage:
    cd backend && python -m scripts.portfolio_backtest
"""

from __future__ import annotations

import asyncio
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine, TIMEFRAME_MAP, _normalize_symbol
from app.engine.candle_cache import load_cached, save_cache
from app.analysis.pipeline import run_analysis
from app.analysis.indicators import rsi as calc_rsi
from app.engine.signal_generator import (
    generate_signals,
    check_reversal_signal,
    detect_rsi_divergence,
    generate_signals_refined_rsi,
    check_exit_refined_rsi,
    get_timeframe_params,
)
from app.engine.divergence_tracker import (
    CANDIDATE_TIMEFRAMES,
    DivergenceSignal,
    DivergenceTracker,
)
from app.engine.risk_manager import validate_signal

import yfinance as yf
import structlog

structlog.configure(processors=[structlog.dev.ConsoleRenderer()])
logger = structlog.get_logger()

# ── Configuration ──────────────────────────────────────────────────────
TICKERS = ["UNH", "AAPL", "GOOGL", "MSFT", "TSLA", "NVDA", "AMZN", "META", "PFE", "JPM"]
POSITION_SIZES = [0.25]
TIMEFRAME = "1m"
STRATEGY = "refined_rsi"
STARTING_BALANCE = 100_000.0
MAX_EXPOSURE_PCT = 1.00  # 100% total exposure cap
LOOKBACK_BARS = 200

# Date range: 29 calendar days
END_DATE = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=29)).strftime("%Y-%m-%d")

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"


async def fetch_ticker_data(
    symbol: str, start: str, end: str, interval: str
) -> pd.DataFrame:
    """Fetch historical data for a ticker, using cache when available."""
    yf_symbol = _normalize_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)

    # Use ReplayEngine's chunked fetcher for caching
    df = await ReplayEngine._fetch_chunked(ticker, start, end, interval, symbol)
    if df.empty:
        logger.warning("no_data", symbol=symbol)
        return pd.DataFrame()
    df.columns = [c.lower() for c in df.columns]
    return df


def build_unified_timeline(dataframes: dict[str, pd.DataFrame]) -> list:
    """Build a sorted union of all timestamps across tickers."""
    all_timestamps = set()
    for df in dataframes.values():
        all_timestamps.update(df.index.tolist())
    return sorted(all_timestamps)


async def run_portfolio_backtest(position_size_pct: float) -> dict:
    """Run a single portfolio backtest with given position size."""

    interval = TIMEFRAME_MAP.get(TIMEFRAME, "1m")
    tf_params = get_timeframe_params(TIMEFRAME)

    # Shift start back for lookback
    lookback_start = ReplayEngine._shift_date(START_DATE, TIMEFRAME, LOOKBACK_BARS)

    # Fetch data for all tickers
    logger.info("fetching_data", tickers=TICKERS, start=lookback_start, end=END_DATE)
    dataframes: dict[str, pd.DataFrame] = {}
    for symbol in TICKERS:
        df = await fetch_ticker_data(symbol, lookback_start, END_DATE, interval)
        if not df.empty:
            dataframes[symbol] = df
            logger.info("data_fetched", symbol=symbol, bars=len(df))

    if not dataframes:
        return {"error": "No data fetched for any ticker"}

    # Build unified timeline starting from the trade period
    start_ts = pd.Timestamp(START_DATE)
    if start_ts.tzinfo is None and next(iter(dataframes.values())).index.tz is not None:
        start_ts = start_ts.tz_localize(next(iter(dataframes.values())).index.tz)

    timeline = build_unified_timeline(dataframes)
    trade_timeline = [ts for ts in timeline if ts >= start_ts]

    logger.info(
        "timeline_built",
        total_bars=len(timeline),
        trade_bars=len(trade_timeline),
        tickers=list(dataframes.keys()),
    )

    # Initialize shared broker
    broker = ReplayBrokerAdapter(starting_balance=STARTING_BALANCE)
    await broker.connect()

    # Per-ticker state
    trackers: dict[str, DivergenceTracker] = {}
    div_dataframes: dict[str, dict[str, pd.DataFrame]] = {}
    cooldown_bars: dict[str, int] = {s: 0 for s in dataframes}
    trade_records: list[dict] = []
    per_ticker_pnl: dict[str, float] = {s: 0.0 for s in dataframes}
    per_ticker_trades: dict[str, int] = {s: 0 for s in dataframes}
    per_ticker_wins: dict[str, int] = {s: 0 for s in dataframes}

    # Set up divergence trackers for each ticker
    for symbol, df in dataframes.items():
        candidates = CANDIDATE_TIMEFRAMES.get(TIMEFRAME, [TIMEFRAME])
        # For 1m backtest, just use the primary timeframe for divergence
        div_dataframes[symbol] = {TIMEFRAME: df}
        active_tfs = list(div_dataframes[symbol].keys())
        trackers[symbol] = DivergenceTracker(active_tfs)

    # Equity tracking
    equity_curve: list[dict] = []

    # Main loop: iterate bar-by-bar across the trade timeline
    for bar_idx, timestamp in enumerate(trade_timeline):
        for symbol, df in dataframes.items():
            # Check if this ticker has data at this timestamp
            if timestamp not in df.index:
                continue

            row_loc = df.index.get_loc(timestamp)
            row = df.iloc[row_loc]
            close_price = float(row["close"])

            # Update price
            broker.set_current_price(symbol, close_price)
            broker.update_position_prices(symbol, close_price)

            # Check stops/targets
            closed_trades = broker.check_stops_and_targets(
                symbol,
                float(row["high"]),
                float(row["low"]),
                close_price,
            )
            for ct in closed_trades:
                _handle_closed_trade(
                    ct, timestamp, trade_records, trackers.get(symbol),
                    cooldown_bars, symbol, tf_params, per_ticker_pnl,
                    per_ticker_trades, per_ticker_wins,
                )

            # Decrement cooldown
            if cooldown_bars.get(symbol, 0) > 0:
                cooldown_bars[symbol] -= 1

            # Need enough bars for analysis
            if row_loc < 20:
                continue

            window_df = df.iloc[: row_loc + 1].copy()

            # Scan for divergences
            best_div = _scan_divergences(
                symbol, timestamp, div_dataframes.get(symbol, {}),
                trackers.get(symbol),
            )

            # Skip during cooldown
            if cooldown_bars.get(symbol, 0) > 0:
                best_div = None

            # Process bar
            await _process_bar(
                broker, symbol, window_df, timestamp, best_div,
                trade_records, position_size_pct, tf_params,
                cooldown_bars, trackers.get(symbol),
                per_ticker_pnl, per_ticker_trades, per_ticker_wins,
                MAX_EXPOSURE_PCT,
            )

        # Track equity after processing all tickers at this timestamp
        balance = await broker.get_account_balance()
        equity_curve.append({
            "timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
            "balance": round(balance, 2),
            "bar_index": bar_idx,
        })

    # Build results
    results = _build_portfolio_results(
        trade_records, equity_curve, broker, per_ticker_pnl,
        per_ticker_trades, per_ticker_wins, position_size_pct,
    )
    return results


def _handle_closed_trade(
    ct: dict, timestamp, trade_records: list, tracker, cooldown_bars: dict,
    symbol: str, tf_params, per_ticker_pnl: dict, per_ticker_trades: dict,
    per_ticker_wins: dict,
):
    """Update trade record on stop/target hit."""
    for tr in trade_records:
        if tr["ib_order_id"] == ct["order_id"] and tr["status"] == "OPEN":
            tr["exit_price"] = ct["exit_price"]
            tr["exit_time"] = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
            tr["pnl"] = ct["pnl"]
            tr["pnl_pct"] = ct["pnl_pct"]
            tr["status"] = "CLOSED"
            tr["exit_reason"] = ct.get("exit_reason", "unknown")

            pnl = ct["pnl"] or 0
            per_ticker_pnl[symbol] = per_ticker_pnl.get(symbol, 0) + pnl
            per_ticker_trades[symbol] = per_ticker_trades.get(symbol, 0) + 1
            if pnl > 0:
                per_ticker_wins[symbol] = per_ticker_wins.get(symbol, 0) + 1

            div_tf = tr.get("divergence_timeframe")
            if div_tf and tracker:
                tracker.record_outcome(div_tf, pnl > 0)

            if pnl < 0:
                cooldown_bars[symbol] = tf_params.cooldown_bars if tf_params else 6
            break


def _scan_divergences(
    symbol: str, current_timestamp, div_dfs: dict[str, pd.DataFrame],
    tracker: DivergenceTracker | None,
) -> DivergenceSignal | None:
    """Scan for RSI divergences on a single ticker."""
    if not tracker or not div_dfs:
        return None

    divergences: dict[str, DivergenceSignal | None] = {}
    weights = tracker.get_weights()

    for tf, df_tf in div_dfs.items():
        tf_window = df_tf[df_tf.index <= current_timestamp]
        if len(tf_window) < 30:
            continue

        rsi_vals = calc_rsi(tf_window["close"], period=14).dropna().tolist()
        prices = tf_window["close"].tolist()

        if len(rsi_vals) < 20:
            continue

        div = detect_rsi_divergence(rsi_vals, prices, lookback=20)
        if div:
            divergences[tf] = DivergenceSignal(
                timeframe=tf,
                direction=div.direction,
                rsi_current=div.rsi_current,
                description=f"{div.description} ({tf})",
                weight=weights.get(tf, 1.0),
                rsi_spread=div.rsi_spread,
            )

    return tracker.get_best_divergence(divergences)


async def _process_bar(
    broker: ReplayBrokerAdapter,
    symbol: str,
    df: pd.DataFrame,
    timestamp,
    best_divergence: DivergenceSignal | None,
    trade_records: list,
    position_size_pct: float,
    tf_params,
    cooldown_bars: dict,
    tracker,
    per_ticker_pnl: dict,
    per_ticker_trades: dict,
    per_ticker_wins: dict,
    max_exposure_pct: float,
):
    """Run analysis → signals → risk → order for one ticker at one bar."""
    try:
        analysis = run_analysis(symbol, df)
        close_prices = df["close"].tolist()

        gen_fn = generate_signals_refined_rsi
        signals = gen_fn(
            analysis, df,
            stop_loss_pct=0.0,  # ATR-based stops
            best_divergence=best_divergence,
            current_timestamp=timestamp,
            tf_params=tf_params,
        )

        current_side = broker.get_position_side(symbol)
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        # Check for exit if we have a position
        if current_side is not None:
            entry_time = None
            for tr in reversed(trade_records):
                if tr["status"] == "OPEN" and tr["symbol"] == symbol:
                    try:
                        entry_time = datetime.fromisoformat(tr["entry_time"])
                    except (ValueError, TypeError):
                        pass
                    break

            current_time = timestamp if isinstance(timestamp, datetime) else None
            if current_time is None:
                try:
                    current_time = datetime.fromisoformat(str(timestamp))
                except (ValueError, TypeError):
                    pass

            reversal = check_exit_refined_rsi(
                    analysis, df, current_side,
                    entry_time=entry_time,
                    current_time=current_time,
                    tf_params=tf_params,
                )

            if reversal:
                close_price = float(df["close"].iloc[-1])
                exit_reason = reversal if isinstance(reversal, str) else "reversal"
                closed_trade = broker.close_position(symbol, close_price, exit_reason=exit_reason)
                if closed_trade:
                    _close_trade_record(
                        closed_trade, ts_str, exit_reason,
                        trade_records, tracker,
                        cooldown_bars, symbol, tf_params,
                        per_ticker_pnl, per_ticker_trades, per_ticker_wins,
                    )
                    current_side = None
            elif signals:
                opposite = [
                    s for s in signals
                    if s.signal_type != current_side and s.has_rsi_divergence
                ]
                if opposite:
                    best_opposite = max(opposite, key=lambda s: s.confidence)
                    close_price = best_opposite.entry_price
                    closed_trade = broker.close_position(symbol, close_price, exit_reason="position_flip")
                    if closed_trade:
                        _close_trade_record(
                            closed_trade, ts_str, best_opposite.reason,
                            trade_records, tracker,
                            cooldown_bars, symbol, tf_params,
                            per_ticker_pnl, per_ticker_trades, per_ticker_wins,
                        )
                        current_side = None
                else:
                    return
            else:
                return

        if current_side is not None:
            return

        if not signals:
            return

        best_signal = max(signals, key=lambda s: s.confidence)

        balance = await broker.get_account_balance()
        buying_power = await broker.get_buying_power()
        positions = await broker.get_positions()
        open_symbols = [p.symbol for p in positions]
        total_exposure = sum(p.market_value for p in positions)

        # Enforce total exposure cap
        if total_exposure / balance >= max_exposure_pct if balance > 0 else True:
            return

        # Check if new position would exceed exposure cap
        potential_position_value = balance * position_size_pct
        if (total_exposure + potential_position_value) / balance > max_exposure_pct if balance > 0 else True:
            # Reduce position size to fit within cap
            remaining_capacity = (max_exposure_pct * balance) - total_exposure
            if remaining_capacity <= 0:
                return
            adjusted_pct = remaining_capacity / balance
            position_size_pct_adj = min(position_size_pct, adjusted_pct)
        else:
            position_size_pct_adj = position_size_pct

        decision = validate_signal(
            best_signal, balance, buying_power, open_symbols, total_exposure,
            position_size_pct=position_size_pct_adj,
        )

        if not decision.approved:
            return

        order_id = await broker.place_order(
            symbol=best_signal.symbol,
            side=best_signal.signal_type,
            quantity=decision.quantity,
            order_type="LIMIT",
            limit_price=best_signal.entry_price,
            stop_loss=best_signal.stop_loss,
            profit_target=best_signal.profit_target,
        )

        trade_record = {
            "id": len(trade_records) + 1,
            "symbol": best_signal.symbol,
            "side": best_signal.signal_type,
            "quantity": decision.quantity,
            "entry_price": best_signal.entry_price,
            "exit_price": None,
            "entry_time": ts_str,
            "exit_time": None,
            "stop_loss": best_signal.stop_loss,
            "profit_target": best_signal.profit_target,
            "pnl": None,
            "pnl_pct": None,
            "reason": best_signal.reason,
            "status": "OPEN",
            "ib_order_id": order_id,
            "signal_id": None,
            "divergence_timeframe": best_signal.divergence_timeframe,
            "exit_reason": None,
        }
        trade_records.append(trade_record)

    except Exception as e:
        logger.error("portfolio_process_error", symbol=symbol, error=str(e))


def _close_trade_record(
    closed_trade: dict, ts_str: str, close_reason: str,
    trade_records: list, tracker,
    cooldown_bars: dict, symbol: str, tf_params,
    per_ticker_pnl: dict, per_ticker_trades: dict, per_ticker_wins: dict,
):
    """Update trade record on position close."""
    for tr in trade_records:
        if tr["ib_order_id"] == closed_trade["order_id"] and tr["status"] == "OPEN":
            tr["exit_price"] = closed_trade["exit_price"]
            tr["exit_time"] = ts_str
            tr["pnl"] = closed_trade["pnl"]
            tr["pnl_pct"] = closed_trade["pnl_pct"]
            tr["status"] = "CLOSED"
            tr["exit_reason"] = closed_trade.get("exit_reason", close_reason)
            tr["reason"] = tr["reason"] + f" | Closed: {close_reason}"

            pnl = closed_trade["pnl"] or 0
            per_ticker_pnl[symbol] = per_ticker_pnl.get(symbol, 0) + pnl
            per_ticker_trades[symbol] = per_ticker_trades.get(symbol, 0) + 1
            if pnl > 0:
                per_ticker_wins[symbol] = per_ticker_wins.get(symbol, 0) + 1

            div_tf = tr.get("divergence_timeframe")
            if div_tf and tracker:
                tracker.record_outcome(div_tf, pnl > 0)

            if pnl < 0:
                cooldown_bars[symbol] = tf_params.cooldown_bars if tf_params else 6
            break


def _build_portfolio_results(
    trade_records: list, equity_curve: list, broker: ReplayBrokerAdapter,
    per_ticker_pnl: dict, per_ticker_trades: dict, per_ticker_wins: dict,
    position_size_pct: float,
) -> dict:
    """Build portfolio-level results."""
    closed = [t for t in trade_records if t["status"] == "CLOSED"]
    starting = broker.get_starting_balance()

    total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
    total_pnl_pct = (total_pnl / starting * 100) if starting > 0 else 0
    wins = [t for t in closed if (t.get("pnl", 0) or 0) > 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0

    # Max drawdown
    max_drawdown = 0.0
    max_drawdown_pct = 0.0
    if equity_curve:
        peak = equity_curve[0]["balance"]
        for point in equity_curve:
            bal = point["balance"]
            if bal > peak:
                peak = bal
            dd = peak - bal
            dd_pct = (dd / peak * 100) if peak > 0 else 0
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_pct = dd_pct

    ending_balance = equity_curve[-1]["balance"] if equity_curve else starting

    # Monthly PnL estimate (based on 7-day window → extrapolate to 21 trading days)
    trading_days = 7
    monthly_pnl_pct = (total_pnl_pct / trading_days * 21) if trading_days > 0 else 0

    # Per-ticker breakdown
    ticker_breakdown = {}
    for symbol in per_ticker_pnl:
        trades = per_ticker_trades.get(symbol, 0)
        ticker_wins = per_ticker_wins.get(symbol, 0)
        ticker_breakdown[symbol] = {
            "pnl": round(per_ticker_pnl[symbol], 2),
            "pnl_pct": round(per_ticker_pnl[symbol] / starting * 100, 2) if starting > 0 else 0,
            "trades": trades,
            "win_rate": round(ticker_wins / trades * 100, 1) if trades > 0 else 0,
        }

    return {
        "position_size_pct": position_size_pct,
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "monthly_pnl_pct_est": round(monthly_pnl_pct, 2),
        "win_rate": round(win_rate, 1),
        "total_trades": len(closed),
        "winning_trades": len(wins),
        "losing_trades": len(closed) - len(wins),
        "max_drawdown": round(max_drawdown, 2),
        "max_drawdown_pct": round(max_drawdown_pct, 2),
        "starting_balance": starting,
        "ending_balance": round(ending_balance, 2),
        "ticker_breakdown": ticker_breakdown,
    }


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"portfolio_{timestamp}.csv"

    all_results: list[dict] = []

    print(f"\n{'='*80}")
    print(f"  PORTFOLIO BACKTEST — {len(TICKERS)} tickers × {len(POSITION_SIZES)} position sizes")
    print(f"  Tickers: {', '.join(TICKERS)}")
    print(f"  Timeframe: {TIMEFRAME}  |  Period: {START_DATE} → {END_DATE}")
    print(f"  Strategy: {STRATEGY}  |  Exposure cap: {MAX_EXPOSURE_PCT*100:.0f}%")
    print(f"{'='*80}\n")

    for pos_size in POSITION_SIZES:
        print(f"\n--- Position size: {pos_size*100:.0f}% per ticker ---")
        result = await run_portfolio_backtest(pos_size)
        all_results.append(result)

        print(f"  Total PnL: ${result['total_pnl']:+,.2f} ({result['total_pnl_pct']:+.2f}%)")
        print(f"  Monthly PnL est: {result['monthly_pnl_pct_est']:+.2f}%")
        print(f"  Win rate: {result['win_rate']:.1f}% ({result['winning_trades']}W / {result['losing_trades']}L)")
        print(f"  Max drawdown: ${result['max_drawdown']:,.2f} ({result['max_drawdown_pct']:.2f}%)")
        print(f"  Ending balance: ${result['ending_balance']:,.2f}")
        print(f"  Per-ticker breakdown:")
        for sym, tb in result.get("ticker_breakdown", {}).items():
            print(f"    {sym:6s}: PnL ${tb['pnl']:+8,.2f} ({tb['pnl_pct']:+.2f}%)  "
                  f"Trades: {tb['trades']}  Win: {tb['win_rate']:.0f}%")

    # Write CSV summary
    if all_results:
        fieldnames = [
            "position_size_pct", "total_pnl", "total_pnl_pct", "monthly_pnl_pct_est",
            "win_rate", "total_trades", "winning_trades", "losing_trades",
            "max_drawdown", "max_drawdown_pct", "starting_balance", "ending_balance",
        ]
        # Add per-ticker columns
        for sym in TICKERS:
            fieldnames.extend([f"{sym}_pnl", f"{sym}_pnl_pct", f"{sym}_trades", f"{sym}_win_rate"])

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in all_results:
                row = {k: r[k] for k in fieldnames[:12]}
                for sym in TICKERS:
                    tb = r.get("ticker_breakdown", {}).get(sym, {})
                    row[f"{sym}_pnl"] = tb.get("pnl", 0)
                    row[f"{sym}_pnl_pct"] = tb.get("pnl_pct", 0)
                    row[f"{sym}_trades"] = tb.get("trades", 0)
                    row[f"{sym}_win_rate"] = tb.get("win_rate", 0)
                writer.writerow(row)

        print(f"\n  Results saved to {csv_path}")

    # Summary
    print(f"\n{'='*80}")
    print("  SUMMARY — Is 5% monthly achievable?")
    print(f"{'='*80}")
    for r in all_results:
        pct = r["position_size_pct"]
        monthly = r["monthly_pnl_pct_est"]
        achievable = "YES" if monthly >= 5.0 else "NO"
        print(f"  {pct*100:3.0f}% position → {monthly:+.2f}% monthly est → 5% target: {achievable}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
