"""Per-user digest settings (logged in) and the confirm/unsubscribe links (public, HMAC-signed).
See app/user_digest.py for the design rules."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .. import db, user_digest
from ..auth import TokenPayload, get_current_user, require_role
from .me import _require_real_user_row

me_router = APIRouter(prefix="/api/me/digest", tags=["me"])
public_router = APIRouter(prefix="/api/digest", tags=["digest"])


class DigestPrefs(BaseModel):
    enabled: bool
    email: Optional[str] = Field(default=None, max_length=254)
    frequency: str = "daily"


@me_router.get("")
async def get_prefs(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    return user_digest.public_view(_require_real_user_row(user))


@me_router.put("")
async def put_prefs(body: DigestPrefs, user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    row = _require_real_user_row(user)
    try:
        out = user_digest.apply_prefs(row, body.enabled, body.email, body.frequency)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    out["confirmation"] = None
    if out.pop("confirmation_needed"):
        fresh = db.get_user_by_username(user.sub) or row
        out["confirmation"] = await run_in_threadpool(user_digest.send_confirmation, fresh)
    return out


@me_router.post("/preview")
async def send_preview(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    row = _require_real_user_row(user)
    return await run_in_threadpool(user_digest.send_preview, row)


def _page(title: str, msg: str, code: int = 200) -> HTMLResponse:
    html = (
        f'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title><body style="font:16px/1.6 -apple-system,Segoe UI,Arial,sans-serif;max-width:480px;margin:15vh auto;padding:0 16px;color:#111827;">'
        f"<h1 style=\"font-size:20px;\">{title}</h1><p>{msg}</p></body>"
    )
    return HTMLResponse(html, status_code=code, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _row_by_id(user_id: str) -> Optional[Dict[str, Any]]:
    return next((r for r in db.list_users() if r.get("id") == user_id), None)


@public_router.get("/confirm", dependencies=[Depends(require_role("public"))])
async def confirm(u: str = Query(max_length=64), t: str = Query(max_length=64)) -> HTMLResponse:
    row = await run_in_threadpool(_row_by_id, u)
    if row is None or not await run_in_threadpool(user_digest.confirm_pending, row, t):
        return _page("Link not valid", "This confirmation link is invalid or out of date. Request a new one from your GlassBox dashboard.", 400)
    return _page("Email confirmed", "Your daily digest is on. You can change or stop it anytime from your dashboard.")


@public_router.api_route("/unsubscribe", methods=["GET", "POST"], dependencies=[Depends(require_role("public"))])
async def unsubscribe(u: str = Query(max_length=64), t: str = Query(max_length=64)) -> HTMLResponse:
    row = await run_in_threadpool(_row_by_id, u)
    if row is None or not user_digest.check_token("unsubscribe", u, t):
        return _page("Link not valid", "This unsubscribe link is invalid. You can turn the digest off from your GlassBox dashboard.", 400)
    await run_in_threadpool(user_digest.unsubscribe, row)
    return _page("Unsubscribed", "You won't get the daily digest any more. You can turn it back on from your dashboard.")
