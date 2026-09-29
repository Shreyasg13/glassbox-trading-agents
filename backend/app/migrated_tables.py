"""Tables created and changed ONLY by Alembic migrations (app/migrations), starting with the S3 work.

Why a separate MetaData: the older tables in app/db.py are created by `metadata.create_all` at import time, which can only
ADD tables. Everything from S3 onward needs real, reversible migrations (renames, new columns, rollbacks). Keeping these tables
out of db.metadata means `create_all` never creates them behind Alembic's back, so the two mechanisms cannot fight.

Rules for adding a table here:
  1. define it below, 2. write a revision under app/migrations/versions with BOTH upgrade() and downgrade(),
  3. never hand-edit a deployed schema. Autogenerate is fenced by `include_object` in migrations/env.py so it can only ever
     see these tables and can never propose dropping one of the older ones.
"""
from __future__ import annotations

from sqlalchemy import Boolean, Column, Float, Index, Integer, MetaData, String, Table, Text, UniqueConstraint

migrated_metadata = MetaData()

feature_flags_table = Table(
    "feature_flags",
    migrated_metadata,
    Column("key", String, primary_key=True),
    Column("enabled", Boolean, nullable=False),
    Column("updated_by", String, nullable=False, default=""),
    Column("updated_at", String, nullable=False, default=""),
)

source_snapshots_table = Table(
    "source_snapshots",
    migrated_metadata,
    Column("id", String, primary_key=True),
    Column("source", String, nullable=False),
    Column("ticker", String, nullable=False, default=""),
    Column("as_of", String, nullable=False),
    Column("fetched_at", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("payload_hash", String(64), nullable=False),
    Index("ix_source_snapshots_source_ticker_fetched", "source", "ticker", "fetched_at"),
    UniqueConstraint("source", "ticker", "payload_hash", name="uq_source_snapshots_source_ticker_hash"),
)

ledger_calls_table = Table(
    "ledger_calls",
    migrated_metadata,
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("call_id", String, nullable=False, unique=True),
    Column("ticker", String, nullable=False),
    Column("call_type", String, nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("input_snapshot_ids", Text, nullable=False, default="[]"),
    Column("committee_config_id", String, nullable=True),
    Column("recorded_at", String, nullable=False),
    Column("prev_hash", String(64), nullable=False),
    Column("hash", String(64), nullable=False),
)

claims_table = Table(
    "claims",
    migrated_metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("ticker", String, nullable=False),
    Column("metric", String, nullable=False),
    Column("value", Float, nullable=False),
    Column("unit", String, nullable=False),
    Column("period", String, nullable=False),
    Column("source", String, nullable=False),
    Column("source_snapshot_id", String, nullable=True),
    Column("source_path", String, nullable=True),
    Column("text_span", String, nullable=True),
    Column("created_at", String, nullable=False),
    Index("ix_claims_run_id", "run_id"),
)

committee_narratives_table = Table(
    "committee_narratives",
    migrated_metadata,
    Column("run_id", String, primary_key=True),
    Column("narrative", Text, nullable=True),
    Column("status", String, nullable=False),  # ok | pending_review | skipped
    Column("attempts", Integer, nullable=False, default=0),
    Column("provider_requested", String, nullable=True),
    Column("model_requested", String, nullable=True),
    Column("provider_answered", String, nullable=True),
    Column("model_answered", String, nullable=True),
    Column("error", Text, nullable=True),
    Column("created_at", String, nullable=False),
)

verification_results_table = Table(
    "verification_results",
    migrated_metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("claim_id", String, nullable=True),
    Column("check_type", String, nullable=False),
    Column("status", String, nullable=False),  # pass | fail | warn
    Column("expected", String, nullable=True),
    Column("observed", String, nullable=True),
    Column("reason", String, nullable=False),
    Column("created_at", String, nullable=False),
    Index("ix_verification_results_run_id", "run_id"),
)

compliance_events_table = Table(
    "compliance_events",
    migrated_metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=True),  # null for outputs that are not committee runs (digests, assistant answers)
    Column("channel", String, nullable=False),  # committee_report | user_digest | assistant | ...
    Column("rule_id", String, nullable=False),
    Column("matched_text", String(300), nullable=False, default=""),
    Column("action", String, nullable=False),  # blocked | rewritten | flagged
    Column("created_at", String, nullable=False),
    Index("ix_compliance_events_created_at", "created_at"),
    Index("ix_compliance_events_run_id", "run_id"),
)

quarantine_items_table = Table(
    "quarantine_items",
    migrated_metadata,
    Column("id", String, primary_key=True),
    Column("channel", String, nullable=False),
    Column("run_id", String, nullable=True),
    Column("content_ref", String, nullable=False),
    Column("stage", String, nullable=False),  # A6 | A7
    Column("status", String, nullable=False),  # pending | approved | rejected | shadow
    Column("reasons_json", Text, nullable=False, default="[]"),
    Column("created_at", String, nullable=False),
    Column("reviewer_id", String, nullable=True),
    Column("review_note", String, nullable=True),
    Column("reviewed_at", String, nullable=True),
    Index("ix_quarantine_items_status", "status"),
    Index("ix_quarantine_items_created_at", "created_at"),
)

call_outcomes_table = Table(
    "call_outcomes",
    migrated_metadata,
    Column("call_id", String, primary_key=True),
    Column("horizon", Integer, primary_key=True),
    Column("evaluated_at", String, nullable=False),
    Column("outcome_json", Text, nullable=False),
    Column("score", Float, nullable=False),
)


def include_object(obj, name, type_, reflected, compare_to):
    """Autogenerate fence (used by migrations/env.py): it may only see the migration-managed tables above, so it can never
    propose dropping or altering an older table that lives in app/db.py."""
    if type_ == "table":
        return name in migrated_metadata.tables
    return True
