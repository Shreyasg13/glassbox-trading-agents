"""Tests for the gate-health aggregates and admin route (S3 T14).

Pure aggregation tests on rows shaped like production's `verification_results`, plus request tests on a temp database.
"""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import create_engine

from app import db, migrate
from app.migrated_tables import claims_table, verification_results_table
from app.verification import health

D1, D2, D3 = date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 19)


def row(run_id, check_type, status, claim_id=None, reason=None):
    default_reason = {"pass": "value matches source", "warn": "data is stale (age 3 day(s) > window 1 day(s))",
                      "fail": "snapshot not found"}[status]
    return {"run_id": run_id, "claim_id": claim_id, "check_type": check_type, "status": status,
            "reason": reason or default_reason}


def claim_rows(run_id, cid, checks):
    """All results for one claim: checks is [(check_type, status, reason_or_None), ...]."""
    return [row(run_id, ct, st, cid, rs) for ct, st, rs in checks]


PASS_SNAPSHOT = [("traceability", "pass", None), ("point_in_time", "pass", "snapshot fetched before or at run time"),
                 ("staleness", "pass", "data is fresh (age 0 day(s) <= window 1 day(s))")]


def mixed_rows():
    """Two days of runs, plus an Ask and a challenger run that must never count."""
    r = []
    # D2 AAPL: 3 claims fully verified, 1 claim with ONE failing check among passes, 1 warn-only claim (still verified).
    for i in range(3):
        r += claim_rows("2026-09-18:AAPL", f"aapl-{i}", PASS_SNAPSHOT)
    r += claim_rows("2026-09-18:AAPL", "aapl-bad", [("traceability", "pass", None),
                                                    ("point_in_time", "fail", "snapshot fetched after run time"),
                                                    ("staleness", "pass", None)])
    r += claim_rows("2026-09-18:AAPL", "aapl-stale", [("traceability", "pass", None), ("point_in_time", "pass", None),
                                                      ("staleness", "warn", None)])
    r.append(row("2026-09-18:AAPL", "narrative", "pass", None, "narrative validates"))
    # D2 MSFT: 2 claims, both verified; narrative only warns (no narrative) -> run ok.
    for i in range(2):
        r += claim_rows("2026-09-18:MSFT", f"msft-{i}", PASS_SNAPSHOT)
    r.append(row("2026-09-18:MSFT", "narrative", "warn", None, "no narrative (skipped or not generated)"))
    # D1 NVDA: 1 of 2 claims verified; the other's snapshot is missing.
    r += claim_rows("2026-09-17:NVDA", "nvda-0", PASS_SNAPSHOT)
    r += claim_rows("2026-09-17:NVDA", "nvda-1", [("traceability", "fail", "snapshot not found")])
    r.append(row("2026-09-17:NVDA", "narrative", "fail", None, "narrative validation failed after retry"))
    # Never counted: an Ask run and a challenger run, full of failures.
    r += claim_rows("ask:2026-09-18:AAPL:abc", "ask-0", [("traceability", "fail", None)])
    r += claim_rows("chal:gpt:2026-09-18:AAPL", "chal-0", [("traceability", "fail", None)])
    return r


# ---- daily pass rate --------------------------------------------------------------


def test_daily_pass_rate_with_mixed_runs():
    days = health.daily_pass_rate(mixed_rows(), D1, D3)
    assert [d["date"] for d in days] == ["2026-09-17", "2026-09-18"]
    d1, d2 = days
    assert d1 == {"date": "2026-09-17", "claims_checked": 2, "claims_verified": 1, "runs_checked": 1, "runs_ok": 0,
                  "pass_rate": 0.5}
    # AAPL: 5 claims, 4 verified (one failing check disqualifies, a staleness warn does not); MSFT: 2/2.
    assert d2["claims_checked"] == 7
    assert d2["claims_verified"] == 6
    assert d2["pass_rate"] == pytest.approx(6 / 7)
    # AAPL has a failed check -> not ok; MSFT only warns -> ok.
    assert (d2["runs_checked"], d2["runs_ok"]) == (2, 1)


def test_warn_only_claim_counts_as_verified_but_other_warns_do_not():
    rows = claim_rows("2026-09-18:AAPL", "c1", [("traceability", "pass", None), ("staleness", "warn", None)])
    [d] = health.daily_pass_rate(rows, D2, D2)
    assert (d["claims_checked"], d["claims_verified"], d["runs_ok"]) == (1, 1, 1)
    # A non-staleness warn is not a pass (gate.summarize's rule), so this claim is not verified.
    rows = claim_rows("2026-09-18:AAPL", "c1", [("traceability", "pass", None), ("point_in_time", "warn", None)])
    [d] = health.daily_pass_rate(rows, D2, D2)
    assert (d["claims_checked"], d["claims_verified"]) == (1, 0)


def test_pass_rate_agrees_with_gate_summarize_per_run():
    from app.verification import gate
    rows = [r for r in mixed_rows() if r["run_id"] == "2026-09-18:AAPL"]
    s = gate.summarize([gate.Result(r["check_type"], r["status"], r["claim_id"], None, None, r["reason"]) for r in rows])
    [d] = health.daily_pass_rate(rows, D2, D2)
    assert (d["claims_verified"], d["claims_checked"]) == (s["verified_claims"], s["total_claims"]) == (4, 5)


def test_run_with_only_a_narrative_result_has_no_pass_rate():
    rows = [row("2026-09-18:AAPL", "narrative", "warn", None, "no narrative row found")]
    [d] = health.daily_pass_rate(rows, D2, D2)
    assert d["claims_checked"] == 0 and d["pass_rate"] is None and d["runs_checked"] == 1


# ---- date range and exclusions ----------------------------------------------------


def test_date_range_edges_are_inclusive():
    rows = mixed_rows()
    assert [d["date"] for d in health.daily_pass_rate(rows, D1, D1)] == ["2026-09-17"]
    assert [d["date"] for d in health.daily_pass_rate(rows, D2, D2)] == ["2026-09-18"]
    assert [d["date"] for d in health.daily_pass_rate(rows, D1, D2)] == ["2026-09-17", "2026-09-18"]
    assert health.daily_pass_rate(rows, D3, D3) == []
    assert health.daily_pass_rate(rows, date(2026, 9, 1), date(2026, 9, 16)) == []


def test_ask_and_challenger_runs_are_excluded():
    assert health.run_date("ask:2026-09-18:AAPL:abc") is None
    assert health.run_date("chal:gpt:2026-09-18:AAPL") is None
    assert health.run_date("not-a-run") is None
    assert health.run_date("2026-09-18:AAPL") == D2
    only_excluded = [r for r in mixed_rows() if r["run_id"].startswith(("ask:", "chal:"))]
    assert only_excluded  # the fixture really has them
    out = health.aggregate(only_excluded, {"ask-0": "revenue", "chal-0": "revenue"}, D1, D3)
    assert out["days"] == [] and out["top_checks"] == [] and out["top_metrics"] == []


def test_empty_range():
    out = health.aggregate([], {}, D1, D3)
    assert out == {"from": "2026-09-17", "to": "2026-09-19", "days": [],
                   "totals": {"claims_checked": 0, "claims_verified": 0, "pass_rate": None, "runs_checked": 0, "runs_ok": 0},
                   "top_checks": [], "top_metrics": []}


# ---- top failing checks -----------------------------------------------------------


def test_top_failing_checks_counts_reasons_and_excludes_warns():
    top = health.top_failing_checks(mixed_rows(), D1, D3)
    # traceability 1 (ask/chal excluded), point_in_time 1, narrative 1: tie -> alphabetical. Staleness only warned.
    assert top == [
        {"check_type": "narrative", "failures": 1, "top_reason": "narrative validation failed after retry"},
        {"check_type": "point_in_time", "failures": 1, "top_reason": "snapshot fetched after run time"},
        {"check_type": "traceability", "failures": 1, "top_reason": "snapshot not found"},
    ]


def test_top_failing_checks_ordering_ties_and_limit_of_five():
    rows = []
    counts = {"traceability": 6, "price": 4, "risk": 4, "point_in_time": 3, "narrative": 2, "staleness_x": 1}
    for ct, n in counts.items():
        for i in range(n):
            rows.append(row(f"2026-09-18:T{i}", ct, "fail", f"{ct}-{i}"))
    # traceability's most common reason: 4x "snapshot not found" beats 2x "value does not match source".
    for i in range(2):
        rows[i]["reason"] = "value does not match source"
    top = health.top_failing_checks(rows, D2, D2)
    assert [(t["check_type"], t["failures"]) for t in top] == [
        ("traceability", 6), ("price", 4), ("risk", 4), ("point_in_time", 3), ("narrative", 2)]
    assert top[0]["top_reason"] == "snapshot not found"


def test_most_common_reason_tie_is_stable():
    rows = [row("2026-09-18:A", "price", "fail", "p1", "price for date 2026-09-18 not in price book"),
            row("2026-09-18:A", "price", "fail", "p2", "price does not match price book")]
    [t] = health.top_failing_checks(rows, D2, D2)
    assert t["top_reason"] == "price does not match price book"


# ---- top failing metrics ----------------------------------------------------------


def test_top_failing_metrics_joins_claims_on_claim_id():
    rows = [
        row("2026-09-18:AAPL", "traceability", "fail", "a-rev", "snapshot not found"),
        row("2026-09-18:AAPL", "point_in_time", "fail", "a-rev", "snapshot fetched after run time"),
        row("2026-09-18:MSFT", "traceability", "fail", "m-rev", "snapshot not found"),
        row("2026-09-18:AAPL", "price", "fail", "a-close", "price does not match price book"),
        row("2026-09-18:AAPL", "risk", "fail", "a-vol", "risk value differs from recomputed"),
        row("2026-09-18:AAPL", "traceability", "pass", "a-eps"),
        row("2026-09-18:AAPL", "staleness", "warn", "a-eps"),
        row("2026-09-18:AAPL", "narrative", "fail", None, "narrative validation failed after retry"),  # no metric
        row("2026-09-18:AAPL", "traceability", "fail", "missing-claim"),  # not in claims -> left out
        row("ask:2026-09-18:AAPL:x", "traceability", "fail", "ask-rev"),  # excluded run
    ]
    metrics = {"a-rev": "revenue", "m-rev": "revenue", "a-close": "close", "a-vol": "risk_vol_pct", "a-eps": "eps",
               "ask-rev": "revenue"}
    top = health.top_failing_metrics(rows, metrics, D2, D2)
    assert top == [
        {"metric": "revenue", "failures": 3, "claims": 2},
        {"metric": "close", "failures": 1, "claims": 1},
        {"metric": "risk_vol_pct", "failures": 1, "claims": 1},
    ]


def test_top_failing_metrics_limit_of_five():
    rows, metrics = [], {}
    for k, name in enumerate(["a", "b", "c", "d", "e", "f", "g"]):
        for i in range(k + 1):
            cid = f"{name}-{i}"
            metrics[cid] = name
            rows.append(row("2026-09-18:AAPL", "traceability", "fail", cid))
    top = health.top_failing_metrics(rows, metrics, D2, D2)
    assert [t["metric"] for t in top] == ["g", "f", "e", "d", "c"]


# ---- loader + admin route on a temp database --------------------------------------


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'gate_health.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(engine)
    with engine.begin() as conn:
        migrate.upgrade(conn)
    monkeypatch.setattr(db, "engine", engine)
    yield engine
    engine.dispose()


def _store(engine, rows, claims):
    ts = "2026-09-18T21:00:00.000000+00:00"
    with engine.begin() as conn:
        conn.execute(verification_results_table.insert(), [
            {"id": f"vr-{i}", "expected": None, "observed": None, "created_at": ts, **r} for i, r in enumerate(rows)])
        conn.execute(claims_table.insert(), [
            {"id": cid, "run_id": run_id, "ticker": run_id.rsplit(":", 1)[-1], "metric": metric, "value": 1.0,
             "unit": "USD", "period": "2026-06-30", "source": "sec_xbrl", "source_snapshot_id": None, "source_path": None,
             "text_span": None, "created_at": ts}
            for cid, (run_id, metric) in claims.items()])


def _seed(engine):
    rows = mixed_rows()
    claims = {r["claim_id"]: (r["run_id"], "close") for r in rows if r["claim_id"]}
    claims["aapl-bad"] = ("2026-09-18:AAPL", "net_income")
    for cid in ("nvda-1", "ask-0", "chal-0"):
        claims[cid] = (claims[cid][0], "revenue")
    _store(engine, rows, claims)


def test_load_reads_the_database(temp_db):
    _seed(temp_db)
    out = health.load(D1, D2)
    assert [d["date"] for d in out["days"]] == ["2026-09-17", "2026-09-18"]
    assert out["totals"] == {"claims_checked": 9, "claims_verified": 7, "pass_rate": pytest.approx(7 / 9),
                             "runs_checked": 3, "runs_ok": 1}
    assert [t["check_type"] for t in out["top_checks"]] == ["narrative", "point_in_time", "traceability"]
    assert out["top_metrics"] == [{"metric": "net_income", "failures": 1, "claims": 1},
                                  {"metric": "revenue", "failures": 1, "claims": 1}]
    # Edge day only.
    assert [d["date"] for d in health.load(D2, D2)["days"]] == ["2026-09-18"]
    assert health.load(D3, D3)["days"] == []


def _client_as(role):
    from fastapi.testclient import TestClient
    from app.auth import TokenPayload, get_current_user
    from app.main import app
    app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub=role, role=role)
    return TestClient(app), app, get_current_user


def test_gate_health_route_viewer_403(temp_db):
    c, app, dep = _client_as("viewer")
    try:
        assert c.get("/api/admin/gate-health").status_code == 403
        assert c.get("/api/admin/gate-health", params={"from": "2026-09-17", "to": "2026-09-18"}).status_code == 403
    finally:
        app.dependency_overrides.pop(dep, None)


def test_gate_health_route_admin_200(temp_db):
    _seed(temp_db)
    c, app, dep = _client_as("admin")
    try:
        r = c.get("/api/admin/gate-health", params={"from": "2026-09-17", "to": "2026-09-18"})
        assert r.status_code == 200
        body = r.json()
        assert body["from"] == "2026-09-17" and body["to"] == "2026-09-18"
        assert [d["date"] for d in body["days"]] == ["2026-09-17", "2026-09-18"]
        assert body["days"][1]["claims_verified"] == 6 and body["days"][1]["claims_checked"] == 7
        assert body["top_metrics"][0]["metric"] == "net_income"
        # Defaults: the last 30 days ending today (inclusive).
        r = c.get("/api/admin/gate-health")
        assert r.status_code == 200
        body = r.json()
        assert (date.fromisoformat(body["to"]) - date.fromisoformat(body["from"])).days == 29
    finally:
        app.dependency_overrides.pop(dep, None)


def test_gate_health_route_rejects_bad_ranges(temp_db):
    c, app, dep = _client_as("admin")
    try:
        # 366 days inclusive is allowed; 367 is not.
        assert c.get("/api/admin/gate-health", params={"from": "2025-09-19", "to": "2026-09-19"}).status_code == 200
        assert c.get("/api/admin/gate-health", params={"from": "2025-09-18", "to": "2026-09-19"}).status_code == 400
        assert c.get("/api/admin/gate-health", params={"from": "2020-01-01", "to": "2026-09-19"}).status_code == 400
        assert c.get("/api/admin/gate-health", params={"from": "2026-09-19", "to": "2026-09-18"}).status_code == 400
        assert c.get("/api/admin/gate-health", params={"from": "not-a-date"}).status_code == 422
    finally:
        app.dependency_overrides.pop(dep, None)
