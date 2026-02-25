"""Built-in scanner presets for stock discovery."""

from dataclasses import dataclass


@dataclass
class ScannerPresetConfig:
    id: str
    name: str
    description: str
    sort_by: str  # field to sort results by
    sort_desc: bool
    min_volume: int
    min_price: float
    max_price: float


PRESETS: dict[str, ScannerPresetConfig] = {
    "volume_volatility": ScannerPresetConfig(
        id="volume_volatility",
        name="High Volume + Volatility",
        description="Scan for stocks with highest volume and volatility today",
        sort_by="volume",
        sort_desc=True,
        min_volume=1_000_000,
        min_price=10.0,
        max_price=500.0,
    ),
    "gap_up": ScannerPresetConfig(
        id="gap_up",
        name="Gap Up",
        description="Stocks that gapped up more than 2% at open",
        sort_by="change_pct",
        sort_desc=True,
        min_volume=500_000,
        min_price=10.0,
        max_price=500.0,
    ),
    "gap_down": ScannerPresetConfig(
        id="gap_down",
        name="Gap Down",
        description="Stocks that gapped down more than 2% at open",
        sort_by="change_pct",
        sort_desc=False,
        min_volume=500_000,
        min_price=10.0,
        max_price=500.0,
    ),
    "breakout": ScannerPresetConfig(
        id="breakout",
        name="52-Week Breakout",
        description="Stocks breaking above 52-week high with volume",
        sort_by="volume",
        sort_desc=True,
        min_volume=1_000_000,
        min_price=10.0,
        max_price=1000.0,
    ),
    "strategy_fitness": ScannerPresetConfig(
        id="strategy_fitness",
        name="Strategy Fitness",
        description="Score tickers on suitability for refined RSI strategy (price, volume, ATR, S/R, RSI bands)",
        sort_by="fitness_score",
        sort_desc=True,
        min_volume=100_000,
        min_price=10.0,
        max_price=10000.0,
    ),
}
