"""Alembic migrations: apply, roll back, never touch the older tables, and autogenerate can't propose dropping them."""
from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from app import db, migrate
from app.migrated_tables import include_object, migrated_metadata


@pytest.fixture
def engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)  # the older tables, exactly as init_schema creates them in production
    return eng


def tables(eng):
    return set(inspect(eng).get_table_names())


def revision(eng):
    with eng.connect() as c:
        return MigrationContext.configure(c).get_current_revision()


def test_upgrade_creates_the_table_and_records_the_revision(engine):
    assert "feature_flags" not in tables(engine)
    assert "source_snapshots" not in tables(engine)
    assert "ledger_calls" not in tables(engine)
    assert "claims" not in tables(engine)
    assert "committee_narratives" not in tables(engine)
    assert "verification_results" not in tables(engine)
    assert "compliance_events" not in tables(engine)
    with engine.begin() as conn:
        migrate.upgrade(conn)
    assert "feature_flags" in tables(engine) and "source_snapshots" in tables(engine) and "ledger_calls" in tables(engine) and "claims" in tables(engine) and "committee_narratives" in tables(engine) and "verification_results" in tables(engine) and "compliance_events" in tables(engine) and revision(engine) == "0006"
    cols = {c["name"] for c in inspect(engine).get_columns("feature_flags")}
    assert cols == {"key", "enabled", "updated_by", "updated_at"}
    snap_cols = {c["name"] for c in inspect(engine).get_columns("source_snapshots")}
    assert snap_cols == {"id", "source", "ticker", "as_of", "fetched_at", "payload_json", "payload_hash"}
    ledger_cols = {c["name"] for c in inspect(engine).get_columns("ledger_calls")}
    assert ledger_cols == {"seq", "call_id", "ticker", "call_type", "payload_json", "input_snapshot_ids", "committee_config_id", "recorded_at", "prev_hash", "hash"}
    claims_cols = {c["name"] for c in inspect(engine).get_columns("claims")}
    expected_claims = {"id", "run_id", "ticker", "metric", "value", "unit", "period", "source", "source_snapshot_id", "source_path", "text_span", "created_at"}
    assert claims_cols == expected_claims
    narr_cols = {c["name"] for c in inspect(engine).get_columns("committee_narratives")}
    expected_narr = {"run_id", "narrative", "status", "attempts", "provider_requested", "model_requested", "provider_answered", "model_answered", "error", "created_at"}
    assert narr_cols == expected_narr
    vr_cols = {c["name"] for c in inspect(engine).get_columns("verification_results")}
    expected_vr = {"id", "run_id", "claim_id", "check_type", "status", "expected", "observed", "reason", "created_at"}
    assert vr_cols == expected_vr
    # Check index
    vr_indexes = {idx["name"] for idx in inspect(engine).get_indexes("verification_results")}
    assert "ix_verification_results_run_id" in vr_indexes
    ce_cols = {c["name"] for c in inspect(engine).get_columns("compliance_events")}
    assert ce_cols == {"id", "run_id", "channel", "rule_id", "matched_text", "action", "created_at"}
    ce_indexes = {idx["name"] for idx in inspect(engine).get_indexes("compliance_events")}
    assert {"ix_compliance_events_created_at", "ix_compliance_events_run_id"} <= ce_indexes


def test_upgrading_twice_is_a_no_op(engine):
    for _ in range(2):
        with engine.begin() as conn:
            migrate.upgrade(conn)
    assert revision(engine) == "0006"


def test_0006_downgrades_to_0005_dropping_only_compliance_events(engine):
    with engine.begin() as conn:
        migrate.upgrade(conn)
    with engine.begin() as conn:
        conn.execute(migrated_metadata.tables["compliance_events"].insert().values(
            id="e1", run_id=None, channel="assistant", rule_id="r", matched_text="", action="flagged", created_at="t"))
    with engine.begin() as conn:
        migrate.downgrade("0005", conn)
    assert revision(engine) == "0005"
    assert "compliance_events" not in tables(engine) and "verification_results" in tables(engine)
    with engine.begin() as conn:
        migrate.upgrade(conn)
    assert revision(engine) == "0006"
    with engine.connect() as c:
        assert c.execute(migrated_metadata.tables["compliance_events"].select()).fetchall() == []


def test_downgrade_removes_only_the_migrated_table_and_leaves_every_older_table_alone(engine):
    before = tables(engine)
    with engine.begin() as conn:
        migrate.upgrade(conn)
    with engine.begin() as conn:
        migrate.downgrade("base", conn)
    after = tables(engine)
    assert "feature_flags" not in after and "source_snapshots" not in after and "ledger_calls" not in after and "claims" not in after and "committee_narratives" not in after and "verification_results" not in after and "compliance_events" not in after and revision(engine) is None
    assert before <= after and {"users", "committee_runs", "paper_accounts"} <= after  # nothing else was dropped


def test_data_survives_a_round_trip_only_until_the_downgrade(engine):
    with engine.begin() as conn:
        migrate.upgrade(conn)
    with engine.begin() as conn:
        conn.execute(migrated_metadata.tables["feature_flags"].insert().values(key="output.email", enabled=False, updated_by="a", updated_at="t"))
        conn.execute(migrated_metadata.tables["source_snapshots"].insert().values(id="s1", source="test", ticker="", as_of="2026-01-01", fetched_at="2026-01-01T00:00:00", payload_json="{}", payload_hash="hash"))
    with engine.begin() as conn:
        migrate.downgrade("base", conn)
        migrate.upgrade(conn)
    with engine.connect() as c:
        assert c.execute(migrated_metadata.tables["feature_flags"].select()).fetchall() == []
        assert c.execute(migrated_metadata.tables["source_snapshots"].select()).fetchall() == []  # a rollback discards the snapshot rows, by design


def test_autogenerate_never_proposes_dropping_an_older_table(engine):
    """The dangerous failure mode: autogenerate sees `users` etc. in the database but not in the migrated metadata and drops them."""
    with engine.begin() as conn:
        migrate.upgrade(conn)
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"include_object": include_object, "compare_type": True})
        assert compare_metadata(ctx, migrated_metadata) == []
        unfenced = MigrationContext.configure(conn, opts={"compare_type": True})
        assert any(op[0] == "remove_table" for op in compare_metadata(unfenced, migrated_metadata))  # proves the fence is what protects us
    # Also verify all migrated tables are in the metadata
    assert "feature_flags" in migrated_metadata.tables
    assert "source_snapshots" in migrated_metadata.tables
    assert "ledger_calls" in migrated_metadata.tables
    assert "claims" in migrated_metadata.tables
    assert "committee_narratives" in migrated_metadata.tables
    assert "verification_results" in migrated_metadata.tables
    assert "compliance_events" in migrated_metadata.tables


def test_the_migrated_tables_are_not_created_by_create_all():
    assert "feature_flags" not in db.metadata.tables  # otherwise create_all and Alembic would fight over it


def test_a_failing_migration_reports_failure_without_raising(monkeypatch, capsys):
    def boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(migrate, "upgrade", boom)
    assert migrate.main([]) == 1  # loud (non-zero, logged) but the container still starts: see the Dockerfile
