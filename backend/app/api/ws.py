from __future__ import annotations

import asyncio
import hmac
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class RealtimeHub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._clients.add(websocket)

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(websocket)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event: dict) -> None:
        if not self._clients:
            return
        dead: list[WebSocket] = []
        for websocket in list(self._clients):
            try:
                await websocket.send_json(event)
            except Exception:
                dead.append(websocket)
        if dead:
            async with self._lock:
                for websocket in dead:
                    self._clients.discard(websocket)


def build_ws_router(hub: RealtimeHub, snapshot_factory, api_token: str = "") -> APIRouter:
    router = APIRouter()

    @router.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        expected = api_token.strip()
        if expected:
            supplied = websocket.query_params.get("token") or websocket.headers.get("x-api-key", "")
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                await websocket.close(code=1008)
                return
        await hub.connect(websocket)
        try:
            await websocket.send_json({"type": "state", "data": await snapshot_factory()})
            while True:
                # Client messages are ignored; this keeps disconnect detection alive.
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.debug("ws client dropped: %s", exc)
        finally:
            await hub.disconnect(websocket)

    return router
