"""Quarantine review (S3 T6): approve re-runs the A6+A7 gates, override needs a reason, reject needs
a note. Also covers the fix to the pre-existing GET /quarantine and POST .../action routes, which
referenced columns ("reason", "decided_at", "decided_by") the quarantine_items table does not have
(the real columns are reasons_json, reviewed_at, reviewer_id) -- calling POST .../action raised a
SQLAlchemy CompileError on every request before this fix.

Real HTTP requests against a migrated temp database, with an admin token (dependency override, same
pattern as test_gate_health.py / test_compliance.py).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select

from app import db, migrate, publish, rate_limit
from app.migrated_tables import quarantine_items_table, verification_results_table

TS = "2026-09-19T00:00:00.000000+00:00"


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


@pytest.fixture
def qdb(tmp_path, monkeypatch):
    """A throwaway database migrated to head (quarantine_items + verification_results included)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'q.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        migrate.upgrade(conn)
    monkeypatch.setattr(db, "engine", eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _reset_admin_rate_limit():
    rate_limit._admin_limiter._hits.clear()
    yield
    rate_limit._admin_limiter._hits.clear()


def _seed_quarantine(eng, item_id, *, run_id=None, content_ref="ref-1", channel="committee_report",
                      stage="A6", status="pending", reasons=None):
    with eng.begin() as conn:
        conn.execute(quarantine_items_table.insert().values(
            id=item_id, channel=channel, run_id=run_id, content_ref=content_ref, stage=stage, status=status,
            reasons_json=json.dumps(reasons or []), created_at=TS, reviewer_id=None, review_note=None, reviewed_at=None,
        ))


def _seed_verification(eng, run_id, status, reason="test reason"):
    with eng.begin() as conn:
        conn.execute(verification_results_table.insert().values(
            id=f"vr-{run_id}-{status}", run_id=run_id, claim_id=None, check_type="traceability",
            status=status, expected=None, observed=None, reason=reason, created_at=TS,
        ))


def _row(eng, item_id):
    with eng.connect() as conn:
        return conn.execute(select(quarantine_items_table).where(quarantine_items_table.c.id == item_id)).first()


def _client_as(role, sub="reviewer-1"):
    from fastapi.testclient import TestClient
    from app.auth import TokenPayload, get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub=sub, role=role)
    return TestClient(app), app, get_current_user


def _anon_client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app), app


# ---- approve: gates pass ----------------------------------------------------------


def test_approve_passes_when_gates_pass(qdb):
    _seed_quarantine(qdb, "q-pass", run_id="run-pass", reasons=["A6 gate failed for run-pass: was bad"])
    _seed_verification(qdb, "run-pass", "pass")
    c, app, dep = _client_as("admin")
    try:
        r = c.post("/api/admin/quarantine/q-pass/approve", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "approved" and body["decided_by"] == "reviewer-1" and body["decided_at"] is not None
    finally:
        app.dependency_overrides.pop(dep, None)
    row = _row(qdb, "q-pass")
    assert row.status == "approved" and row.reviewer_id == "reviewer-1" and row.review_note is None
    assert row.reviewed_at is not None


# ---- approve: gates fail, no override -> 409 --------------------------------------


def test_approve_without_override_409_when_gates_fail(qdb):
    _seed_quarantine(qdb, "q-fail", run_id="run-fail")
    _seed_verification(qdb, "run-fail", "fail")
    c, app, dep = _client_as("admin")
    try:
        r = c.post("/api/admin/quarantine/q-fail/approve", json={})
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert detail["failing_checks"] == ["A6 gate failed for run-fail: 0/0 numbers verified against source"]
    finally:
        app.dependency_overrides.pop(dep, None)
    row = _row(qdb, "q-fail")
    assert row.status == "pending" and row.reviewer_id is None  # untouched


# ---- approve: override too short -> 422 --------------------------------------------


def test_approve_override_too_short_is_422(qdb):
    _seed_quarantine(qdb, "q-short", run_id="run-fail2")
    _seed_verification(qdb, "run-fail2", "fail")
    c, app, dep = _client_as("admin")
    try:
        r = c.post("/api/admin/quarantine/q-short/approve", json={"override_reason": "too short"})
        assert r.status_code == 422
    finally:
        app.dependency_overrides.pop(dep, None)
    row = _row(qdb, "q-short")
    assert row.status == "pending"  # rejected by validation before any DB write


# ---- approve: override with a real reason stores it + the reviewer ----------------


def test_approve_override_stores_reason_and_reviewer(qdb):
    _seed_quarantine(qdb, "q-override", run_id="run-fail3")
    _seed_verification(qdb, "run-fail3", "fail")
    c, app, dep = _client_as("admin", sub="admin-42")
    try:
        r = c.post("/api/admin/quarantine/q-override/approve", json={"override_reason": "reviewed manually, false positive"})
        assert r.status_code == 200
        assert r.json()["status"] == "approved"
    finally:
        app.dependency_overrides.pop(dep, None)
    row = _row(qdb, "q-override")
    assert row.status == "approved"
    assert row.reviewer_id == "admin-42"
    assert row.review_note == "reviewed manually, false positive"
    assert row.reviewed_at is not None


# ---- reject: needs a note -----------------------------------------------------------


def test_reject_needs_a_note(qdb):
    _seed_quarantine(qdb, "q-reject-1", run_id=None)
    c, app, dep = _client_as("admin")
    try:
        assert c.post("/api/admin/quarantine/q-reject-1/reject", json={}).status_code == 422
        assert c.post("/api/admin/quarantine/q-reject-1/reject", json={"note": ""}).status_code == 422
        r = c.post("/api/admin/quarantine/q-reject-1/reject", json={"note": "content is inaccurate"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "rejected" and body["decided_by"] == "reviewer-1"
    finally:
        app.dependency_overrides.pop(dep, None)
    row = _row(qdb, "q-reject-1")
    assert row.status == "rejected" and row.review_note == "content is inaccurate" and row.reviewer_id == "reviewer-1"
    assert row.reviewed_at is not None


# ---- auth: non-admin 403, anonymous 401, on all three routes -----------------------


def test_non_admin_and_anonymous_on_review_routes(qdb):
    _seed_quarantine(qdb, "q-auth", run_id=None)

    c, app, dep = _client_as("viewer")
    try:
        assert c.post("/api/admin/quarantine/q-auth/approve", json={}).status_code == 403
        assert c.post("/api/admin/quarantine/q-auth/reject", json={"note": "x"}).status_code == 403
        assert c.post("/api/admin/quarantine/q-auth/action", json={"action": "approve"}).status_code == 403
    finally:
        app.dependency_overrides.pop(dep, None)

    c2, app2 = _anon_client()
    assert c2.post("/api/admin/quarantine/q-auth/approve", json={}).status_code == 401
    assert c2.post("/api/admin/quarantine/q-auth/reject", json={"note": "x"}).status_code == 401
    assert c2.post("/api/admin/quarantine/q-auth/action", json={"action": "approve"}).status_code == 401

    row = _row(qdb, "q-auth")
    assert row.status == "pending"  # none of the rejected/unauthenticated calls touched it


# ---- the old /action route still works (it was broken: wrong column names) --------


def test_old_action_route_still_works(qdb):
    _seed_quarantine(qdb, "q-old-approve", run_id=None, reasons=["A7 blocked"])
    _seed_quarantine(qdb, "q-old-reject", run_id=None, reasons=["A7 blocked"])
    c, app, dep = _client_as("admin", sub="admin-9")
    try:
        r = c.post("/api/admin/quarantine/q-old-approve/action", json={"action": "approve", "reason": "manual ok"})
        assert r.status_code == 204
        r = c.post("/api/admin/quarantine/q-old-reject/action", json={"action": "reject", "reason": "manual no"})
        assert r.status_code == 204
        # 400 for a bad action, 404 for a missing item -- unchanged behaviour.
        _seed_quarantine(qdb, "q-old-bad", run_id=None)
        assert c.post("/api/admin/quarantine/q-old-bad/action", json={"action": "nope"}).status_code == 400
        assert c.post("/api/admin/quarantine/does-not-exist/action", json={"action": "approve"}).status_code == 404
    finally:
        app.dependency_overrides.pop(dep, None)

    approved = _row(qdb, "q-old-approve")
    assert approved.status == "approved" and approved.reviewer_id == "admin-9" and approved.review_note == "manual ok"
    assert approved.reviewed_at is not None

    rejected = _row(qdb, "q-old-reject")
    assert rejected.status == "rejected" and rejected.reviewer_id == "admin-9" and rejected.review_note == "manual no"
    assert rejected.reviewed_at is not None


# ---- GET /quarantine reflects the reason (joined from reasons_json) and the decision ----


def test_list_quarantine_reflects_reason_and_decision(qdb):
    _seed_quarantine(qdb, "q-list", run_id=None, reasons=["A6 gate failed for r: bad", "A7 blocked: advice.banned_phrase"])
    c, app, dep = _client_as("admin")
    try:
        items = c.get("/api/admin/quarantine", params={"status": "pending"}).json()
        assert len(items) == 1
        assert items[0]["reason"] == "A6 gate failed for r: bad; A7 blocked: advice.banned_phrase"
        assert items[0]["decided_at"] is None and items[0]["decided_by"] is None

        c.post("/api/admin/quarantine/q-list/reject", json={"note": "bad numbers"})
        items = c.get("/api/admin/quarantine", params={"status": "rejected"}).json()
        assert len(items) == 1
        assert items[0]["decided_by"] == "reviewer-1" and items[0]["decided_at"] is not None
        assert items[0]["reason"] == "A6 gate failed for r: bad; A7 blocked: advice.banned_phrase"  # unchanged by the review note
    finally:
        app.dependency_overrides.pop(dep, None)


# ---- publish.recheck_quarantine_item: unit coverage for both gates -----------------


def test_recheck_quarantine_item_a6_only(qdb):
    _seed_verification(qdb, "run-a6-pass", "pass")
    assert publish.recheck_quarantine_item({"run_id": "run-a6-pass", "content_ref": "nope", "channel": "committee_report"}) == (True, [])

    _seed_verification(qdb, "run-a6-fail", "fail")
    ok, reasons = publish.recheck_quarantine_item({"run_id": "run-a6-fail", "content_ref": "nope", "channel": "committee_report"})
    assert ok is False and reasons == ["A6 gate failed for run-a6-fail: 0/0 numbers verified against source"]


def test_recheck_quarantine_item_a7_blocked_when_the_narrative_still_reads_that_way(qdb):
    db.create_report_narrative({
        "id": "nar-blocked", "date": "2026-09-19", "provider": "system", "model": "n/a",
        "narrative": "This trade is guaranteed to double, you should buy now.", "created_at": TS,
    })
    ok, reasons = publish.recheck_quarantine_item({"run_id": None, "content_ref": "nar-blocked", "channel": "committee_report"})
    assert ok is False
    assert reasons == ["A7 blocked: advice.banned_phrase, advice.direct_instruction, disclaimer.required"]


def test_recheck_quarantine_item_a7_passes_once_the_narrative_is_fixed(qdb):
    db.create_report_narrative({
        "id": "nar-fixed", "date": "2026-09-19", "provider": "system", "model": "n/a",
        "narrative": "The committee voted to buy AAPL.", "created_at": TS,
    })
    assert publish.recheck_quarantine_item({"run_id": None, "content_ref": "nar-fixed", "channel": "committee_report"}) == (True, [])


def test_recheck_quarantine_item_no_content_ref_match_trivially_passes_a7(qdb):
    assert publish.recheck_quarantine_item({"run_id": None, "content_ref": "no-such-narrative", "channel": "committee_report"}) == (True, [])
