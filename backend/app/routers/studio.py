"""Admin Daily Studio: the share-ready drafts written from each day's Committee Casebook, and the
weekly recap review. Admin-only. Nothing here posts to any social network: the owner copies a
draft and posts it by hand. A recap reaches the public blog only when published here."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import db, studio
from ..auth import TokenPayload, require_admin
from ..rate_limit import rate_limit_admin_mutations

router = APIRouter(
    prefix="/api/admin/studio",
    tags=["studio"],
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_mutations)],
)


class RunRequest(BaseModel):
    recap: bool = False


class StatusRequest(BaseModel):
    status: Literal["published", "rejected", "pending"]


@router.get("/drafts")
async def drafts(limit: int = Query(60, ge=1, le=300), kind: Optional[str] = None) -> dict[str, Any]:
    return {"drafts": await run_in_threadpool(studio.list_drafts, limit, kind)}


@router.post("/run")
async def run(body: RunRequest, user: TokenPayload = Depends(require_admin)) -> dict[str, Any]:
    """Write today's drafts now (the same step that runs after the daily committee); recap=true also writes this week's recap."""
    result = await studio.run(force_recap=body.recap)
    db.log_audit(user.sub, "studio.run", "studio", result.get("date"), {"recap": body.recap})
    return result


@router.post("/drafts/{item_id}/status")
async def set_status(item_id: str, body: StatusRequest, user: TokenPayload = Depends(require_admin)) -> dict[str, Any]:
    item = await run_in_threadpool(studio.set_status, item_id, body.status)
    if item is None:
        raise HTTPException(status_code=404, detail="No recap with that id")
    db.log_audit(user.sub, f"studio.recap.{body.status}", "studio", item_id, {})
    return item
