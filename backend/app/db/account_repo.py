"""Persist and retrieve IB account balance snapshots."""

from datetime import datetime

import structlog
from sqlalchemy import select

from app.db.session import async_session
from app.models.account_snapshot import AccountSnapshot

logger = structlog.get_logger()


async def save_snapshot(broker: str, balance: float) -> int:
    """Save a new account balance snapshot. Returns DB id."""
    async with async_session() as session:
        snap = AccountSnapshot(broker=broker, balance=balance, captured_at=datetime.utcnow())
        session.add(snap)
        await session.commit()
        await session.refresh(snap)
        logger.info("account_snapshot_saved", broker=broker, balance=balance, id=snap.id)
        return snap.id


async def get_starting_balance(broker: str = "ib") -> float | None:
    """Return the earliest captured balance for this broker, or None."""
    async with async_session() as session:
        result = await session.execute(
            select(AccountSnapshot)
            .where(AccountSnapshot.broker == broker)
            .order_by(AccountSnapshot.captured_at.asc())
            .limit(1)
        )
        snap = result.scalar_one_or_none()
    if snap:
        return snap.balance
    return None
