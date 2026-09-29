"""Tests for GET /api/reports/narratives/{id}/evidence (S3 T7, "Show my work").

SAFETY (plan rule 5.1/5.4): the response must never contain failure reasons, raw
snapshot payloads, check names or anything from quarantine_items, and a
quarantined narrative must 404 exactly like the report itself.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import db, flags, migrate
from app.auth import TokenPayload, get_current_user
from app.migrated_tables import claims_table, quarantine_items_table, source_snapshots_table, verification_results_table

RUN_ID = "2026-09-21:AAPL"
NARRATIVE_ID = "narrative-evidence-test"
VIEWER = TokenPayload(sub="ann", role="viewer")
ADMIN = TokenPayload(sub="root", role="admin")


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


@pytest.fixture
def migrated(real_db):
    """A throwaway database with the S3 migrations applied (claims, snapshots, verification_results, quarantine_items)."""
    with real_db.engine.begin() as conn:
        migrate.upgrade(conn)
    return real_db


@pytest.fixture(autouse=True)
def _fresh_flags():
    flags.clear_cache()
    yield
    flags.clear_cache()


def _client(user=None) -> TestClient:
    from app.main import app

    c = TestClient(app)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    else:
        app.dependency_overrides.pop(get_current_user, None)
    return c


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    from app.main import app
    app.dependency_overrides.pop(get_current_user, None)


def _seed_narrative(run_id: str | None = RUN_ID, narrative_id: str = NARRATIVE_ID) -> None:
    payload = {
        "id": narrative_id,
        "date": "20260921",
        "provider": "system",
        "model": "committee-vote",
        "title": "Investment Committee · daily review",
        "narrative": "The committee bought AAPL.",
        "created_at": _iso_now(),
    }
    if run_id is not None:
        payload["run_id"] = run_id
    db.create_report_narrative(payload)


def _seed_snapshot(conn, snap_id: str, fetched_at: str) -> None:
    conn.execute(
        source_snapshots_table.insert().values(
            id=snap_id,
            source="sec_facts",
            ticker="AAPL",
            as_of="2025-12-31",
            fetched_at=fetched_at,
            payload_json=json.dumps({"concepts": {"revenue": {"series": [{"end": "2025-12-31", "val": 100.0}]}}}),
            payload_hash="deadbeef",
        )
    )


def _seed_claims_and_verification(conn, *, second_claim_status: str = "pass") -> None:
    """One pricebook claim (no snapshot) and one sec_facts claim (with a snapshot), both checked.

    second_claim_status controls the sec_facts claim's traceability check result, so tests can
    flip a single claim from pass to fail without duplicating the whole fixture.
    """
    snap_id = str(uuid.uuid4())
    _seed_snapshot(conn, snap_id, "2026-09-20T12:00:00.000000+00:00")

    conn.execute(
        claims_table.insert(),
        [
            {
                "id": "claim-close",
                "run_id": RUN_ID,
                "ticker": "AAPL",
                "metric": "close",
                "value": 150.0,
                "unit": "USD",
                "period": "2026-09-21",
                "source": "pricebook",
                "source_snapshot_id": None,
                "source_path": None,
                "text_span": None,
                "created_at": _iso_now(),
            },
            {
                "id": "claim-revenue-growth",
                "run_id": RUN_ID,
                "ticker": "AAPL",
                "metric": "revenue_growth",
                "value": 0.12,
                "unit": "pct",
                "period": "2025-12-31",
                "source": "sec_facts",
                "source_snapshot_id": snap_id,
                "source_path": "/concepts/revenue/series/0/val",
                "text_span": "derived: (revenue - revenue_prior) / revenue_prior | revenue=/concepts/revenue/series/0/val | revenue_prior=/concepts/revenue/series/0/val",
                "created_at": _iso_now(),
            },
        ],
    )

    conn.execute(
        verification_results_table.insert(),
        [
            {
                "id": str(uuid.uuid4()),
                "run_id": RUN_ID,
                "claim_id": "claim-close",
                "check_type": "price",
                "status": "pass",
                "expected": "150.0",
                "observed": "150.0",
                "reason": "price matches price book",
                "created_at": _iso_now(),
            },
            {
                "id": str(uuid.uuid4()),
                "run_id": RUN_ID,
                "claim_id": "claim-revenue-growth",
                "check_type": "traceability",
                "status": second_claim_status,
                "expected": "0.12",
                "observed": "0.12" if second_claim_status == "pass" else "mismatch: source contains a materially different number",
                "reason": "value matches source" if second_claim_status == "pass" else "value does not match source (internal detail never shown to users)",
                "created_at": _iso_now(),
            },
            {
                "id": str(uuid.uuid4()),
                "run_id": RUN_ID,
                "claim_id": None,
                "check_type": "narrative",
                "status": "pass",
                "expected": None,
                "observed": None,
                "reason": "narrative validates",
                "created_at": _iso_now(),
            },
        ],
    )


def _no_forbidden_keys(node) -> bool:
    """Recursively assert no 'reason', 'detail' or 'payload' key anywhere in the response body."""
    forbidden = {"reason", "detail", "payload"}
    if isinstance(node, dict):
        if forbidden & node.keys():
            return False
        return all(_no_forbidden_keys(v) for v in node.values())
    if isinstance(node, list):
        return all(_no_forbidden_keys(v) for v in node)
    return True


# --------------------------------------------------------------------- tests --


def test_passing_claims_return_200_verified_true_with_exact_claim_fields(migrated):
    with migrated.engine.begin() as conn:
        _seed_claims_and_verification(conn)
    _seed_narrative()

    r = _client(VIEWER).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is True
    assert len(body["claims"]) == 2

    by_id = {c["claim_id"]: c for c in body["claims"]}
    close = by_id["claim-close"]
    assert close["label"] == "close"
    assert close["value"] == 150.0
    assert close["unit"] == "USD"
    assert close["source"] == "Market data"
    assert close["field_path"] is None
    assert close["as_of"] == "2026-09-21"  # falls back to the claim's own period (no snapshot for pricebook)
    assert close["passed"] is True

    rg = by_id["claim-revenue-growth"]
    assert rg["label"] == "revenue_growth"
    assert rg["value"] == 0.12
    assert rg["unit"] == "pct"
    assert rg["source"] == "SEC"
    assert rg["field_path"] == "/concepts/revenue/series/0/val"
    assert rg["as_of"] == "2026-09-20T12:00:00.000000+00:00"  # the snapshot's fetched_at
    assert rg["passed"] is True

    assert _no_forbidden_keys(body)


def test_one_failing_claim_makes_verified_false_and_leaks_no_failure_reason(migrated):
    with migrated.engine.begin() as conn:
        _seed_claims_and_verification(conn, second_claim_status="fail")
    _seed_narrative()

    r = _client(VIEWER).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r.status_code == 200
    body = r.json()
    assert body["verified"] is False

    by_id = {c["claim_id"]: c for c in body["claims"]}
    assert by_id["claim-close"]["passed"] is True
    assert by_id["claim-revenue-growth"]["passed"] is False

    assert _no_forbidden_keys(body)
    # Nothing from the failed check (its status, its check_type, its stored mismatch text) leaks either.
    raw = json.dumps(body)
    assert "mismatch" not in raw
    assert "traceability" not in raw
    assert "does not match source" not in raw


def test_quarantined_narrative_returns_404(migrated):
    with migrated.engine.begin() as conn:
        _seed_claims_and_verification(conn)
        conn.execute(
            quarantine_items_table.insert().values(
                id=str(uuid.uuid4()),
                channel="committee_report",
                run_id=RUN_ID,
                content_ref=NARRATIVE_ID,
                stage="A6",
                status="pending",
                reasons_json=json.dumps(["A6 gate failed"]),
                created_at=_iso_now(),
                reviewer_id=None,
                review_note=None,
                reviewed_at=None,
            )
        )
    _seed_narrative()
    flags.set_flag("publish.enforce", True, "admin")

    r = _client(VIEWER).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r.status_code == 404
    assert "A6 gate failed" not in json.dumps(r.json())  # the quarantine item's reasons never leak

    # An admin can still see it through.
    r_admin = _client(ADMIN).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r_admin.status_code == 200


def test_no_login_returns_401(migrated):
    _seed_narrative()
    r = _client(None).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r.status_code == 401


def test_unknown_narrative_returns_404(migrated):
    r = _client(VIEWER).get("/api/reports/narratives/does-not-exist/evidence")
    assert r.status_code == 404


def test_narrative_without_a_run_id_has_no_claims_and_is_vacuously_verified(migrated):
    _seed_narrative(run_id=None)
    r = _client(VIEWER).get(f"/api/reports/narratives/{NARRATIVE_ID}/evidence")
    assert r.status_code == 200
    assert r.json() == {"verified": True, "claims": []}
