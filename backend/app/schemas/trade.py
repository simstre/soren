from datetime import datetime

from pydantic import BaseModel


class TradeRead(BaseModel):
    id: int
    symbol: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float | None
    entry_time: datetime
    exit_time: datetime | None
    stop_loss: float
    profit_target: float
    pnl: float | None
    pnl_pct: float | None
    reason: str
    status: str
    ib_order_id: str | None
    signal_id: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
