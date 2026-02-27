"""Async helpers to persist live trades to SQLite."""

from datetime import datetime

import structlog
from sqlalchemy import select

from app.db.session import async_session
from app.models.trade import Trade

logger = structlog.get_logger()

# Columns on the Trade ORM model that we read/write
_TRADE_COLUMNS = [
    "symbol", "side", "quantity", "entry_price", "exit_price",
    "entry_time", "exit_time", "stop_loss", "profit_target",
    "pnl", "pnl_pct", "reason", "status", "exit_reason",
    "strategy", "divergence_timeframe", "ib_order_id", "signal_id",
]


def _parse_dt(val):
    """Convert ISO string to datetime if needed."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    return datetime.fromisoformat(val)


async def save_trade(record: dict) -> int:
    """INSERT a new Trade row from an in-memory trade record dict. Returns DB id."""
    async with async_session() as session:
        trade = Trade(
            **{col: record[col] for col in _TRADE_COLUMNS if col in record},
        )
        # Convert ISO strings to datetime for ORM columns
        trade.entry_time = _parse_dt(record.get("entry_time"))
        trade.exit_time = _parse_dt(record.get("exit_time"))
        trade.created_at = _parse_dt(record.get("created_at")) or datetime.utcnow()
        trade.updated_at = _parse_dt(record.get("updated_at")) or datetime.utcnow()

        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        logger.info("trade_saved", db_id=trade.id, symbol=trade.symbol)
        return trade.id


async def update_trade(db_id: int, updates: dict):
    """UPDATE specific fields on an existing Trade row."""
    async with async_session() as session:
        trade = await session.get(Trade, db_id)
        if trade is None:
            logger.warning("trade_update_miss", db_id=db_id)
            return
        for key, val in updates.items():
            if key in ("entry_time", "exit_time", "created_at", "updated_at"):
                val = _parse_dt(val)
            if hasattr(trade, key):
                setattr(trade, key, val)
        trade.updated_at = datetime.utcnow()
        await session.commit()
        logger.info("trade_updated", db_id=db_id, fields=list(updates.keys()))


async def load_all_trades() -> list[dict]:
    """SELECT all trades ordered by created_at, return as dicts matching _trade_records shape."""
    async with async_session() as session:
        result = await session.execute(
            select(Trade).order_by(Trade.created_at)
        )
        trades = result.scalars().all()

    records = []
    for t in trades:
        records.append({
            "id": t.id,
            "db_id": t.id,
            "symbol": t.symbol,
            "side": t.side,
            "quantity": t.quantity,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "entry_time": t.entry_time.isoformat() if t.entry_time else None,
            "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            "stop_loss": t.stop_loss,
            "profit_target": t.profit_target,
            "pnl": t.pnl,
            "pnl_pct": t.pnl_pct,
            "reason": t.reason,
            "status": t.status,
            "exit_reason": t.exit_reason,
            "strategy": t.strategy,
            "divergence_timeframe": t.divergence_timeframe,
            "ib_order_id": t.ib_order_id,
            "signal_id": t.signal_id,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "updated_at": t.updated_at.isoformat() if t.updated_at else None,
        })

    logger.info("trades_loaded_from_db", count=len(records))
    return records
