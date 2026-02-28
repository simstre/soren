from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WSMessage(BaseModel):
    type: Literal[
        "candle_update",
        "trade_opened",
        "trade_closed",
        "position_update",
        "signal_fired",
        "engine_state",
        "metrics_update",
        "replay_state",
        "replay_candle",
        "replay_trade",
        "replay_complete",
        "replay_positions",
        "ticker_rescan",
    ]
    payload: dict[str, Any]
    timestamp: datetime
