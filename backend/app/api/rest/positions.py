from fastapi import APIRouter

from app.dependencies import get_broker
from app.schemas.position import PositionRead

router = APIRouter(prefix="/api/positions", tags=["positions"])


@router.get("", response_model=list[PositionRead])
async def list_positions():
    broker = get_broker()
    if not await broker.is_connected():
        return []
    return await broker.get_positions()
