"""Main trading engine - orchestrates the full pipeline."""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
import structlog

from app.broker.base import BrokerAdapter
from app.analysis.pipeline import run_analysis
from app.engine.signal_generator import (
    TradingSignal,
    generate_signals,
    generate_signals_mean_reversion,
    generate_signals_vwap_bounce,
    generate_signals_vwap_revert,
    generate_signals_refined_rsi,
    generate_signals_rsi_divergence_pct,
    check_exit_rsi_divergence_pct,
    check_exit_vwap_revert,
    detect_rsi_divergence,
    get_timeframe_params,
)
from app.engine.vwap_revert import (
    VWAPRevertParams,
    compute_indicators as vwap_compute_indicators,
    check_entry as vwap_check_entry,
    check_exit as vwap_check_exit,
    compute_stops as vwap_compute_stops,
)
from app.engine.strategies import get_strategy, StrategyDef
from app.engine.divergence_tracker import (
    DivergenceTracker,
    DivergenceSignal,
    CANDIDATE_TIMEFRAMES,
)
from app.engine.risk_manager import validate_signal
from app.engine.state import EngineState
from app.api.ws.manager import ws_manager
from app.config import settings
from app.db.trade_repo import save_trade, update_trade, load_all_trades
from app.scanner.fitness import run_fitness_scan

logger = structlog.get_logger()

TIMEFRAME_MAP = {
    "1m": {"interval": "1m", "period": "1d"},
    "5m": {"interval": "5m", "period": "5d"},
    "15m": {"interval": "15m", "period": "5d"},
    "1h": {"interval": "1h", "period": "1mo"},
    "1D": {"interval": "1d", "period": "6mo"},
}

IB_WARMUP_DAYS = 2   # ~780 1m bars, well above 100-bar minimum for S/R
MAX_BUFFER_BARS = 500  # trim oldest bars to bound memory


class TradingEngine:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker
        self.state = EngineState()
        self._task: asyncio.Task | None = None
        self._rescan_task: asyncio.Task | None = None
        self._trade_records: list[dict] = []
        self._divergence_tracker: DivergenceTracker | None = None
        self._strategy_def: StrategyDef | None = None
        self._bar_buffers: dict[str, pd.DataFrame] = {}
        self._use_live_bars: bool = False

    async def start(self, tickers: list[str], timeframe: str | None = None, strategy: str = "refined_rsi", scan_tactic: str = "market_scan"):
        if self.state.running:
            return

        # Load historical trades from DB
        try:
            self._trade_records = await load_all_trades()
            logger.info("loaded_trade_history", count=len(self._trade_records))
        except Exception as e:
            logger.error("load_trades_failed", error=str(e))

        # Look up strategy definition for baked-in params
        self._strategy_def = get_strategy(strategy)

        tf = timeframe or (self._strategy_def.timeframe if self._strategy_def else settings.default_timeframe)
        self.state.start(tickers, timeframe=tf, strategy=strategy, scan_tactic=scan_tactic)

        # Set up divergence tracker for strategies that need it
        candidates = CANDIDATE_TIMEFRAMES.get(tf, [tf])
        self._divergence_tracker = DivergenceTracker(candidates)

        # Detect IB mode for live bar streaming
        self._use_live_bars = hasattr(self.broker, "_live_bar_subscriptions")
        if self._use_live_bars:
            await self._subscribe_all_tickers()

        self._task = asyncio.create_task(self._run_loop())
        self._rescan_task = asyncio.create_task(self._rescan_loop())
        await ws_manager.broadcast("engine_state", self.state.to_dict())
        logger.info("engine_started", tickers=tickers, timeframe=tf, strategy=strategy, live_bars=self._use_live_bars)

    async def stop(self):
        if not self.state.running:
            return

        # Stop the loop first so no new positions are opened
        self.state.stop()
        if self._rescan_task:
            self._rescan_task.cancel()
            try:
                await self._rescan_task
            except asyncio.CancelledError:
                pass
            self._rescan_task = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Unsubscribe live bar streams
        if self._use_live_bars:
            for symbol in list(self._bar_buffers.keys()):
                try:
                    await self.broker.unsubscribe_live_bars(symbol)
                except Exception as e:
                    logger.error("unsubscribe_live_bars_failed", symbol=symbol, error=str(e))
            self._bar_buffers.clear()

        # Close all open positions
        try:
            order_ids = await self.broker.close_all_positions()
            if order_ids:
                logger.info("positions_closed_on_stop", count=len(order_ids), order_ids=order_ids)
                for record in self._trade_records:
                    if record["status"] == "OPEN":
                        await self._close_trade_record(record, "engine_stop")
                await ws_manager.broadcast("positions_update", [])
        except Exception as e:
            logger.error("close_positions_on_stop_failed", error=str(e))

        await ws_manager.broadcast("engine_state", self.state.to_dict())
        logger.info("engine_stopped")

    def _seconds_until_next_rescan(self) -> float:
        """Calculate seconds until the next 9:10 AM ET, skipping weekends."""
        et = ZoneInfo("America/New_York")
        now = datetime.now(et)
        target = now.replace(hour=9, minute=10, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        # Skip weekends: Saturday=5, Sunday=6
        while target.weekday() >= 5:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    async def _rescan_loop(self):
        """Sleep until 9:10 AM ET each trading day, then rescan tickers."""
        try:
            while self.state.running:
                sleep_secs = self._seconds_until_next_rescan()
                logger.info("rescan_scheduled", sleep_seconds=round(sleep_secs), next_rescan_in_hours=round(sleep_secs / 3600, 1))
                await asyncio.sleep(sleep_secs)
                if not self.state.running:
                    break
                await self._run_rescan()
        except asyncio.CancelledError:
            logger.info("rescan_loop_cancelled")
            raise

    async def _run_rescan(self):
        """Run scanner + validation and swap active tickers, preserving open positions."""
        logger.info("rescan_starting", current_tickers=self.state.active_tickers, scan_tactic=self.state.scan_tactic)
        old_tickers = list(self.state.active_tickers)
        try:
            scan_config = self._strategy_def.scan_config if self._strategy_def else None
            use_pct = scan_config is not None and getattr(scan_config, "use_pct_stops", False)
            top_n = max(len(old_tickers), 4)
            scan_n = (top_n * 2 + 2) if use_pct else top_n

            if self.state.scan_tactic == "market_scan":
                from app.scanner.percentile_scanner import run_percentile_scan
                results = await run_percentile_scan(top_n=scan_n, scan_config=scan_config)
            else:
                results = await run_fitness_scan(top_n=scan_n, scan_config=scan_config)

            # Validation gate for pct-based strategies
            if use_pct and results:
                from app.scanner.backtest_scorer import validate_candidates
                candidate_symbols = [r.symbol for r in results]
                validations = await validate_candidates(
                    symbols=candidate_symbols,
                    days=14,
                    strategy=self.state.strategy,
                )
                passing_syms = [v.symbol for v in validations if v.passed]
                results = [r for r in results if r.symbol in passing_syms][:top_n]
                logger.info("rescan_validation", candidates=len(candidate_symbols), passed=len(passing_syms))

            new_tickers = [r.symbol for r in results]

            # Preserve tickers with open positions
            open_symbols = {r["symbol"] for r in self._trade_records if r["status"] == "OPEN"}
            for sym in open_symbols:
                if sym not in new_tickers:
                    new_tickers.append(sym)

            self.state.active_tickers = new_tickers
            self.state.last_rescan_at = datetime.now(timezone.utc)
            self.state.rescan_error = None  # Clear any previous error

            added = [t for t in new_tickers if t not in old_tickers]
            removed = [t for t in old_tickers if t not in new_tickers]

            # Update live bar subscriptions for ticker changes
            if self._use_live_bars:
                for sym in removed:
                    try:
                        await self.broker.unsubscribe_live_bars(sym)
                        self._bar_buffers.pop(sym, None)
                    except Exception as e:
                        logger.error("rescan_unsubscribe_failed", symbol=sym, error=str(e))
                for sym in added:
                    await self._subscribe_ticker(sym)

            logger.info("rescan_complete", new_tickers=new_tickers, added=added, removed=removed)

            await ws_manager.broadcast("ticker_rescan", {
                "old_tickers": old_tickers,
                "new_tickers": new_tickers,
                "added": added,
                "removed": removed,
            })
            await ws_manager.broadcast("engine_state", self.state.to_dict())
        except Exception as e:
            error_msg = f"Scanner failed: {e}"
            logger.error("rescan_failed", error=str(e))
            self.state.rescan_error = error_msg
            await ws_manager.broadcast("ticker_rescan", {
                "error": error_msg,
                "old_tickers": old_tickers,
            })
            await ws_manager.broadcast("engine_state", self.state.to_dict())

    async def _subscribe_all_tickers(self):
        """Subscribe all active tickers to TWS live bar streaming."""
        for symbol in self.state.active_tickers:
            await self._subscribe_ticker(symbol)

    async def _subscribe_ticker(self, symbol: str):
        """Subscribe a single ticker to TWS live bars and store warmup data."""
        try:
            warmup_df = await self.broker.subscribe_live_bars(
                symbol, self._on_live_bar, IB_WARMUP_DAYS,
            )
            if not warmup_df.empty:
                self._bar_buffers[symbol] = warmup_df.iloc[-MAX_BUFFER_BARS:]
            else:
                self._bar_buffers[symbol] = pd.DataFrame()
            logger.info("ticker_warmup_complete", symbol=symbol, bars=len(self._bar_buffers[symbol]))
        except Exception as e:
            self._bar_buffers[symbol] = pd.DataFrame()
            logger.error("ticker_subscribe_failed", symbol=symbol, error=str(e))

    async def _on_live_bar(self, bar_dict: dict):
        """Callback for each new 1-min bar from TWS streaming."""
        symbol = bar_dict.get("symbol")
        if not symbol:
            return

        # Append new row to buffer
        ts = bar_dict.get("timestamp", "")
        try:
            idx = pd.DatetimeIndex([pd.Timestamp(ts)], name="Datetime")
        except Exception:
            idx = pd.DatetimeIndex([pd.Timestamp.now(tz="UTC")], name="Datetime")
        new_row = pd.DataFrame(
            [{
                "open": bar_dict["open"],
                "high": bar_dict["high"],
                "low": bar_dict["low"],
                "close": bar_dict["close"],
                "volume": bar_dict["volume"],
            }],
            index=idx,
        )

        if symbol in self._bar_buffers and not self._bar_buffers[symbol].empty:
            self._bar_buffers[symbol] = pd.concat(
                [self._bar_buffers[symbol], new_row],
            ).iloc[-MAX_BUFFER_BARS:]
        else:
            self._bar_buffers[symbol] = new_row

        # Broadcast candle to frontend (real-time)
        candle_data = {
            "symbol": symbol,
            "timestamp": ts,
            "open": round(float(bar_dict["open"]), 2),
            "high": round(float(bar_dict["high"]), 2),
            "low": round(float(bar_dict["low"]), 2),
            "close": round(float(bar_dict["close"]), 2),
            "volume": int(bar_dict["volume"]),
        }
        await ws_manager.broadcast("candle_update", candle_data)

    async def _run_loop(self):
        """Main trading loop - collect signals from all tickers, rank, execute best."""
        timeframe = self.state.timeframe
        tf_config = TIMEFRAME_MAP.get(timeframe, TIMEFRAME_MAP["5m"])

        interval_seconds = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "1h": 3600,
            "1D": 86400,
        }.get(timeframe, 300)

        try:
            while self.state.running:
                # Phase 0: Enforce max_hold — close positions held too long
                if self.state.strategy in ("rsi_divergence_pct", "vwap_revert"):
                    await self._enforce_max_hold()

                # Phase 1: Collect signals from all tickers
                # Skip new entries if rescan failed — only manage existing positions
                if self.state.rescan_error:
                    logger.warning("entries_paused", reason="rescan_error", error=self.state.rescan_error)
                else:
                    all_signals: list[TradingSignal] = []
                    for symbol in self.state.active_tickers:
                        try:
                            signals = await self._collect_signals(symbol, tf_config)
                            all_signals.extend(signals)
                        except Exception as e:
                            logger.error("symbol_processing_error", symbol=symbol, error=str(e))

                    # Phase 2+3: Rank and execute
                    if all_signals:
                        await self._rank_and_execute(all_signals)

                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            logger.info("engine_loop_cancelled")
            raise

    async def _collect_signals(self, symbol: str, tf_config: dict) -> list[TradingSignal]:
        """Fetch data, run analysis, generate signals via strategy dispatch."""
        # 1. Fetch candle data — IB live buffer or yfinance fallback
        if self._use_live_bars and symbol in self._bar_buffers and len(self._bar_buffers[symbol]) >= 34:
            df = self._bar_buffers[symbol].copy()
            logger.debug("using_live_buffer", symbol=symbol, bars=len(df))
        else:
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=tf_config["period"], interval=tf_config["interval"])
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
            if self._use_live_bars:
                logger.debug("live_buffer_fallback_yfinance", symbol=symbol,
                             buffer_len=len(self._bar_buffers.get(symbol, [])))

        if df.empty:
            logger.warning("no_data", symbol=symbol)
            return []

        # 2. Broadcast latest candle (skip in IB mode — _on_live_bar already broadcasts)
        if not self._use_live_bars:
            last_row = df.iloc[-1]
            candle_data = {
                "symbol": symbol,
                "timestamp": df.index[-1].isoformat(),
                "open": round(float(last_row["open"]), 2),
                "high": round(float(last_row["high"]), 2),
                "low": round(float(last_row["low"]), 2),
                "close": round(float(last_row["close"]), 2),
                "volume": int(last_row["volume"]),
            }
            await ws_manager.broadcast("candle_update", candle_data)

        # 3. Run analysis
        analysis = run_analysis(symbol, df)
        await ws_manager.broadcast("signal_fired", {
            "symbol": symbol,
            "analysis": analysis.to_dict(),
        })

        # 4. Strategy dispatch
        strategy = self.state.strategy
        sdef = self._strategy_def
        stop_loss_pct = sdef.stop_loss_pct if sdef else (settings.stop_loss_pct or None)

        if strategy == "rsi_divergence_pct":
            best_div = self._get_divergence(analysis, df)
            tf_params = get_timeframe_params(self.state.timeframe)
            timestamp = df.index[-1] if len(df) > 0 else None
            signals = generate_signals_rsi_divergence_pct(
                analysis, df,
                stop_loss_pct=stop_loss_pct,
                best_divergence=best_div,
                current_timestamp=timestamp,
                tf_params=tf_params,
                scan_config=sdef.scan_config if sdef else None,
            )
        elif strategy == "refined_rsi":
            best_div = self._get_divergence(analysis, df)
            tf_params = get_timeframe_params(self.state.timeframe)
            signals = generate_signals_refined_rsi(
                analysis, df,
                stop_loss_pct=stop_loss_pct,
                best_divergence=best_div,
                tf_params=tf_params,
            )
        elif strategy == "rsi_divergence_sr":
            best_div = self._get_divergence(analysis, df)
            signals = generate_signals(analysis, stop_loss_pct=stop_loss_pct, best_divergence=best_div)
        elif strategy == "vwap_revert":
            vwap_params = VWAPRevertParams()
            ind = vwap_compute_indicators(df)
            bar = ind.n_bars - 1
            result = vwap_check_entry(ind, bar, vwap_params)
            if result:
                side, price = result
                stop, target = vwap_compute_stops(price, side, vwap_params)
                vwap_val = ind.vwap_daily[bar]
                vwap_s = ind.vwap_std[bar]
                deviation = (price - vwap_val) / vwap_s if vwap_s > 0 else 0
                rsi_val = ind.rsi_14[bar]
                dev_strength = min(abs(deviation) / 3.0, 1.0)
                rsi_extremity = abs(rsi_val - 50) / 50
                confidence = min(0.90, 0.40 + dev_strength * 0.25 + rsi_extremity * 0.25)
                reason = (
                    f"VWAP revert {side}: price ${price:.2f} is {deviation:+.1f}σ from "
                    f"VWAP ${vwap_val:.2f}, RSI {rsi_val:.0f} "
                    f"[SL={vwap_params.stop_pct*100:.1f}% T=VWAP]"
                )
                signals = [TradingSignal(
                    symbol=symbol,
                    signal_type=side,
                    entry_price=price,
                    stop_loss=stop,
                    profit_target=target,
                    reason=reason,
                    pattern_type="vwap_revert",
                    confidence=confidence,
                    created_at=datetime.now(timezone.utc),
                    entry_bar_idx=bar,
                )]
            else:
                signals = []
        elif strategy == "vwap_bounce":
            signals = generate_signals_vwap_bounce(analysis, df, stop_loss_pct=stop_loss_pct)
        elif strategy == "mean_reversion":
            signals = generate_signals_mean_reversion(analysis, df, stop_loss_pct=stop_loss_pct)
        else:
            logger.warning("unknown_strategy", strategy=strategy, fallback="refined_rsi")
            best_div = self._get_divergence(analysis, df)
            tf_params = get_timeframe_params(self.state.timeframe)
            signals = generate_signals_refined_rsi(
                analysis, df,
                stop_loss_pct=stop_loss_pct,
                best_divergence=best_div,
                tf_params=tf_params,
            )

        return signals

    async def _close_trade_record(self, record: dict, exit_reason: str):
        """Fill exit_price, pnl, pnl_pct on a trade record being closed."""
        now = datetime.now(timezone.utc).isoformat()
        record["status"] = "CLOSED"
        record["exit_time"] = now
        record["exit_reason"] = exit_reason
        record["updated_at"] = now

        # Fetch current price for P&L calculation
        try:
            exit_price = await self.broker.get_current_price(record["symbol"])
            record["exit_price"] = round(exit_price, 2)
            entry = record["entry_price"]
            qty = record["quantity"]
            if record["side"] == "BUY":
                pnl = round((exit_price - entry) * qty, 2)
            else:
                pnl = round((entry - exit_price) * qty, 2)
            pnl_pct = round(pnl / (entry * qty) * 100, 2) if entry else 0.0
            record["pnl"] = pnl
            record["pnl_pct"] = pnl_pct
        except Exception as e:
            logger.error("exit_price_fetch_failed", symbol=record["symbol"], error=str(e))

        # Persist to DB
        if record.get("db_id"):
            try:
                await update_trade(record["db_id"], {
                    "status": record["status"],
                    "exit_time": record["exit_time"],
                    "exit_reason": exit_reason,
                    "exit_price": record.get("exit_price"),
                    "pnl": record.get("pnl"),
                    "pnl_pct": record.get("pnl_pct"),
                })
            except Exception as e:
                logger.error("update_trade_failed", db_id=record["db_id"], error=str(e))

    async def _enforce_max_hold(self):
        """Close positions that have exceeded max hold time / VWAP target."""
        if self.state.strategy == "vwap_revert":
            await self._enforce_vwap_revert_exits()
            return

        sdef = self._strategy_def
        max_hold = sdef.scan_config.max_hold_bars if sdef else 390
        now = datetime.now(timezone.utc)
        for record in self._trade_records:
            if record["status"] != "OPEN":
                continue
            entry_time = record.get("entry_time")
            if not entry_time:
                continue
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            held_minutes = (now - entry_time).total_seconds() / 60
            if held_minutes >= max_hold:
                try:
                    close_oid = await self.broker.close_position(record["symbol"])
                    await self._close_trade_record(record, "max_hold")
                    logger.info("max_hold_exit", symbol=record["symbol"], held_minutes=round(held_minutes), pnl=record.get("pnl"), close_order_id=close_oid)
                    await ws_manager.broadcast("trade_closed", record)
                except Exception as e:
                    logger.error("max_hold_close_failed", symbol=record["symbol"], error=str(e))

    async def _enforce_vwap_revert_exits(self):
        """Check VWAP revert exit conditions (max_hold + vwap_target) for open positions."""
        vwap_params = VWAPRevertParams()
        for record in self._trade_records:
            if record["status"] != "OPEN":
                continue
            sym = record["symbol"]
            df = self._bar_buffers.get(sym)
            if df is None or df.empty:
                continue
            ind = vwap_compute_indicators(df)
            entry_bar = record.get("entry_bar_idx", 0)
            exit_reason = vwap_check_exit(ind, ind.n_bars - 1, record["side"], entry_bar, vwap_params)
            if exit_reason:
                try:
                    close_oid = await self.broker.close_position(sym)
                    await self._close_trade_record(record, exit_reason)
                    logger.info("vwap_revert_exit", symbol=sym, reason=exit_reason, pnl=record.get("pnl"), close_order_id=close_oid)
                    await ws_manager.broadcast("trade_closed", record)
                except Exception as e:
                    logger.error("vwap_revert_exit_failed", symbol=sym, error=str(e))

    def _get_divergence(self, analysis, df: pd.DataFrame) -> DivergenceSignal | None:
        """Detect RSI divergence and return best signal via tracker."""
        if len(analysis.rsi_values) < 20 or len(df) < 20:
            return None

        prices = df["close"].tolist()
        div = detect_rsi_divergence(analysis.rsi_values, prices)
        if div is None:
            return None

        tf = self.state.timeframe
        tracker = self._divergence_tracker
        if tracker is None:
            return None

        weights = tracker.get_weights()
        weight = weights.get(tf, 1.0)

        div_signal = DivergenceSignal(
            timeframe=tf,
            direction=div.direction,
            rsi_current=div.rsi_current,
            description=div.description,
            weight=weight,
            rsi_spread=div.rsi_spread,
        )

        return tracker.get_best_divergence({tf: div_signal})

    async def _rank_and_execute(self, all_signals: list[TradingSignal]):
        """Sort signals by confidence descending, greedily fill best-to-worst."""
        # Rank by confidence
        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        # Get account state once
        balance = await self.broker.get_account_balance()
        buying_power = await self.broker.get_buying_power()
        positions = await self.broker.get_positions()
        open_symbols = [p.symbol for p in positions]
        total_exposure = sum(p.market_value for p in positions)

        for signal in all_signals:
            logger.info(
                "signal_generated",
                symbol=signal.symbol,
                type=signal.signal_type,
                reason=signal.reason,
                confidence=signal.confidence,
            )

            # Risk validation with position size from strategy (fallback to settings)
            sdef = self._strategy_def
            pos_pct = sdef.position_size_pct if sdef else settings.max_position_pct
            exp_pct = sdef.max_exposure_pct if sdef else settings.max_exposure_pct
            decision = validate_signal(
                signal, balance, buying_power, open_symbols, total_exposure,
                position_size_pct=pos_pct,
                max_exposure_pct=exp_pct,
            )

            if not decision.approved:
                logger.info("signal_rejected", symbol=signal.symbol, reason=decision.reason)
                continue

            # Place order
            try:
                order_id = await self.broker.place_order(
                    symbol=signal.symbol,
                    side=signal.signal_type,
                    quantity=decision.quantity,
                    order_type="LIMIT",
                    limit_price=signal.entry_price,
                    stop_loss=signal.stop_loss,
                    profit_target=signal.profit_target,
                )

                # Update running totals so next signal sees accurate budget
                fill_value = decision.quantity * signal.entry_price
                total_exposure += fill_value
                buying_power -= fill_value
                open_symbols.append(signal.symbol)

                trade_record = {
                    "id": len(self._trade_records) + 1,
                    "symbol": signal.symbol,
                    "side": signal.signal_type,
                    "quantity": decision.quantity,
                    "entry_price": signal.entry_price,
                    "exit_price": None,
                    "entry_time": datetime.now(timezone.utc).isoformat(),
                    "exit_time": None,
                    "stop_loss": signal.stop_loss,
                    "profit_target": signal.profit_target,
                    "pnl": None,
                    "pnl_pct": None,
                    "reason": signal.reason,
                    "status": "OPEN",
                    "ib_order_id": order_id,
                    "signal_id": None,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }

                # Add strategy metadata
                trade_record["strategy"] = self.state.strategy
                trade_record["divergence_timeframe"] = self.state.timeframe
                # Store bar index for vwap_revert bar-based exit tracking
                if signal.entry_bar_idx is not None:
                    trade_record["entry_bar_idx"] = signal.entry_bar_idx

                self._trade_records.append(trade_record)

                # Persist to DB
                try:
                    db_id = await save_trade(trade_record)
                    trade_record["db_id"] = db_id
                except Exception as e:
                    logger.error("save_trade_failed", symbol=signal.symbol, error=str(e))

                await ws_manager.broadcast("trade_opened", trade_record)

                logger.info(
                    "order_placed",
                    order_id=order_id,
                    symbol=signal.symbol,
                    side=signal.signal_type,
                    qty=decision.quantity,
                    entry=signal.entry_price,
                    stop=signal.stop_loss,
                    target=signal.profit_target,
                    reason=signal.reason,
                )

            except Exception as e:
                logger.error("order_failed", symbol=signal.symbol, error=str(e))

    def get_trade_records(self) -> list[dict]:
        return self._trade_records


# Global engine instance (set during app lifespan)
_engine: TradingEngine | None = None


def get_engine() -> TradingEngine | None:
    return _engine


def set_engine(engine: TradingEngine):
    global _engine
    _engine = engine
