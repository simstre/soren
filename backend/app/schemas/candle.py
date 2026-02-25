from datetime import datetime

from pydantic import BaseModel


class CandleData(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
