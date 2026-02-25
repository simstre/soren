from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import yfinance as yf
import structlog

logger = structlog.get_logger()
router = APIRouter(prefix="/api/tickers", tags=["tickers"])

# Cache ticker info to avoid repeated yfinance lookups
_info_cache: dict[str, dict] = {}


# --- Watchlist data model ---

class Watchlist(BaseModel):
    id: str
    name: str
    symbols: list[str]


class WatchlistCreate(BaseModel):
    name: str


class WatchlistRename(BaseModel):
    name: str


class WatchlistAddSymbol(BaseModel):
    symbol: str


# In-memory watchlists
_next_id = 2
_watchlists: list[dict] = [
    {"id": "1", "name": "Main", "symbols": ["AAPL", "MSFT", "NVDA"]},
]


def _get_all_symbols() -> list[str]:
    """Return deduplicated list of all symbols across all watchlists."""
    seen: set[str] = set()
    result: list[str] = []
    for wl in _watchlists:
        for s in wl["symbols"]:
            if s not in seen:
                seen.add(s)
                result.append(s)
    return result


# Backward-compatible flat list used by engine start
_tracked_tickers: list[str] = _get_all_symbols()


def _sync_tracked():
    """Keep _tracked_tickers in sync with watchlists."""
    global _tracked_tickers
    _tracked_tickers.clear()
    _tracked_tickers.extend(_get_all_symbols())


# --- Flat ticker endpoints (backward compat) ---

class TickerAdd(BaseModel):
    symbol: str


@router.get("", response_model=list[str])
async def list_tickers():
    _sync_tracked()
    return _tracked_tickers


@router.post("", response_model=list[str])
async def add_ticker(body: TickerAdd):
    symbol = body.symbol.upper()
    # Add to first watchlist by default
    if _watchlists and symbol not in _watchlists[0]["symbols"]:
        _watchlists[0]["symbols"].append(symbol)
    _sync_tracked()
    return _tracked_tickers


@router.delete("/{symbol}")
async def remove_ticker(symbol: str):
    symbol = symbol.upper()
    removed = False
    for wl in _watchlists:
        if symbol in wl["symbols"]:
            wl["symbols"].remove(symbol)
            removed = True
    if not removed:
        raise HTTPException(status_code=404, detail="Ticker not found")
    _sync_tracked()
    return {"removed": symbol}


# --- Watchlist CRUD endpoints ---

@router.get("/watchlists", response_model=list[Watchlist])
async def list_watchlists():
    return [Watchlist(**wl) for wl in _watchlists]


@router.post("/watchlists", response_model=Watchlist)
async def create_watchlist(body: WatchlistCreate):
    global _next_id
    wl = {"id": str(_next_id), "name": body.name, "symbols": []}
    _next_id += 1
    _watchlists.append(wl)
    return Watchlist(**wl)


@router.put("/watchlists/{watchlist_id}", response_model=Watchlist)
async def rename_watchlist(watchlist_id: str, body: WatchlistRename):
    for wl in _watchlists:
        if wl["id"] == watchlist_id:
            wl["name"] = body.name
            return Watchlist(**wl)
    raise HTTPException(status_code=404, detail="Watchlist not found")


@router.delete("/watchlists/{watchlist_id}")
async def delete_watchlist(watchlist_id: str):
    for i, wl in enumerate(_watchlists):
        if wl["id"] == watchlist_id:
            _watchlists.pop(i)
            _sync_tracked()
            return {"deleted": watchlist_id}
    raise HTTPException(status_code=404, detail="Watchlist not found")


@router.post("/watchlists/{watchlist_id}/symbols", response_model=Watchlist)
async def add_symbol_to_watchlist(watchlist_id: str, body: WatchlistAddSymbol):
    symbol = body.symbol.upper()
    for wl in _watchlists:
        if wl["id"] == watchlist_id:
            if symbol not in wl["symbols"]:
                wl["symbols"].append(symbol)
            _sync_tracked()
            return Watchlist(**wl)
    raise HTTPException(status_code=404, detail="Watchlist not found")


@router.delete("/watchlists/{watchlist_id}/symbols/{symbol}", response_model=Watchlist)
async def remove_symbol_from_watchlist(watchlist_id: str, symbol: str):
    symbol = symbol.upper()
    for wl in _watchlists:
        if wl["id"] == watchlist_id:
            if symbol in wl["symbols"]:
                wl["symbols"].remove(symbol)
                _sync_tracked()
                return Watchlist(**wl)
            raise HTTPException(status_code=404, detail="Symbol not in watchlist")
    raise HTTPException(status_code=404, detail="Watchlist not found")


# --- Ticker info ---

@router.get("/{symbol}/info")
async def get_ticker_info(symbol: str):
    symbol = symbol.upper()
    if symbol in _info_cache:
        return _info_cache[symbol]

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        result = {
            "symbol": symbol,
            "name": info.get("longName") or info.get("shortName") or symbol,
        }
        _info_cache[symbol] = result
        return result
    except Exception as e:
        logger.error("ticker_info_error", symbol=symbol, error=str(e))
        return {"symbol": symbol, "name": symbol}
