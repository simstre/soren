"""Replay / backtest REST endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.engine.replay_engine import get_replay_engine

router = APIRouter(prefix="/api/replay", tags=["replay"])


class ReplayStartRequest(BaseModel):
    symbols: list[str] = []
    symbol: str | None = None  # backwards compat — single ticker fallback
    timeframe: str = "5m"
    start_date: str
    end_date: str
    speed: float = 1.0
    stop_loss_pct: float = 0.02  # 2% stop loss by default
    position_size_pct: float = 0.05  # 5% of account per trade
    strategy: str = "rsi_divergence_sr"
    instant: bool = True  # instant mode: skip candle broadcasts & sleep


class ReplaySpeedRequest(BaseModel):
    speed: float


class ReplayStatusResponse(BaseModel):
    running: bool
    paused: bool
    progress: float
    speed: float
    bar_index: int
    total_bars: int
    symbol: str  # primary symbol (first) for display
    symbols: list[str] = []
    timeframe: str


@router.post("/start")
async def start_replay(req: ReplayStartRequest):
    import structlog
    _log = structlog.get_logger()
    # Resolve symbols list: prefer symbols array, fall back to single symbol
    symbols = req.symbols if req.symbols else ([req.symbol] if req.symbol else ["SPY"])

    _log.info(
        "replay_start_request",
        symbols=symbols,
        timeframe=req.timeframe,
        start_date=req.start_date,
        end_date=req.end_date,
        speed=req.speed,
        stop_loss_pct=req.stop_loss_pct,
        position_size_pct=req.position_size_pct,
        strategy=req.strategy,
    )

    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")

    try:
        await engine.start(
            symbols=symbols,
            timeframe=req.timeframe,
            start_date=req.start_date,
            end_date=req.end_date,
            speed=req.speed,
            stop_loss_pct=req.stop_loss_pct,
            position_size_pct=req.position_size_pct,
            strategy=req.strategy,
            instant=req.instant,
        )
    except ValueError as e:
        _log.error("replay_start_failed", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "started", "symbols": symbols}


@router.post("/stop")
async def stop_replay():
    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")
    await engine.stop()
    return {"status": "stopped"}


@router.post("/pause")
async def pause_replay():
    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")
    await engine.pause()
    return {"status": "paused"}


@router.post("/resume")
async def resume_replay():
    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")
    await engine.resume()
    return {"status": "resumed"}


@router.post("/speed")
async def set_replay_speed(req: ReplaySpeedRequest):
    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")
    engine.set_speed(req.speed)
    return {"status": "speed_updated", "speed": req.speed}


@router.get("/status", response_model=ReplayStatusResponse)
async def get_replay_status():
    engine = get_replay_engine()
    if not engine:
        return ReplayStatusResponse(
            running=False,
            paused=False,
            progress=0.0,
            speed=1.0,
            bar_index=0,
            total_bars=0,
            symbol="",
            timeframe="",
        )
    return ReplayStatusResponse(**engine.get_status())


@router.get("/results")
async def get_replay_results():
    engine = get_replay_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Replay engine not initialized")
    results = engine.get_results()
    if not results:
        raise HTTPException(status_code=404, detail="No replay results available")
    return results.to_dict()
