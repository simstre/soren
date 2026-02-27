from datetime import datetime

from sqlalchemy import Float, String, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AccountSnapshot(Base):
    __tablename__ = "account_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker: Mapped[str] = mapped_column(String(20))  # "ib", "paper", etc.
    balance: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
