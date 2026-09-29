"""Visit analytics: a public, cookie-free beacon and an admin-only summary. See app/analytics.py for the privacy rules."""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import analytics, db
from ..auth import require_admin, require_role
from ..rate_limit import SlidingWindowLimiter, client_key

log = logging.getLogger("glassbox.analytics")

public_router = APIRouter(prefix="/api/analytics", tags=["analytics"])
admin_router = APIRouter(prefix="/api/admin/analytics", tags=["analytics"], dependencies=[Depends(require_admin)])

# Per visitor IP. Generous for real browsing (each page view is one call) but stops anyone flooding the table.
_hit_limiter = SlidingWindowLimiter(120, window_s=60.0, detail="Too many requests")


class Hit(BaseModel):
    path: str = Field(max_length=300)
    referrer: str = Field(default="", max_length=500)
    utm_source: str = Field(default="", max_length=100)
    utm_medium: str = Field(default="", max_length=100)
    utm_campaign: str = Field(default="", max_length=100)


@public_router.post("/hit", status_code=204, dependencies=[Depends(require_role("public"))])
async def hit(body: Hit, request: Request) -> Response:
    """Always answers 204: analytics must never surface an error to a visitor."""
    try:
        _hit_limiter.check(client_key(request))
        dnt = request.headers.get("dnt") == "1" or request.headers.get("sec-gpc") == "1"
        await run_in_threadpool(
            lambda: analytics.record_hit(
                body.path, body.referrer, body.utm_source, body.utm_medium, body.utm_campaign,
                ip=client_key(request), ua=request.headers.get("user-agent", ""), dnt=dnt,
            )
        )
    except Exception as exc:  # noqa: BLE001 -- including the 429: a rate-limited beacon is simply dropped
        log.debug("analytics hit dropped: %s", type(exc).__name__)
    return Response(status_code=204)


@admin_router.get("/summary")
async def summary(days: int = Query(30, ge=1, le=180)) -> Dict[str, Any]:
    return await run_in_threadpool(lambda: analytics.summary(days, db.list_users()))
