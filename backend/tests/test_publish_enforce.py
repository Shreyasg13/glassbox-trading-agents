"""Enforce-mode tests for every publish channel (S3 T5b).

One test per channel, each with enforce ON (blocked, pending quarantine item) and
OFF (delivered, shadow item), driven through the channel's real entry point.
Plus the held-narrative 404 (non-admin) / 200 (admin) test.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pytest
from sqlalchemy import select

from app import db, flags, migrate, publish
from app.migrated_tables import quarantine_items_table, verification_results_table, feature_flags_table

log = logging.getLogger(__name__)


@pytest.fixture
def migrated_db_for_enforce_tests(real_db):
    """A database with migrations applied."""
    with real_db.engine.begin() as conn:
        migrate.upgrade(conn)
    return real_db


def _iso_now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _create_run_doc(
    run_id: str,
    decision: str = "BUY",
    action: str = "BUY",
    engine_signal: str = "BUY",
    votes: Optional[Dict[str, float]] = None,
    ceo_consensus: Optional[float] = 0.6,
    symbol: str = "AAPL",
    date: str = "2026-09-21",
) -> Dict[str, Any]:
    """Create a committee run document matching _run_doc structure."""
    if votes is None:
        votes = {"BUY": 0.6, "HOLD": 0.3, "SELL": 0.1}
    ceo = {"consensus": ceo_consensus} if ceo_consensus is not None else {}
    return {
        "id": run_id,
        "date": date,
        "symbol": symbol,
        "why": f"engine signal {engine_signal} (80% confidence)",
        "engine_signal": engine_signal,
        "engine_confidence": 80,
        "price": 150.0,
        "decision": decision,
        "action": action,
        "gate": "pass",
        "risk": None,
        "analyst_risk": None,
        "votes": votes,
        "ceo": ceo,
        "context": "test context",
        "debate": None,
        "engine": "legacy",
        "agrees_with_engine": decision == engine_signal,
        "agents": [],
        "answered": 10,
        "total": 10,
        "quorum_ok": True,
        "providers": {},
        "error": None,
        "seconds": 1.0,
        "created_at": _iso_now(),
    }


def _save_run_doc(doc: Dict[str, Any], conn) -> None:
    """Save a committee run document directly to the database."""
    conn.execute(
        db.committee_runs_table.insert().values(id=doc["id"], config=json.dumps(doc))
    )


# Channels to test
CHANNELS = [
    "committee_report",
    "paper_report",
    "research_digest",
    "orchestration_report",
    "user_report",
    "admin_digest",
    "user_digest",
    "notification",  # inbox/notification
    "assistant",
    "tts",
]

# Channels that receive the holding text in enforce mode when held
HOLDING_TEXT_CHANNELS = {
    "committee_report",
    "paper_report",
    "research_digest",
    "orchestration_report",
    "admin_digest",
    "user_digest",
    "inbox",
    "user_report",
    "assistant",
}


def _publish_helper(
    channel: str,
    text: str,
    *,
    run_ids: Tuple[str, ...] = (),
    content_ref: Optional[str] = None,
    is_html: bool = False,
    committee_output: bool = True,
    writer_payload: Optional[Dict[str, Any]] = None,
    enforce: bool = False,
) -> Tuple[Any, Any]:
    """Helper to set enforce flag and call publish, returning (result, connection)."""
    flags.set_flag("publish.enforce", enforce, "user")
    current = flags.flag("publish.enforce")
    # Read from database directly
    with db.engine.connect() as conn:
        row = conn.execute(
            db.select(feature_flags_table).where(feature_flags_table.c.key == "publish.enforce")
        ).fetchone()

    # Right before calling publish
    enforce_right_before = flags.flag("publish.enforce")

    result = publish.publish(
        channel=channel,
        text=text,
        run_ids=run_ids,
        content_ref=content_ref,
        is_html=is_html,
        committee_output=committee_output,
        writer_payload=writer_payload,
    )

    # Return a connection for quarantine checks
    conn = db.engine.connect()
    return result, conn


def _check_quarantine(conn, channel: str, content_ref: str, enforce: bool) -> None:
    """Check that a quarantine item exists with correct status."""
    stmt = select(quarantine_items_table).where(
        quarantine_items_table.c.channel == channel,
        quarantine_items_table.c.content_ref == content_ref,
    )
    q_item = conn.execute(stmt).first()
    assert q_item is not None, f"Quarantine item missing for {channel}"

    expected_status = "pending" if enforce else "shadow"
    assert q_item.status == expected_status, (
        f"Expected quarantine status {expected_status} for {channel} in "
        f"{'enforce' if enforce else 'shadow'} mode, got {q_item.status}"
    )
    # Stage should be A7 because we trigger via banned phrase
    assert q_item.stage == "A7", f"Expected stage A7, got {q_item.stage}"
    conn.close()


def _seed_verification_results(conn, run_id: str) -> None:
    """Seed a passing verification result for the given run_id."""
    verification_id = str(uuid.uuid4())
    conn.execute(
        verification_results_table.insert().values(
            id=verification_id,
            run_id=run_id,
            claim_id=None,
            check_type="narrative",
            status="pass",
            expected=None,
            observed=None,
            reason="test",
            created_at=_iso_now(),
        )
    )


# Test each channel
def test_committee_report_enforce(migrated_db_for_enforce_tests):
    """committee_report: enforce ON blocks, creates pending item; OFF allows, shadow."""
    run_id = "2026-09-21:AAPL"
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)
        _seed_verification_results(conn, run_id)  # Ensure A6 passes

    text = "This content has a guaranteed return."  # triggers A7 block
    content_ref_on = f"committee:{run_id}"
    content_ref_off = f"committee:{run_id}-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="committee_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "committee-vote",
            "title": "Investment Committee · daily review",
            "profile": content_ref_on,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."  # in holding text list
    _check_quarantine(conn_on, "committee_report", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="committee_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "committee-vote",
            "title": "Investment Committee · daily review",
            "profile": content_ref_off,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True  # held=True means "would be held"
    assert result_off.text != "This content is being reviewed."  # should have disclaimer
    _check_quarantine(conn_off, "committee_report", content_ref_off, enforce=False)
    conn_off.close()


def test_paper_report_enforce(migrated_db_for_enforce_tests):
    """paper_report: enforce ON blocks, creates pending item; OFF allows, shadow."""
    run_id = "2026-09-21:MSFT"
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        doc = _create_run_doc(run_id, symbol="MSFT")
        _save_run_doc(doc, conn)
        _seed_verification_results(conn, run_id)

    text = "This content has a guaranteed return."
    content_ref_on = f"paper:{run_id}"
    content_ref_off = f"paper:{run_id}-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="paper_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "committee-vote",
            "title": "Paper Trading · daily review",
            "profile": content_ref_on,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "paper_report", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="paper_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "committee-vote",
            "title": "Paper Trading · daily review",
            "profile": content_ref_off,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."
    _check_quarantine(conn_off, "paper_report", content_ref_off, enforce=False)
    conn_off.close()


def test_research_digest_enforce(migrated_db_for_enforce_tests):
    """research_digest: enforce ON blocks, creates pending item; OFF allows, shadow."""
    run_id = "2026-09-21:RESEARCH"
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        doc = _create_run_doc(run_id, symbol="GOOGL")
        _save_run_doc(doc, conn)
        _seed_verification_results(conn, run_id)

    text = "This content has a guaranteed return."
    content_ref_on = f"research:{run_id}"
    content_ref_off = f"research:{run_id}-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="research_digest",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "research-vote",
            "title": "Research Digest",
            "profile": content_ref_on,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "research_digest", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="research_digest",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "research-vote",
            "title": "Research Digest",
            "profile": content_ref_off,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."
    _check_quarantine(conn_off, "research_digest", content_ref_off, enforce=False)
    conn_off.close()


def test_orchestration_report_enforce(migrated_db_for_enforce_tests):
    """orchestration_report: enforce ON blocks, creates pending item; OFF allows, shadow."""
    # orchestration_report has no run_ids (admin report)
    text = "This content has a guaranteed return."
    content_ref_on = f"orchestration_report:20260921:test"
    content_ref_off = f"orchestration_report:20260921:test-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="orchestration_report",
        text=text,
        run_ids=(),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=False,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "orchestration-vote",
            "title": "Orchestration Report",
            "profile": content_ref_on,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "orchestration_report", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="orchestration_report",
        text=text,
        run_ids=(),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=False,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "orchestration-vote",
            "title": "Orchestration Report",
            "profile": content_ref_off,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."
    _check_quarantine(conn_off, "orchestration_report", content_ref_off, enforce=False)
    conn_off.close()


def test_user_report_enforce(migrated_db_for_enforce_tests):
    """user_report: enforce ON blocks, creates pending item; OFF allows, shadow."""
    run_id = "2026-09-21:USER"
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        doc = _create_run_doc(run_id, symbol="TSLA")
        _save_run_doc(doc, conn)
        _seed_verification_results(conn, run_id)

    text = "This content has a guaranteed return."
    content_ref_on = f"user_report:{run_id}"
    content_ref_off = f"user_report:{run_id}-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="user_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "user-vote",
            "title": "User Report",
            "profile": content_ref_on,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "user_report", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="user_report",
        text=text,
        run_ids=(run_id,),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "user-vote",
            "title": "User Report",
            "profile": content_ref_off,
            "narrative": text,
            "created_at": _iso_now(),
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."
    _check_quarantine(conn_off, "user_report", content_ref_off, enforce=False)
    conn_off.close()


def test_admin_digest_enforce(migrated_db_for_enforce_tests):
    """admin_digest: enforce ON blocks, creates pending item; OFF allows, shadow."""
    # admin_digest is an email, no run_ids
    text = "This content has a guaranteed return."
    content_ref_on = f"admin_digest:20260921:test"
    content_ref_off = f"admin_digest:20260921:test-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="admin_digest",
        text=text,
        run_ids=(),
        content_ref=content_ref_on,
        is_html=True,  # admin_digest is HTML
        committee_output=False,
        writer_payload={
            "subject": "Admin Digest",
            "html": text,
            "cfg": {"user": "test@example.com", "to": "test@example.com", "host": "smtp.example.com", "port": 587, "password": "secret"},
            "headers": {},
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "admin_digest", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="admin_digest",
        text=text,
        run_ids=(),
        content_ref=content_ref_off,
        is_html=True,
        committee_output=False,
        writer_payload={
            "subject": "Admin Digest",
            "html": text,
            "cfg": {"user": "test@example.com", "to": "test@example.com", "host": "smtp.example.com", "port": 587, "password": "secret"},
            "headers": {},
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."  # HTML may have disclaimer added
    _check_quarantine(conn_off, "admin_digest", content_ref_off, enforce=False)
    conn_off.close()


def test_user_digest_enforce(migrated_db_for_enforce_tests):
    """user_digest: enforce ON blocks, creates pending item; OFF allows, shadow."""
    text = "This content has a guaranteed return."
    content_ref_on = f"user_digest:20260921:test"
    content_ref_off = f"user_digest:20260921:test-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="user_digest",
        text=text,
        run_ids=(),
        content_ref=content_ref_on,
        is_html=True,
        committee_output=False,
        writer_payload={
            "subject": "User Digest",
            "html": text,
            "cfg": {"user": "test@example.com", "to": "test@example.com", "host": "smtp.example.com", "port": 587, "password": "secret"},
            "headers": {},
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "user_digest", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="user_digest",
        text=text,
        run_ids=(),
        content_ref=content_ref_off,
        is_html=True,
        committee_output=False,
        writer_payload={
            "subject": "User Digest",
            "html": text,
            "cfg": {"user": "test@example.com", "to": "test@example.com", "host": "smtp.example.com", "port": 587, "password": "secret"},
            "headers": {},
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."
    _check_quarantine(conn_off, "user_digest", content_ref_off, enforce=False)
    conn_off.close()


def test_notification_enforce(migrated_db_for_enforce_tests):
    """notification: enforce ON blocks, creates pending item; OFF allows, shadow."""
    text = "This content has a guaranteed return."
    content_ref_on = f"notification:testuser:alert:AAPL:2026-09-21"
    content_ref_off = f"notification:testuser:alert:AAPL:2026-09-21-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="notification",
        text=text,
        run_ids=(),
        content_ref=content_ref_on,
        is_html=False,
        committee_output=False,
        writer_payload={
            "book": None,
            "users": [],
            "narratives": [],
            "stance_fn": None,
        },
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    # notification is not in HOLDING_TEXT_CHANNELS, so should not get holding text
    assert result_on.text != "This content is being reviewed."
    _check_quarantine(conn_on, "notification", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="notification",
        text=text,
        run_ids=(),
        content_ref=content_ref_off,
        is_html=False,
        committee_output=False,
        writer_payload={
            "book": None,
            "users": [],
            "narratives": [],
            "stance_fn": None,
        },
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != "This content is being reviewed."  # not holding text
    assert result_off.text != text  # A7 blocked and disclaimer added in shadow mode
    _check_quarantine(conn_off, "notification", content_ref_off, enforce=False)
    conn_off.close()


def test_assistant_enforce(migrated_db_for_enforce_tests):
    """assistant: enforce ON blocks, creates pending item; OFF allows, shadow."""
    text = "This content has a guaranteed return."
    content_ref_on = f"assistant:user1:2026-09-21"
    content_ref_off = f"assistant:user1:2026-09-21-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="assistant",
        text=text,
        content_ref=content_ref_on,
        is_html=False,
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    # assistant is in HOLDING_TEXT_CHANNELS, so should get holding text
    assert result_on.text == "This content is being reviewed."
    _check_quarantine(conn_on, "assistant", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="assistant",
        text=text,
        content_ref=content_ref_off,
        is_html=False,
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != text  # A7 blocked and disclaimer added in shadow mode
    _check_quarantine(conn_off, "assistant", content_ref_off, enforce=False)
    conn_off.close()


def test_tts_enforce(migrated_db_for_enforce_tests):
    """tts: enforce ON blocks, creates pending item; OFF allows, shadow."""
    text = "This content has a guaranteed return."
    content_ref_on = f"tts:user1:2026-09-21"
    content_ref_off = f"tts:user1:2026-09-21-off"

    # Enforce ON
    result_on, conn_on = _publish_helper(
        channel="tts",
        text=text,
        content_ref=content_ref_on,
        is_html=False,
        enforce=True,
    )
    assert result_on.allowed is False
    assert result_on.a7_action == "blocked"
    assert result_on.held is True
    # tts is not in HOLDING_TEXT_CHANNELS, so should not get holding text
    assert result_on.text != "This content is being reviewed."
    _check_quarantine(conn_on, "tts", content_ref_on, enforce=True)
    conn_on.close()

    # Enforce OFF
    result_off, conn_off = _publish_helper(
        channel="tts",
        text=text,
        content_ref=content_ref_off,
        is_html=False,
        enforce=False,
    )
    assert result_off.allowed is True
    assert result_off.a7_action == "blocked"
    assert result_off.held is True
    assert result_off.text != text  # A7 blocked and disclaimer added in shadow mode
    _check_quarantine(conn_off, "tts", content_ref_off, enforce=False)
    conn_off.close()


# Test held committee narrative returns 404 to non-admins and 200 to admins
def test_held_committee_narrative_404_200(migrated_db_for_enforce_tests):
    """Held committee narrative returns 404 by id to non-admins and 200 to admins."""
    from fastapi.testclient import TestClient
    from app.main import app

    run_id = "2026-09-21:AAPL"
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)
        _seed_verification_results(conn, run_id)

    text = "This content has a guaranteed return."  # triggers A7 block
    narrative_id = "test-narrative-id"

    # First, create the narrative record directly in the database.
    narrative_payload = {
        "id": narrative_id,
        "date": "20260921",
        "provider": "system",
        "model": "committee-vote",
        "title": "Investment Committee · daily review",
        "profile": f"committee:{run_id}",
        "narrative": text,
        "created_at": _iso_now(),
    }
    with migrated_db_for_enforce_tests.engine.begin() as conn:
        db.create_report_narrative(narrative_payload)

    # Now, turn enforce ON and publish to hold the narrative.
    # We use the narrative id as content_ref for the quarantine item.
    result_on, conn_on = _publish_helper(
        channel="committee_report",
        text=text,
        run_ids=("dummy",),
        content_ref=narrative_id,
        is_html=False,
        committee_output=False,
        writer_payload=None,
        enforce=True,
    )
    assert result_on.allowed is False  # held
    _check_quarantine(conn_on, "committee_report", narrative_id, enforce=True)
    conn_on.close()

    # (Enforce OFF behaviour is covered by the per-channel tests above; re-publishing the SAME content_ref here would add a
    # shadow item next to the pending one and make the quarantine lookup ambiguous.)

    # Now test the endpoint
    client = TestClient(app)

    # Non-admin user (no auth headers) should get 404
    response = client.get(f"/api/reports/narratives/{narrative_id}")
    assert response.status_code == 404, f"Expected 404 for non-admin, got {response.status_code}"

    # Admin user should get 200
    # We need to create an admin token. Let's use a mock or rely on the test client's ability to override dependencies.
    from app.auth import get_current_user_optional, TokenPayload

    app.dependency_overrides[get_current_user_optional] = lambda: TokenPayload(
        sub="admin", username="admin", role="admin"
    )
    try:
        response_admin = client.get(f"/api/reports/narratives/{narrative_id}")
        assert response_admin.status_code == 200, f"Expected 200 for admin, got {response_admin.status_code}"
    finally:
        # never leak the admin override into other tests, even when the assert fails
        app.dependency_overrides.pop(get_current_user_optional, None)


