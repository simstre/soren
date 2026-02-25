from fastapi import APIRouter, Query, HTTPException

from app.engine.trading_engine import get_engine

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("")
async def list_trades(
    symbol: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
):
    engine = get_engine()
    trades = engine.get_trade_records() if engine else []

    if symbol:
        trades = [t for t in trades if t["symbol"] == symbol.upper()]
    if status:
        trades = [t for t in trades if t["status"] == status.upper()]

    # Sort newest first
    trades = sorted(trades, key=lambda t: t["created_at"], reverse=True)
    return trades[offset : offset + limit]


@router.get("/{trade_id}")
async def get_trade(trade_id: int):
    engine = get_engine()
    trades = engine.get_trade_records() if engine else []
    for t in trades:
        if t["id"] == trade_id:
            return t
    raise HTTPException(status_code=404, detail="Trade not found")
