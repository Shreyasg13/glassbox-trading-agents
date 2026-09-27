"""Phase 5: LLM-narrated daily reports.

Reuses the same "latest daily_*.json on disk" resolution `data_source.py`
already implements for /api/historical-reports and /api/daily-summary
(`ds.load_latest_data`) rather than reimplementing report-file lookup.
Generation always runs as a background job -- see jobs.py -- so a slow
provider round-trip can never block this or any other request.

Phase 6: narration now goes through `complete_with_logging` (same helper
`orchestration.py` uses) instead of calling the provider directly, so
report-generation calls show up in the `llm_calls` cost/latency log too
-- previously this was the one LLM call path with no observability. The
generate call is also written to the audit log.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException

from .. import data_source as ds
from .. import db, flags
from .. import jobs
from ..auth import TokenPayload, get_current_user_optional, require_admin
from .. import llm_router
from ..models import DailyReportNarrative, JobAccepted, ReportGenerateRequest

# /api/reports, not /reports -- the frontend's own /reports/* pages live
# at that exact path; under single-domain path-based routing (see
# deploy/Caddyfile) a bare /reports prefix here would collide with them.
router = APIRouter(prefix="/api/reports", tags=["reports"])


def _build_prompt(report_date: str, report_data: list, live_signals: dict) -> str:
    latest = report_data[-1] if report_data else {}
    return "\n".join(
        [
            f"Daily trading report for {report_date}.",
            (
                f"Portfolio value: {latest.get('portfolio_value')}, "
                f"daily return: {latest.get('daily_return')}, "
                f"signal: {latest.get('signal')}, positions: {latest.get('positions')}."
            ),
            f"Live signal summary: {live_signals.get('summary')}.",
            "Write a concise, professional narrative (3-5 sentences) summarizing the "
            "day's performance and current signal posture for an investor audience.",
        ]
    )


@router.post("/generate", response_model=JobAccepted, status_code=202, dependencies=[Depends(require_admin)])
async def generate_report(body: ReportGenerateRequest, user: TokenPayload = Depends(require_admin)):
    data = await asyncio.to_thread(ds.load_latest_data)
    if not data:
        raise HTTPException(status_code=404, detail="No report data available to narrate")

    system_prompt = None
    if body.agent_id:
        agent_row = db.get_agent(body.agent_id)
        if agent_row:
            system_prompt = agent_row.get("system_prompt")

    job = jobs.new_job("report_generate")
    job_id = job["job_id"]
    report_date = body.date or datetime.now(timezone.utc).strftime("%Y%m%d")
    db.log_audit(
        user.sub,
        "report.generate",
        "report",
        None,
        {"job_id": job_id, "date": report_date, "provider": body.provider, "model": body.model},
    )

    async def _work():
        await jobs.log(job_id, "Loading latest report and live signals")
        live = await asyncio.to_thread(ds.get_live_signals)
        prompt = _build_prompt(report_date, data, live)

        async def _on_token(piece: str) -> None:
            await jobs.token(job_id, piece)

        await jobs.log(job_id, f"Calling {body.provider}/{body.model}")
        async def _on_fallback(candidate: str, reason: str) -> None:
            await jobs.log(job_id, f"trying '{candidate}' ({reason})")

        routed = await llm_router.complete_routed(
            body.provider,
            body.model,
            prompt,
            agent_id=body.agent_id,
            system=system_prompt,
            on_token=_on_token,
            on_fallback=_on_fallback,
        )
        text = routed.text

        narrative_payload = {
            "id": str(uuid.uuid4()),
            "date": report_date,
            "provider": routed.provider,  # the provider that ACTUALLY answered (may differ if the requested one was out of quota)
            "model": routed.model,
            "narrative": text,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Publish through the single exit (includes DB write via writer_payload)
        from .. import publish
        result = publish.publish(
            channel="orchestration_report",
            text=text,
            run_ids=(),  # admin reports don't have committee run_ids
            content_ref=f"admin_report:{report_date}:{job_id}",
            is_html=False,
            committee_output=False,
            writer_payload=narrative_payload,
        )

        # In enforce mode, held reports are not written
        if not result.allowed:
            await jobs.log(job_id, "Report held by publish (enforce mode)")
            return {"held": True}

        return narrative_payload

    asyncio.create_task(jobs.run_job(job_id, _work))
    return JobAccepted(job_id=job_id)


@router.get("/narratives", response_model=List[DailyReportNarrative], dependencies=[Depends(require_admin)])
async def list_narratives():
    return db.list_report_narratives()


@router.get("/narratives/{narrative_id}", response_model=DailyReportNarrative)
async def get_narrative(narrative_id: str, user: Optional[TokenPayload] = Depends(get_current_user_optional)):
    if not flags.flag("output.reports") and not (user and user.role == "admin"):  # kill switch: 404, admins can still open it
        raise HTTPException(status_code=404, detail="Narrative not found")
    narrative = db.get_report_narrative(narrative_id)
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")

    # Check for quarantine items when enforcement is on
    if flags.flag("publish.enforce") and not (user and user.role == "admin"):
        from ..migrated_tables import quarantine_items_table
        from sqlalchemy import select
        with db.engine.connect() as conn:
            q_item = conn.execute(
                select(quarantine_items_table).where(
                    quarantine_items_table.c.content_ref == narrative_id,
                    quarantine_items_table.c.status.in_(("pending", "rejected"))
                )
            ).first()
        if q_item:
            raise HTTPException(status_code=404, detail="Narrative not found")

    return narrative
