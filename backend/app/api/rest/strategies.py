"""Strategies REST endpoints."""

from fastapi import APIRouter

from app.engine.strategies import list_strategies

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("/")
async def get_strategies():
    return list_strategies()
