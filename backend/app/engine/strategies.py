"""Strategy registry — defines available trading strategies."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StrategyDef:
    id: str
    name: str
    description: str
    entry_rule: str


# All available strategies.  Only the first is fully implemented;
# the rest are placeholders that will use their own signal logic
# once built out.
STRATEGIES: list[StrategyDef] = [
    StrategyDef(
        id="refined_rsi",
        name="RSI Divergence Smart",
        description="Optimized for 1m candles on high-fitness tickers. Timeframe-adaptive RSI divergence + S/R proximity. ATR-based stops (2x) and targets (3x). Auto-scales RSI bands, cooldown, and hold limits. Best results at 30%+ position size on liquid, high-price names.",
        entry_rule="RSI div + S/R ≥3 + ATR stops + fitness-filtered tickers",
    ),
    StrategyDef(
        id="refined_rsi_v3",
        name="RSI Divergence Smart v3",
        description="Empirically optimized from 27-ticker analysis. Wider RSI bands (28-46/54-72), lower S/R threshold (>=3), tighter stops (1.5x ATR), wider targets (3.5x ATR), volume confirmation, morning session filter. Fewer filtered trades, better R:R.",
        entry_rule="RSI div + S/R ≥3 + vol confirm + wider RSI bands + ATR 1.5/3.5",
    ),
    StrategyDef(
        id="refined_rsi_v4",
        name="RSI Divergence Smart v4",
        description="Bear-market-tuned variant of v1. Skips 2pm hour, wider short RSI band (56-70 vs 58-70), wider ATR target 3.5x. Contains directional bias — use v5 for direction-agnostic trading.",
        entry_rule="RSI div + S/R ≥5 + skip 2pm + wider short band + ATR 2.0/3.5",
    ),
    StrategyDef(
        id="refined_rsi_v5",
        name="RSI Divergence Smart v5",
        description="Direction-agnostic improvement to v1. Wider ATR target 3.5x (was 3.0x) for better R:R on both longs and shorts equally. Symmetric RSI bands, no time-of-day bias, no directional skew.",
        entry_rule="RSI div + S/R ≥5 + ATR 2.0/3.5",
    ),
    StrategyDef(
        id="refined_rsi_v2",
        name="RSI Divergence Smart v2",
        description="Wider entry filters: RSI bands 28-48/52-72, S/R strength >= 3, min spread 5. More trades, tested against UNH/AAPL/GOOGL/MSFT/TSLA performance profile. ATR stops 1.8x, targets 3.5x.",
        entry_rule="RSI div + S/R ≥3 + wider RSI bands + ATR stops",
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
        }
        for s in STRATEGIES
    ]
