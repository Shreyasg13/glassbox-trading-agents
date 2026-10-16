"""Read-only R2 exports from the backend's temporary database fixture."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.r2 import dataset, export_runs, inject  # noqa: E402
from app import paper, snapshot_store  # noqa: E402
from app.migrated_tables import claims_table, committee_narratives_table, migrated_metadata, source_snapshots_table  # noqa: E402
from app.verification import gate, runner  # noqa: E402


@pytest.fixture
def archive(real_db):
    migrated_metadata.create_all(real_db.engine)
    run_id = "2026-01-02:TEST"
    run_time = "2026-01-02T20:00:00+00:00"
    payload = {"revenue": 100.0}
    with real_db.engine.begin() as conn:
        conn.execute(real_db.committee_runs_table.insert(), [
            {"id": run_id, "config": json.dumps({"id": run_id, "run_time": run_time})},
            {"id": "2026-01-03:TEST", "config": "{}"},
            {"id": "2026-01-04:TEST", "config": json.dumps({"run_time": run_time})},
            {"id": "2027-01-02:TEST", "config": "{}"},
            {"id": "ask:ignored", "config": "{}"},
        ])
        conn.execute(real_db.price_bars_table.insert(), {"symbol": "TEST", "d": "2026-01-02", "close": 10.0})
        conn.execute(source_snapshots_table.insert(), {
            "id": "snapshot", "source": "sec_facts", "ticker": "TEST",
            "as_of": "2026-01-01", "fetched_at": "2026-01-02T19:00:00+00:00",
            "payload_json": json.dumps(payload), "payload_hash": snapshot_store._payload_hash(payload)})
        conn.execute(claims_table.insert(), {
            "id": "claim", "run_id": run_id, "ticker": "TEST", "metric": "eps",
            "value": 100.0, "unit": "USD", "period": "2026-01-01", "source": "sec_facts",
            "source_snapshot_id": "snapshot", "source_path": "/revenue", "created_at": run_time})
        conn.execute(committee_narratives_table.insert(), {
            "run_id": run_id, "narrative": "EPS was {{claim:claim}}.", "status": "ok", "created_at": run_time})
    return real_db, run_id, run_time


def test_export_loads_and_matches_production_gate_without_db_changes(archive, tmp_path):
    db, run_id, run_time = archive
    book = paper.PriceBook()
    book.close = {"TEST": {"2026-01-02": 10.0}}
    book._sorted_dates = {"TEST": ["2026-01-02"]}
    production = asyncio.run(runner.run_gate(run_id, run_time, book))
    database = Path(db.engine.url.database)
    before = database.read_bytes()
    report = export_runs.export_runs(str(db.engine.url), "2027-01-01", tmp_path / "out")
    assert database.read_bytes() == before
    assert report["exported"] == 1 and report["skipped"] == 2, report
    assert report["reasons"]["2026-01-03:TEST"] == "missing original run_time"
    assert report["reasons"]["2026-01-04:TEST"] == "missing claims"
    loaded = dataset.load_runs(tmp_path / "out")
    assert len(loaded) == 1 and loaded.excluded_run_count == 0
    assert loaded[0]["run_id"] == run_id and loaded[0]["symbol"] == "TEST"
    assert gate.summarize(gate.verify_run(loaded[0])) == production
    assert inject.gate_status(loaded[0]) == "pass"
    assert json.loads((tmp_path / "out" / "export_report.json").read_text()) == report


def test_export_skips_missing_snapshot(archive, tmp_path):
    db, run_id, _ = archive
    with db.engine.begin() as conn:
        conn.execute(source_snapshots_table.delete())
    report = export_runs.export_runs(str(db.engine.url), "2027-01-01", tmp_path / "out")
    assert report["reasons"][run_id] == "missing snapshot"
    assert dataset.load_runs(tmp_path / "out") == []


@pytest.mark.parametrize("failure", ["price", "risk", "gate"])
def test_export_reports_unrebuildable_or_failing_inputs(archive, tmp_path, failure):
    db, run_id, _ = archive
    with db.engine.begin() as conn:
        if failure == "price":
            conn.execute(db.price_bars_table.delete())
        elif failure == "risk":
            conn.execute(claims_table.update().values(source="risk", source_snapshot_id=None))
        else:
            conn.execute(claims_table.update().values(value=200.0))
    report = export_runs.export_runs(str(db.engine.url), "2027-01-01", tmp_path / "out")
    assert report["reasons"][run_id] == {
        "price": "missing price on run date", "risk": "missing risk history",
        "gate": "unmodified gate failed"}[failure]
    assert report["exported"] == 0


def test_export_cli(archive, tmp_path, monkeypatch):
    db, _, _ = archive
    monkeypatch.setattr(sys, "argv", ["export_runs", str(db.engine.url), "2027-01-01", str(tmp_path / "out")])
    export_runs.main()
    assert len(dataset.load_runs(tmp_path / "out")) == 1


def test_readonly_connection_rejects_writes(archive):
    db, _, _ = archive
    engine = export_runs.readonly_engine(str(db.engine.url))
    try:
        with engine.connect() as conn:
            assert conn.execute(select(db.committee_runs_table.c.id)).first()
            with pytest.raises(OperationalError, match="readonly"):
                conn.execute(text("DELETE FROM committee_runs"))
    finally:
        engine.dispose()


def test_missing_database_is_not_created(tmp_path):
    database = tmp_path / "missing.db"
    engine = export_runs.readonly_engine(f"sqlite:///{database}")
    try:
        with pytest.raises(OperationalError):
            with engine.connect():
                pytest.fail("Missing database unexpectedly opened")
    finally:
        engine.dispose()
    assert not database.exists()
