"""Gate health: read-only aggregates over stored A6 gate results (S3 T14).

The aggregation functions are PURE (rows in, dicts out) so they can be tested without a database. `load` is the only
function that reads the database. Nothing here writes anything.

A committee run's date comes from its run_id (`YYYY-MM-DD:SYMBOL`). Ask (`ask:`) and challenger (`chal:`) runs are not our
committee's decisions and are never counted, the same as the admin verification summary.

A claim counts as verified only by `gate.summarize`'s rule (every check passed; a staleness warn does not disqualify), so
this dashboard can never count differently from the badge.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Mapping, Optional

from sqlalchemy import select

from . import gate
from .. import db
from ..migrated_tables import claims_table, verification_results_table

TOP_N = 5
_EXCLUDED_PREFIXES = (db.COMMITTEE_ASK_PREFIX, db.CHALLENGER_PREFIX)


def _get(row: Any, key: str) -> Any:
    """Rows may be dicts (tests) or SQLAlchemy rows (the loader)."""
    return row[key] if isinstance(row, Mapping) else getattr(row, key)


def run_date(run_id: str) -> Optional[date]:
    """The run date of a committee run_id, or None for ask/challenger runs and anything that is not `YYYY-MM-DD:SYMBOL`."""
    run_id = str(run_id or "")
    if run_id.startswith(_EXCLUDED_PREFIXES) or ":" not in run_id:
        return None
    try:
        return date.fromisoformat(run_id.split(":", 1)[0])
    except ValueError:
        return None


def in_range(rows: Iterable[Any], start: date, end: date) -> List[Any]:
    """Only the rows of committee runs dated start..end (both inclusive)."""
    out = []
    for r in rows:
        d = run_date(_get(r, "run_id"))
        if d is not None and start <= d <= end:
            out.append(r)
    return out


def _as_result(r: Any) -> gate.Result:
    return gate.Result(check_type=_get(r, "check_type"), status=_get(r, "status"), claim_id=_get(r, "claim_id"),
                       expected=None, observed=None, reason=_get(r, "reason") or "")


def daily_pass_rate(rows: Iterable[Any], start: date, end: date) -> List[Dict[str, Any]]:
    """One entry per day that has gate results: claims fully verified / claims checked, and runs checked / runs ok.

    Each run is summarised by `gate.summarize` (the badge's own rule). `pass_rate` is None when no claim was checked that
    day (e.g. only a narrative check was stored). Days are in ascending order.
    """
    by_run: Dict[str, List[gate.Result]] = defaultdict(list)
    for r in in_range(rows, start, end):
        by_run[_get(r, "run_id")].append(_as_result(r))

    days: Dict[date, Dict[str, int]] = defaultdict(lambda: {"claims_checked": 0, "claims_verified": 0, "runs_checked": 0, "runs_ok": 0})
    for rid, results in by_run.items():
        s = gate.summarize(results)
        day = days[run_date(rid)]  # type: ignore[index]
        day["claims_checked"] += s["total_claims"]
        day["claims_verified"] += s["verified_claims"]
        day["runs_checked"] += 1
        day["runs_ok"] += 1 if s["ok"] else 0

    return [
        {"date": d.isoformat(), **v, "pass_rate": (v["claims_verified"] / v["claims_checked"]) if v["claims_checked"] else None}
        for d, v in sorted(days.items())
    ]


def _most_common(counter: Counter) -> Optional[str]:
    """The most frequent key; ties go to the alphabetically first so the answer is stable."""
    if not counter:
        return None
    return min(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]


def top_failing_checks(rows: Iterable[Any], start: date, end: date, n: int = TOP_N) -> List[Dict[str, Any]]:
    """The n check types with the most `fail` results, each with its most common reason.

    Warns are not failures. Ordered by count (highest first), ties by check type name.
    """
    counts: Counter = Counter()
    reasons: Dict[str, Counter] = defaultdict(Counter)
    for r in in_range(rows, start, end):
        if _get(r, "status") != "fail":
            continue
        ct = _get(r, "check_type")
        counts[ct] += 1
        reasons[ct][_get(r, "reason") or ""] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"check_type": ct, "failures": c, "top_reason": _most_common(reasons[ct])} for ct, c in ranked]


def top_failing_metrics(rows: Iterable[Any], metric_by_claim_id: Mapping[str, str], start: date, end: date,
                        n: int = TOP_N) -> List[Dict[str, Any]]:
    """The n claim metrics with the most `fail` results (failing results joined to `claims` on claim_id).

    `failures` counts failing results; `claims` counts distinct claims with at least one failure. Results without a
    claim_id (the run-level narrative check) or whose claim is not found have no metric and are left out.
    Ordered by failures (highest first), ties by metric name.
    """
    failures: Counter = Counter()
    claim_ids: Dict[str, set] = defaultdict(set)
    for r in in_range(rows, start, end):
        if _get(r, "status") != "fail":
            continue
        cid = _get(r, "claim_id")
        metric = metric_by_claim_id.get(cid) if cid else None
        if metric is None:
            continue
        failures[metric] += 1
        claim_ids[metric].add(cid)
    ranked = sorted(failures.items(), key=lambda kv: (-kv[1], kv[0]))[:n]
    return [{"metric": m, "failures": c, "claims": len(claim_ids[m])} for m, c in ranked]


def aggregate(rows: Iterable[Any], metric_by_claim_id: Mapping[str, str], start: date, end: date) -> Dict[str, Any]:
    """Everything the Gate health tab shows, for committee runs dated start..end (inclusive)."""
    rows = in_range(rows, start, end)
    days = daily_pass_rate(rows, start, end)
    checked = sum(d["claims_checked"] for d in days)
    verified = sum(d["claims_verified"] for d in days)
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "days": days,
        "totals": {
            "claims_checked": checked,
            "claims_verified": verified,
            "pass_rate": (verified / checked) if checked else None,
            "runs_checked": sum(d["runs_checked"] for d in days),
            "runs_ok": sum(d["runs_ok"] for d in days),
        },
        "top_checks": top_failing_checks(rows, start, end),
        "top_metrics": top_failing_metrics(rows, metric_by_claim_id, start, end),
    }


def load(start: date, end: date) -> Dict[str, Any]:
    """Read the stored gate results (and the metrics of failing claims) for start..end, then aggregate them.

    run_ids begin with their date, so the SQL range on run_id selects exactly the committee runs of those days
    (`ask:`/`chal:` ids sort after digits and fall outside it; `in_range` drops them again regardless).
    """
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    vr = verification_results_table.c
    with db.engine.connect() as conn:
        rows = conn.execute(
            select(vr.run_id, vr.claim_id, vr.check_type, vr.status, vr.reason).where(vr.run_id >= lo, vr.run_id < hi)
        ).fetchall()
        failing_ids = sorted({r.claim_id for r in rows if r.status == "fail" and r.claim_id})
        metric_by_claim_id: Dict[str, str] = {}
        for i in range(0, len(failing_ids), 500):  # stay well under SQLite's bound-parameter limit
            chunk = failing_ids[i:i + 500]
            for c in conn.execute(select(claims_table.c.id, claims_table.c.metric).where(claims_table.c.id.in_(chunk))):
                metric_by_claim_id[c.id] = c.metric
    return aggregate(rows, metric_by_claim_id, start, end)
