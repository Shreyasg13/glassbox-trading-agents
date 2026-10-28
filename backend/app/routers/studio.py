"""Admin Daily Studio: the share-ready drafts written from each day's Committee Casebook, and the
weekly recap review. Admin-only. Nothing here posts to any social network: the owner copies a
draft and posts it by hand. A recap reaches the public blog only when published here."""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import casebook, db, editorial, studio
from ..auth import TokenPayload, require_admin
from ..rate_limit import rate_limit_admin_mutations

router = APIRouter(
    prefix="/api/admin/studio",
    tags=["studio"],
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_mutations)],
)


class RunRequest(BaseModel):
    recap: bool = False


class EditorialRequest(BaseModel):
    date: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    all_days: bool = False  # backfill: every day without a published pick


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


@router.post("/editorial/run")
async def run_editorial(body: EditorialRequest, user: TokenPayload = Depends(require_admin)) -> dict[str, Any]:
    """Ask the editorial board for a day's title, dek and tags (default: the latest day). Picks wait here for approval."""
    days = sorted(await run_in_threadpool(casebook._by_date), reverse=True) if body.all_days else [body.date]
    results = [await editorial.run(d) for d in days]
    db.log_audit(user.sub, "studio.editorial.run", "studio", body.date, {"all_days": body.all_days, "days": len(results)})
    return {"results": results}


@router.post("/drafts/{item_id}/status")
async def set_status(item_id: str, body: StatusRequest, user: TokenPayload = Depends(require_admin)) -> dict[str, Any]:
    item = await run_in_threadpool(studio.set_status, item_id, body.status)
    if item is None:
        raise HTTPException(status_code=404, detail="No recap or editorial pick with that id")
    db.log_audit(user.sub, f"studio.{item['kind']}.{body.status}", "studio", item_id, {})
    return item
