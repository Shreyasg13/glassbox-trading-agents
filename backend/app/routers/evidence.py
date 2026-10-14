"""User evidence view: "Show my work" (S3 T7).

For a report narrative, lists the structured claims (T3) behind its numbers, each
checked against the A6 verification gate's stored results (T4), so a user can see
exactly where a number came from and whether it was verified.

SAFETY (plan rule 5.1/5.4): this endpoint must NEVER return failure reasons, raw
snapshot payloads, check names, or anything from quarantine_items. A quarantined
narrative returns 404, exactly like GET /api/reports/narratives/{id} itself
(routers/reports.py's get_narrative). Only the fields declared on EvidenceClaim
ever leave this module -- response_model enforces that even if a caller adds a
field here by mistake, FastAPI drops it before it reaches the client.

Narrative -> run linkage: claims and verification results are keyed by committee run_id ("YYYY-MM-DD:TICKER",
see claims.py/verification/runner.py). The daily committee report stores the `run_ids` it covers; reports written
before that are matched to their day's runs by date (`_run_ids`). A narrative with no runs has no claims to show and
is NOT verified -- "verified" is only ever claimed when at least one number was actually checked.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import db, flags
from ..auth import TokenPayload, require_role
from ..migrated_tables import claims_table, source_snapshots_table, verification_results_table
from .reports import is_quarantined

# Same prefix as routers/reports.py -- this is a sibling route under the same
# resource, registered as its own router per the spec's file scope.
router = APIRouter(prefix="/api/reports", tags=["reports"])

# Friendly source labels for the evidence panel (internal source names stay
# lowercase/snake_case everywhere else in the pipeline).
SOURCE_LABELS: Dict[str, str] = {
    "sec_facts": "SEC",
    "treasury": "Treasury",
    "bls": "BLS",
    "risk": "Risk model",
    "pricebook": "Market data",
}


class EvidenceClaim(BaseModel):
    claim_id: str
    label: str
    metric: str
    value: float
    unit: str
    source: str
    field_path: Optional[str] = None
    as_of: Optional[str] = None
    passed: bool


class EvidenceResponse(BaseModel):
    verified: bool
    claims: List[EvidenceClaim]


def _claim_passed(check_rows: List[Any]) -> bool:
    """A claim is verified only if every check recorded for it passed (S3 T4's rule:
    a staleness WARN does not disqualify a claim; any FAIL, or any other WARN, does).
    A claim with no recorded checks at all (the gate never ran for its run) counts
    as not passed -- unverified, never assumed innocent."""
    if not check_rows:
        return False
    for row in check_rows:
        if row.status == "fail":
            return False
        if row.status == "warn" and row.check_type != "staleness":
            return False
    return True


def _run_ids(narrative: Dict[str, Any]) -> List[str]:
    """Committee runs behind a report. New reports store `run_ids`; older committee reports cover that day's runs,
    whose ids are "YYYY-MM-DD:TICKER" (claims.py), so they are found by date."""
    if narrative.get("run_ids"):
        return list(narrative["run_ids"])
    if narrative.get("run_id"):
        return [narrative["run_id"]]
    d = str(narrative.get("date") or "")
    if narrative.get("model") != "committee-vote" or len(d) != 8:
        return []
    prefix = f"{d[:4]}-{d[4:6]}-{d[6:]}:"
    with db.engine.connect() as conn:
        rows = conn.execute(select(claims_table.c.run_id).where(claims_table.c.run_id.like(prefix + "%")).distinct()).fetchall()
    return sorted(r.run_id for r in rows)


def _build_claims(run_ids: List[str]) -> List[EvidenceClaim]:
    with db.engine.connect() as conn:
        claim_rows = conn.execute(
            select(claims_table).where(claims_table.c.run_id.in_(run_ids)).order_by(claims_table.c.run_id, claims_table.c.created_at)
        ).fetchall()
        check_rows = conn.execute(
            select(
                verification_results_table.c.claim_id,
                verification_results_table.c.status,
                verification_results_table.c.check_type,
            ).where(verification_results_table.c.run_id.in_(run_ids))
        ).fetchall()
        snap_ids = sorted({c.source_snapshot_id for c in claim_rows if c.source_snapshot_id})
        as_of_by_snap = {
            r.id: r.as_of
            for r in conn.execute(select(source_snapshots_table.c.id, source_snapshots_table.c.as_of).where(source_snapshots_table.c.id.in_(snap_ids)))
        } if snap_ids else {}

    checks_by_claim: Dict[str, List[Any]] = {}
    for row in check_rows:
        if row.claim_id is None:  # the run-level narrative check (gate.check_narrative) is not per-claim
            continue
        checks_by_claim.setdefault(row.claim_id, []).append(row)

    return [
        EvidenceClaim(
            claim_id=c.id,
            label=f"{c.ticker} {c.metric}".strip(),
            metric=c.metric,
            value=c.value,
            unit=c.unit,
            source=SOURCE_LABELS.get(c.source, c.source),
            field_path=c.source_path,
            # the data's own date; pricebook/risk claims have no snapshot and their period IS the as-of date
            as_of=as_of_by_snap.get(c.source_snapshot_id) or c.period,
            passed=_claim_passed(checks_by_claim.get(c.id, [])),
        )
        for c in claim_rows
    ]


@router.get("/narratives/{narrative_id}/evidence", response_model=EvidenceResponse, dependencies=[Depends(require_role("user"))])
async def get_narrative_evidence(narrative_id: str, user: TokenPayload = Depends(require_role("user"))) -> EvidenceResponse:
    is_admin = user.role == "admin"

    # Same kill switch as the report itself: off, and non-admins get 404.
    if not flags.flag("output.reports") and not is_admin:
        raise HTTPException(status_code=404, detail="Narrative not found")

    narrative = db.get_report_narrative(narrative_id)
    if narrative is None:
        raise HTTPException(status_code=404, detail="Narrative not found")

    # Same quarantine check as the report itself: a held narrative is invisible to non-admins.
    if flags.flag("publish.enforce") and not is_admin and is_quarantined(narrative):
        raise HTTPException(status_code=404, detail="Narrative not found")

    run_ids = _run_ids(narrative)
    if not run_ids:
        return EvidenceResponse(verified=False, claims=[])

    claims_out = _build_claims(run_ids)
    verified = bool(claims_out) and all(c.passed for c in claims_out)  # nothing checked is never "verified"
    return EvidenceResponse(verified=verified, claims=claims_out)
