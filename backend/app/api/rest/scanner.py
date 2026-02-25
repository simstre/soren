from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.scanner.presets import PRESETS
from app.scanner.runner import run_scan
from app.scanner.fitness import run_fitness_scan

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


class ScannerPresetResponse(BaseModel):
    id: str
    name: str
    description: str


class ScannerResultResponse(BaseModel):
    symbol: str
    name: str
    price: float
    volume: int
    change_pct: float


@router.get("/presets", response_model=list[ScannerPresetResponse])
async def list_presets():
    return [
        ScannerPresetResponse(id=p.id, name=p.name, description=p.description)
        for p in PRESETS.values()
    ]


class FitnessResultResponse(BaseModel):
    symbol: str
    total_score: float
    price: float
    avg_volume: int
    atr_pct: float
    sr_strong_count: int
    volume_score: float
    price_score: float
    sr_score: float
    atr_score: float
    disqualified: bool
    disqualify_reason: str


@router.get("/fitness", response_model=list[FitnessResultResponse])
async def scan_fitness(top_n: int = Query(15, ge=1, le=50)):
    """Run strategy-fitness scan and return scored tickers."""
    results = await run_fitness_scan(top_n=top_n)
    return [
        FitnessResultResponse(
            symbol=r.symbol,
            total_score=r.total_score,
            price=r.price,
            avg_volume=r.avg_volume,
            atr_pct=r.atr_pct,
            sr_strong_count=r.sr_strong_count,
            volume_score=r.volume_score,
            price_score=r.price_score,
            sr_score=r.sr_score,
            atr_score=r.atr_score,
            disqualified=r.disqualified,
            disqualify_reason=r.disqualify_reason,
        )
        for r in results
    ]


@router.post("/run", response_model=list[ScannerResultResponse])
async def run_scanner(preset_id: str = Query(...)):
    results = await run_scan(preset_id)
    return [
        ScannerResultResponse(
            symbol=r.symbol,
            name=r.name,
            price=r.price,
            volume=r.volume,
            change_pct=r.change_pct,
        )
        for r in results
    ]
