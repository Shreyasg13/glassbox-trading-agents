from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool

from .. import auth as auth_module
from .. import db
from ..models import LoginRequest, SignupRequest, Token
from ..rate_limit import login_lockout, rate_limit_login, rate_limit_signup
from ..auth import require_role

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=Token, dependencies=[Depends(require_role("public")), Depends(rate_limit_login)])
async def login(payload: LoginRequest):
    lockout_key = payload.username.strip().lower()
    # Per-account failed-attempt budget (on top of the per-IP one above) so
    # guesses spread across many IPs still get stopped. Checked BEFORE
    # authenticate() so a locked account doesn't spend bcrypt time either.
    login_lockout.raise_if_blocked(lockout_key)
    # bcrypt is ~250ms of CPU by design; off the event loop so a burst of
    # logins can't stall every other request on this worker.
    identity = await run_in_threadpool(auth_module.authenticate, payload.username, payload.password)
    if identity is None:
        login_lockout.record(lockout_key)
        # Log the attempted username for audit trails, never the password.
        db.log_audit("unknown", "auth.login_failed", "auth", None, {"username": payload.username[:100]})
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    login_lockout.reset(lockout_key)
    token = auth_module.create_access_token(identity)
    db.log_audit(identity.sub, "auth.login_success", "auth", None, {})
    return Token(access_token=token, role=identity.role)


@router.post("/signup", response_model=Token, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_role("public")), Depends(rate_limit_signup)])
async def signup(payload: SignupRequest):
    try:
        identity = await run_in_threadpool(auth_module.signup, payload.username, payload.password)
    except auth_module.SignupError as exc:
        db.log_audit("unknown", "auth.signup_failed", "auth", None, {"username": payload.username[:100], "reason": str(exc)})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    token = auth_module.create_access_token(identity)
    db.log_audit(identity.sub, "auth.signup_success", "auth", None, {"role": identity.role})
    return Token(access_token=token, role=identity.role)
