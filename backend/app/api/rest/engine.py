from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.trading_engine import get_engine
from app.engine.strategies import get_strategy
from app.api.rest.tickers import _tracked_tickers

router = APIRouter(prefix="/api/engine", tags=["engine"])


class EngineStartRequest(BaseModel):
    strategy: str = "refined_rsi_v8"
    tickers: list[str] | None = None
    timeframe: str | None = None  # defaults to strategy's baked-in timeframe


class EngineStatusResponse(BaseModel):
    running: bool
    started_at: str | None
    active_tickers: list[str]
    uptime_seconds: float
    timeframe: str
    strategy: str = "refined_rsi"


@router.get("/status", response_model=EngineStatusResponse)
async def get_status():
    engine = get_engine()
    if not engine:
        return EngineStatusResponse(
            running=False, started_at=None, active_tickers=[], uptime_seconds=0.0,
            timeframe="5m",
        )
    state = engine.state.to_dict()
    return EngineStatusResponse(**state)


@router.post("/start")
async def start_engine(req: EngineStartRequest | None = None):
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Trading engine not initialized")
    if engine.state.running:
        return {"status": "already_running"}

    strategy = req.strategy if req else "refined_rsi_v8"
    sdef = get_strategy(strategy)
    timeframe = (req.timeframe if req and req.timeframe else None) or (sdef.timeframe if sdef else "1m")
    tickers = (req.tickers if req and req.tickers else None) or _tracked_tickers or ["AAPL"]

    await engine.start(tickers, timeframe=timeframe, strategy=strategy)
    return {"status": "started", "tickers": tickers, "timeframe": timeframe, "strategy": strategy}


@router.post("/stop")
async def stop_engine():
    engine = get_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Trading engine not initialized")
    await engine.stop()
    return {"status": "stopped"}
