"""Live progress stream for a background job (agent test-run,
orchestration run, or report generation).

Frame envelope matches /ws/signals' {"type", "data"} convention -- see
the doc block at the bottom of models.py. The socket closes right after
the terminal `status` frame (job_events.JobBroadcaster.finish does this).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends

from .. import db
from ..job_events import broadcaster
from ..auth import require_role

router = APIRouter(tags=["ws"])


@router.websocket("/ws/jobs/{job_id}")
async def ws_job(websocket: WebSocket, job_id: str, role: str = Depends(require_role("public"))) -> None:
    await websocket.accept()
    job = db.get_job(job_id)
    if job is None:
        await websocket.send_text(
            json.dumps({"type": "status", "data": {"job_id": job_id, "status": "error", "error": "job not found"}})
        )
        await websocket.close()
        return

    # Job already finished before the client connected: send the final
    # status and close immediately rather than waiting for a broadcast
    # that will never come.
    if job.get("status") in ("done", "error"):
        await websocket.send_text(json.dumps({"type": "status", "data": job}))
        await websocket.close()
        return

    broadcaster.register(job_id, websocket)
    await websocket.send_text(json.dumps({"type": "status", "data": job}))
    try:
        while True:
            # No inbound messages are expected; reading just detects disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unregister(job_id, websocket)
