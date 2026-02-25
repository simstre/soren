from fastapi import APIRouter, Query, HTTPException
import pandas as pd
import yfinance as yf
import structlog

from app.analysis.pipeline import run_analysis

logger = structlog.get_logger()
router = APIRouter(prefix="/api/analysis", tags=["analysis"])

TIMEFRAME_MAP = {
    "1m": {"interval": "1m", "period": "1d"},
    "5m": {"interval": "5m", "period": "5d"},
    "15m": {"interval": "15m", "period": "5d"},
    "1h": {"interval": "1h", "period": "1mo"},
    "4h": {"interval": "1h", "period": "3mo"},
    "1D": {"interval": "1d", "period": "6mo"},
    "1W": {"interval": "1wk", "period": "2y"},
}


@router.get("/{symbol}")
async def get_analysis(
    symbol: str,
    timeframe: str = Query("5m"),
):
    symbol = symbol.upper()
    tf_config = TIMEFRAME_MAP.get(timeframe)
    if not tf_config:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")

    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=tf_config["period"], interval=tf_config["interval"])

        if df.empty:
            raise HTTPException(status_code=404, detail=f"No data for {symbol}")

        # Normalize column names to lowercase
        df.columns = [c.lower() for c in df.columns]

        result = run_analysis(symbol, df)
        # Pass timestamps so key_points and channels include time info
        timestamps = [ts.to_pydatetime().isoformat() for ts in df.index]
        return result.to_dict(timestamps=timestamps)

    except HTTPException:
        raise
    except Exception as e:
        logger.error("analysis_error", symbol=symbol, error=str(e))
        raise HTTPException(status_code=500, detail=f"Analysis failed for {symbol}")
