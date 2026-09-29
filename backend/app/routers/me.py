"""Self-service endpoints any logged-in user (viewer or admin) can call
on their own behalf -- distinct from /api/admin/* (admin-role-gated CRUD
over every agent/orchestration) and from the fully public /api/* data
endpoints. Backs the "subscribe to agents; only those run for my report"
feature: a user's subscriptions are stored on their own `users` row
(db.update_user), and running "my report" filters the seeded Investment
Committee orchestration's agent list down to that subscription before
calling the exact same orchestration.run_orchestration() the admin-
triggered run already uses -- no separate execution path to maintain.

System accounts (the bootstrap admin, and the opt-in dev users in app/auth.py) have no real `users`
row to persist a subscription onto -- PUT rejects them with a clear
400 rather than silently no-op'ing or crashing on a None row.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status

from .. import data_source as ds
from .. import flags
from .. import db
from .. import jobs
from .. import orchestration
from ..auth import RESERVED_USERNAMES, TokenPayload, get_current_user
from .. import llm_router
from ..rate_limit import check_user_heavy, check_user_report
from ..models import (
    AgentSubscriptions,
    AgentSummary,
    Entitlements,
    JobAccepted,
    JobStatus,
    OrchestrationConfig,
    OrchestrationRunRequest,
    VerifiedSignalResponse,
)
from ..scripts.seed_agents import ORCHESTRATION_NAME

router = APIRouter(prefix="/api/me", tags=["me"])

# Real free-tier quota (Plan-correction.MD's "5 free verified signals").
# Dev accounts (admin/user) have no `users` row to persist a count on and
# aren't real customers -- they bypass the quota entirely rather than
# being blocked, the same "no row = the more permissive path" convention
# run_my_report already uses for subscriptions.
FREE_VERIFIED_SIGNAL_LIMIT = 5

# Letters/digits with an optional class/exchange suffix (BRK.B, BF-B). This
# only exists to reject junk and injection characters before the value is
# used as a lookup key or written to the audit log -- an unknown but
# well-formed symbol still gets the normal 404 below.
_TICKER_RE = re.compile(r"^[A-Z0-9]{1,10}([.-][A-Z0-9]{1,3})?$")


def _require_real_user_row(user: TokenPayload) -> Dict[str, Any]:
    if user.sub.lower() in RESERVED_USERNAMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Dev accounts (admin/user) can't save preferences -- sign up for a real account",
        )
    row = db.get_user_by_username(user.sub)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return row


@router.get("/agents", response_model=List[AgentSummary])
async def list_available_agents(user: TokenPayload = Depends(get_current_user)):
    return db.list_agents()


@router.get("/agent-subscriptions", response_model=AgentSubscriptions)
async def get_my_subscriptions(user: TokenPayload = Depends(get_current_user)):
    if user.sub.lower() in RESERVED_USERNAMES:
        return AgentSubscriptions(agent_ids=[])
    row = db.get_user_by_username(user.sub)
    return AgentSubscriptions(agent_ids=(row or {}).get("subscribed_agent_ids") or [])


@router.put("/agent-subscriptions", response_model=AgentSubscriptions)
async def set_my_subscriptions(body: AgentSubscriptions, user: TokenPayload = Depends(get_current_user)):
    row = _require_real_user_row(user)
    known_ids = {a["id"] for a in db.list_agents()}
    unknown = [aid for aid in body.agent_ids if aid not in known_ids]
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown agent id(s): {unknown}")
    db.update_user(row["id"], {"subscribed_agent_ids": body.agent_ids})
    db.log_audit(user.sub, "me.agent_subscriptions_updated", "user", row["id"], {"agent_ids": body.agent_ids})
    return AgentSubscriptions(agent_ids=body.agent_ids)


@router.post("/run-report", response_model=JobAccepted, status_code=202)
async def run_my_report(body: OrchestrationRunRequest, user: TokenPayload = Depends(get_current_user)):
    """Runs the seeded Investment Committee orchestration filtered to
    the caller's own subscribed agents -- falls back to that
    orchestration's full agent list if the caller has no subscription
    set (every dev account, and any real account that hasn't visited
    the subscriptions page yet), so "no subscription" means "everyone",
    not "nobody"."""
    if not flags.flag("output.user_reports"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Report generation is switched off for now.")
    check_user_report(user.sub)  # LLM fan-out: tight hourly per-user budget
    orchestrations_by_name = {o["name"]: o for o in db.list_orchestrations()}
    orch_data = orchestrations_by_name.get(ORCHESTRATION_NAME)
    if orch_data is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{ORCHESTRATION_NAME!r} orchestration isn't seeded yet -- run app.scripts.seed_agents first",
        )
    orch = OrchestrationConfig(**orch_data)

    subscribed: Optional[List[str]] = None
    if user.sub.lower() not in RESERVED_USERNAMES:
        row = db.get_user_by_username(user.sub)
        subscribed = (row or {}).get("subscribed_agent_ids") or None

    if subscribed:
        filtered_ids = [aid for aid in orch.agent_ids if aid in set(subscribed)]
        orch = orch.model_copy(update={"agent_ids": filtered_ids})

    job = jobs.new_job("my_agents_run", owner=user.sub)
    job_id = job["job_id"]
    db.log_audit(user.sub, "me.run_report", "orchestration", orch_data["id"], {"job_id": job_id, "agent_count": len(orch.agent_ids)})

    async def _work():
        # Runs started by end users may carry their own text; LLM_FAILOVER_USER_RUNS=0
        # keeps those on the requested provider only (see llm_router's PRIVACY note).
        result = await orchestration.run_orchestration(orch, body.input, job_id=job_id, allow_failover=llm_router.user_runs_may_fail_over())

        # Publish the orchestration report through the single exit
        from .. import publish
        # Convert result to text for publishing
        import json
        result_text = json.dumps(result, default=str, separators=(',', ':'))
        content_ref = f"user_report:{user.sub}:{job_id}"
        pub_result = publish.publish(
            channel="user_report",
            text=result_text,
            run_ids=(),  # user reports don't have committee run_ids
            content_ref=content_ref,
            is_html=False,
            committee_output=False,
        )

        # In enforce mode, held reports return a generic message
        if not pub_result.allowed:
            return {"held": True, "message": "This report is being reviewed."}
        return result

    asyncio.create_task(jobs.run_job(job_id, _work))
    return JobAccepted(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_my_job(job_id: str, user: TokenPayload = Depends(get_current_user)):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    if job.get("owner") != user.sub:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your job")
    return job


@router.get("/entitlements", response_model=Entitlements)
async def get_my_entitlements(user: TokenPayload = Depends(get_current_user)):
    if user.sub.lower() in RESERVED_USERNAMES:
        return Entitlements(limit=FREE_VERIFIED_SIGNAL_LIMIT, used=0, remaining=FREE_VERIFIED_SIGNAL_LIMIT)
    row = db.get_user_by_username(user.sub)
    used = (row or {}).get("verified_signal_count", 0)
    return Entitlements(
        limit=FREE_VERIFIED_SIGNAL_LIMIT,
        used=used,
        remaining=max(0, FREE_VERIFIED_SIGNAL_LIMIT - used),
    )


@router.post("/verify/{ticker}", response_model=VerifiedSignalResponse)
async def verify_ticker(ticker: str, user: TokenPayload = Depends(get_current_user)):
    """The real endpoint StepVerify.tsx's mock has been waiting on. Meters
    against the real per-account free-tier quota (read-modify-write on
    the user's own row, same as set_my_subscriptions above -- no
    distributed lock, so two truly concurrent calls from the same account
    could both slip through on the last unit; an acceptable soft-limit
    risk for a free-tier nudge, not a billing-grade guarantee)."""
    check_user_heavy(user.sub)
    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid ticker")
    is_dev = user.sub.lower() in RESERVED_USERNAMES
    row: Optional[Dict[str, Any]] = None
    used = 0
    if not is_dev:
        row = db.get_user_by_username(user.sub)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        used = row.get("verified_signal_count", 0)
        if used >= FREE_VERIFIED_SIGNAL_LIMIT:
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=f"Free verification limit reached ({FREE_VERIFIED_SIGNAL_LIMIT}/{FREE_VERIFIED_SIGNAL_LIMIT} used).",
            )

    signals = await asyncio.to_thread(ds.get_live_signals)
    match = next((s for s in signals["signals"] if s["symbol"] == symbol), None)
    if match is None:
        known = ", ".join(sorted(ds.STOCK_INFO))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No signal for '{symbol}'. Known symbols: {known}",
        )

    if not is_dev:
        used += 1
        db.update_user(row["id"], {"verified_signal_count": used})
        db.log_audit(user.sub, "me.verify_signal", "signal", symbol, {"verified_signal_count": used})

    remaining = FREE_VERIFIED_SIGNAL_LIMIT if is_dev else max(0, FREE_VERIFIED_SIGNAL_LIMIT - used)
    return VerifiedSignalResponse(
        signal=match,
        verified_at=datetime.now(timezone.utc).isoformat(),
        entitlements=Entitlements(limit=FREE_VERIFIED_SIGNAL_LIMIT, used=0 if is_dev else used, remaining=remaining),
    )
