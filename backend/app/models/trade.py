from datetime import datetime

from sqlalchemy import Float, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(10), index=True)
    side: Mapped[str] = mapped_column(String(4))  # BUY or SELL
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_time: Mapped[datetime] = mapped_column(DateTime)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stop_loss: Mapped[float] = mapped_column(Float)
    profit_target: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    reason: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="OPEN")  # OPEN, CLOSED, CANCELLED
    exit_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    strategy: Mapped[str | None] = mapped_column(String(50), nullable=True)
    divergence_timeframe: Mapped[str | None] = mapped_column(String(10), nullable=True)
    ib_order_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    signal_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("signals.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
