"""Strategy registry — defines available trading strategies."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScanConfig:
    """Parameters for the fitness scanner's mini-backtest."""
    atr_stop_mult: float = 2.0
    atr_target_mult: float = 3.0
    rsi_buy_lo: float = 30.0
    rsi_buy_hi: float = 42.0
    rsi_sell_lo: float = 58.0
    rsi_sell_hi: float = 70.0
    min_rsi_spread: float = 7.0
    sr_min_strength: int = 5
    max_hold_bars: int = 120
    cooldown_bars: int = 30
    flip_signals: bool = False  # contrarian strategies (v6/v7)
    # Percentage-based stops/targets (v8+). When use_pct_stops=True,
    # stop/target are computed from entry price × pct, ignoring ATR mults.
    use_pct_stops: bool = False
    stop_pct: float = 0.02   # 2% stop loss
    target_pct: float = 0.06  # 6% take profit


@dataclass
class StrategyDef:
    id: str
    name: str
    description: str
    entry_rule: str
    scan_config: ScanConfig = field(default_factory=ScanConfig)
    # Trading parameters baked into each strategy
    position_size_pct: float = 0.50
    max_exposure_pct: float = 1.0
    stop_loss_pct: float = 0.02
    timeframe: str = "1m"


# All available strategies.  Only the first is fully implemented;
# the rest are placeholders that will use their own signal logic
# once built out.
STRATEGIES: list[StrategyDef] = [
    StrategyDef(
        id="refined_rsi",
        name="RSI Divergence Smart",
        description="Optimized for 1m candles on high-fitness tickers. Timeframe-adaptive RSI divergence + S/R proximity. ATR-based stops (2x) and targets (3x). Auto-scales RSI bands, cooldown, and hold limits. Best results at 30%+ position size on liquid, high-price names.",
        entry_rule="RSI div + S/R ≥3 + ATR stops + fitness-filtered tickers",
        scan_config=ScanConfig(),  # defaults = v1 params
    ),
    StrategyDef(
        id="refined_rsi_v3",
        name="RSI Divergence Smart v3",
        description="Empirically optimized from 27-ticker analysis. Wider RSI bands (28-46/54-72), lower S/R threshold (>=3), tighter stops (1.5x ATR), wider targets (3.5x ATR), volume confirmation, morning session filter. Fewer filtered trades, better R:R.",
        entry_rule="RSI div + S/R ≥3 + vol confirm + wider RSI bands + ATR 1.5/3.5",
        scan_config=ScanConfig(atr_stop_mult=1.5, atr_target_mult=3.5, rsi_buy_hi=46, rsi_sell_lo=54, min_rsi_spread=5, sr_min_strength=3),
    ),
    StrategyDef(
        id="refined_rsi_v4",
        name="RSI Divergence Smart v4",
        description="Bear-market-tuned variant of v1. Skips 2pm hour, wider short RSI band (56-70 vs 58-70), wider ATR target 3.5x. Contains directional bias — use v5 for direction-agnostic trading.",
        entry_rule="RSI div + S/R ≥5 + skip 2pm + wider short band + ATR 2.0/3.5",
        scan_config=ScanConfig(atr_target_mult=3.5),
    ),
    StrategyDef(
        id="refined_rsi_v5",
        name="RSI Divergence Smart v5",
        description="Direction-agnostic improvement to v1. Wider ATR target 3.5x (was 3.0x) for better R:R on both longs and shorts equally. Symmetric RSI bands, no time-of-day bias, no directional skew.",
        entry_rule="RSI div + S/R ≥5 + ATR 2.0/3.5",
        scan_config=ScanConfig(atr_target_mult=3.5),
    ),
    StrategyDef(
        id="refined_rsi_v7",
        name="RSI Divergence Contrarian v7",
        description="Contrarian momentum: flips v1's RSI divergence signals. When v1 detects reversal setups, v7 trades continuation. Stop=1.5x ATR, Target=5.0x ATR. Time-only exits (no RSI reversal). 24% WR but +0.40% over 15d with 0.48% DD.",
        entry_rule="Contra RSI div + S/R ≥5 + ATR 1.5/5.0 + time-only exit",
        scan_config=ScanConfig(atr_stop_mult=1.5, atr_target_mult=5.0, flip_signals=True),
    ),
    StrategyDef(
        id="refined_rsi_v2",
        name="RSI Divergence Smart v2",
        description="Wider entry filters: RSI bands 28-48/52-72, S/R strength >= 3, min spread 5. More trades, tested against UNH/AAPL/GOOGL/MSFT/TSLA performance profile. ATR stops 1.8x, targets 3.5x.",
        entry_rule="RSI div + S/R ≥3 + wider RSI bands + ATR stops",
        scan_config=ScanConfig(atr_stop_mult=1.8, atr_target_mult=3.5, rsi_buy_hi=48, rsi_sell_lo=52, min_rsi_spread=5, sr_min_strength=3),
    ),
    StrategyDef(
        id="refined_rsi_v8",
        name="RSI Divergence PCT v8",
        description="Percentage-based stops/targets (2% SL, 6% target) instead of ATR-based. 3h max hold (180 bars), no RSI reversal exit, no trailing stops. Designed for 1m candles where ATR-based stops are too tight.",
        entry_rule="RSI div + S/R ≥5 + vol confirm + pct stops + extended hold",
        scan_config=ScanConfig(
            max_hold_bars=180, cooldown_bars=60,
            use_pct_stops=True, stop_pct=0.02, target_pct=0.06,
        ),
        position_size_pct=0.50,
        max_exposure_pct=1.0,
        stop_loss_pct=0.02,
        timeframe="1m",
    ),
    StrategyDef(
        id="rsi_divergence_sr",
        name="RSI Divergence + S/R",
        description="Enter when RSI divergence is detected on a lower timeframe AND price is near support/resistance. Adaptive multi-timeframe learning.",
        entry_rule="RSI divergence + S/R proximity",
    ),
    StrategyDef(
        id="vwap_bounce",
        name="VWAP Bounce",
        description="Enter when price pulls back to VWAP and shows a bounce with RSI confirmation. Best for intraday timeframes.",
        entry_rule="VWAP proximity + RSI confirmation",
    ),
    StrategyDef(
        id="mean_reversion",
        name="Mean Reversion",
        description="Enter when price deviates significantly from its moving average and shows signs of reverting. Uses Bollinger Bands and RSI extremes.",
        entry_rule="Bollinger Band touch + RSI extreme",
    ),
]

STRATEGY_MAP: dict[str, StrategyDef] = {s.id: s for s in STRATEGIES}
DEFAULT_STRATEGY = STRATEGIES[0].id


def get_strategy(strategy_id: str) -> StrategyDef | None:
    return STRATEGY_MAP.get(strategy_id)


def list_strategies() -> list[dict]:
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "entry_rule": s.entry_rule,
            "timeframe": s.timeframe,
            "position_size_pct": s.position_size_pct,
            "stop_loss_pct": s.stop_loss_pct,
            "max_exposure_pct": s.max_exposure_pct,
        }
        for s in STRATEGIES
    ]
