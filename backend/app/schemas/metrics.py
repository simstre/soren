from pydantic import BaseModel


class PerformanceMetrics(BaseModel):
    starting_balance: float
    current_balance: float
    total_pnl: float
    total_pnl_pct: float
    pnl_1d: float
    pnl_1w: float
    pnl_1m: float
    pnl_1y: float
    pnl_ytd: float
    win_rate: float
    long_win_rate: float
    short_win_rate: float
    long_pnl: float
    short_pnl: float
    total_trades: int
    open_positions: int
    largest_win: float
    largest_loss: float
    avg_win: float
    avg_loss: float
    profit_factor: float
