from fastapi import APIRouter

from app.schemas.metrics import PerformanceMetrics
from app.config import settings
from app.dependencies import get_broker
from app.engine.trading_engine import get_engine

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=PerformanceMetrics)
async def get_metrics():
    broker = get_broker()
    engine = get_engine()

    # Get balance from broker
    current_balance = settings.starting_balance
    if await broker.is_connected():
        try:
            current_balance = await broker.get_account_balance()
        except Exception:
            pass

    # Compute metrics from trade records
    trades = engine.get_trade_records() if engine else []
    closed_trades = [t for t in trades if t["status"] == "CLOSED"]
    open_trades = [t for t in trades if t["status"] == "OPEN"]

    wins = [t for t in closed_trades if (t.get("pnl") or 0) > 0]
    losses = [t for t in closed_trades if (t.get("pnl") or 0) < 0]

    total_pnl = current_balance - settings.starting_balance
    total_pnl_pct = (total_pnl / settings.starting_balance * 100) if settings.starting_balance > 0 else 0

    win_rate = (len(wins) / len(closed_trades) * 100) if closed_trades else 0

    # Long/short breakdown
    long_closed = [t for t in closed_trades if t.get("side") == "BUY"]
    short_closed = [t for t in closed_trades if t.get("side") == "SELL"]
    long_wins = [t for t in long_closed if (t.get("pnl") or 0) > 0]
    short_wins = [t for t in short_closed if (t.get("pnl") or 0) > 0]
    long_win_rate = (len(long_wins) / len(long_closed) * 100) if long_closed else 0
    short_win_rate = (len(short_wins) / len(short_closed) * 100) if short_closed else 0
    long_pnl = sum(t.get("pnl", 0) or 0 for t in long_closed)
    short_pnl = sum(t.get("pnl", 0) or 0 for t in short_closed)

    largest_win = max((t.get("pnl") or 0) for t in closed_trades) if closed_trades else 0
    largest_loss = min((t.get("pnl") or 0) for t in closed_trades) if closed_trades else 0
    avg_win = (sum(t.get("pnl", 0) for t in wins) / len(wins)) if wins else 0
    avg_loss = (sum(t.get("pnl", 0) for t in losses) / len(losses)) if losses else 0

    gross_profit = sum(t.get("pnl", 0) for t in wins)
    gross_loss = abs(sum(t.get("pnl", 0) for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0

    return PerformanceMetrics(
        starting_balance=settings.starting_balance,
        current_balance=round(current_balance, 2),
        total_pnl=round(total_pnl, 2),
        total_pnl_pct=round(total_pnl_pct, 2),
        pnl_1d=round(total_pnl, 2),  # Simplified for now
        pnl_1w=round(total_pnl, 2),
        pnl_1m=round(total_pnl, 2),
        pnl_1y=round(total_pnl, 2),
        pnl_ytd=round(total_pnl, 2),
        win_rate=round(win_rate, 2),
        long_win_rate=round(long_win_rate, 2),
        short_win_rate=round(short_win_rate, 2),
        long_pnl=round(long_pnl, 2),
        short_pnl=round(short_pnl, 2),
        total_trades=len(trades),
        open_positions=len(open_trades),
        largest_win=round(largest_win, 2),
        largest_loss=round(largest_loss, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
    )
