from datetime import datetime

from sqlalchemy import Float, Integer, String, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    signal_type: Mapped[str] = mapped_column(String(4))  # BUY or SELL
    reason: Mapped[str] = mapped_column(String(200))
    pattern_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    profit_target: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(10), default="PENDING")  # PENDING, EXECUTED, EXPIRED, REJECTED
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
