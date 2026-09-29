"""Google OAuth "Sign in with Google" -- see app/oauth_google.py for the
provider integration itself; this file is just the two HTTP endpoints
(start + callback), the CSRF state cookie, and the redirect back to the
frontend once a JWT is minted.

No server-side session store exists in this app (JWT bearer tokens,
stateless), so the OAuth `state` CSRF token is round-tripped through a
short-lived httpOnly cookie rather than server memory -- set on /start,
checked (and cleared) on /callback. Same-origin by design now that the
whole deployment lives on one domain (see deploy/Caddyfile), so no
cross-site cookie complications.

The minted JWT is handed back to the frontend via a URL fragment
(#token=...&role=...), not a query string -- fragments are never sent
to the server on the next request and don't show up in proxy/access
logs the way a query string would. frontend/app/oauth/complete/page.tsx
is the page that reads it.
"""
from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, HTTPException, Request, status, Depends
from fastapi.responses import RedirectResponse

from .. import auth as auth_module
from .. import db
from .. import oauth_google
from ..auth import require_role

router = APIRouter(prefix="/auth/oauth", tags=["auth"])

STATE_COOKIE = "glassbox_oauth_state"
STATE_MAX_AGE_S = 600


def _app_origin() -> str:
    domain = os.environ.get("APP_DOMAIN")
    if not domain:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OAuth not configured")
    return f"https://{domain}"


def _redirect_uri(provider: str) -> str:
    return f"{_app_origin()}/auth/oauth/{provider}/callback"


@router.get("/google/start", dependencies=[Depends(require_role("public"))])
async def google_start():
    if not oauth_google.is_configured():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Google sign-in isn't configured yet")
    state = secrets.token_urlsafe(24)
    url = oauth_google.build_authorize_url(_redirect_uri("google"), state)
    resp = RedirectResponse(url, status_code=status.HTTP_302_FOUND)
    resp.set_cookie(STATE_COOKIE, state, max_age=STATE_MAX_AGE_S, httponly=True, secure=True, samesite="lax")
    return resp


@router.get("/google/callback", dependencies=[Depends(require_role("public"))])
async def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    origin = _app_origin()
    fail_redirect = RedirectResponse(f"{origin}/login?oauth_error=1", status_code=status.HTTP_302_FOUND)

    cookie_state = request.cookies.get(STATE_COOKIE)
    if error or not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state):
        db.log_audit("unknown", "auth.oauth_login_failed", "auth", None, {"provider": "google", "reason": "state_or_code"})
        return fail_redirect

    profile = await oauth_google.exchange_code(code, _redirect_uri("google"))
    if profile is None:
        db.log_audit("unknown", "auth.oauth_login_failed", "auth", None, {"provider": "google", "reason": "exchange_failed"})
        return fail_redirect

    identity = auth_module.oauth_login("google", profile["sub"], profile["email"])
    token = auth_module.create_access_token(identity)
    db.log_audit(identity.sub, "auth.oauth_login_success", "auth", None, {"provider": "google"})

    resp = RedirectResponse(
        f"{origin}/oauth/complete#token={token}&role={identity.role}",
        status_code=status.HTTP_302_FOUND,
    )
    resp.delete_cookie(STATE_COOKIE)
    return resp
