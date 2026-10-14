"""Admin-gated CRUD for the Agent Factory (Phase 4) plus test-run,
orchestration-run, job polling, and provider health (Phase 5).

Phase 6: every mutation is rate-limited (rate_limit.py) and written to
the audit log (db.log_audit), and /api/admin/audit-log + /api/admin/llm-calls
expose that history + the LLM cost/latency log to the admin dashboard.

Prefix is /api/admin, not /admin -- the frontend's own /admin/* pages
(Agent Factory UI) live at that exact path, so under single-domain
path-based routing (see deploy/Caddyfile) a bare /admin prefix here
would collide with them. Joining the /api convention already used by
data.py/monte_carlo.py/tts.py sidesteps that.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator

from .. import claims, compliance, db, jobs, narrative, orchestration, publish, snapshot_store, verification
from ..verification import gate, health
from ..auth import TokenPayload, require_admin
from ..models import (
    AgentConfig,
    AgentTestRunRequest,
    JobAccepted,
    JobStatus,
    OrchestrationConfig,
    OrchestrationRunRequest,
    PaginatedAuditLog,
    PaginatedLLMCalls,
    Provider,
    ProviderHealth,
)
from ..providers.factory import get_provider
from ..rate_limit import rate_limit_admin_mutations

router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_mutations)],
)


# ---- Agents ----

@router.get("/agents", response_model=List[AgentConfig])
async def list_agents():
    return db.list_agents()


@router.post("/agents", response_model=AgentConfig, status_code=201)
async def create_agent(agent: AgentConfig, user: TokenPayload = Depends(require_admin)):
    created = db.create_agent(agent.model_dump(exclude={"id"}))
    db.log_audit(user.sub, "agent.create", "agent", created["id"], {"name": created.get("name")})
    return created


@router.get("/agents/{agent_id}", response_model=AgentConfig)
async def get_agent(agent_id: str):
    agent = db.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.put("/agents/{agent_id}", response_model=AgentConfig)
async def update_agent(agent_id: str, agent: AgentConfig, user: TokenPayload = Depends(require_admin)):
    updated = db.update_agent(agent_id, agent.model_dump(exclude={"id"}))
    if updated is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    db.log_audit(user.sub, "agent.update", "agent", agent_id, {"name": updated.get("name")})
    return updated


@router.delete("/agents/{agent_id}", status_code=204)
async def delete_agent(agent_id: str, user: TokenPayload = Depends(require_admin)):
    if not db.delete_agent(agent_id):
        raise HTTPException(status_code=404, detail="Agent not found")
    db.log_audit(user.sub, "agent.delete", "agent", agent_id, {})


# ---- Orchestrations ----

@router.get("/orchestrations", response_model=List[OrchestrationConfig])
async def list_orchestrations():
    return db.list_orchestrations()


@router.post("/orchestrations", response_model=OrchestrationConfig, status_code=201)
async def create_orchestration(orch: OrchestrationConfig, user: TokenPayload = Depends(require_admin)):
    created = db.create_orchestration(orch.model_dump(exclude={"id"}))
    db.log_audit(user.sub, "orchestration.create", "orchestration", created["id"], {"name": created.get("name")})
    return created


@router.get("/orchestrations/{orch_id}", response_model=OrchestrationConfig)
async def get_orchestration(orch_id: str):
    orch = db.get_orchestration(orch_id)
    if orch is None:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    return orch


@router.put("/orchestrations/{orch_id}", response_model=OrchestrationConfig)
async def update_orchestration(orch_id: str, orch: OrchestrationConfig, user: TokenPayload = Depends(require_admin)):
    updated = db.update_orchestration(orch_id, orch.model_dump(exclude={"id"}))
    if updated is None:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    db.log_audit(user.sub, "orchestration.update", "orchestration", orch_id, {"name": updated.get("name")})
    return updated


@router.delete("/orchestrations/{orch_id}", status_code=204)
async def delete_orchestration(orch_id: str, user: TokenPayload = Depends(require_admin)):
    if not db.delete_orchestration(orch_id):
        raise HTTPException(status_code=404, detail="Orchestration not found")
    db.log_audit(user.sub, "orchestration.delete", "orchestration", orch_id, {})


# ---- Test-run / orchestration-run (Phase 4/5) ----
#
# Both endpoints return immediately with a job id -- the actual agent/LLM
# work happens in a background task per PERFORMANCE_AND_ORCHESTRATION.md
# section 1 (never await an LLM call inline in a request handler).

@router.post("/agents/{agent_id}/test-run", response_model=JobAccepted, status_code=202)
async def test_run_agent(agent_id: str, body: AgentTestRunRequest, user: TokenPayload = Depends(require_admin)):
    agent_data = db.get_agent(agent_id)
    if agent_data is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent = AgentConfig(**agent_data)
    job = jobs.new_job("agent_test_run")
    job_id = job["job_id"]
    db.log_audit(user.sub, "agent.test_run", "agent", agent_id, {"job_id": job_id})

    async def _work():
        return await orchestration.run_agent(agent, body.input, job_id=job_id)

    asyncio.create_task(jobs.run_job(job_id, _work))
    return JobAccepted(job_id=job_id)


@router.post("/orchestrations/{orch_id}/run", response_model=JobAccepted, status_code=202)
async def run_orchestration_endpoint(
    orch_id: str, body: OrchestrationRunRequest, user: TokenPayload = Depends(require_admin)
):
    orch_data = db.get_orchestration(orch_id)
    if orch_data is None:
        raise HTTPException(status_code=404, detail="Orchestration not found")
    orch = OrchestrationConfig(**orch_data)
    job = jobs.new_job("orchestration_run")
    job_id = job["job_id"]
    db.log_audit(user.sub, "orchestration.run", "orchestration", orch_id, {"job_id": job_id})

    async def _work():
        return await orchestration.run_orchestration(orch, body.input, job_id=job_id)

    asyncio.create_task(jobs.run_job(job_id, _work))
    return JobAccepted(job_id=job_id)


@router.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job(job_id: str):
    job = db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


# ---- Provider health (Phase 5) ----

@router.get("/providers/gemini/models")
async def gemini_models():
    """Why are Gemini calls failing? Compares the models agents are configured
    to use with the models this API key is actually offered, and shows which
    have recently 404'd and the free-tier ceilings we track."""
    from ..providers import gemini_quota
    from ..providers.factory import get_provider

    provider = get_provider("gemini")
    offered = await provider.available_models()
    configured = sorted(
        {
            m
            for a in db.list_agents()
            if a.get("provider") == "gemini"
            for m in [a.get("model"), *(a.get("fallback_models") or [])]
            if m
        }
    )
    return {
        "key_configured": bool(getattr(provider, "api_key", None)),
        "configured_models": configured,
        "offered_by_key": sorted(offered) if offered is not None else None,
        "configured_but_not_offered": [m for m in configured if offered is not None and m not in offered],
        "recently_404_retry_in_s": gemini_quota.unavailable_models(),
        "free_tier_ceilings": {m: {"rpm": r, "tpm": t, "rpd": d} for m, (r, t, d) in gemini_quota.GEMINI_LIMITS.items()},
    }


@router.get("/providers/health", response_model=List[ProviderHealth])
async def providers_health():
    from ..providers.factory import all_provider_names

    names: List[Provider] = all_provider_names()  # native + every OpenAI-compatible failover provider
    results = await asyncio.gather(*(get_provider(name).health_check() for name in names))
    return list(results)


class RoutingTestRequest(BaseModel):
    provider: Provider = "gemini"
    model: str = "gemini-2.5-flash-lite"
    prompt: str = Field(default="Reply with the single word: ready", max_length=500)


@router.get("/providers/routing")
async def routing_status(models: bool = False):
    """Failover routing: the order, which providers are configured (a key is all
    it takes), which are cooling down after a quota error and for how long, and
    where to get a free key for the rest."""
    from .. import llm_router

    return await llm_router.status(include_models=models)


@router.post("/providers/routing/test")
async def routing_test(body: RoutingTestRequest, user: TokenPayload = Depends(require_admin)):
    """Send one tiny prompt through the router and report which provider answered
    (or why every one failed). The quickest way to prove failover works."""
    from .. import llm_router

    try:
        r = await llm_router.complete_routed(body.provider, body.model, body.prompt, max_tokens=40, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        tried = getattr(exc, "tried", [])
        db.log_audit(user.sub, "routing.test_failed", "provider", None, {"requested": body.provider})
        raise HTTPException(status_code=503, detail={"error": str(exc)[:300], "tried": tried}) from None
    db.log_audit(user.sub, "routing.test", "provider", None, {"requested": body.provider, "answered": r.provider})
    return {"ok": True, "answered_by": r.provider, "model": r.model, "failed_over": r.provider != body.provider, "reply": r.text[:120], "skipped_or_failed": r.tried}


# ---- Observability (Phase 6) ----

@router.get("/audit-log", response_model=PaginatedAuditLog)
async def audit_log(limit: int = 50, offset: int = 0):
    items, total = db.list_audit_log(limit=limit, offset=offset)
    return {"items": items, "total": total}


@router.get("/llm-calls", response_model=PaginatedLLMCalls)
async def llm_calls(limit: int = 50, offset: int = 0):
    items, total = db.list_llm_calls_page(limit=limit, offset=offset)
    return {"items": items, "total": total}


# ---- Snapshots (S3 T10) ----


class SnapshotListItem(BaseModel):
    id: str
    source: str
    ticker: str
    as_of: str
    fetched_at: str
    payload_hash: str


@router.get("/snapshots")
async def list_snapshots(source: Optional[str] = None, ticker: Optional[str] = None, limit: int = 50) -> List[SnapshotListItem]:
    """Metadata only (no payload) for the admin inspector."""
    limit = max(1, min(limit, 500))
    items = snapshot_store.list(source=source, ticker=ticker, limit=limit)
    return [SnapshotListItem(**item) for item in items]


@router.get("/snapshots/{snap_id}")
async def get_snapshot(snap_id: str):
    """Full row including payload, for the admin detail view."""
    item = snapshot_store.get_by_id(snap_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return item


# ---- Claims (S3 T3) ----

from sqlalchemy import select
from ..migrated_tables import claims_table, committee_narratives_table


class ClaimItem(BaseModel):
    id: str
    run_id: str
    ticker: str
    metric: str
    value: float
    unit: str
    period: str
    source: str
    source_snapshot_id: Optional[str] = None
    source_path: Optional[str] = None
    text_span: Optional[str] = None
    created_at: str


@router.get("/claims")
async def list_claims(run_id: str, limit: int = 200) -> List[ClaimItem]:
    """All claims for a committee run_id."""
    limit = max(1, min(limit, 1000))
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(claims_table).where(claims_table.c.run_id == run_id).order_by(claims_table.c.created_at).limit(limit)
        ).fetchall()
    return [
        ClaimItem(
            id=r.id,
            run_id=r.run_id,
            ticker=r.ticker,
            metric=r.metric,
            value=r.value,
            unit=r.unit,
            period=r.period,
            source=r.source,
            source_snapshot_id=r.source_snapshot_id,
            source_path=r.source_path,
            text_span=r.text_span,
            created_at=r.created_at,
        )
        for r in rows
    ]


class NarrativeItem(BaseModel):
    run_id: str
    narrative: Optional[str] = None
    status: str
    attempts: int
    provider_requested: Optional[str] = None
    model_requested: Optional[str] = None
    provider_answered: Optional[str] = None
    model_answered: Optional[str] = None
    error: Optional[str] = None
    created_at: str


@router.get("/narratives/{run_id}")
async def get_narrative(run_id: str) -> NarrativeItem:
    """Narrative for a committee run_id."""
    with db.engine.connect() as conn:
        row = conn.execute(
            select(committee_narratives_table).where(committee_narratives_table.c.run_id == run_id)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Narrative not found")
    return NarrativeItem(
        run_id=row.run_id,
        narrative=row.narrative,
        status=row.status,
        attempts=row.attempts,
        provider_requested=row.provider_requested,
        model_requested=row.model_requested,
        provider_answered=row.provider_answered,
        model_answered=row.model_answered,
        error=row.error,
        created_at=row.created_at,
    )


# ---- Verification (S3 T4) ----

from sqlalchemy import select
from ..migrated_tables import verification_results_table


class VerificationResultItem(BaseModel):
    id: str
    run_id: str
    claim_id: Optional[str] = None
    check_type: str
    status: str  # pass | fail | warn
    expected: Optional[str] = None
    observed: Optional[str] = None
    reason: str
    created_at: str


class VerificationSummary(BaseModel):
    total: int
    passed: int
    failed: int
    warned: int
    ok: bool
    badge: str


def _verification_rows(run_id: str) -> List[Any]:
    with db.engine.connect() as conn:
        return conn.execute(
            select(verification_results_table)
            .where(verification_results_table.c.run_id == run_id)
            .order_by(verification_results_table.c.created_at)
        ).fetchall()


def _summary(rows: List[Any]) -> Dict[str, Any]:
    """The gate's own summarize(), so the admin API can never count differently from the gate (badge = fully verified claims)."""
    return gate.summarize([gate.Result(check_type=r.check_type, status=r.status, claim_id=r.claim_id,
                                       expected=r.expected, observed=r.observed, reason=r.reason) for r in rows])


@router.get("/verification")
async def get_verification(run_id: str) -> Dict[str, Any]:
    """All verification results for a committee run_id, plus summary."""
    rows = _verification_rows(run_id)
    results = [
        VerificationResultItem(
            id=r.id, run_id=r.run_id, claim_id=r.claim_id, check_type=r.check_type, status=r.status,
            expected=r.expected, observed=r.observed, reason=r.reason, created_at=r.created_at,
        )
        for r in rows
    ]
    return {"run_id": run_id, "results": results, "summary": _summary(rows)}


@router.get("/verification/summary")
async def get_verification_summary(date: str) -> Dict[str, Any]:
    """One summary per committee run of that date (Ask and challenger runs excluded)."""
    run_ids = [r["id"] for r in db.list_committee_runs_for_date(date) if not str(r["id"]).startswith(("ask:", "chal:"))]
    return {"date": date, "summaries": [{"run_id": rid, "summary": _summary(_verification_rows(rid))} for rid in run_ids]}


# ---- Compliance (S3 T8) ----

from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from ..migrated_tables import compliance_events_table

COMPLIANCE_ACTIONS = ("blocked", "rewritten", "flagged")


class ComplianceEventItem(BaseModel):
    id: str
    run_id: Optional[str] = None
    channel: str
    rule_id: str
    matched_text: str
    action: str  # blocked | rewritten | flagged
    created_at: str


def _compliance_bound(value: str, name: str, *, upper: bool) -> tuple[str, bool]:
    """A `from`/`to` query value as (created_at bound in the stored format, inclusive?). A bare date covers that whole day."""
    try:
        if len(value) == 10:
            day = _date.fromisoformat(value)
            if upper:
                return f"{(day + _timedelta(days=1)).isoformat()}T00:00:00.000000+00:00", False
            return f"{day.isoformat()}T00:00:00.000000+00:00", True
        return compliance.filter.iso_timestamp(_datetime.fromisoformat(value.replace("Z", "+00:00"))), True
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{name} must be a date (YYYY-MM-DD) or an ISO timestamp")


@router.get("/compliance/events")
async def list_compliance_events(
    from_: Optional[str] = Query(None, alias="from"),
    to: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = 100,
) -> List[ComplianceEventItem]:
    """Compliance filter hits, newest first."""
    limit = max(1, min(limit, 500))
    stmt = select(compliance_events_table)
    if from_:
        bound, _ = _compliance_bound(from_, "from", upper=False)
        stmt = stmt.where(compliance_events_table.c.created_at >= bound)
    if to:
        bound, inclusive = _compliance_bound(to, "to", upper=True)
        col = compliance_events_table.c.created_at
        stmt = stmt.where(col <= bound if inclusive else col < bound)
    if action:
        if action not in COMPLIANCE_ACTIONS:
            raise HTTPException(status_code=400, detail=f"action must be one of {', '.join(COMPLIANCE_ACTIONS)}")
        stmt = stmt.where(compliance_events_table.c.action == action)
    stmt = stmt.order_by(compliance_events_table.c.created_at.desc(), compliance_events_table.c.id).limit(limit)
    with db.engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [
        ComplianceEventItem(
            id=r.id, run_id=r.run_id, channel=r.channel, rule_id=r.rule_id, matched_text=r.matched_text or "",
            action=r.action, created_at=r.created_at,
        )
        for r in rows
    ]


@router.get("/compliance/rules")
async def list_compliance_rules() -> List[Dict[str, Any]]:
    """The rules the filter has loaded (from config/compliance_rules.json)."""
    try:
        return [r.public() for r in compliance.load_rules()]
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=f"compliance rules could not be loaded: {exc}")

# ---- Gate health (S3 T14) ----

GATE_HEALTH_DEFAULT_DAYS = 30
GATE_HEALTH_MAX_DAYS = 366


@router.get("/gate-health")
async def gate_health(
    from_: Optional[date] = Query(None, alias="from"),
    to: Optional[date] = Query(None),
) -> Dict[str, Any]:
    """Daily claim pass rate, top failing checks and top failing metrics for committee runs dated from..to (inclusive).
    Defaults to the last 30 days; a range longer than 366 days is refused. Read-only. The DB read and aggregation run
    in a worker thread so they never block the event loop."""
    end = to or datetime.now(timezone.utc).date()
    start = from_ or end - timedelta(days=GATE_HEALTH_DEFAULT_DAYS - 1)
    if start > end:
        raise HTTPException(status_code=400, detail="'from' must be on or before 'to'")
    if (end - start).days + 1 > GATE_HEALTH_MAX_DAYS:
        raise HTTPException(status_code=400, detail=f"Date range is longer than {GATE_HEALTH_MAX_DAYS} days")
    return await run_in_threadpool(health.load, start, end)


# ---- Quarantine (S3 T5) ----

from sqlalchemy import select
from ..migrated_tables import quarantine_items_table


class QuarantineItem(BaseModel):
    id: str
    channel: str
    run_id: Optional[str] = None
    content_ref: str
    stage: str  # A6 | A7
    status: str  # pending | approved | rejected | shadow
    reason: Optional[str] = None
    created_at: str
    decided_at: Optional[str] = None
    decided_by: Optional[str] = None


class QuarantineAction(BaseModel):
    action: str  # approve | reject
    reason: Optional[str] = None


def _iso_now() -> str:
    """Same timestamp format the quarantine_items table is written in elsewhere (publish.py's _iso_now)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _join_reasons(reasons_json: str) -> Optional[str]:
    """reasons_json is the JSON list publish.py's _create_quarantine_item stored; shown as one string."""
    try:
        reasons = json.loads(reasons_json) if reasons_json else []
    except (TypeError, ValueError):
        return None
    return "; ".join(reasons) if reasons else None


def _quarantine_row_or_404(item_id: str):
    with db.engine.connect() as conn:
        row = conn.execute(select(quarantine_items_table).where(quarantine_items_table.c.id == item_id)).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Quarantine item not found")
    return row


UNDECIDED_QUARANTINE_STATUSES = ("pending", "shadow")


def _undecided_quarantine_row_or_409(item_id: str):
    """The review routes decide an item once: approving a rejected item (or the reverse) silently would rewrite the audit
    trail, so an already-decided item is a 409 and must be quarantined again to be re-reviewed."""
    row = _quarantine_row_or_404(item_id)
    if row.status not in UNDECIDED_QUARANTINE_STATUSES:
        raise HTTPException(status_code=409, detail=f"Quarantine item already {row.status}")
    return row


def _quarantine_item_response(row) -> QuarantineItem:
    return QuarantineItem(
        id=row.id,
        channel=row.channel,
        run_id=row.run_id,
        content_ref=row.content_ref,
        stage=row.stage,
        status=row.status,
        reason=_join_reasons(row.reasons_json),
        created_at=row.created_at,
        decided_at=row.reviewed_at,
        decided_by=row.reviewer_id,
    )


@router.get("/quarantine", response_model=List[QuarantineItem])
async def list_quarantine(status: Optional[str] = None, limit: int = 100, offset: int = 0) -> List[QuarantineItem]:
    """List quarantine items, optionally filtered by status. Viewer gets 403; admin gets 200."""
    limit = max(1, min(limit, 500))
    stmt = select(quarantine_items_table).order_by(quarantine_items_table.c.created_at.desc()).limit(limit).offset(offset)
    if status:
        stmt = stmt.where(quarantine_items_table.c.status == status)
    with db.engine.connect() as conn:
        rows = conn.execute(stmt).fetchall()
    return [_quarantine_item_response(r) for r in rows]


@router.post("/quarantine/{item_id}/action", status_code=204)
async def action_quarantine(item_id: str, body: QuarantineAction, user: TokenPayload = Depends(require_admin)):
    """Approve or reject a quarantine item (admin only)."""
    if body.action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="action must be 'approve' or 'reject'")
    new_status = "approved" if body.action == "approve" else "rejected"
    _quarantine_row_or_404(item_id)
    with db.engine.begin() as conn:
        conn.execute(
            quarantine_items_table.update()
            .where(quarantine_items_table.c.id == item_id)
            .values(status=new_status, reviewed_at=_iso_now(), reviewer_id=user.sub, review_note=body.reason)
        )
    db.log_audit(user.sub, f"quarantine.{body.action}", "quarantine_item", item_id, {"reason": body.reason})


# ---- Quarantine review (S3 T6): approve re-runs the gates, override needs a reason ----


class QuarantineApprove(BaseModel):
    override_reason: Optional[str] = None

    @field_validator("override_reason")
    @classmethod
    def _override_reason_min_length(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and len(v.strip()) < 10:
            raise ValueError("override_reason must be at least 10 characters")
        return v


class QuarantineReject(BaseModel):
    note: str = Field(min_length=1)


@router.post("/quarantine/{item_id}/approve", response_model=QuarantineItem)
async def approve_quarantine(item_id: str, body: QuarantineApprove, user: TokenPayload = Depends(require_admin)):
    """Re-run A6+A7 for the item's run/content. Passing -> approved. Still failing -> 409 with the
    failing checks, unless override_reason (>= 10 characters) is given, in which case it is approved
    anyway and the reason + reviewer are stored."""
    row = _undecided_quarantine_row_or_409(item_id)
    gates_ok, failing_checks = publish.recheck_quarantine_item(dict(row._mapping))
    if not gates_ok and not body.override_reason:
        raise HTTPException(
            status_code=409,
            detail={"error": "quarantine gates still fail", "failing_checks": failing_checks},
        )
    review_note = body.override_reason if not gates_ok else None
    with db.engine.begin() as conn:
        conn.execute(
            quarantine_items_table.update()
            .where(quarantine_items_table.c.id == item_id)
            .values(status="approved", reviewed_at=_iso_now(), reviewer_id=user.sub, review_note=review_note)
        )
    db.log_audit(
        user.sub, "quarantine.approve", "quarantine_item", item_id,
        {"override_reason": review_note, "gates_ok": gates_ok, "failing_checks": failing_checks},
    )
    return _quarantine_item_response(_quarantine_row_or_404(item_id))


@router.post("/quarantine/{item_id}/reject", response_model=QuarantineItem)
async def reject_quarantine(item_id: str, body: QuarantineReject, user: TokenPayload = Depends(require_admin)):
    """Reject a quarantine item. A note is required."""
    _undecided_quarantine_row_or_409(item_id)
    with db.engine.begin() as conn:
        conn.execute(
            quarantine_items_table.update()
            .where(quarantine_items_table.c.id == item_id)
            .values(status="rejected", reviewed_at=_iso_now(), reviewer_id=user.sub, review_note=body.note)
        )
    db.log_audit(user.sub, "quarantine.reject", "quarantine_item", item_id, {"note": body.note})
    return _quarantine_item_response(_quarantine_row_or_404(item_id))
