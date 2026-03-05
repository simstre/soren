#!/usr/bin/env python3
"""Portfolio backtest: run multi-ticker concurrent backtest sharing one account.

Iterates bar-by-bar across all tickers simultaneously using a single
ReplayBrokerAdapter (shared balance/positions). Enforces total exposure cap.

Uses TWS/IB for 90-day 1m historical data (falls back to yfinance if TWS
unavailable, capped at ~30 days). Tickers are selected by the percentile
scanner at runtime.

Usage:
    cd backend && python -m scripts.portfolio_backtest
"""

from __future__ import annotations

import asyncio
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.replay_engine import ReplayEngine, TIMEFRAME_MAP, _normalize_symbol
from app.engine.candle_cache import load_cached, save_cache, get_cache_range
from app.analysis.pipeline import run_analysis
from app.analysis.indicators import rsi as calc_rsi
from app.engine.signal_generator import (
    detect_rsi_divergence,
    generate_signals_rsi_divergence_pct,
    check_exit_rsi_divergence_pct,
    get_timeframe_params,
)
from app.engine.strategies import get_strategy, ScanConfig
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
TICKERS: list[str] = []  # populated by scanner at runtime
POSITION_SIZES = [0.50]  # v8 default
TIMEFRAME = "1m"
STRATEGY = "rsi_divergence_pct"  # v8
STARTING_BALANCE = 100_000.0
MAX_EXPOSURE_PCT = 1.00  # 100% total exposure cap
LOOKBACK_BARS = 200
DAYS = 90  # 90-day backtest window

# Date range
END_DATE = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
START_DATE = (datetime.now() - timedelta(days=DAYS)).strftime("%Y-%m-%d")

# TWS pacing
IB_HOST = "127.0.0.1"
IB_PORT = 7497
IB_CLIENT_ID = 10  # separate from live engine (clientId=1)
IB_CHUNK_DAYS = 30  # 30 days per request
IB_PACING_DELAY = 12  # seconds between requests (stay under 60/10min)

# Fallback tickers if scanner fails
FALLBACK_TICKERS = ["TSLA", "CEG", "PLTR", "RIVN"]

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "backtest_results"

# Load v8 strategy config
_strategy_def = get_strategy(STRATEGY)
SCAN_CONFIG: ScanConfig = _strategy_def.scan_config if _strategy_def else ScanConfig(
    max_hold_bars=390, cooldown_bars=60,
    use_pct_stops=True, stop_pct=0.02, target_pct=0.06,
)


async def run_scanner(top_n: int = 6) -> list[str]:
    """Run percentile scanner to pick tickers. Falls back to hardcoded list."""
    try:
        from app.scanner.percentile_scanner import run_percentile_scan
        results = await run_percentile_scan(top_n=top_n, scan_config=SCAN_CONFIG)
        if results:
            symbols = [r.symbol for r in results]
            logger.info("scanner_picked", tickers=symbols)
            return symbols
    except Exception as e:
        logger.warning("scanner_failed", error=str(e))
    logger.info("scanner_fallback", tickers=FALLBACK_TICKERS)
    return list(FALLBACK_TICKERS)


async def fetch_ib_historical_1m(
    symbols: list[str], start: str, end: str,
) -> dict[str, pd.DataFrame]:
    """Fetch 90-day 1m data from TWS in 30-day chunks with pacing.

    - Checks parquet cache first; skips fetch if cache covers the date range.
    - On IB connection failure: warns and returns empty dict (caller falls back
      to yfinance).
    """
    start_dt = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    result: dict[str, pd.DataFrame] = {}

    try:
        from ib_async import IB, Stock
    except ImportError:
        logger.warning("ib_async_not_installed", msg="falling back to yfinance")
        return result

    ib = IB()
    try:
        await ib.connectAsync(host=IB_HOST, port=IB_PORT, clientId=IB_CLIENT_ID)
        logger.info("ib_backtest_connected", host=IB_HOST, port=IB_PORT, clientId=IB_CLIENT_ID)
    except Exception as e:
        logger.warning("ib_connect_failed", error=str(e), msg="falling back to yfinance")
        return result

    try:
        for sym in symbols:
            # Check cache coverage
            cache_range = get_cache_range(sym, "1m")
            if cache_range:
                cached_start, cached_end = cache_range
                if cached_start <= start_dt.to_pydatetime().replace(tzinfo=cached_start.tzinfo) and \
                   cached_end >= end_dt.to_pydatetime().replace(tzinfo=cached_end.tzinfo) - timedelta(days=2):
                    cached_df = load_cached(sym, "1m")
                    if cached_df is not None and not cached_df.empty:
                        logger.info("ib_cache_hit", symbol=sym, bars=len(cached_df))
                        result[sym] = cached_df
                        continue

            # Build 30-day chunks working backwards from end
            contract = Stock(sym, "SMART", "USD")
            await ib.qualifyContractsAsync(contract)

            chunks: list[pd.DataFrame] = []
            chunk_end = end_dt
            chunk_num = 0

            while chunk_end > start_dt:
                chunk_num += 1
                duration_days = min(IB_CHUNK_DAYS, (chunk_end - start_dt).days + 1)
                if duration_days <= 0:
                    break

                end_str = chunk_end.strftime("%Y%m%d %H:%M:%S") + " US/Eastern"
                logger.info(
                    "ib_fetch_chunk",
                    symbol=sym, chunk=chunk_num,
                    duration=f"{duration_days} D", end=end_str,
                )

                retry = 0
                bars = None
                while retry < 2:
                    try:
                        bars = await ib.reqHistoricalDataAsync(
                            contract,
                            endDateTime=end_str,
                            durationStr=f"{duration_days} D",
                            barSizeSetting="1 min",
                            whatToShow="TRADES",
                            useRTH=True,
                            formatDate=2,
                        )
                        break
                    except Exception as e:
                        err_str = str(e).lower()
                        if "pacing" in err_str or "limit" in err_str:
                            retry += 1
                            logger.warning("ib_pacing_violation", symbol=sym, retry=retry)
                            await asyncio.sleep(30)
                        else:
                            logger.error("ib_fetch_error", symbol=sym, error=str(e))
                            break

                if bars:
                    records = []
                    for bar in bars:
                        records.append({
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                            "volume": bar.volume,
                        })
                    idx = pd.DatetimeIndex([bar.date for bar in bars], name="Datetime")
                    chunk_df = pd.DataFrame(records, index=idx)
                    chunks.append(chunk_df)
                    logger.info("ib_chunk_fetched", symbol=sym, chunk=chunk_num, bars=len(chunk_df))

                chunk_end -= timedelta(days=duration_days)

                # Pacing delay between requests
                if chunk_end > start_dt:
                    logger.info("ib_pacing_delay", seconds=IB_PACING_DELAY)
                    await asyncio.sleep(IB_PACING_DELAY)

            if chunks:
                merged = pd.concat(chunks)
                merged = merged[~merged.index.duplicated(keep="first")]
                merged = merged.sort_index()
                merged.columns = [c.lower() for c in merged.columns]

                # Save to cache
                save_cache(sym, "1m", merged)
                result[sym] = merged
                logger.info("ib_symbol_done", symbol=sym, total_bars=len(merged))
            else:
                logger.warning("ib_no_data", symbol=sym)

            # Pacing delay between symbols
            if sym != symbols[-1]:
                await asyncio.sleep(IB_PACING_DELAY)

    finally:
        if ib.isConnected():
            ib.disconnect()
            logger.info("ib_backtest_disconnected")

    return result


async def fetch_ticker_data(
    symbol: str, start: str, end: str, interval: str,
) -> pd.DataFrame:
    """Load data from parquet cache (populated by TWS fetch). Falls back to yfinance."""
    # Try cache first
    cached = load_cached(symbol, interval)
    if cached is not None and not cached.empty:
        # Filter to requested date range
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end)
        if cached.index.tz is not None:
            if start_ts.tzinfo is None:
                start_ts = start_ts.tz_localize(cached.index.tz)
            if end_ts.tzinfo is None:
                end_ts = end_ts.tz_localize(cached.index.tz)
        filtered = cached[(cached.index >= start_ts) & (cached.index <= end_ts)]
        if not filtered.empty:
            filtered.columns = [c.lower() for c in filtered.columns]
            return filtered

    # Fallback to yfinance (limited to ~30 days for 1m)
    logger.warning("cache_miss_yfinance_fallback", symbol=symbol)
    yf_symbol = _normalize_symbol(symbol)
    ticker = yf.Ticker(yf_symbol)
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
                cooldown_bars[symbol] = SCAN_CONFIG.cooldown_bars
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
    """Run analysis -> v8 signals -> risk -> order for one ticker at one bar."""
    try:
        analysis = run_analysis(symbol, df)

        signals = generate_signals_rsi_divergence_pct(
            analysis, df,
            stop_loss_pct=SCAN_CONFIG.stop_pct,
            best_divergence=best_divergence,
            current_timestamp=timestamp,
            tf_params=tf_params,
            scan_config=SCAN_CONFIG,
        )

        current_side = broker.get_position_side(symbol)
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        # Check for exit if we have a position
        if current_side is not None:
            entry_time = None
            entry_price = None
            for tr in reversed(trade_records):
                if tr["status"] == "OPEN" and tr["symbol"] == symbol:
                    try:
                        entry_time = datetime.fromisoformat(tr["entry_time"])
                    except (ValueError, TypeError):
                        pass
                    entry_price = tr.get("entry_price")
                    break

            current_time = timestamp if isinstance(timestamp, datetime) else None
            if current_time is None:
                try:
                    current_time = datetime.fromisoformat(str(timestamp))
                except (ValueError, TypeError):
                    pass

            exit_reason = check_exit_rsi_divergence_pct(
                analysis, df, current_side,
                entry_time=entry_time,
                current_time=current_time,
                tf_params=tf_params,
                entry_price=entry_price,
                scan_config=SCAN_CONFIG,
            )

            if exit_reason:
                close_price = float(df["close"].iloc[-1])
                closed_trade = broker.close_position(symbol, close_price, exit_reason=exit_reason)
                if closed_trade:
                    _close_trade_record(
                        closed_trade, ts_str, exit_reason,
                        trade_records, tracker,
                        cooldown_bars, symbol, tf_params,
                        per_ticker_pnl, per_ticker_trades, per_ticker_wins,
                    )
                    current_side = None
            else:
                # v8: no position-flip logic; only exit on max_hold or stop_loss
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
                cooldown_bars[symbol] = SCAN_CONFIG.cooldown_bars
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

    # Compute actual trading days from trade timeline
    trading_days = 1
    if closed:
        trade_times = []
        for t in closed:
            for key in ("entry_time", "exit_time"):
                ts = t.get(key)
                if ts:
                    try:
                        trade_times.append(pd.Timestamp(ts).date())
                    except Exception:
                        pass
        if trade_times:
            unique_days = len(set(trade_times))
            trading_days = max(unique_days, 1)
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
        "trading_days": trading_days,
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
    global TICKERS

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUTPUT_DIR / f"portfolio_{ts_str}.csv"

    # Step 1: Run scanner to pick tickers (skip if pre-set)
    print(f"\n{'='*80}")
    if not TICKERS:
        print("  STEP 1: Running percentile scanner...")
        print(f"{'='*80}")
        TICKERS = await run_scanner(top_n=6)
    else:
        print(f"  STEP 1: Using pre-set tickers (scanner skipped)")
        print(f"{'='*80}")
    print(f"  Selected tickers: {', '.join(TICKERS)}")

    # Step 2: Fetch 90-day 1m data from TWS
    print(f"\n{'='*80}")
    print(f"  STEP 2: Fetching {DAYS}-day 1m data from TWS...")
    print(f"  Pacing: {IB_CHUNK_DAYS}d chunks, {IB_PACING_DELAY}s delay between requests")
    print(f"  Estimated time: ~{len(TICKERS) * 3 * IB_PACING_DELAY / 60:.1f} min for {len(TICKERS)} tickers")
    print(f"{'='*80}")

    lookback_start = ReplayEngine._shift_date(START_DATE, TIMEFRAME, LOOKBACK_BARS)
    ib_data = await fetch_ib_historical_1m(TICKERS, lookback_start, END_DATE)

    if ib_data:
        print(f"  TWS: fetched data for {len(ib_data)} tickers")
        for sym, df in ib_data.items():
            span = (df.index.max() - df.index.min()).days if not df.empty else 0
            print(f"    {sym}: {len(df)} bars, {span} days")
    else:
        print("  TWS unavailable — will fall back to yfinance (~30 days max)")

    # Step 3: Run portfolio backtest
    print(f"\n{'='*80}")
    print(f"  STEP 3: Running v8 portfolio backtest")
    print(f"  Tickers: {', '.join(TICKERS)}")
    print(f"  Timeframe: {TIMEFRAME}  |  Period: {START_DATE} -> {END_DATE} ({DAYS}d)")
    print(f"  Strategy: {STRATEGY}  |  Exposure cap: {MAX_EXPOSURE_PCT*100:.0f}%")
    print(f"  Stop: {SCAN_CONFIG.stop_pct*100:.0f}%  |  Target: {SCAN_CONFIG.target_pct*100:.0f}%  |  Max hold: {SCAN_CONFIG.max_hold_bars} bars")
    print(f"{'='*80}\n")

    all_results: list[dict] = []

    for pos_size in POSITION_SIZES:
        print(f"\n--- Position size: {pos_size*100:.0f}% per ticker ---")
        result = await run_portfolio_backtest(pos_size)
        all_results.append(result)

        if "error" in result:
            print(f"  ERROR: {result['error']}")
            continue

        print(f"  Total PnL: ${result['total_pnl']:+,.2f} ({result['total_pnl_pct']:+.2f}%)")
        print(f"  Trading days: {result['trading_days']}  |  Monthly PnL est: {result['monthly_pnl_pct_est']:+.2f}%")
        print(f"  Win rate: {result['win_rate']:.1f}% ({result['winning_trades']}W / {result['losing_trades']}L)")
        print(f"  Max drawdown: ${result['max_drawdown']:,.2f} ({result['max_drawdown_pct']:.2f}%)")
        print(f"  Ending balance: ${result['ending_balance']:,.2f}")
        print(f"  Per-ticker breakdown:")
        for sym, tb in result.get("ticker_breakdown", {}).items():
            print(f"    {sym:6s}: PnL ${tb['pnl']:+8,.2f} ({tb['pnl_pct']:+.2f}%)  "
                  f"Trades: {tb['trades']}  Win: {tb['win_rate']:.0f}%")

    # Write CSV summary
    if all_results and "error" not in all_results[0]:
        fieldnames = [
            "position_size_pct", "total_pnl", "total_pnl_pct", "monthly_pnl_pct_est",
            "trading_days", "win_rate", "total_trades", "winning_trades", "losing_trades",
            "max_drawdown", "max_drawdown_pct", "starting_balance", "ending_balance",
        ]
        # Add per-ticker columns
        for sym in TICKERS:
            fieldnames.extend([f"{sym}_pnl", f"{sym}_pnl_pct", f"{sym}_trades", f"{sym}_win_rate"])

        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for r in all_results:
                if "error" in r:
                    continue
                row = {k: r.get(k, "") for k in fieldnames[:13]}
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
    print(f"  SUMMARY — v8 90-day backtest ({DAYS}d)")
    print(f"{'='*80}")
    for r in all_results:
        if "error" in r:
            continue
        pct = r["position_size_pct"]
        monthly = r["monthly_pnl_pct_est"]
        days = r["trading_days"]
        achievable = "YES" if monthly >= 5.0 else "NO"
        print(f"  {pct*100:3.0f}% position | {days} trading days | "
              f"{monthly:+.2f}% monthly est | 5% target: {achievable}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
