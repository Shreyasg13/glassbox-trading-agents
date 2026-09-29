"""Weekly discrepancy report: a Monday draft built from `verification_results` and `compliance_events`, reviewed and
published by an admin, then shown on the public transparency page (S3 T13).

`build_draft` is PURE (rows in, dict out; no DB, no clock) so its arithmetic is testable with hand-built rows -- the same
split `app/verification/health.py` uses. Counts only: no raw payloads, no claim text, no user data. The A6 pass rate
uses `gate.summarize`'s own rule (every check for a claim must pass; a staleness warn does not disqualify it), so this
report can never count differently from the badge or the Gate health tab.

Only `fetch_week`, `create_draft_for_week` and the row-level helpers below touch the database.
"""
from __future__ import annotations

import json
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import select

from . import db
from .migrated_tables import claims_table, compliance_events_table, verification_results_table, weekly_reports_table
from .verification import gate

TOP_N = 5
CLAIM_ID_CHUNK = 500  # failing claim ids looked up per IN (...) query, same bound as health.py
# The only action values app/compliance/filter.py ever records (RULE_ACTIONS); always reported, even at zero.
COMPLIANCE_ACTIONS = ("blocked", "rewritten", "flagged")


def _get(row: Any, key: str) -> Any:
    """Rows may be dicts (tests) or SQLAlchemy rows (the loader)."""
    return row[key] if isinstance(row, Mapping) else getattr(row, key)


def _most_common(counter: Counter) -> Optional[str]:
    """The most frequent key; ties go to the alphabetically first so the answer is stable."""
    if not counter:
        return None
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def _as_result(r: Any) -> gate.Result:
    return gate.Result(check_type=_get(r, "check_type"), status=_get(r, "status"), claim_id=_get(r, "claim_id"),
                       expected=None, observed=None, reason=_get(r, "reason") or "")


def _top_failing_checks(rows: Iterable[Any], n: int = TOP_N) -> List[Dict[str, Any]]:
    """The n check types with the most `fail` results, each with its most common reason. Warns are not failures."""
    counts: Counter = Counter()
    reasons: Dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if _get(r, "status") != "fail":
            continue
        ct = _get(r, "check_type")
        counts[ct] += 1
        reasons[ct][_get(r, "reason") or ""] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"check_type": ct, "failures": c, "top_reason": _most_common(reasons[ct])} for ct, c in ranked]


def _top_failing_metrics(rows: Iterable[Any], n: int = TOP_N) -> List[Dict[str, Any]]:
    """The n claim metrics with the most `fail` results. `metric` is expected to already be joined onto each row (see
    `fetch_week`); rows with no metric (the run-level narrative check, or a claim id fetch_week could not join) are
    left out."""
    failures: Counter = Counter()
    claim_ids: Dict[str, set] = defaultdict(set)
    for r in rows:
        if _get(r, "status") != "fail":
            continue
        metric = _get(r, "metric")
        if not metric:
            continue
        failures[metric] += 1
        cid = _get(r, "claim_id")
        if cid:
            claim_ids[metric].add(cid)
    ranked = sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"metric": m, "failures": c, "claims": len(claim_ids[m])} for m, c in ranked]


def _a6_pass_rate(rows: Iterable[Any]) -> Dict[str, Any]:
    """Runs checked and the A6 claim pass rate for the week, using the gate's own summarize() per run."""
    by_run: Dict[str, List[gate.Result]] = defaultdict(list)
    for r in rows:
        by_run[_get(r, "run_id")].append(_as_result(r))
    checked = verified = runs_ok = 0
    for results in by_run.values():
        s = gate.summarize(results)
        checked += s["total_claims"]
        verified += s["verified_claims"]
        runs_ok += 1 if s["ok"] else 0
    return {
        "runs_checked": len(by_run),
        "runs_ok": runs_ok,
        "claims_checked": checked,
        "claims_verified": verified,
        "pass_rate": (verified / checked) if checked else None,
    }


def _a7_by_action(rows: Iterable[Any]) -> Dict[str, int]:
    """Compliance event counts by action, always reporting every known action (even zero)."""
    counts = {a: 0 for a in COMPLIANCE_ACTIONS}
    for r in rows:
        action = _get(r, "action")
        counts[action] = counts.get(action, 0) + 1
    return counts


def build_draft(verification_rows: Iterable[Any], compliance_rows: Iterable[Any], week_start: str) -> Dict[str, Any]:
    """Everything the weekly report shows, for one Mon-Sun week. Pure: the caller has already restricted both row
    sets to that week (see `fetch_week`). Counts only -- no raw payloads, no user data."""
    verification_rows = list(verification_rows)
    compliance_rows = list(compliance_rows)
    a6 = _a6_pass_rate(verification_rows)
    return {
        "week_start": week_start,
        "runs_checked": a6["runs_checked"],
        "a6": a6,
        "top_failing_checks": _top_failing_checks(verification_rows),
        "top_failing_metrics": _top_failing_metrics(verification_rows),
        "a7_by_action": _a7_by_action(compliance_rows),
    }


# ---------------------------------------------------------------------------------------------------------- db layer --


def previous_week_start(today: date) -> date:
    """The Monday of the Mon-Sun week before `today`'s week."""
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(days=7)


def fetch_week(week_start: date) -> Dict[str, Any]:
    """Read verification_results (joined to claims for `metric`) and compliance_events for the Mon-Sun week starting
    `week_start`. Same date-range-on-run_id trick as `verification/health.py::load` (ask:/chal: ids sort after digits
    and fall outside it, so they are excluded automatically)."""
    week_end = week_start + timedelta(days=6)
    lo, hi = week_start.isoformat(), (week_end + timedelta(days=1)).isoformat()
    vr = verification_results_table.c
    with db.engine.connect() as conn:
        vrows = conn.execute(
            select(vr.run_id, vr.claim_id, vr.check_type, vr.status, vr.reason).where(vr.run_id >= lo, vr.run_id < hi)
        ).fetchall()
        failing_ids = sorted({r.claim_id for r in vrows if r.status == "fail" and r.claim_id})
        metric_by_claim_id: Dict[str, str] = {}
        for i in range(0, len(failing_ids), CLAIM_ID_CHUNK):  # stay well under SQLite's bound-parameter limit
            chunk = failing_ids[i:i + CLAIM_ID_CHUNK]
            for c in conn.execute(select(claims_table.c.id, claims_table.c.metric).where(claims_table.c.id.in_(chunk))):
                metric_by_claim_id[c.id] = c.metric

        ce = compliance_events_table.c
        ce_lo = f"{lo}T00:00:00.000000+00:00"
        ce_hi = f"{hi}T00:00:00.000000+00:00"
        crows = conn.execute(select(ce.action).where(ce.created_at >= ce_lo, ce.created_at < ce_hi)).fetchall()

    verification_rows = [
        {"run_id": r.run_id, "claim_id": r.claim_id, "check_type": r.check_type, "status": r.status, "reason": r.reason,
         "metric": metric_by_claim_id.get(r.claim_id) if r.claim_id else None}
        for r in vrows
    ]
    compliance_rows = [{"action": r.action} for r in crows]
    return {"verification_rows": verification_rows, "compliance_rows": compliance_rows}


def get_by_week_start(week_start: date) -> Optional[Dict[str, Any]]:
    with db.engine.connect() as conn:
        row = conn.execute(select(weekly_reports_table).where(weekly_reports_table.c.week_start == week_start.isoformat())).fetchone()
    return dict(row._mapping) if row else None


def create_draft_for_week(week_start: date, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """Create the draft for `week_start` if one does not already exist. Idempotent: a second call for the same week
    creates nothing and returns None."""
    if get_by_week_start(week_start) is not None:
        return None
    now = now or datetime.now(timezone.utc)
    week = fetch_week(week_start)
    draft = build_draft(week["verification_rows"], week["compliance_rows"], week_start.isoformat())
    row = {
        "id": str(uuid.uuid4()),
        "week_start": week_start.isoformat(),
        "status": "draft",
        "body_json": json.dumps(draft, sort_keys=True),
        "created_at": now.isoformat(),
        "published_at": None,
        "published_by": None,
    }
    with db.engine.begin() as conn:
        conn.execute(weekly_reports_table.insert().values(**row))
    return row


def _as_dict(row: Any) -> Dict[str, Any]:
    return dict(row._mapping) if not isinstance(row, Mapping) else dict(row)


def list_reports(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest week first. `status` restricts to "draft" or "published"."""
    stmt = select(weekly_reports_table)
    if status:
        stmt = stmt.where(weekly_reports_table.c.status == status)
    stmt = stmt.order_by(weekly_reports_table.c.week_start.desc())
    with db.engine.connect() as conn:
        return [_as_dict(r) for r in conn.execute(stmt).fetchall()]


def get(report_id: str) -> Optional[Dict[str, Any]]:
    with db.engine.connect() as conn:
        row = conn.execute(select(weekly_reports_table).where(weekly_reports_table.c.id == report_id)).fetchone()
    return _as_dict(row) if row else None


class AlreadyPublished(Exception):
    """Raised by `publish` when the report is not in "draft" status."""


class NotFound(Exception):
    """Raised by `publish` when no report has this id."""


def publish(report_id: str, published_by: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Move a draft to published, recording who and when. A published report can never be edited (there is no edit
    route) and publishing twice raises `AlreadyPublished`."""
    report = get(report_id)
    if report is None:
        raise NotFound(report_id)
    if report["status"] != "draft":
        raise AlreadyPublished(report_id)
    now = now or datetime.now(timezone.utc)
    published_at = now.isoformat()
    with db.engine.begin() as conn:
        conn.execute(
            weekly_reports_table.update()
            .where(weekly_reports_table.c.id == report_id)
            .values(status="published", published_at=published_at, published_by=published_by)
        )
    return get(report_id)
