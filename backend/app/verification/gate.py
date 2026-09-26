"""Verification gate — pure functions (S3 T4).

Every number that reaches a user must pass through this gate. The gate runs a
series of checks against the source data and records a pass/fail/warn per check.
T4 does NOT block anything; T5 (single publish exit) will consult the results.

IMPORTANT: All functions here are PURE — no DB, no network, no clock. Everything
is passed in as arguments. This makes them fully testable and deterministic.

Known limitation (do not fix in T4): a snapshot payload that goes A -> B -> A is
stored once (idempotent on payload_hash), so the store may return B (the later
fetch) even though A was first. This means the point-in-time guarantee holds
for "fetched after run_time" but not for "which version of the same payload
was seen first". See T10 for a fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import config
from .. import claims, narrative, snapshot_store


@dataclass(frozen=True)
class Result:
    """A single verification check result."""
    check_type: str
    status: str  # "pass" | "fail" | "warn"
    claim_id: Optional[str]
    expected: Optional[str]
    observed: Optional[str]
    reason: str


def _iso_to_dt(s: str) -> datetime:
    """Parse ISO string to UTC datetime with microsecond precision."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


def _normalize_ts(s: str) -> datetime:
    """Normalize an ISO timestamp string to the same format snapshot_store uses.

    snapshot_store stores timestamps using _iso() which produces:
    YYYY-MM-DDTHH:MM:SS.ffffff+00:00 (always 6 microsecond digits)

    This function parses any valid ISO format and returns a datetime,
    ensuring both timestamps being compared are normalized identically.
    """
    return _iso_to_dt(s)


def _iso_to_date(s: str) -> date:
    """Parse the date a snapshot describes. Accepts YYYY-MM-DD (or a longer ISO timestamp) and YYYY-MM: monthly sources
    such as BLS report a month, which counts as its LAST day (the data describes the whole month). Raises ValueError for
    anything else; verify_run turns that into a failed check instead of a crash."""
    s = str(s).strip()
    if len(s) == 7 and s[4] == "-":  # YYYY-MM
        y, m = int(s[:4]), int(s[5:7])
        first_of_next = date(y + (m == 12), m % 12 + 1, 1)
        return first_of_next - timedelta(days=1)
    return date.fromisoformat(s[:10])


def _trading_days_between(start: date, end: date) -> int:
    """Count trading days between two dates (inclusive of end, exclusive of start).
    Skips Saturday (5) and Sunday (6). No holiday calendar.
    """
    if start >= end:
        return 0
    days = 0
    current = start
    while current < end:
        if current.weekday() < 5:  # Mon-Fri
            days += 1
        current += timedelta(days=1)
    return days


def _is_trading_day_source(source: str) -> bool:
    return source in config.TRADING_DAY_SOURCES


def check_traceability(claim: Dict[str, Any], payload: Optional[Any], prices: Optional[Dict[str, float]] = None) -> Result:
    """Verify that a claim's value matches its source using claims.check_claim.

    Snapshot-source claim whose snapshot payload is missing -> fail ("snapshot not found").

    Tolerance (S3 T4b): the plan allows 0.5% relative or rounding at the displayed precision. Claim values are COPIED
    from the source (and derived values recomputed from the same inputs), so this check stays exact: a relative
    difference of 1e-9 at most, which only absorbs float noise. That is stricter than the plan, which is allowed.
    The plan's looser tolerance (config.RENDER_TOLERANCE_REL) is for numbers as rendered to a user (T7).
    """
    claim_id = claim.get("id")
    source = claim.get("source")

    # Sources without snapshots (risk, pricebook) are considered passing by definition
    if source in ("risk", "pricebook"):
        return Result(
            check_type="traceability",
            status="pass",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason="source has no snapshot to verify against",
        )

    # Snapshot source but no payload available
    if payload is None:
        return Result(
            check_type="traceability",
            status="fail",
            claim_id=claim_id,
            expected=str(claim.get("value")),
            observed=None,
            reason="snapshot not found",
        )

    # Use the existing check_claim function
    ok = claims.check_claim(claim, payload, prices)
    return Result(
        check_type="traceability",
        status="pass" if ok else "fail",
        claim_id=claim_id,
        expected=str(claim.get("value")),
        observed=str(claim.get("value")) if ok else "mismatch",
        reason="value matches source" if ok else "value does not match source",
    )


def check_point_in_time(claim: Dict[str, Any], snapshot_meta: Dict[str, Any], run_time: str) -> Result:
    """Verify the snapshot's fetched_at is <= run_time.

    snapshot_meta must contain 'fetched_at' (ISO string).
    Both timestamps are normalized to the same format (microsecond precision)
    before comparison, since datetime.isoformat() drops trailing zeros.
    """
    claim_id = claim.get("id")
    fetched_at = snapshot_meta.get("fetched_at")
    if not fetched_at:
        return Result(
            check_type="point_in_time",
            status="fail",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason="snapshot metadata missing fetched_at",
        )

    # Normalize both timestamps to the same format for correct comparison
    # datetime.isoformat() drops microseconds when they are 0, so raw string
    # comparison is wrong at the boundary (e.g., "12:00:00+00:00" vs
    # "12:00:00.000001+00:00").
    run_dt = _normalize_ts(run_time)
    fetched_dt = _normalize_ts(fetched_at)

    if fetched_dt <= run_dt:
        return Result(
            check_type="point_in_time",
            status="pass",
            claim_id=claim_id,
            expected=f"fetched_at <= {run_time}",
            observed=f"fetched_at = {fetched_at}",
            reason="snapshot fetched before or at run time",
        )
    else:
        return Result(
            check_type="point_in_time",
            status="fail",
            claim_id=claim_id,
            expected=f"fetched_at <= {run_time}",
            observed=f"fetched_at = {fetched_at}",
            reason="snapshot fetched after run time",
        )


def check_staleness(claim: Dict[str, Any], snapshot_meta: Dict[str, Any], run_date: str, windows: Optional[Dict[str, timedelta]] = None) -> Result:
    """Verify the age of the data (as_of vs run_date) is within the window for its source.

    Beyond the window -> warn (not fail). Trading-day-aware for prices source.
    """
    claim_id = claim.get("id")
    source = claim.get("source")
    as_of = snapshot_meta.get("as_of")

    if not as_of:
        return Result(
            check_type="staleness",
            status="warn",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason="snapshot metadata missing as_of",
        )

    w = windows or config.DEFAULT_WINDOWS
    window = w.get(source)
    if window is None:
        return Result(
            check_type="staleness",
            status="warn",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason=f"no staleness window configured for source {source}",
        )

    run_d = _iso_to_date(run_date)
    as_of_d = _iso_to_date(as_of)

    if _is_trading_day_source(source):
        age = _trading_days_between(as_of_d, run_d)
        window_days = window.days
        within = age <= window_days
        unit = "trading day(s)"
    else:
        age = (run_d - as_of_d).days
        window_days = window.days
        within = age <= window_days
        unit = "day(s)"

    if within:
        return Result(
            check_type="staleness",
            status="pass",
            claim_id=claim_id,
            expected=f"age <= {window_days} {unit}",
            observed=f"age = {age} {unit}",
            reason=f"data is fresh (age {age} {unit} <= window {window_days} {unit})",
        )
    else:
        return Result(
            check_type="staleness",
            status="warn",
            claim_id=claim_id,
            expected=f"age <= {window_days} {unit}",
            observed=f"age = {age} {unit}",
            reason=f"data is stale (age {age} {unit} > window {window_days} {unit})",
        )


def check_price(claim: Dict[str, Any], prices: Dict[str, float]) -> Result:
    """For source == 'pricebook': value equals the price book close for that date."""
    claim_id = claim.get("id")
    period = claim.get("period")  # the date
    value = claim.get("value")

    if claim.get("source") != "pricebook":
        return Result(
            check_type="price",
            status="pass",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason="not a pricebook claim",
        )

    if not period or period not in prices:
        return Result(
            check_type="price",
            status="fail",
            claim_id=claim_id,
            expected=str(value),
            observed="missing",
            reason=f"price for date {period} not in price book",
        )

    book_price = prices[period]
    # Relative tolerance 1e-9 like check_claim
    if value == 0 and book_price == 0:
        ok = True
    elif value == 0:
        ok = abs(book_price) < 1e-9
    else:
        rel_diff = abs(book_price - value) / abs(value)
        ok = rel_diff < 1e-9

    return Result(
        check_type="price",
        status="pass" if ok else "fail",
        claim_id=claim_id,
        expected=str(value),
        observed=str(book_price),
        reason="price matches price book" if ok else "price does not match price book",
    )


def check_risk(claims_for_run: List[Dict[str, Any]], recomputed_risk: Optional[Dict[str, Any]]) -> List[Result]:
    """Every 'risk' claim equals the independently recomputed risk.risk_at value.

    Returns a list of Results, one per risk claim.
    """
    results: List[Result] = []
    risk_claims = [c for c in claims_for_run if c.get("source") == "risk"]

    if not recomputed_risk:
        for claim in risk_claims:
            results.append(Result(
                check_type="risk",
                status="fail",
                claim_id=claim.get("id"),
                expected=str(claim.get("value")),
                observed="missing",
                reason="recomputed risk not available",
            ))
        return results

    # Map claim metric to risk dict key
    metric_to_key = {
        "risk_score": "score",
        "risk_vol_pct": "vol_pct",
        "risk_drawdown": "drawdown",
        "risk_below_ma200": "below_ma200",
    }

    for claim in risk_claims:
        metric = claim.get("metric")
        key = metric_to_key.get(metric)
        if key is None:
            results.append(Result(
                check_type="risk",
                status="fail",
                claim_id=claim.get("id"),
                expected=str(claim.get("value")),
                observed="unknown metric",
                reason=f"unknown risk metric {metric}",
            ))
            continue

        claim_value = claim.get("value")
        recomputed_value = recomputed_risk.get(key)

        # For below_ma200, claim stores 1.0/0.0, risk stores bool
        if key == "below_ma200":
            claim_bool = bool(claim_value)
            ok = claim_bool == recomputed_value
        else:
            # Numeric comparison with relative tolerance
            if claim_value == 0 and recomputed_value == 0:
                ok = True
            elif claim_value == 0:
                ok = abs(recomputed_value) < 1e-9
            else:
                rel_diff = abs(recomputed_value - claim_value) / abs(claim_value)
                ok = rel_diff < 1e-9

        results.append(Result(
            check_type="risk",
            status="pass" if ok else "fail",
            claim_id=claim.get("id"),
            expected=str(claim_value),
            observed=str(recomputed_value),
            reason="risk value matches recomputed" if ok else "risk value differs from recomputed",
        ))

    return results


def check_narrative(narrative_row: Optional[Dict[str, Any]], claims_for_run: List[Dict[str, Any]]) -> Result:
    """If a narrative with status ok exists, every {{claim:id}} refers to a claim
    of THIS run and no stray digits. Status pending_review -> fail; skipped/none -> warn.
    """
    run_id = claims_for_run[0].get("run_id") if claims_for_run else None
    claim_ids = {c["id"] for c in claims_for_run}

    if narrative_row is None:
        return Result(
            check_type="narrative",
            status="warn",
            claim_id=None,
            expected=None,
            observed=None,
            reason="no narrative row found",
        )

    status = narrative_row.get("status")
    if status == "pending_review":
        return Result(
            check_type="narrative",
            status="fail",
            claim_id=None,
            expected="ok",
            observed="pending_review",
            reason="narrative validation failed after retry",
        )
    if status in ("skipped", None):
        return Result(
            check_type="narrative",
            status="warn",
            claim_id=None,
            expected="ok",
            observed=status or "none",
            reason="no narrative (skipped or not generated)",
        )

    # status == "ok" - validate using narrative.validate_narrative
    narrative_text = narrative_row.get("narrative")
    if not narrative_text:
        return Result(
            check_type="narrative",
            status="fail",
            claim_id=None,
            expected="valid narrative with placeholders",
            observed="empty",
            reason="narrative status ok but text is empty",
        )

    problems = narrative.validate_narrative(narrative_text, claim_ids)  # type: ignore
    if problems:
        return Result(
            check_type="narrative",
            status="fail",
            claim_id=None,
            expected="all placeholders valid, no stray digits",
            observed=problems[0],
            reason="narrative validation failed: " + "; ".join(problems),
        )

    return Result(
        check_type="narrative",
        status="pass",
        claim_id=None,
        expected="ok",
        observed="ok",
        reason="narrative validates: all placeholders reference known claims, no stray digits",
    )


def check_snapshot_integrity(claim: Dict[str, Any], snapshot_meta: Dict[str, Any], payload: Any) -> Result:
    """The snapshot payload still hashes to the payload_hash stored with it (plan T4 check 4).

    Recomputes sha256 over the same canonical JSON snapshot_store hashed when it stored the snapshot. A payload changed
    after it was stored (tampered or corrupted) fails; so does a snapshot with no stored hash.
    """
    claim_id = claim.get("id")
    stored = snapshot_meta.get("payload_hash")
    if not stored:
        return Result(
            check_type="snapshot_integrity",
            status="fail",
            claim_id=claim_id,
            expected=None,
            observed=None,
            reason="snapshot metadata missing payload_hash",
        )

    actual = snapshot_store._payload_hash(payload)
    ok = actual == stored
    return Result(
        check_type="snapshot_integrity",
        status="pass" if ok else "fail",
        claim_id=claim_id,
        expected=str(stored),
        observed=actual,
        reason="payload matches its stored hash" if ok else "payload does not match its stored hash",
    )


def check_unit(claim: Dict[str, Any]) -> Result:
    """The claim's unit is the one expected for its metric (config.EXPECTED_UNITS), so a percentage and a ratio are never
    compared as if they were the same kind of number. Different unit -> fail; metric not in the table -> warn."""
    claim_id = claim.get("id")
    metric = claim.get("metric")
    unit = claim.get("unit")
    expected = config.EXPECTED_UNITS.get(metric)

    if expected is None:
        return Result(
            check_type="unit",
            status="warn",
            claim_id=claim_id,
            expected=None,
            observed=str(unit),
            reason=f"no expected unit for metric {metric}",
        )

    ok = unit == expected
    return Result(
        check_type="unit",
        status="pass" if ok else "fail",
        claim_id=claim_id,
        expected=expected,
        observed=str(unit),
        reason=f"unit {unit} as expected" if ok else f"unit {unit}, expected {expected}",
    )


def _safe(check, *args) -> Result:
    """Run one check. A check that meets data it cannot handle records a FAIL with the reason (the claim is then not
    counted as verified) instead of raising: one odd value must never stop the gate from recording the rest."""
    try:
        return check(*args)
    except Exception as exc:  # noqa: BLE001
        claim = args[0] if args and isinstance(args[0], dict) else {}
        name = getattr(check, "__name__", "check").replace("check_", "")
        return Result(check_type=name, status="fail", claim_id=claim.get("id"), expected=None, observed=None,
                      reason=f"check error: {type(exc).__name__}: {exc}"[:300])


def verify_run(inputs: Dict[str, Any]) -> List[Result]:
    """Run all verification checks for a single committee run.

    inputs must contain:
    - claims: List[Dict] - all claims for this run_id
    - snapshots_by_claim_id: Dict[claim_id, (snapshot_meta, payload)] - fetched snapshots
    - run_time: str - ISO timestamp of the run
    - run_date: str - date of the run (YYYY-MM-DD)
    - prices: Dict[str, float] - price book closes {date: close}
    - recomputed_risk: Dict - risk.risk_at result for this symbol/date
    - narrative_row: Dict or None - committee_narratives row for this run_id
    - windows: Dict[str, timedelta] - optional staleness windows override
    """
    claims_list = inputs.get("claims", [])
    snapshots = inputs.get("snapshots_by_claim_id", {})
    run_time = inputs.get("run_time")
    run_date = inputs.get("run_date")
    prices = inputs.get("prices", {})
    recomputed_risk = inputs.get("recomputed_risk")
    narrative_row = inputs.get("narrative_row")
    windows = inputs.get("windows")

    results: List[Result] = []

    # Group snapshots by claim_id for easy lookup
    for claim in claims_list:
        claim_id = claim.get("id")
        snap_info = snapshots.get(claim_id)
        snapshot_meta = snap_info[0] if snap_info else None
        payload = snap_info[1] if snap_info else None

        # Traceability
        results.append(_safe(check_traceability, claim, payload, prices))

        # Unit (every claim)
        results.append(_safe(check_unit, claim))

        # Snapshot integrity (every claim that has a snapshot)
        if snap_info:
            results.append(_safe(check_snapshot_integrity, claim, snapshot_meta or {}, payload))

        # Point in time (only for snapshot sources)
        if claim.get("source") not in ("risk", "pricebook") and snapshot_meta:
            results.append(_safe(check_point_in_time, claim, snapshot_meta, run_time))

        # Staleness (only for snapshot sources with as_of)
        if claim.get("source") not in ("risk", "pricebook") and snapshot_meta:
            results.append(_safe(check_staleness, claim, snapshot_meta, run_date, windows))

        # Price (only for pricebook source)
        if claim.get("source") == "pricebook":
            results.append(_safe(check_price, claim, prices))

    # Risk checks (one per risk claim)
    try:
        results.extend(check_risk(claims_list, recomputed_risk))
    except Exception as exc:  # noqa: BLE001
        results.append(Result(check_type="risk", status="fail", claim_id=None, expected=None, observed=None, reason=f"check error: {type(exc).__name__}: {exc}"[:300]))

    # Narrative check (once per run)
    results.append(_safe(check_narrative, narrative_row, claims_list))

    return results


def summarize(results: List[Result]) -> Dict[str, Any]:
    """Summarize verification results.

    Returns: {"total", "passed", "failed", "warned", "ok": failed == 0,
              "verified_claims", "total_claims",
              "badge": "N/N numbers verified against source"}

    A claim counts as "verified" only if EVERY check for that claim_id is 'pass'.
    Staleness 'warn' does NOT disqualify a claim.
    The badge uses verified_claims / total_claims.
    """
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    warned = sum(1 for r in results if r.status == "warn")

    # Group results by claim_id to determine if each claim is fully verified
    # Claims with no claim_id (narrative check) are not counted in the badge
    results_by_claim: Dict[Optional[str], List[Result]] = {}
    for r in results:
        cid = r.claim_id
        if cid not in results_by_claim:
            results_by_claim[cid] = []
        results_by_claim[cid].append(r)

    # A claim is verified if ALL its results are 'pass'
    # (staleness 'warn' is not a 'pass', so it disqualifies unless we skip it)
    # But per spec: staleness warn does NOT disqualify. So we ignore staleness warn
    # when checking if a claim is fully verified.
    def claim_is_verified(claim_results: List[Result]) -> bool:
        for r in claim_results:
            if r.status == "fail":
                return False
            if r.status == "warn" and r.check_type != "staleness":
                return False
        return True

    # Count verified claims (excluding narrative check which has claim_id=None)
    verified_claims = sum(
        1 for cid, crs in results_by_claim.items()
        if cid is not None and claim_is_verified(crs)
    )
    total_claims = sum(1 for cid in results_by_claim if cid is not None)

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "warned": warned,
        "ok": failed == 0,
        "verified_claims": verified_claims,
        "total_claims": total_claims,
        "badge": f"{verified_claims}/{total_claims} numbers verified against source",
    }