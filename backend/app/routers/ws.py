"""Live signal push over WebSocket.

Broadcasts the same payload as GET /api/live-signals on an interval so
clients get push updates instead of polling.

Phase 6: `ConnectionManager.broadcast` sends to all connections
concurrently with a per-connection timeout instead of awaiting each
`send_text` sequentially -- previously one slow/stalled client could
delay delivery to every other client. The broadcast loop also reads
through `cache.hot_read_cache`, the same cache REST callers use, so a
tick here doesn't force its own redundant parquet/JSON read.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from .. import data_source as ds
from ..cache import hot_read_cache
from ..auth import require_role

router = APIRouter(tags=["ws"])

PUSH_INTERVAL_SECONDS = 3
SEND_TIMEOUT_SECONDS = 2.0
MAX_WS_CONNECTIONS = int(os.environ.get("WS_MAX_CONNECTIONS", "300"))


class ConnectionManager:
    def __init__(self) -> None:
        self.active: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active:
            self.active.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        if not self.active:
            return
        payload = json.dumps(message)

        async def _send(connection: WebSocket) -> Optional[WebSocket]:
            try:
                await asyncio.wait_for(connection.send_text(payload), timeout=SEND_TIMEOUT_SECONDS)
                return None
            except Exception:
                return connection

        results = await asyncio.gather(*(_send(c) for c in list(self.active)))
        for stale in results:
            if stale is not None:
                self.disconnect(stale)


manager = ConnectionManager()
_broadcaster_task: asyncio.Task | None = None


async def _get_live_signals_cached() -> dict:
    return await hot_read_cache.get_or_compute_async(
        "live_signals", ds.live_signals_source_paths(), ds.get_live_signals
    )


async def _broadcast_loop() -> None:
    while True:
        try:
            payload = await _get_live_signals_cached()
            await manager.broadcast({"type": "signals", "data": payload})
        except Exception as exc:  # keep the loop alive across transient read errors
            await manager.broadcast({"type": "error", "message": str(exc)})
        await asyncio.sleep(PUSH_INTERVAL_SECONDS)


def ensure_broadcaster_started() -> None:
    global _broadcaster_task
    if _broadcaster_task is None or _broadcaster_task.done():
        _broadcaster_task = asyncio.create_task(_broadcast_loop())


@router.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket, role: str = Depends(require_role("public"))) -> None:
    # Public and unauthenticated by design (it's the demo's live ticker), so
    # cap concurrent sockets -- otherwise opening thousands of idle
    # connections is a free way to exhaust file descriptors and memory.
    if len(manager.active) >= MAX_WS_CONNECTIONS:
        await websocket.close(code=1013)  # "try again later"
        return
    ensure_broadcaster_started()
    await manager.connect(websocket)
    try:
        # Send an immediate snapshot so the client doesn't wait a full interval.
        await websocket.send_text(json.dumps({"type": "signals", "data": await _get_live_signals_cached()}))
        while True:
            # We don't expect inbound messages, but read to detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
