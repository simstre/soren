import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import structlog

from app.api.ws.manager import ws_manager

logger = structlog.get_logger()
router = APIRouter()


@router.websocket("/ws/stream")
async def websocket_stream(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                action = msg.get("action")
                symbol = msg.get("symbol")
                if action == "subscribe" and symbol:
                    logger.info("ws_subscribe", symbol=symbol)
                elif action == "unsubscribe" and symbol:
                    logger.info("ws_unsubscribe", symbol=symbol)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
