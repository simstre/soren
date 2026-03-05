from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Query, HTTPException, Response
import pandas as pd
import yfinance as yf
import structlog

from app.schemas.candle import CandleData

logger = structlog.get_logger()
router = APIRouter(prefix="/api/candles", tags=["candles"])

# Map our timeframes to yfinance intervals and appropriate periods
# max_history_days: absolute max days from NOW that yfinance allows for this interval
TIMEFRAME_MAP = {
    "1m": {"interval": "1m", "period": "7d", "max_history_days": 7},
    "5m": {"interval": "5m", "period": "60d", "max_history_days": 60},
    "15m": {"interval": "15m", "period": "60d", "max_history_days": 60},
    "1h": {"interval": "1h", "period": "2y", "max_history_days": 730},
    "4h": {"interval": "1h", "period": "2y", "aggregate": 4, "max_history_days": 730},
    "1D": {"interval": "1d", "period": "10y", "max_history_days": 10000},
    "1W": {"interval": "1wk", "period": "max", "max_history_days": 20000},
}


def _aggregate_candles(df: pd.DataFrame, factor: int) -> pd.DataFrame:
    """Aggregate candles by a given factor (e.g., 4 x 1h = 4h)."""
    agg = df.resample(f"{factor}h").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return agg


@router.get("/{symbol}", response_model=list[CandleData])
async def get_candles(
    symbol: str,
    response: Response,
    timeframe: str = Query("5m"),
    limit: int = Query(100, le=500),
    end: str | None = Query(None, description="ISO datetime - fetch candles ending before this time"),
):
    response.headers["Cache-Control"] = "no-store, max-age=0"
    symbol = symbol.upper()
    tf_config = TIMEFRAME_MAP.get(timeframe)
    if not tf_config:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")

    try:
        ticker = yf.Ticker(symbol)

        if end:
            # Fetch a historical range ending before `end`
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            max_history_days = tf_config["max_history_days"]

            # Clamp start to yfinance's absolute limit from now
            earliest_allowed = now - timedelta(days=max_history_days)
            start_dt = max(earliest_allowed, end_dt - timedelta(days=max_history_days))

            if start_dt >= end_dt:
                # Already at the yfinance limit for this interval
                return []

            # yfinance only accepts date strings (not datetime) for start/end
            # Add 1 day to end date so we don't miss the end day, then filter precisely later
            end_date = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")

            df = ticker.history(
                start=start_dt.strftime("%Y-%m-%d"),
                end=end_date,
                interval=tf_config["interval"],
            )
        else:
            # Default: fetch the most recent period
            df = ticker.history(
                period=tf_config["period"],
                interval=tf_config["interval"],
            )

        if df.empty:
            if end:
                # No more historical data available — return empty list
                return []
            raise HTTPException(status_code=404, detail=f"No data found for {symbol}")

        # Aggregate if needed (e.g., 1h -> 4h)
        if "aggregate" in tf_config:
            df = _aggregate_candles(df, tf_config["aggregate"])

        # If fetching history with `end`, filter out candles at or after end time
        if end:
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
            df = df[df.index < end_dt]

        # Take last `limit` rows
        df = df.tail(limit)

        candles = []
        for ts, row in df.iterrows():
            candles.append(
                CandleData(
                    timestamp=ts.to_pydatetime().astimezone(timezone.utc),
                    open=round(row["Open"], 2),
                    high=round(row["High"], 2),
                    low=round(row["Low"], 2),
                    close=round(row["Close"], 2),
                    volume=int(row["Volume"]),
                )
            )

        logger.info("candles_fetched", symbol=symbol, timeframe=timeframe, count=len(candles), historical=bool(end))
        return candles

    except HTTPException:
        raise
    except Exception as e:
        logger.error("candle_fetch_error", symbol=symbol, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to fetch data for {symbol}")
