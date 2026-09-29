"""Weekly discrepancy report (S3 T13): pure build_draft() on hand-built rows, the idempotent job, and the admin/public
routes on a migrated temp database."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine

from app import db, migrate, weekly_report as wr
from app.migrated_tables import claims_table, compliance_events_table, verification_results_table
from app.scripts import weekly_report as weekly_report_job

WEEK = "2026-09-21"  # a Monday


def vrow(run_id, claim_id, check_type, status, metric=None, reason=None):
    default_reason = {"pass": "value matches source", "warn": "data is stale", "fail": "check failed"}[status]
    return {"run_id": run_id, "claim_id": claim_id, "check_type": check_type, "status": status,
            "reason": reason or default_reason, "metric": metric}


# --------------------------------------------------------------------------------------------------- build_draft --


def _week_rows():
    """One week, three committee runs, hand-computed below."""
    rows = []
    # AAPL: c1 fully verified (3 passing checks), c2 has one failing check (point_in_time), c3 verified on a warn only.
    for ct in ("traceability", "point_in_time", "staleness"):
        rows.append(vrow("2026-09-21:AAPL", "c1", ct, "pass", metric="revenue"))
    rows.append(vrow("2026-09-21:AAPL", "c2", "traceability", "pass", metric="revenue"))
    rows.append(vrow("2026-09-21:AAPL", "c2", "point_in_time", "fail", metric="revenue", reason="snapshot fetched after run time"))
    rows.append(vrow("2026-09-21:AAPL", "c2", "staleness", "pass", metric="revenue"))
    rows.append(vrow("2026-09-21:AAPL", "c3", "staleness", "warn", metric="revenue"))
    # MSFT: c4 fully verified, c5 fails traceability.
    rows.append(vrow("2026-09-21:MSFT", "c4", "traceability", "pass", metric="eps"))
    rows.append(vrow("2026-09-21:MSFT", "c4", "point_in_time", "pass", metric="eps"))
    rows.append(vrow("2026-09-21:MSFT", "c5", "traceability", "fail", metric="eps", reason="missing filing"))
    # TSLA: c6 verified; the run also has a narrative-level fail (no claim_id, no metric).
    rows.append(vrow("2026-09-22:TSLA", "c6", "traceability", "pass", metric="net_income"))
    rows.append(vrow("2026-09-22:TSLA", None, "narrative", "fail", metric=None, reason="narrative validation failed after retry"))
    return rows


def test_build_draft_computes_the_exact_a6_pass_rate_and_top_5_lists():
    draft = wr.build_draft(_week_rows(), [{"action": "blocked"}, {"action": "blocked"}, {"action": "rewritten"}], WEEK)
    assert draft["week_start"] == WEEK
    assert draft["runs_checked"] == 3
    # checked: c1,c2,c3 (AAPL) + c4,c5 (MSFT) + c6 (TSLA) = 6; verified: c1,c3,c4,c6 = 4 (c2, c5 each have a failing check)
    assert draft["a6"] == {"runs_checked": 3, "runs_ok": 0, "claims_checked": 6, "claims_verified": 4, "pass_rate": pytest.approx(4 / 6)}
    assert draft["top_failing_checks"] == [
        {"check_type": "narrative", "failures": 1, "top_reason": "narrative validation failed after retry"},
        {"check_type": "point_in_time", "failures": 1, "top_reason": "snapshot fetched after run time"},
        {"check_type": "traceability", "failures": 1, "top_reason": "missing filing"},
    ]
    assert draft["top_failing_metrics"] == [
        {"metric": "eps", "failures": 1, "claims": 1},
        {"metric": "revenue", "failures": 1, "claims": 1},
    ]
    assert draft["a7_by_action"] == {"blocked": 2, "rewritten": 1, "flagged": 0}


def test_build_draft_is_pure_and_free_of_db_access(monkeypatch):
    """No engine, no connection -- build_draft never touches app.db."""
    monkeypatch.delattr(db, "engine")
    draft = wr.build_draft(_week_rows(), [], WEEK)
    assert draft["runs_checked"] == 3  # would have raised AttributeError if build_draft read db.engine


def test_top_failing_checks_and_metrics_keep_only_the_top_5_highest_first():
    rows = []
    names = ["a", "b", "c", "d", "e", "f", "g"]  # a:1 failure, b:2, ... g:7 -- the top 5 are c..g, highest first
    for i, name in enumerate(names):
        for k in range(i + 1):
            rows.append(vrow(f"r{i}", f"{name}-{k}", name, "fail", metric=name))
    checks = wr._top_failing_checks(rows)
    metrics = wr._top_failing_metrics(rows)
    assert [c["check_type"] for c in checks] == ["g", "f", "e", "d", "c"]
    assert [(m["metric"], m["failures"]) for m in metrics] == [("g", 7), ("f", 6), ("e", 5), ("d", 4), ("c", 3)]


def test_top_failing_checks_and_metrics_tie_break_alphabetically():
    rows = [vrow("r1", "x1", "zzz", "fail", metric="zzz"), vrow("r2", "x2", "aaa", "fail", metric="aaa"),
            vrow("r3", "x3", "mmm", "fail", metric="mmm")]
    assert [c["check_type"] for c in wr._top_failing_checks(rows)] == ["aaa", "mmm", "zzz"]
    assert [m["metric"] for m in wr._top_failing_metrics(rows)] == ["aaa", "mmm", "zzz"]


def test_a7_by_action_reports_all_three_actions_even_at_zero():
    assert wr.build_draft([], [], WEEK)["a7_by_action"] == {"blocked": 0, "rewritten": 0, "flagged": 0}


def test_a6_pass_rate_is_none_when_no_claim_was_checked():
    draft = wr.build_draft([vrow("r1", None, "narrative", "fail", reason="x")], [], WEEK)
    assert draft["a6"]["pass_rate"] is None and draft["a6"]["claims_checked"] == 0


def test_previous_week_start_is_the_monday_before_this_monday():
    assert wr.previous_week_start(date(2026, 9, 28)) == date(2026, 9, 21)  # today is a Monday
    assert wr.previous_week_start(date(2026, 10, 1)) == date(2026, 9, 21)  # a Thursday, same current week


# ------------------------------------------------------------------------------------------------------- db layer --


@pytest.fixture
def report_db(tmp_path, monkeypatch):
    """A throwaway database migrated to head (weekly_reports included), swapped in for app.db.engine."""
    eng = create_engine(f"sqlite:///{tmp_path / 'wr.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        migrate.upgrade(conn)
    monkeypatch.setattr(db, "engine", eng)
    yield eng
    eng.dispose()


def _seed_week(engine, week_start: date):
    """One claim + one failing verification result + one compliance event, dated inside `week_start`'s Mon-Sun week.
    Ids are suffixed by the week so a test can seed more than one week without a primary-key collision."""
    wk = week_start.isoformat()
    ts = f"{wk}T12:00:00.000000+00:00"
    with engine.begin() as conn:
        conn.execute(claims_table.insert().values(
            id=f"c1-{wk}", run_id=f"{wk}:AAPL", ticker="AAPL", metric="revenue", value=1.0, unit="USD",
            period="2026-06-30", source="sec_xbrl", source_snapshot_id=None, source_path=None, text_span=None, created_at=ts))
        conn.execute(verification_results_table.insert().values(
            id=f"vr1-{wk}", run_id=f"{wk}:AAPL", claim_id=f"c1-{wk}", check_type="traceability", status="fail",
            expected=None, observed=None, reason="snapshot not found", created_at=ts))
        conn.execute(compliance_events_table.insert().values(
            id=f"ce1-{wk}", run_id=None, channel="assistant", rule_id="r1", matched_text="", action="blocked", created_at=ts))


def test_fetch_week_joins_the_metric_and_stays_inside_the_week(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    # A row from the following week must never leak in.
    _seed_week(report_db, week_start + timedelta(days=7))
    out = wr.fetch_week(week_start)
    assert out["verification_rows"] == [{"run_id": "2026-09-21:AAPL", "claim_id": "c1-2026-09-21", "check_type": "traceability",
                                          "status": "fail", "reason": "snapshot not found", "metric": "revenue"}]
    assert out["compliance_rows"] == [{"action": "blocked"}]


def test_create_draft_for_week_is_idempotent(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    first = wr.create_draft_for_week(week_start)
    assert first is not None and first["status"] == "draft"
    second = wr.create_draft_for_week(week_start)
    assert second is None
    rows = wr.list_reports()
    assert len(rows) == 1 and rows[0]["week_start"] == week_start.isoformat()


def test_the_job_is_idempotent(report_db, monkeypatch):
    NOW = datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc)  # a Monday; the job drafts the week before
    monkeypatch.setattr(weekly_report_job, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    _seed_week(report_db, date(2026, 9, 21))
    first = weekly_report_job.main([])
    assert first == {"ok": True, "week_start": "2026-09-21", "created": True, "id": first["id"]}
    second = weekly_report_job.main([])
    assert second == {"ok": True, "week_start": "2026-09-21", "created": False}
    assert len(wr.list_reports()) == 1


def test_publish_moves_draft_to_published_and_twice_is_409(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    row = wr.create_draft_for_week(week_start)
    published = wr.publish(row["id"], "alice")
    assert published["status"] == "published" and published["published_by"] == "alice" and published["published_at"]
    with pytest.raises(wr.AlreadyPublished):
        wr.publish(row["id"], "alice")


def test_publish_unknown_id_raises_not_found(report_db):
    with pytest.raises(wr.NotFound):
        wr.publish("no-such-id", "alice")


def test_list_reports_filters_by_status_newest_week_first(report_db):
    _seed_week(report_db, date(2026, 9, 7))
    _seed_week(report_db, date(2026, 9, 14))
    r1 = wr.create_draft_for_week(date(2026, 9, 7))
    r2 = wr.create_draft_for_week(date(2026, 9, 14))
    wr.publish(r2["id"], "alice")
    assert [r["week_start"] for r in wr.list_reports()] == ["2026-09-14", "2026-09-07"]
    assert [r["week_start"] for r in wr.list_reports("published")] == ["2026-09-14"]
    assert [r["week_start"] for r in wr.list_reports("draft")] == ["2026-09-07"]


# --------------------------------------------------------------------------------------------------------- routes --


def _client_as(role):
    from fastapi.testclient import TestClient

    from app.auth import TokenPayload, get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub=role, role=role)
    return TestClient(app), app, get_current_user


def test_admin_routes_require_login_and_admin_role(report_db):
    from fastapi.testclient import TestClient
    from app.main import app

    no_auth = TestClient(app)
    assert no_auth.get("/api/admin/weekly-reports").status_code == 401
    assert no_auth.post("/api/admin/weekly-reports/x/publish").status_code == 401

    c, app_, dep = _client_as("viewer")
    try:
        assert c.get("/api/admin/weekly-reports").status_code == 403
        assert c.post("/api/admin/weekly-reports/x/publish").status_code == 403
    finally:
        app_.dependency_overrides.pop(dep, None)


def test_publish_twice_is_409_over_the_route(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    row = wr.create_draft_for_week(week_start)
    c, app_, dep = _client_as("admin")
    try:
        first = c.post(f"/api/admin/weekly-reports/{row['id']}/publish")
        assert first.status_code == 200 and first.json()["status"] == "published"
        second = c.post(f"/api/admin/weekly-reports/{row['id']}/publish")
        assert second.status_code == 409
    finally:
        app_.dependency_overrides.pop(dep, None)


def test_publish_unknown_id_is_404_over_the_route(report_db):
    c, app_, dep = _client_as("admin")
    try:
        assert c.post("/api/admin/weekly-reports/no-such-id/publish").status_code == 404
    finally:
        app_.dependency_overrides.pop(dep, None)


def test_drafts_never_appear_on_public_routes(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    draft = wr.create_draft_for_week(week_start)
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    listed = client.get("/api/public/weekly-reports").json()
    assert listed == []
    assert client.get(f"/api/public/weekly-reports/{draft['id']}").status_code == 404


def test_published_reports_appear_on_public_routes(report_db):
    week_start = date(2026, 9, 21)
    _seed_week(report_db, week_start)
    draft = wr.create_draft_for_week(week_start)
    wr.publish(draft["id"], "alice")
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    listed = client.get("/api/public/weekly-reports").json()
    assert len(listed) == 1 and listed[0]["id"] == draft["id"] and "body" not in listed[0]
    detail = client.get(f"/api/public/weekly-reports/{draft['id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["status"] == "published" and body["body"]["week_start"] == week_start.isoformat()


def test_public_detail_404_for_an_unknown_id(report_db):
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    assert client.get("/api/public/weekly-reports/no-such-id").status_code == 404
