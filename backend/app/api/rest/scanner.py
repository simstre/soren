from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.scanner.presets import PRESETS
from app.scanner.runner import run_scan
from app.scanner.fitness import run_fitness_scan
from app.engine.strategies import STRATEGY_MAP, ScanConfig

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
    # Validation backtest fields (populated for pct-based strategies)
    validation_pnl_pct: float | None = None
    validation_win_rate: float | None = None
    validation_trades: int | None = None
    validation_sl_rate: float | None = None
    validation_mh_rate: float | None = None
    validated: bool | None = None


@router.get("/fitness", response_model=list[FitnessResultResponse])
async def scan_fitness(
    top_n: int = Query(15, ge=1, le=50),
    strategy: str = Query("refined_rsi", description="Strategy ID for scanning parameters"),
    scan_tactic: str = Query("watchlist", description="Scan tactic: 'watchlist' or 'market_scan'"),
):
    """Run strategy-fitness scan and return scored tickers."""
    strat = STRATEGY_MAP.get(strategy)
    scan_config: ScanConfig | None = strat.scan_config if strat else None
    use_pct = scan_config is not None and getattr(scan_config, "use_pct_stops", False)

    # Over-select if we'll validate
    scan_n = (top_n * 2 + 2) if use_pct else top_n

    if scan_tactic == "market_scan":
        from app.scanner.percentile_scanner import run_percentile_scan
        results = await run_percentile_scan(top_n=scan_n, scan_config=scan_config)
    else:
        results = await run_fitness_scan(top_n=scan_n, scan_config=scan_config)

    # Build validation lookup (symbol -> BacktestValidation)
    validation_map: dict = {}
    if use_pct and results:
        from app.scanner.backtest_scorer import validate_candidates
        candidate_symbols = [r.symbol for r in results]
        # Relax thresholds for tight-stop strategies (≤1% SL) since they
        # naturally have higher stop-loss rates but compensate with larger winners.
        sl_pct = scan_config.stop_pct if scan_config else 0.02
        min_wr = 30.0 if sl_pct <= 0.01 else 50.0
        max_sl = 80.0 if sl_pct <= 0.01 else 40.0
        validations = await validate_candidates(
            symbols=candidate_symbols,
            days=14,
            strategy=strategy,
            min_win_rate=min_wr,
            max_sl_rate=max_sl,
        )
        validation_map = {v.symbol: v for v in validations}

        # Filter to passing tickers only, keep up to top_n
        passing_syms = [v.symbol for v in validations if v.passed]
        results = [r for r in results if r.symbol in passing_syms][:top_n]

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
            validation_pnl_pct=round(validation_map[r.symbol].pnl_pct, 2) if r.symbol in validation_map else None,
            validation_win_rate=round(validation_map[r.symbol].win_rate, 1) if r.symbol in validation_map else None,
            validation_trades=validation_map[r.symbol].trade_count if r.symbol in validation_map else None,
            validation_sl_rate=round(validation_map[r.symbol].stop_loss_rate, 1) if r.symbol in validation_map else None,
            validation_mh_rate=round(validation_map[r.symbol].max_hold_rate, 1) if r.symbol in validation_map else None,
            validated=validation_map[r.symbol].passed if r.symbol in validation_map else None,
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
