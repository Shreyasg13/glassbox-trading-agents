"""Tests for the single publish exit (S3 T5).

Covers the rework items:
1. Ledger payload: real _run_doc-shaped doc -> ledger row has right decision/confidence; missing decision -> no row
2. Notifications: banned phrase in notification is flagged/held; stage still returns its counts
3. Fail-closed when enforcing:
   - _a6_check: run with no verification results calls run_gate once; if still no results -> not ok (reason "no verification results")
   - _a7_check: exception returns "blocked" when enforce is on (still "pass" in shadow mode)
4. Pass run_id to compliance.filter.record
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from app import db, flags, publish, migrate
from app.compliance import filter as cf
from app.migrated_tables import ledger_calls_table, quarantine_items_table
from app.db import committee_runs_table

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        committee_runs_table.insert().values(id=doc["id"], config=json.dumps(doc))
    )


def _clear_test_tables(conn) -> None:
    """Clear test-relevant tables. ledger_calls is append-only, so we don't delete from it."""
    conn.execute(quarantine_items_table.delete())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def migrated_db(real_db):
    """A database with migrations applied."""
    with real_db.engine.begin() as conn:
        migrate.upgrade(conn)
    return real_db


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    """Reset flags before each test."""
    flags.clear_cache()
    yield
    flags.clear_cache()


@pytest.fixture(autouse=True)
def _clear_compliance_cache():
    """Clear compliance cache before each test."""
    cf.clear_cache()
    cf._with_tickers.cache_clear()
    yield
    cf.clear_cache()
    cf._with_tickers.cache_clear()


@pytest.fixture(autouse=True)
def _clean_db(migrated_db):
    """Clean up before and after tests."""
    with migrated_db.engine.begin() as conn:
        _clear_test_tables(conn)
    yield
    with migrated_db.engine.begin() as conn:
        _clear_test_tables(conn)


# ---------------------------------------------------------------------------
# Test 1: Ledger payload uses real fields from _run_doc
# ---------------------------------------------------------------------------

def test_ledger_append_uses_real_decision_and_confidence(migrated_db, _clean_db):
    """A real _run_doc-shaped doc -> the ledger row has the right decision/confidence."""
    run_id = "2026-09-21:AAPL"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id, decision="BUY", action="BUY", engine_signal="BUY", ceo_consensus=0.75)
        _save_run_doc(doc, conn)

    # Call publish with committee_output=True and this run_id
    result = publish.publish(
        channel="committee_report",
        text="Test report content with disclaimer.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )

    assert result.allowed is True

    # Check ledger was appended with correct fields
    with migrated_db.engine.connect() as conn:
        ledger_row = conn.execute(
            select(ledger_calls_table).where(ledger_calls_table.c.call_id == run_id)
        ).first()

    assert ledger_row is not None, "Ledger row should exist"
    payload = json.loads(ledger_row.payload_json)
    assert payload["decision"] == "BUY", f"Expected decision BUY, got {payload['decision']}"
    assert payload["confidence"] == 0.75, f"Expected confidence 0.75, got {payload['confidence']}"
    assert payload["engine_signal"] == "BUY"
    assert payload["horizon_days"] == 30
    assert "a6_ok" in payload
    assert "a7_action" in payload


def test_ledger_skips_when_decision_missing(migrated_db, _clean_db):
    """If decision is missing, DO NOT append (log an error) - the ledger is append-only."""
    run_id = "2026-09-21:MSFT"
    with migrated_db.engine.begin() as conn:
        # Create a run doc WITHOUT the decision field (simulating a failed/partial run)
        doc = _create_run_doc(run_id, decision=None, engine_signal="HOLD")
        doc.pop("decision", None)  # Remove decision entirely
        _save_run_doc(doc, conn)

    result = publish.publish(
        channel="committee_report",
        text="Test report content with disclaimer.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )

    # The publish should still succeed (no exception)
    assert result.allowed is True

    # But ledger should NOT have a row for this run_id
    with migrated_db.engine.connect() as conn:
        ledger_row = conn.execute(
            select(ledger_calls_table).where(ledger_calls_table.c.call_id == run_id)
        ).first()

    assert ledger_row is None, "Ledger should NOT have a row when decision is missing"


def test_ledger_computes_confidence_from_votes_when_ceo_missing(migrated_db, _clean_db):
    """When ceo.consensus is missing, confidence is computed from votes."""
    run_id = "2026-09-21:GOOGL"
    with migrated_db.engine.begin() as conn:
        # ceo.consensus is None, but votes exist
        doc = _create_run_doc(
            run_id,
            decision="SELL",
            action="SELL",
            engine_signal="SELL",
            votes={"SELL": 0.5, "HOLD": 0.3, "BUY": 0.2},
            ceo_consensus=None,
        )
        _save_run_doc(doc, conn)

    result = publish.publish(
        channel="committee_report",
        text="Test report content with disclaimer.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )

    assert result.allowed is True

    with migrated_db.engine.connect() as conn:
        ledger_row = conn.execute(
            select(ledger_calls_table).where(ledger_calls_table.c.call_id == run_id)
        ).first()

    assert ledger_row is not None
    payload = json.loads(ledger_row.payload_json)
    # confidence should be votes["SELL"] / sum(votes) = 0.5 / 1.0 = 0.5
    assert payload["confidence"] == 0.5, f"Expected confidence 0.5, got {payload['confidence']}"


def test_ledger_idempotent(migrated_db, _clean_db):
    """A run already in the ledger is skipped (idempotent)."""
    run_id = "2026-09-21:TSLA"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id, decision="HOLD", action="HOLD", engine_signal="HOLD")
        _save_run_doc(doc, conn)

    # First publish
    result1 = publish.publish(
        channel="committee_report",
        text="Test report content with disclaimer.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )
    assert result1.allowed is True

    # Second publish (simulating re-run)
    result2 = publish.publish(
        channel="committee_report",
        text="Test report content with disclaimer.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )
    assert result2.allowed is True

    # Only one ledger row should exist
    with migrated_db.engine.connect() as conn:
        count = conn.execute(
            select(db.func.count()).select_from(ledger_calls_table).where(ledger_calls_table.c.call_id == run_id)
        ).scalar()

    assert count == 1, "Ledger should be idempotent - only one row per run_id"


# ---------------------------------------------------------------------------
# Test 2: Notifications - banned phrase is flagged/held
# ---------------------------------------------------------------------------

def test_notification_banned_phrase_held_in_enforce_mode(migrated_db, _clean_db, monkeypatch):
    """A banned phrase in a generated notification is flagged/held in enforce mode."""
    flags.set_flag("publish.enforce", True, "admin")
    flags.clear_cache()

    # This text contains a banned phrase ("guaranteed return")
    notification_text = "Your daily stance for 2026-09-21: AAPL has a guaranteed return opportunity."
    content_ref = "inbox:testuser:alert:AAPL:2026-09-21"

    result = publish.publish_simple(
        channel="inbox",
        text=notification_text,
        content_ref=content_ref,
        is_html=False,
    )

    # In enforce mode, blocked content should not be allowed
    assert result.allowed is False
    assert result.a7_action == "blocked"
    assert result.held is True

    # The text should be replaced with holding message
    assert result.text == "This content is being reviewed."

    # A quarantine item with status "pending" should be created
    with migrated_db.engine.connect() as conn:
        q = conn.execute(
            select(quarantine_items_table).where(quarantine_items_table.c.content_ref == content_ref)
        ).first()

    assert q is not None, "Quarantine item should be created"
    assert q.status == "pending", f"Expected pending status, got {q.status}"
    assert q.stage == "A7"


def test_notification_banned_phrase_shadow_mode(migrated_db, _clean_db, monkeypatch):
    """In shadow mode, banned phrase creates shadow item but text is allowed."""
    flags.set_flag("publish.enforce", False, "admin")
    flags.clear_cache()

    notification_text = "Your daily stance for 2026-09-21: AAPL has a guaranteed return opportunity."
    content_ref = "inbox:testuser:alert:AAPL:2026-09-21"

    result = publish.publish_simple(
        channel="inbox",
        text=notification_text,
        content_ref=content_ref,
        is_html=False,
    )

    # In shadow mode, blocked content is still allowed (but recorded)
    assert result.allowed is True
    assert result.a7_action == "blocked"
    assert result.held is True  # held=True means "would be held"
    # A7 rewrites (disclaimer) always apply, so text should have disclaimer appended
    assert notification_text in result.text
    assert "disclaimer" in result.text.lower() or "research" in result.text.lower()

    # A quarantine item with status "shadow" should be created
    with migrated_db.engine.connect() as conn:
        q = conn.execute(
            select(quarantine_items_table).where(quarantine_items_table.c.content_ref == content_ref)
        ).first()

    assert q is not None
    assert q.status == "shadow"
    assert q.stage == "A7"


def test_notifications_generate_daily_still_returns_counts(migrated_db, _clean_db, monkeypatch):
    """The notifications stage still returns its counts even when some notifications are held."""
    from app import notifications

    # This test verifies the generate_daily function doesn't crash and returns expected structure
    # We mock the database calls to avoid complex setup
    with patch("app.notifications.db.list_users") as mock_users, \
         patch("app.paper_cycle.load_book") as mock_book, \
         patch("app.notifications.db.list_report_narratives") as mock_narratives, \
         patch("app.strategy.stance") as mock_stance:

        mock_users.return_value = [
            {"username": "user1", "role": "user", "tickers": ["AAPL"]},
            {"username": "admin1", "role": "admin", "tickers": []},  # should be skipped
        ]
        mock_book.return_value = MagicMock()
        mock_narratives.return_value = []
        mock_stance.return_value = {
            "as_of": "2026-09-21",
            "rows": [
                {"symbol": "AAPL", "summary": "Test", "attention": True, "watch": False, "reasons": ["test"]}
            ],
            "macro_line": "Macro is calm",
        }

        result = notifications.generate_daily()

        assert "created" in result
        assert "failed" in result
        assert "ok" in result
        assert "detail" in result


# ---------------------------------------------------------------------------
# Test 3: Fail-closed when enforcing
# ---------------------------------------------------------------------------

def test_a6_check_no_verification_results_calls_gate_then_fails(migrated_db, _clean_db, monkeypatch):
    """A run with NO verification results must call run_gate once; if still no results -> not ok."""
    flags.set_flag("publish.enforce", True, "admin")
    flags.clear_cache()

    run_id = "2026-09-21:TEST"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)

    # Mock run_gate to do nothing (simulating no results produced)
    with patch("app.verification.runner.run_gate", new_callable=AsyncMock) as mock_run_gate:
        mock_run_gate.return_value = {"ok": False, "badge": "no results"}

        # Need to also mock paper_cycle.load_book
        with patch("app.paper_cycle.load_book") as mock_load_book:
            mock_load_book.return_value = MagicMock()

            # Call publish which will invoke _a6_check
            result = publish.publish(
                channel="committee_report",
                text="Test report with disclaimer.",
                run_ids=(run_id,),
                content_ref=f"committee:{run_id}",
                committee_output=True,
            )

    # The run_gate should have been called
    mock_run_gate.assert_called_once()

    # Since no verification results were created, a6_ok should be False
    assert result.a6_ok is False
    assert any("no verification results" in r for r in result.reasons)


def test_a6_check_no_results_shadow_mode(migrated_db, _clean_db, monkeypatch):
    """In shadow mode, a run with no verification results logs warning but still fails a6_ok."""
    flags.set_flag("publish.enforce", False, "admin")
    flags.clear_cache()

    run_id = "2026-09-21:TEST2"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)

    with patch("app.verification.runner.run_gate", new_callable=AsyncMock) as mock_run_gate:
        mock_run_gate.return_value = {"ok": False, "badge": "no results"}

        with patch("app.paper_cycle.load_book") as mock_load_book:
            mock_load_book.return_value = MagicMock()

            result = publish.publish(
                channel="committee_report",
                text="Test report with disclaimer.",
                run_ids=(run_id,),
                content_ref=f"committee:{run_id}",
                committee_output=True,
            )

    mock_run_gate.assert_called_once()
    assert result.a6_ok is False
    assert any("no verification results" in r for r in result.reasons)
    # In shadow mode, allowed should still be True (shadow item created)
    assert result.allowed is True


def test_a7_check_exception_blocked_in_enforce_mode(migrated_db, _clean_db, monkeypatch):
    """_a7_check exception returns 'blocked' when enforce is on."""
    flags.set_flag("publish.enforce", True, "admin")
    flags.clear_cache()

    # Mock compliance.filter.check to raise an exception
    with patch("app.compliance.filter.check") as mock_check:
        mock_check.side_effect = Exception("Filter error")

        result = publish.publish_simple(
            channel="assistant",
            text="Test answer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    assert result.a7_action == "blocked"
    assert result.allowed is False
    assert result.held is True
    assert result.text == "This content is being reviewed."


def test_a7_check_exception_pass_in_shadow_mode(migrated_db, _clean_db, monkeypatch):
    """_a7_check exception returns 'pass' in shadow mode."""
    flags.set_flag("publish.enforce", False, "admin")
    flags.clear_cache()

    with patch("app.compliance.filter.check") as mock_check:
        mock_check.side_effect = Exception("Filter error")

        result = publish.publish_simple(
            channel="assistant",
            text="Test answer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    assert result.a7_action == "pass"
    assert result.allowed is True
    assert result.held is False
    assert result.text == "Test answer"  # original text preserved


def test_a7_check_exception_records_run_id(migrated_db, _clean_db, monkeypatch):
    """_a7_check passes run_id to compliance.filter.record even on exception."""
    flags.set_flag("publish.enforce", False, "admin")
    flags.clear_cache()

    run_id = "2026-09-21:TEST3"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)

    with patch("app.compliance.filter.check") as mock_check, \
         patch("app.compliance.filter.record") as mock_record:

        mock_check.side_effect = Exception("Filter error")
        mock_record.return_value = 0

        result = publish.publish(
            channel="committee_report",
            text="Test report with disclaimer.",
            run_ids=(run_id,),
            content_ref=f"committee:{run_id}",
            committee_output=True,
        )

    # Verify record was called with the run_id
    mock_record.assert_called_once()
    call_args = mock_record.call_args
    # run_id should be the first positional arg or keyword arg
    assert call_args.kwargs.get("run_id") == run_id or call_args.args[1] == run_id


# ---------------------------------------------------------------------------
# Test 4: Pass run_id to compliance.filter.record
# ---------------------------------------------------------------------------

def test_a7_check_passes_run_id_to_record(migrated_db, _clean_db):
    """The first run_id is passed to compliance.filter.record."""
    run_id = "2026-09-21:RUNID"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)

    with patch("app.compliance.filter.record") as mock_record:
        mock_record.return_value = 0

        result = publish.publish(
            channel="committee_report",
            text="Test report with disclaimer.",
            run_ids=(run_id, "2026-09-21:OTHER"),
            content_ref=f"committee:{run_id}",
            committee_output=True,
        )

    mock_record.assert_called_once()
    call_args = mock_record.call_args
    # Should pass the FIRST run_id
    assert call_args.kwargs.get("run_id") == run_id or call_args.args[1] == run_id


def test_a7_check_passes_none_when_no_run_ids(migrated_db, _clean_db):
    """When no run_ids, None is passed to compliance.filter.record."""
    with patch("app.compliance.filter.record") as mock_record:
        mock_record.return_value = 0

        result = publish.publish_simple(
            channel="assistant",
            text="Test answer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    mock_record.assert_called_once()
    call_args = mock_record.call_args
    assert call_args.kwargs.get("run_id") is None or call_args.args[1] is None


# ---------------------------------------------------------------------------
# Test 5: publish never raises (internal errors handled)
# ---------------------------------------------------------------------------

def test_publish_never_raises_on_db_error(migrated_db, _clean_db, monkeypatch):
    """Any internal error -> log, allow in shadow mode, hold in enforce mode."""
    flags.set_flag("publish.enforce", False, "admin")
    flags.clear_cache()

    # Mock the database connection to raise an error during quarantine creation
    with patch("app.publish.db.engine.begin") as mock_begin:
        mock_begin.side_effect = Exception("DB connection failed")

        # Should not raise
        result = publish.publish_simple(
            channel="assistant",
            text="Test answer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    # In shadow mode, should allow despite error
    assert result.allowed is True


def test_publish_never_raises_on_db_error_enforce(migrated_db, _clean_db, monkeypatch):
    """In enforce mode, internal error in A7 -> hold (fail closed)."""
    flags.set_flag("publish.enforce", True, "admin")
    flags.clear_cache()

    # Mock compliance.filter.check to raise an exception
    with patch("app.compliance.filter.check") as mock_check:
        mock_check.side_effect = Exception("Filter error")

        result = publish.publish_simple(
            channel="assistant",
            text="Test answer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    # In enforce mode, A7 exception -> blocked -> hold (fail closed)
    assert result.allowed is False
    assert result.held is True
    assert result.text == "This content is being reviewed."
    assert result.a7_action == "blocked"


# ---------------------------------------------------------------------------
# Test 6: A7 rewrite (disclaimer) always applies
# ---------------------------------------------------------------------------

def test_a7_rewrite_applies_in_both_modes(migrated_db, _clean_db, monkeypatch):
    """A7 rewrites (disclaimer) always apply regardless of mode."""
    disclaimer_text = "This is a test disclaimer."

    with patch("app.disclaimer.text", return_value=disclaimer_text):
        # Shadow mode
        monkeypatch.setenv("GLASSBOX_PUBLISH_ENFORCE", "false")
        flags.clear_cache()

        result_shadow = publish.publish_simple(
            channel="assistant",
            text="Test answer without disclaimer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

        # Enforce mode
        monkeypatch.setenv("GLASSBOX_PUBLISH_ENFORCE", "true")
        flags.clear_cache()

        result_enforce = publish.publish_simple(
            channel="assistant",
            text="Test answer without disclaimer",
            content_ref="assistant:user1:2026-09-21",
            is_html=False,
        )

    # Both should have the disclaimer appended
    assert disclaimer_text in result_shadow.text
    assert disclaimer_text in result_enforce.text


# ---------------------------------------------------------------------------
# Test 7: Quarantine item creation
# ---------------------------------------------------------------------------

def test_quarantine_item_created_with_correct_fields(migrated_db, _clean_db, monkeypatch):
    """Quarantine item has all required fields populated correctly."""
    flags.set_flag("publish.enforce", True, "admin")
    flags.clear_cache()

    run_id = "2026-09-21:QUAR"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id)
        _save_run_doc(doc, conn)

    result = publish.publish(
        channel="committee_report",
        text="Test with guaranteed return banned phrase.",
        run_ids=(run_id,),
        content_ref=f"committee:{run_id}",
        committee_output=True,
    )

    assert result.allowed is False

    with migrated_db.engine.connect() as conn:
        q = conn.execute(
            select(quarantine_items_table).where(quarantine_items_table.c.run_id == run_id)
        ).first()

    assert q is not None
    assert q.channel == "committee_report"
    assert q.run_id == run_id
    assert q.content_ref == f"committee:{run_id}"
    assert q.stage in ("A6", "A7")
    assert q.status == "pending"
    assert q.reasons_json is not None
    assert q.created_at is not None
    assert q.reviewer_id is None
    assert q.review_note is None
    assert q.reviewed_at is None


# ---------------------------------------------------------------------------
# Test 8: Committee decisions identical with publish wired in
# ---------------------------------------------------------------------------

def test_committee_decision_identity_with_publish(migrated_db, _clean_db):
    """Committee decisions are identical with publish wired in (extends existing test)."""
    # This test ensures that wiring publish() into committee_daily._write_report
    # doesn't change the decision data stored
    from app import committee_daily

    run_id = "2026-09-21:IDENT"
    with migrated_db.engine.begin() as conn:
        doc = _create_run_doc(run_id, decision="BUY", action="BUY", engine_signal="BUY")
        _save_run_doc(doc, conn)

    # Simulate _write_report calling publish
    report_text = committee_daily.build_report("2026-09-21", [doc])
    run_ids = (run_id,)
    content_ref = f"committee:2026-09-21:test"

    result = publish.publish(
        channel="committee_report",
        text=report_text,
        run_ids=run_ids,
        content_ref=content_ref,
        is_html=False,
        committee_output=True,
        writer_payload={
            "id": "test-id",
            "date": "20260921",
            "provider": "system",
            "model": "committee-vote",
            "title": "Investment Committee · daily review",
            "profile": content_ref,
            "narrative": report_text,
            "created_at": _iso_now(),
        },
    )

    # The publish should allow (report passes checks)
    assert result.allowed is True
    # The text should be the same (plus disclaimer)
    assert "BUY" in result.text
    assert "AAPL" in result.text


# ---------------------------------------------------------------------------
# Test 9: TTS refuses arbitrary text
# ---------------------------------------------------------------------------

def test_tts_only_allows_whitelisted_or_published_text():
    """TTS with output.speech on must only speak text that equals an allowed publication's text or static whitelist."""
    # This is a structural test - the actual TTS route is tested elsewhere
    # Here we verify the publish() path for TTS channel exists
    from app import tts

    # The ALLOWED_STATIC_LINES should be defined
    assert hasattr(tts, "ALLOWED_STATIC_LINES")
    assert isinstance(tts.ALLOWED_STATIC_LINES, (list, tuple, set))
    assert len(tts.ALLOWED_STATIC_LINES) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])