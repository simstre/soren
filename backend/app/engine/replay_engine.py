"""Replay engine — feeds historical candles bar-by-bar through the trading pipeline."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
import structlog

from app.broker.replay_adapter import ReplayBrokerAdapter
from app.engine.candle_cache import load_cached, save_cache
from app.analysis.pipeline import run_analysis
from app.analysis.indicators import rsi as calc_rsi
from app.engine.signal_generator import (
    TradingSignal,
    generate_signals,
    check_reversal_signal,
    detect_rsi_divergence,
    generate_signals_mean_reversion,
    generate_signals_momentum_breakout,
    generate_signals_vwap_bounce,
    generate_signals_exhaustion_reversal,
    generate_signals_refined_rsi,
    generate_signals_refined_rsi_v2,
    check_exit_mean_reversion,
    check_exit_momentum,
    check_exit_vwap,
    check_exit_exhaustion,
    check_exit_refined_rsi,
    TimeframeParams,
    get_timeframe_params,
)
from app.engine.divergence_tracker import (
    CANDIDATE_TIMEFRAMES,
    DivergenceSignal,
    DivergenceTracker,
)
from app.engine.risk_manager import validate_signal
from app.api.ws.manager import ws_manager

logger = structlog.get_logger()

TIMEFRAME_MAP = {
    "1m": "1m",
    "2m": "2m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "1D": "1d",
}

# Common quote currencies for crypto pairs (e.g. ETHUSD → ETH-USD)
_CRYPTO_QUOTES = ("USD", "EUR", "GBP", "JPY", "BTC", "ETH", "USDT")


def _normalize_symbol(symbol: str) -> str:
    """Convert symbols like ETHUSD → ETH-USD for yfinance compatibility."""
    s = symbol.upper()
    if "-" in s:
        return s  # already has hyphen
    for quote in _CRYPTO_QUOTES:
        if s.endswith(quote) and len(s) > len(quote):
            return f"{s[:-len(quote)]}-{quote}"
    return s

# Base delay per bar in seconds (at 1x speed)
BASE_DELAY = 1.0


@dataclass
class ReplayResults:
    trades: list[dict] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_pct: float = 0.0
    win_rate: float = 0.0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    long_pnl: float = 0.0
    short_pnl: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    divergence_weights: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "trades": self.trades,
            "equity_curve": self.equity_curve,
            "total_pnl": self.total_pnl,
            "total_pnl_pct": self.total_pnl_pct,
            "win_rate": self.win_rate,
            "long_win_rate": self.long_win_rate,
            "short_win_rate": self.short_win_rate,
            "long_pnl": self.long_pnl,
            "short_pnl": self.short_pnl,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "max_drawdown": self.max_drawdown,
            "max_drawdown_pct": self.max_drawdown_pct,
            "starting_balance": self.starting_balance,
            "ending_balance": self.ending_balance,
            "divergence_weights": self.divergence_weights,
        }


class ReplayEngine:
    def __init__(self, broker: ReplayBrokerAdapter):
        self.broker = broker
        self._task: asyncio.Task | None = None
        self._running = False
        self._paused = False
        self._speed = 1.0
        self._bar_index = 0
        self._total_bars = 0
        self._symbols: list[str] = []
        self._symbol = ""  # primary symbol for display
        self._timeframe = ""
        self._trade_records: list[dict] = []
        self._cooldown_bars: dict[str, int] = {}  # per-symbol cooldown
        self._results: ReplayResults | None = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # Not paused initially
        self._trackers: dict[str, DivergenceTracker] = {}  # per-symbol tracker
        self._div_dataframes: dict[str, dict[str, pd.DataFrame]] = {}  # {symbol: {tf: df}}
        self._symbol_stats: dict[str, dict] = {}  # per-symbol {wins, losses}
        self._gated_symbols: set[str] = set()  # symbols disabled by performance gate
        self._tf_params: TimeframeParams | None = None
        self._headless: bool = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    async def start(
        self,
        symbols: list[str] | None = None,
        symbol: str | None = None,
        timeframe: str = "5m",
        start_date: str = "",
        end_date: str = "",
        speed: float = 1.0,
        stop_loss_pct: float = 0.02,
        position_size_pct: float = 0.05,
        strategy: str = "rsi_divergence_sr",
        headless: bool = False,
    ) -> None:
        if self._running:
            await self.stop()

        # Resolve symbols
        if symbols:
            self._symbols = [s.upper() for s in symbols]
        elif symbol:
            self._symbols = [symbol.upper()]
        else:
            self._symbols = ["SPY"]

        self._symbol = self._symbols[0]  # primary for display
        self._timeframe = timeframe
        self._speed = speed
        self._stop_loss_pct = stop_loss_pct
        self._position_size_pct = position_size_pct
        self._strategy = strategy
        self._bar_index = 0
        self._trade_records = []
        self._results = None
        self._paused = False
        self._pause_event.set()
        self._headless = headless
        self._cooldown_bars = {}
        self._symbol_stats = {}
        self._gated_symbols = set()

        # Compute timeframe-adaptive params for refined_rsi
        self._tf_params = get_timeframe_params(timeframe)

        # Reset broker state
        self.broker.reset()

        # Fetch data for all symbols
        interval = TIMEFRAME_MAP.get(timeframe, "5m")
        lookback_bars = self._lookback_bars(timeframe)
        prefetch_start = self._shift_date(start_date, timeframe, lookback_bars)

        # {symbol: full_df} — all primary data
        symbol_dataframes: dict[str, pd.DataFrame] = {}
        # {symbol: trade_start_idx} — where backtest starts per symbol
        symbol_trade_starts: dict[str, int] = {}

        for sym in self._symbols:
            yf_symbol = _normalize_symbol(sym)
            ticker = yf.Ticker(yf_symbol)

            df = await self._fetch_chunked(ticker, prefetch_start, end_date, interval, sym)

            if df.empty:
                max_age = self._YF_MAX_AGE.get(interval, 59)
                earliest = (datetime.now() - timedelta(days=max_age)).strftime("%Y-%m-%d")
                logger.warning(
                    "replay_no_data_for_symbol",
                    symbol=sym,
                    interval=interval,
                    start=start_date,
                    end=end_date,
                    earliest=earliest,
                )
                continue

            df.columns = [c.lower() for c in df.columns]

            # Find where the actual backtest period starts
            start_ts = pd.Timestamp(start_date)
            if start_ts.tzinfo is None and df.index.tz is not None:
                start_ts = start_ts.tz_localize(df.index.tz)
            trade_start_idx = df.index.searchsorted(start_ts)
            trade_start_idx = max(trade_start_idx, 0)

            symbol_dataframes[sym] = df
            symbol_trade_starts[sym] = trade_start_idx

        if not symbol_dataframes:
            raise ValueError(
                f"No historical data for any of {self._symbols} ({interval}) "
                f"from {start_date} to {end_date}."
            )

        # Fetch divergence data per symbol (RSI strategies only)
        self._div_dataframes = {}
        self._trackers = {}

        if self._strategy in ("rsi_divergence_sr", "refined_rsi"):
            candidates = CANDIDATE_TIMEFRAMES.get(timeframe, [timeframe])
            active_tfs = list(CANDIDATE_TIMEFRAMES.get(timeframe, [timeframe]))
            for sym in symbol_dataframes:
                self._div_dataframes[sym] = {}
                self._trackers[sym] = DivergenceTracker(active_tfs)
                yf_symbol = _normalize_symbol(sym)
                ticker = yf.Ticker(yf_symbol)
                for tf in candidates:
                    tf_interval = TIMEFRAME_MAP.get(tf, tf)
                    if tf_interval == interval:
                        self._div_dataframes[sym][tf] = symbol_dataframes[sym]
                        continue
                    try:
                        tf_df = await self._fetch_chunked(
                            ticker, prefetch_start, end_date, tf_interval, sym,
                        )
                        if not tf_df.empty:
                            tf_df.columns = [c.lower() for c in tf_df.columns]
                            self._div_dataframes[sym][tf] = tf_df
                    except Exception as e:
                        logger.warning("replay_div_fetch_error", symbol=sym, timeframe=tf, error=str(e))

                if not self._div_dataframes[sym]:
                    self._div_dataframes[sym][timeframe] = symbol_dataframes[sym]

        # Build unified timeline from all symbols' trade periods
        all_timestamps: set = set()
        for sym, df in symbol_dataframes.items():
            trade_start = symbol_trade_starts[sym]
            all_timestamps.update(df.index[trade_start:].tolist())

        unified_timeline = sorted(all_timestamps)
        self._total_bars = len(unified_timeline)

        logger.info(
            "replay_data_fetched",
            symbols=list(symbol_dataframes.keys()),
            total_bars=self._total_bars,
            symbols_count=len(symbol_dataframes),
        )

        self._running = True
        self._task = asyncio.create_task(
            self._run_loop(symbol_dataframes, symbol_trade_starts, unified_timeline)
        )

        if not self._headless:
            await self._broadcast_state()
        logger.info(
            "replay_started",
            symbols=self._symbols,
            timeframe=timeframe,
            bars=self._total_bars,
            speed=speed,
        )

    async def stop(self) -> None:
        if not self._running:
            return

        self._running = False
        self._paused = False
        self._pause_event.set()

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self._results = self._build_results()

        await self._broadcast_state()
        logger.info("replay_stopped", bar=self._bar_index, total=self._total_bars)

    async def pause(self) -> None:
        if self._running and not self._paused:
            self._paused = True
            self._pause_event.clear()
            await self._broadcast_state()
            logger.info("replay_paused", bar=self._bar_index)

    async def resume(self) -> None:
        if self._running and self._paused:
            self._paused = False
            self._pause_event.set()
            await self._broadcast_state()
            logger.info("replay_resumed", bar=self._bar_index)

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, min(speed, 100.0))
        logger.info("replay_speed_changed", speed=self._speed)

    # yfinance per-request limits (calendar days) by granularity
    _YF_MAX_DAYS = {"1m": 7, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "1h": 729, "1d": 100_000}
    # yfinance maximum historical data age (calendar days)
    _YF_MAX_AGE = {"1m": 30, "2m": 59, "5m": 59, "15m": 59, "30m": 59, "1h": 729, "1d": 100_000}

    @staticmethod
    def _lookback_bars(timeframe: str) -> int:
        return {
            "1m": 200,
            "5m": 200,
            "15m": 200,
            "1h": 200,
            "1D": 200,
        }.get(timeframe, 200)

    @staticmethod
    def _shift_date(date_str: str, timeframe: str, bars: int) -> str:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        minutes_per_bar = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1D": 1440}.get(timeframe, 5)
        trading_minutes = bars * minutes_per_bar
        trading_days = max(1, trading_minutes / 390)
        calendar_days = int(trading_days * 1.5) + 5
        shifted = dt - timedelta(days=calendar_days)
        return shifted.strftime("%Y-%m-%d")

    @classmethod
    async def _fetch_chunked(
        cls,
        ticker: yf.Ticker,
        start: str,
        end: str,
        interval: str,
        symbol: str,
    ) -> pd.DataFrame:
        """Fetch historical data in chunks that respect yfinance limits.

        Uses a disk cache to avoid re-fetching data that hasn't changed.
        Only fetches the missing date ranges from yfinance.
        """
        max_chunk_days = cls._YF_MAX_DAYS.get(interval, 59)
        max_age_days = cls._YF_MAX_AGE.get(interval, 59)

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")

        # Clamp start to the oldest date yfinance can serve
        earliest_allowed = datetime.now() - timedelta(days=max_age_days)
        if start_dt < earliest_allowed:
            start_dt = earliest_allowed
            logger.info(
                "replay_clamped_start",
                symbol=symbol,
                interval=interval,
                original_start=start,
                clamped_start=start_dt.strftime("%Y-%m-%d"),
                max_age_days=max_age_days,
            )

        if start_dt >= end_dt:
            return pd.DataFrame()

        # --- Cache lookup ---
        cached_df = load_cached(symbol, interval)
        if cached_df is not None and not cached_df.empty:
            # Make start/end tz-aware to match cache index if needed
            s_ts = pd.Timestamp(start_dt)
            e_ts = pd.Timestamp(end_dt)
            if s_ts.tzinfo is None and cached_df.index.tz is not None:
                s_ts = s_ts.tz_localize(cached_df.index.tz)
                e_ts = e_ts.tz_localize(cached_df.index.tz)

            cache_min = cached_df.index.min()
            cache_max = cached_df.index.max()

            cache_min_date = cache_min.normalize()
            cache_max_date = cache_max.normalize()
            s_date = s_ts.normalize()
            e_date = (e_ts - pd.Timedelta(days=1)).normalize()

            if cache_min_date <= s_date and cache_max_date >= e_date:
                logger.info(
                    "candle_cache_hit",
                    symbol=symbol,
                    interval=interval,
                    rows=len(cached_df),
                )
                return cached_df.loc[s_ts:e_ts]

            # Determine which gaps need fetching
            gaps: list[tuple[datetime, datetime]] = []
            if s_date < cache_min_date:
                gaps.append((start_dt, cache_min.to_pydatetime().replace(tzinfo=None)))
            if e_date > cache_max_date:
                gaps.append((cache_max.to_pydatetime().replace(tzinfo=None), end_dt))

            if gaps:
                logger.info(
                    "candle_cache_partial",
                    symbol=symbol,
                    interval=interval,
                    gaps=len(gaps),
                    cached_rows=len(cached_df),
                )
        else:
            cached_df = None
            gaps = [(start_dt, end_dt)]

        # --- Fetch missing ranges from yfinance ---
        fetched_chunks: list[pd.DataFrame] = []
        for gap_start, gap_end in gaps:
            chunks = await cls._fetch_range(
                ticker, gap_start, gap_end, interval, symbol, max_chunk_days,
            )
            fetched_chunks.extend(chunks)

        # --- Merge with cache and save ---
        all_parts: list[pd.DataFrame] = []
        if cached_df is not None:
            all_parts.append(cached_df)
        all_parts.extend(fetched_chunks)

        if not all_parts:
            return pd.DataFrame()

        combined = pd.concat(all_parts)
        combined = combined[~combined.index.duplicated(keep="first")].sort_index()

        # Save the full merged dataset back to cache
        save_cache(symbol, interval, combined)

        # Return only the requested slice
        s_ts = pd.Timestamp(start_dt)
        e_ts = pd.Timestamp(end_dt)
        if s_ts.tzinfo is None and combined.index.tz is not None:
            s_ts = s_ts.tz_localize(combined.index.tz)
            e_ts = e_ts.tz_localize(combined.index.tz)
        return combined.loc[s_ts:e_ts]

    @classmethod
    async def _fetch_range(
        cls,
        ticker: yf.Ticker,
        start_dt: datetime,
        end_dt: datetime,
        interval: str,
        symbol: str,
        max_chunk_days: int,
    ) -> list[pd.DataFrame]:
        """Fetch a date range from yfinance in chunks with retries."""
        chunks: list[pd.DataFrame] = []
        chunk_start = start_dt
        while chunk_start < end_dt:
            chunk_end = min(chunk_start + timedelta(days=max_chunk_days), end_dt)
            cs = chunk_start.strftime("%Y-%m-%d")
            ce = chunk_end.strftime("%Y-%m-%d")

            chunk_df = pd.DataFrame()
            for attempt in range(3):
                logger.info(
                    "replay_fetch_chunk",
                    attempt=attempt + 1,
                    symbol=symbol,
                    start=cs,
                    end=ce,
                    interval=interval,
                )
                try:
                    chunk_df = ticker.history(start=cs, end=ce, interval=interval)
                except Exception as e:
                    logger.error("replay_fetch_exception", attempt=attempt + 1, error=str(e))
                    chunk_df = pd.DataFrame()

                if not chunk_df.empty:
                    break
                await asyncio.sleep(2 * (attempt + 1))

            if not chunk_df.empty:
                chunks.append(chunk_df)

            chunk_start = chunk_end

        return chunks

    def get_status(self) -> dict:
        progress = (self._bar_index / self._total_bars * 100) if self._total_bars > 0 else 0
        return {
            "running": self._running,
            "paused": self._paused,
            "progress": round(progress, 1),
            "speed": self._speed,
            "bar_index": self._bar_index,
            "total_bars": self._total_bars,
            "symbol": self._symbol,
            "symbols": self._symbols,
            "timeframe": self._timeframe,
        }

    def get_results(self) -> ReplayResults | None:
        return self._results

    # ------------------------------------------------------------------
    # Multi-timeframe divergence scanning (per symbol)
    # ------------------------------------------------------------------

    def _scan_divergences(self, symbol: str, current_timestamp) -> DivergenceSignal | None:
        """Check all candidate timeframes for RSI divergence for a given symbol."""
        tracker = self._trackers.get(symbol)
        if not tracker:
            return None

        sym_divs = self._div_dataframes.get(symbol)
        if not sym_divs:
            return None

        divergences: dict[str, DivergenceSignal | None] = {}
        weights = tracker.get_weights()

        for tf, df_tf in sym_divs.items():
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

    # ------------------------------------------------------------------
    # Main replay loop — multi-ticker
    # ------------------------------------------------------------------

    async def _run_loop(
        self,
        symbol_dataframes: dict[str, pd.DataFrame],
        symbol_trade_starts: dict[str, int],
        unified_timeline: list,
    ) -> None:
        min_bars = 20
        equity_curve: list[dict] = []
        headless = self._headless

        try:
            for bar_idx, timestamp in enumerate(unified_timeline):
                if not self._running:
                    break

                if not headless:
                    await self._pause_event.wait()
                    if not self._running:
                        break

                self._bar_index = bar_idx + 1

                # --- For each symbol that has a bar at this timestamp ---
                all_signals: list[TradingSignal] = []

                for sym, df in symbol_dataframes.items():
                    trade_start = symbol_trade_starts[sym]
                    # Check if this symbol has a bar at this timestamp
                    if timestamp not in df.index:
                        continue
                    bar_pos = df.index.get_loc(timestamp)
                    if bar_pos < trade_start:
                        continue

                    row = df.iloc[bar_pos]
                    close_price = float(row["close"])

                    # Update broker prices
                    self.broker.set_current_price(sym, close_price)
                    self.broker.update_position_prices(sym, close_price)

                    if not headless:
                        candle_data = {
                            "symbol": sym,
                            "timestamp": timestamp.isoformat(),
                            "open": round(float(row["open"]), 2),
                            "high": round(float(row["high"]), 2),
                            "low": round(float(row["low"]), 2),
                            "close": round(close_price, 2),
                            "volume": int(row["volume"]),
                        }
                        await ws_manager.broadcast("replay_candle", candle_data)

                    # Check stops/targets
                    closed_trades = self.broker.check_stops_and_targets(
                        sym,
                        float(row["high"]),
                        float(row["low"]),
                        close_price,
                    )
                    for ct in closed_trades:
                        self._handle_closed_trade(ct, timestamp, sym)

                    # Decrement cooldown
                    if self._cooldown_bars.get(sym, 0) > 0:
                        self._cooldown_bars[sym] -= 1

                    # Run analysis + signals
                    if bar_pos >= min_bars:
                        window_df = df.iloc[: bar_pos + 1].copy()
                        signals = self._generate_signals_for_bar(
                            sym, window_df, timestamp, close_price,
                        )
                        all_signals.extend(signals)

                # --- Rank and execute across all symbols ---
                if all_signals:
                    await self._rank_and_execute(all_signals, timestamp)

                # Track equity
                balance = await self.broker.get_account_balance()
                equity_curve.append({
                    "timestamp": timestamp.isoformat(),
                    "balance": round(balance, 2),
                    "bar_index": bar_idx,
                })

                if not headless:
                    positions = await self.broker.get_positions()
                    await ws_manager.broadcast("replay_positions", {
                        "positions": [
                            {
                                "symbol": p.symbol,
                                "quantity": abs(p.quantity),
                                "avg_cost": p.avg_cost,
                                "current_price": p.current_price,
                                "unrealized_pnl": p.unrealized_pnl,
                                "unrealized_pnl_pct": p.unrealized_pnl_pct,
                                "market_value": p.market_value,
                            }
                            for p in positions
                        ]
                    })

                    await self._broadcast_state()

                    delay = BASE_DELAY / self._speed
                    await asyncio.sleep(delay)

            # Replay complete
            self._running = False
            self._results = self._build_results(equity_curve)

            if not headless:
                await ws_manager.broadcast("replay_complete", {
                    "results": self._results.to_dict(),
                })
                await self._broadcast_state()
            logger.info(
                "replay_complete",
                trades=self._results.total_trades,
                divergence_weights=self._results.divergence_weights,
            )

        except asyncio.CancelledError:
            logger.info("replay_loop_cancelled")
            raise

    def _generate_signals_for_bar(
        self,
        symbol: str,
        df: pd.DataFrame,
        timestamp,
        close_price: float,
    ) -> list[TradingSignal]:
        """Generate signals for one symbol at one bar. Handles exits and entries."""
        try:
            analysis = run_analysis(symbol, df)

            if not self._headless:
                asyncio.ensure_future(ws_manager.broadcast("signal_fired", {
                    "symbol": symbol,
                    "analysis": analysis.to_dict(),
                }))

            close_prices = df["close"].tolist()

            # Check for exit on open positions first
            current_side = self.broker.get_position_side(symbol)
            if current_side is not None:
                self._check_and_exit(symbol, analysis, df, current_side, close_prices, timestamp)
                # Re-check after potential exit
                current_side = self.broker.get_position_side(symbol)

            # If still in a position, no new entry
            if current_side is not None:
                return []

            # Performance gate: skip symbols that consistently lose
            if symbol in self._gated_symbols:
                return []

            # Divergence
            best_div = None
            if self._strategy in ("rsi_divergence_sr", "refined_rsi", "refined_rsi_v2"):
                best_div = self._scan_divergences(symbol, timestamp)

            # Suppress during cooldown
            if self._cooldown_bars.get(symbol, 0) > 0 and self._strategy in ("refined_rsi", "refined_rsi_v2"):
                best_div = None

            # Strategy dispatch
            if self._strategy == "mean_reversion":
                signals = generate_signals_mean_reversion(
                    analysis, df, stop_loss_pct=self._stop_loss_pct,
                )
            elif self._strategy == "momentum_breakout":
                signals = generate_signals_momentum_breakout(
                    analysis, df, stop_loss_pct=self._stop_loss_pct,
                )
            elif self._strategy == "vwap_bounce":
                signals = generate_signals_vwap_bounce(
                    analysis, df, stop_loss_pct=self._stop_loss_pct,
                )
            elif self._strategy == "exhaustion_reversal":
                signals = generate_signals_exhaustion_reversal(
                    analysis, df, stop_loss_pct=self._stop_loss_pct,
                )
            elif self._strategy == "refined_rsi":
                signals = generate_signals_refined_rsi(
                    analysis, df,
                    stop_loss_pct=self._stop_loss_pct,
                    best_divergence=best_div,
                    current_timestamp=timestamp,
                    tf_params=self._tf_params,
                )
            elif self._strategy == "refined_rsi_v2":
                signals = generate_signals_refined_rsi_v2(
                    analysis, df,
                    stop_loss_pct=self._stop_loss_pct,
                    best_divergence=best_div,
                    current_timestamp=timestamp,
                    tf_params=self._tf_params,
                )
            else:  # rsi_divergence_sr (default)
                signals = generate_signals(
                    analysis,
                    stop_loss_pct=self._stop_loss_pct,
                    best_divergence=best_div,
                )

            return signals

        except Exception as e:
            logger.error("replay_signal_gen_error", symbol=symbol, error=str(e))
            return []

    def _check_and_exit(
        self,
        symbol: str,
        analysis,
        df: pd.DataFrame,
        current_side: str,
        close_prices: list[float],
        timestamp,
    ) -> None:
        """Check exit conditions and close position if triggered."""
        if self._strategy == "mean_reversion":
            reversal = check_exit_mean_reversion(analysis, df, current_side)
        elif self._strategy == "momentum_breakout":
            reversal = check_exit_momentum(analysis, df, current_side)
        elif self._strategy == "vwap_bounce":
            reversal = check_exit_vwap(analysis, df, current_side)
        elif self._strategy == "exhaustion_reversal":
            reversal = check_exit_exhaustion(analysis, df, current_side)
        elif self._strategy in ("refined_rsi", "refined_rsi_v2"):
            entry_time = None
            for tr in reversed(self._trade_records):
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
                tf_params=self._tf_params,
            )
        else:
            reversal = check_reversal_signal(analysis, current_side, close_prices)

        if reversal:
            close_price = float(df["close"].iloc[-1])
            ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
            closed_trade = self.broker.close_position(symbol, close_price)
            if closed_trade:
                self._close_trade_record(closed_trade, ts_str,
                    f"Reversal detected (RSI {analysis.rsi_current:.0f})")

    async def _rank_and_execute(self, all_signals: list[TradingSignal], timestamp) -> None:
        """Sort signals by confidence, greedily fill best-to-worst."""
        all_signals.sort(key=lambda s: s.confidence, reverse=True)

        balance = await self.broker.get_account_balance()
        buying_power = await self.broker.get_buying_power()
        positions = await self.broker.get_positions()
        open_symbols = [p.symbol for p in positions]
        total_exposure = sum(p.market_value for p in positions)

        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)

        for signal in all_signals:
            # Exposure cap = min(1.0, position_size × 5), same formula as live auto-scale
            max_exposure = min(1.0, self._position_size_pct * 5)
            decision = validate_signal(
                signal, balance, buying_power, open_symbols, total_exposure,
                position_size_pct=self._position_size_pct,
                max_exposure_pct=max_exposure,
            )

            if not decision.approved:
                continue

            order_id = await self.broker.place_order(
                symbol=signal.symbol,
                side=signal.signal_type,
                quantity=decision.quantity,
                order_type="LIMIT",
                limit_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                profit_target=signal.profit_target,
            )

            # Update running totals
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
                "entry_time": ts_str,
                "exit_time": None,
                "stop_loss": signal.stop_loss,
                "profit_target": signal.profit_target,
                "pnl": None,
                "pnl_pct": None,
                "reason": signal.reason,
                "status": "OPEN",
                "ib_order_id": order_id,
                "signal_id": None,
                "divergence_timeframe": signal.divergence_timeframe,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            self._trade_records.append(trade_record)
            if not self._headless:
                await ws_manager.broadcast("replay_trade", trade_record)

            logger.info(
                "replay_order_placed",
                order_id=order_id,
                symbol=signal.symbol,
                side=signal.signal_type,
                qty=decision.quantity,
                entry=signal.entry_price,
                position_size_pct=round(self._position_size_pct * 100, 1),
            )

    def _handle_closed_trade(self, ct: dict, timestamp, symbol: str) -> None:
        """Update trade record when stopped out or target hit."""
        for tr in self._trade_records:
            if tr["ib_order_id"] == ct["order_id"] and tr["status"] == "OPEN":
                tr["exit_price"] = ct["exit_price"]
                tr["exit_time"] = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
                tr["pnl"] = ct["pnl"]
                tr["pnl_pct"] = ct["pnl_pct"]
                tr["status"] = "CLOSED"
                tr["updated_at"] = datetime.now(timezone.utc).isoformat()

                # Record outcome for per-symbol adaptive weighting
                div_tf = tr.get("divergence_timeframe")
                tracker = self._trackers.get(symbol)
                if div_tf and tracker:
                    tracker.record_outcome(div_tf, (ct["pnl"] or 0) > 0)

                # Update per-symbol performance stats + gate check
                self._update_symbol_stats(symbol, (ct["pnl"] or 0) > 0)

                # Cooldown after a loss for refined_rsi
                if self._strategy == "refined_rsi" and (ct["pnl"] or 0) < 0:
                    self._cooldown_bars[symbol] = self._tf_params.cooldown_bars if self._tf_params else 6

                if not self._headless:
                    asyncio.ensure_future(ws_manager.broadcast("replay_trade", tr))
                break

    def _close_trade_record(self, closed_trade: dict, ts_str: str, close_reason: str) -> None:
        """Update trade record on position close and record outcome."""
        for tr in self._trade_records:
            if tr["ib_order_id"] == closed_trade["order_id"] and tr["status"] == "OPEN":
                symbol = tr["symbol"]
                tr["exit_price"] = closed_trade["exit_price"]
                tr["exit_time"] = ts_str
                tr["pnl"] = closed_trade["pnl"]
                tr["pnl_pct"] = closed_trade["pnl_pct"]
                tr["status"] = "CLOSED"
                tr["reason"] = tr["reason"] + f" | Closed: {close_reason}"
                tr["updated_at"] = datetime.now(timezone.utc).isoformat()

                # Record outcome for per-symbol adaptive weighting
                div_tf = tr.get("divergence_timeframe")
                tracker = self._trackers.get(symbol)
                if div_tf and tracker:
                    tracker.record_outcome(div_tf, (closed_trade["pnl"] or 0) > 0)

                # Update per-symbol performance stats + gate check
                self._update_symbol_stats(symbol, (closed_trade["pnl"] or 0) > 0)

                if not self._headless:
                    asyncio.ensure_future(ws_manager.broadcast("replay_trade", tr))
                break

    # Performance gate constants
    _GATE_MIN_TRADES = 4   # minimum trades before gating
    _GATE_WIN_RATE = 0.25  # gate symbol if win rate falls below 25%

    def _update_symbol_stats(self, symbol: str, profitable: bool) -> None:
        """Track per-symbol outcomes and gate symbols that consistently lose."""
        if symbol not in self._symbol_stats:
            self._symbol_stats[symbol] = {"wins": 0, "losses": 0}

        stats = self._symbol_stats[symbol]
        if profitable:
            stats["wins"] += 1
        else:
            stats["losses"] += 1

        total = stats["wins"] + stats["losses"]
        if total >= self._GATE_MIN_TRADES:
            win_rate = stats["wins"] / total
            if win_rate < self._GATE_WIN_RATE and symbol not in self._gated_symbols:
                self._gated_symbols.add(symbol)
                logger.info(
                    "symbol_gated",
                    symbol=symbol,
                    win_rate=round(win_rate * 100, 1),
                    wins=stats["wins"],
                    losses=stats["losses"],
                )

    def _build_results(self, equity_curve: list[dict] | None = None) -> ReplayResults:
        closed = [t for t in self._trade_records if t["status"] == "CLOSED"]
        starting = self.broker.get_starting_balance()

        wins = [t for t in closed if t.get("pnl", 0) and t["pnl"] > 0]
        losses = [t for t in closed if t.get("pnl", 0) and t["pnl"] <= 0]

        total_pnl = sum(t.get("pnl", 0) or 0 for t in closed)
        total_pnl_pct = (total_pnl / starting * 100) if starting > 0 else 0
        win_rate = (len(wins) / len(closed) * 100) if closed else 0

        # Long/short breakdown
        long_closed = [t for t in closed if t.get("side") == "BUY"]
        short_closed = [t for t in closed if t.get("side") == "SELL"]
        long_wins = [t for t in long_closed if t.get("pnl", 0) and t["pnl"] > 0]
        short_wins = [t for t in short_closed if t.get("pnl", 0) and t["pnl"] > 0]
        long_win_rate = (len(long_wins) / len(long_closed) * 100) if long_closed else 0
        short_win_rate = (len(short_wins) / len(short_closed) * 100) if short_closed else 0
        long_pnl = sum(t.get("pnl", 0) or 0 for t in long_closed)
        short_pnl = sum(t.get("pnl", 0) or 0 for t in short_closed)

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

        # Capture final divergence weights (aggregate per-symbol)
        div_weights: dict[str, float] = {}
        for sym, tracker in self._trackers.items():
            sym_weights = tracker.get_weights()
            for tf, w in sym_weights.items():
                key = f"{sym}:{tf}"
                div_weights[key] = w

        return ReplayResults(
            trades=self._trade_records,
            equity_curve=equity_curve or [],
            total_pnl=round(total_pnl, 2),
            total_pnl_pct=round(total_pnl_pct, 2),
            win_rate=round(win_rate, 1),
            long_win_rate=round(long_win_rate, 1),
            short_win_rate=round(short_win_rate, 1),
            long_pnl=round(long_pnl, 2),
            short_pnl=round(short_pnl, 2),
            total_trades=len(closed),
            winning_trades=len(wins),
            losing_trades=len(losses),
            max_drawdown=round(max_drawdown, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            starting_balance=starting,
            ending_balance=round(ending_balance, 2),
            divergence_weights={tf: round(w, 3) for tf, w in div_weights.items()},
        )

    async def _broadcast_state(self) -> None:
        await ws_manager.broadcast("replay_state", self.get_status())


# Global singleton
_replay_engine: ReplayEngine | None = None


def get_replay_engine() -> ReplayEngine | None:
    return _replay_engine


def set_replay_engine(engine: ReplayEngine) -> None:
    global _replay_engine
    _replay_engine = engine
