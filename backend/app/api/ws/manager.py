from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
import numpy as np
import structlog

from app.schemas.ws_messages import WSMessage

logger = structlog.get_logger()


def _sanitize(obj: Any) -> Any:
    """Recursively convert numpy types to native Python types."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info("ws_client_connected", total=len(self.active_connections))

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)
        logger.info("ws_client_disconnected", total=len(self.active_connections))

    async def broadcast(self, message_type: str, payload: dict[str, Any]):
        msg = WSMessage(
            type=message_type,
            payload=_sanitize(payload),
            timestamp=datetime.now(timezone.utc),
        )
        data = msg.model_dump_json()
        disconnected = []
        for connection in self.active_connections:
            try:
                await connection.send_text(data)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.active_connections.remove(conn)


ws_manager = ConnectionManager()
