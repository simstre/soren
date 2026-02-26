"""Main trading engine - orchestrates the full pipeline."""

import asyncio
from datetime import datetime, timezone

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
    generate_signals_refined_rsi,
    generate_signals_refined_rsi_v8,
    check_exit_refined_rsi_v8,
    detect_rsi_divergence,
    get_timeframe_params,
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

logger = structlog.get_logger()

TIMEFRAME_MAP = {
    "1m": {"interval": "1m", "period": "1d"},
    "5m": {"interval": "5m", "period": "5d"},
    "15m": {"interval": "15m", "period": "5d"},
    "1h": {"interval": "1h", "period": "1mo"},
    "1D": {"interval": "1d", "period": "6mo"},
}


class TradingEngine:
    def __init__(self, broker: BrokerAdapter):
        self.broker = broker
        self.state = EngineState()
        self._task: asyncio.Task | None = None
        self._trade_records: list[dict] = []
        self._divergence_tracker: DivergenceTracker | None = None
        self._strategy_def: StrategyDef | None = None

    async def start(self, tickers: list[str], timeframe: str | None = None, strategy: str = "refined_rsi"):
        if self.state.running:
            return

        # Look up strategy definition for baked-in params
        self._strategy_def = get_strategy(strategy)

        tf = timeframe or (self._strategy_def.timeframe if self._strategy_def else settings.default_timeframe)
        self.state.start(tickers, timeframe=tf, strategy=strategy)

        # Set up divergence tracker for strategies that need it
        candidates = CANDIDATE_TIMEFRAMES.get(tf, [tf])
        self._divergence_tracker = DivergenceTracker(candidates)

        self._task = asyncio.create_task(self._run_loop())
        await ws_manager.broadcast("engine_state", self.state.to_dict())
        logger.info("engine_started", tickers=tickers, timeframe=tf, strategy=strategy)

    async def stop(self):
        if not self.state.running:
            return

        # Stop the loop first so no new positions are opened
        self.state.stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Close all open positions
        try:
            order_ids = await self.broker.close_all_positions()
            if order_ids:
                logger.info("positions_closed_on_stop", count=len(order_ids), order_ids=order_ids)
                now = datetime.now(timezone.utc).isoformat()
                for record in self._trade_records:
                    if record["status"] == "OPEN":
                        record["status"] = "CLOSED"
                        record["exit_time"] = now
                        record["updated_at"] = now
                await ws_manager.broadcast("positions_update", [])
        except Exception as e:
            logger.error("close_positions_on_stop_failed", error=str(e))

        await ws_manager.broadcast("engine_state", self.state.to_dict())
        logger.info("engine_stopped")

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
                # Phase 0: Enforce max_hold for v8 — close positions held too long
                if self.state.strategy == "refined_rsi_v8":
                    await self._enforce_max_hold()

                # Phase 1: Collect signals from all tickers
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
        # 1. Fetch candle data
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=tf_config["period"], interval=tf_config["interval"])

        if df.empty:
            logger.warning("no_data", symbol=symbol)
            return []

        df.columns = [c.lower() for c in df.columns]

        # 2. Broadcast latest candle
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

        if strategy == "refined_rsi_v8":
            best_div = self._get_divergence(analysis, df)
            tf_params = get_timeframe_params(self.state.timeframe)
            timestamp = df.index[-1] if len(df) > 0 else None
            signals = generate_signals_refined_rsi_v8(
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

    async def _enforce_max_hold(self):
        """Close positions that have exceeded v8's max hold time."""
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
                    record["status"] = "CLOSED"
                    record["exit_time"] = now.isoformat()
                    record["exit_reason"] = "max_hold"
                    record["updated_at"] = now.isoformat()
                    logger.info("max_hold_exit", symbol=record["symbol"], held_minutes=round(held_minutes), close_order_id=close_oid)
                    await ws_manager.broadcast("trade_closed", record)
                except Exception as e:
                    logger.error("max_hold_close_failed", symbol=record["symbol"], error=str(e))

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

                self._trade_records.append(trade_record)
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
