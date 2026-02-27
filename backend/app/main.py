from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from app.config import settings
from app.db.session import engine
from app.db.base import Base
import app.models  # noqa: F401 — register all ORM models for create_all
from app.api.rest import trades, positions, metrics, tickers, engine as engine_api, scanner, candles, analysis, replay
from app.api.ws.handlers import router as ws_router

structlog.configure(
    processors=[
        structlog.dev.ConsoleRenderer(),
    ],
)
logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("starting_soren", broker_mode=settings.broker_mode)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("database_ready")

    # Connect broker and create trading engine
    from app.dependencies import get_broker
    from app.engine.trading_engine import TradingEngine, set_engine
    broker = get_broker()
    await broker.connect()
    logger.info("broker_connected", mode=settings.broker_mode)

    # Snapshot IB balance on first connect
    from app.db.account_repo import save_snapshot, get_starting_balance
    starting = broker.get_starting_balance()
    if starting and starting > 0:
        existing = await get_starting_balance(settings.broker_mode)
        if existing is None:
            await save_snapshot(settings.broker_mode, starting)
            logger.info("initial_balance_snapshot", balance=starting)
        else:
            logger.info("starting_balance_from_db", balance=existing)

    trading_engine = TradingEngine(broker)
    set_engine(trading_engine)
    logger.info("trading_engine_ready")

    # Create replay engine with its own broker adapter
    from app.broker.replay_adapter import ReplayBrokerAdapter
    from app.engine.replay_engine import ReplayEngine, set_replay_engine
    replay_broker = ReplayBrokerAdapter(starting_balance=settings.starting_balance)
    await replay_broker.connect()
    replay_engine = ReplayEngine(replay_broker)
    set_replay_engine(replay_engine)
    logger.info("replay_engine_ready")

    yield

    if replay_engine.running:
        await replay_engine.stop()
    await replay_broker.disconnect()

    if trading_engine.state.running:
        await trading_engine.stop()
    await broker.disconnect()
    logger.info("shutting_down_soren")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST routes
app.include_router(trades.router)
app.include_router(positions.router)
app.include_router(metrics.router)
app.include_router(tickers.router)
app.include_router(engine_api.router)
app.include_router(scanner.router)
app.include_router(candles.router)
app.include_router(analysis.router)
app.include_router(replay.router)
from app.api.rest import strategies as strategies_api
app.include_router(strategies_api.router)

# WebSocket
app.include_router(ws_router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "app": settings.app_name}


# ---------------------------------------------------------------------------
# Runtime-configurable trading settings
# ---------------------------------------------------------------------------

from pydantic import BaseModel as _BM


class _TradingSettingsResponse(_BM):
    max_position_pct: float
    max_exposure_pct: float
    risk_per_trade_pct: float
    stop_loss_pct: float



@app.get("/api/settings/trading", response_model=_TradingSettingsResponse)
async def get_trading_settings():
    return _TradingSettingsResponse(
        max_position_pct=settings.max_position_pct,
        max_exposure_pct=settings.max_exposure_pct,
        risk_per_trade_pct=settings.risk_per_trade_pct,
        stop_loss_pct=settings.stop_loss_pct,
    )


