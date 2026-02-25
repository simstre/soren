from pydantic import BaseModel


class PositionRead(BaseModel):
    symbol: str
    quantity: int
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    market_value: float
