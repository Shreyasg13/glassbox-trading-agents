"""The single publish exit (S3 T5).

Every piece of system-generated text that reaches a user goes through `publish()`.
It runs the A6 gate (for committee outputs) and the A7 compliance filter, records
the verdict, and either allows or holds the text depending on the
`publish.enforce` flag.

Shadow mode (default): checks run and are recorded, but nothing is held.
Enforce mode: failing outputs are held (quarantine status "pending").

The function never raises: any internal error logs and allows in shadow mode,
holds in enforce mode (fail closed only when enforcing).
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import compliance, db, flags, ledger, verification
from .verification import gate
from .migrated_tables import quarantine_items_table

log = logging.getLogger("glassbox.publish")


@dataclass(frozen=True)
class PublishResult:
    allowed: bool
    text: str
    a6_ok: bool
    a7_action: str
    held: bool
    reasons: List[str]


def _iso_now() -> str:
    """The one timestamp format of the S3 tables: YYYY-MM-DDTHH:MM:SS.ffffff+00:00"""
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def _a6_check(run_ids: Tuple[str, ...], enforce: bool = False) -> Tuple[bool, List[str]]:
    """Check A6 gate results for each run_id from stored verification results.
    Returns (all_ok, reasons). If a run has no verification results, calls
    verification.runner.run_gate once; if still no results -> not ok (reason
    "no verification results"). In shadow mode, logs warning; in enforce mode,
    fails closed.
    """
    if not run_ids:
        return True, []
    all_ok = True
    reasons: List[str] = []
    for run_id in run_ids:
        try:
            with db.engine.connect() as conn:
                rows = conn.execute(
                    db.verification_results_table.select().where(db.verification_results_table.c.run_id == run_id)
                ).fetchall()
            if not rows:
                # No verification results yet - try running the gate once
                log.warning("A6 check: no verification results for %s, running gate", run_id)
                try:
                    import asyncio
                    # We need a book - load it synchronously since we're in a sync context
                    from . import paper_cycle
                    book = paper_cycle.load_book()
                    # Get run_time from the committee run
                    run_doc = db.get_committee_run(run_id)
                    run_time = run_doc.get("created_at") if run_doc else None
                    try:
                        asyncio.get_running_loop()
                        in_loop = True
                    except RuntimeError:
                        in_loop = False
                    if in_loop:
                        # asyncio.run() cannot run inside an active event loop (run_daily calls publish from it); the gate
                        # normally ran just before, so a missing result here means it failed: treat it as unverified.
                        summary = {"ok": False, "badge": "gate not run (called from the event loop)"}
                    elif run_time:
                        summary = asyncio.run(verification.runner.run_gate(run_id, run_time, book))
                    else:
                        summary = {"ok": False, "badge": "no run_time"}
                except Exception as gate_exc:  # noqa: BLE001
                    log.error("A6 gate run failed for %s: %s", run_id, gate_exc)
                    summary = {"ok": False, "badge": f"gate error: {type(gate_exc).__name__}"}
                # Check results again after running gate
                with db.engine.connect() as conn:
                    rows = conn.execute(
                        db.verification_results_table.select().where(db.verification_results_table.c.run_id == run_id)
                    ).fetchall()
                if not rows:
                    all_ok = False
                    reasons.append(f"A6 gate: no verification results for {run_id}")
                    continue
            # Convert rows to objects with attributes for gate.summarize
            class RowObj:
                def __init__(self, mapping):
                    for k, v in mapping.items():
                        setattr(self, k, v)
            results = [RowObj(r._mapping) for r in rows]
            summary = gate.summarize(results)
            if not summary.get("ok", True):
                all_ok = False
                reasons.append(f"A6 gate failed for {run_id}: {summary.get('badge', 'unknown')}")
        except Exception as exc:  # noqa: BLE001
            log.error("A6 check failed for %s: %s", run_id, exc)
            all_ok = False
            reasons.append(f"A6 check error for {run_id}: {type(exc).__name__}")
    return all_ok, reasons


def _a7_check(text: str, channel: str, run_id: Optional[str], enforce: bool = False) -> Tuple[str, str, List[Dict[str, Any]]]:
    """Run the A7 compliance filter. Returns (action, rewritten_text, events).
    On exception: returns "blocked" when enforce is on, "pass" in shadow mode.
    Still calls record() with an empty result so the run_id is logged.
    """
    try:
        result = compliance.filter.check(text, channel=channel)
        compliance.filter.record(result, run_id=run_id)
        return result.action, result.text, result.events
    except Exception as exc:  # noqa: BLE001
        log.error("A7 check failed for channel %s: %s", channel, exc)
        # Record an empty result so the run_id is logged for audit
        compliance.filter.record(compliance.filter.FilterResult(text=text, action="pass", events=[]), run_id=run_id)
        if enforce:
            return "blocked", text, []
        return "pass", text, []


def _create_quarantine_item(
    channel: str,
    run_id: Optional[str],
    content_ref: str,
    stage: str,
    status: str,
    reasons: List[str],
) -> None:
    """Create a quarantine item. Never raises."""
    try:
        item_id = str(uuid.uuid4())
        with db.engine.begin() as conn:
            conn.execute(
                quarantine_items_table.insert().values(
                    id=item_id,
                    channel=channel,
                    run_id=run_id,
                    content_ref=content_ref,
                    stage=stage,
                    status=status,
                    reasons_json=json.dumps(reasons),
                    created_at=_iso_now(),
                    reviewer_id=None,
                    review_note=None,
                    reviewed_at=None,
                )
            )
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to create quarantine item: %s", exc)


# ---------------------------------------------------------------------------
# Quarantine review re-check (S3 T6): re-runs A6+A7 for ONE already-quarantined
# item so the admin approve route can decide whether it passes now, without
# touching publish() itself.
# ---------------------------------------------------------------------------

def recheck_quarantine_item(item: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Re-run the A6 gate (if the item has a run_id) and the A7 filter (if the item's current
    text can still be found) for one quarantine item. Returns (ok, reasons); reasons explains
    every failing check, same wording as `_a6_check`/`_a7_check` use elsewhere.

    A7 can only be re-checked for channels whose text is stored and retrievable by content_ref
    (the report-narrative channels, via db.get_report_narrative) -- for channels that only ever
    send (digests, tts) there is nothing left to re-check, so A7 counts as still passing.
    """
    reasons: List[str] = []

    run_id = item.get("run_id")
    a6_ok = True
    if run_id:
        a6_ok, a6_reasons = _a6_check((run_id,))
        reasons.extend(a6_reasons)

    a7_ok = True
    content_ref = item.get("content_ref")
    if content_ref:
        narrative_row = db.get_report_narrative(content_ref)
        text = narrative_row.get("narrative") if narrative_row else None
        if text:
            result = compliance.filter.check(text, channel=item.get("channel", ""))
            if result.action == "blocked":
                a7_ok = False
                rule_ids = ", ".join(e.get("rule_id", "") for e in result.events) or "compliance filter"
                reasons.append(f"A7 blocked: {rule_ids}")

    return a6_ok and a7_ok, reasons


# ---------------------------------------------------------------------------
# Channel-specific writer functions (the ONLY place raw writers are called)
# ---------------------------------------------------------------------------

def _write_committee_report(payload: Dict[str, Any]) -> None:
    """Write a committee report narrative to the database."""
    db.create_report_narrative(payload)


def _write_paper_report(payload: Dict[str, Any]) -> None:
    """Write a paper trading report narrative to the database."""
    db.create_report_narrative(payload)


def _write_research_digest(payload: Dict[str, Any]) -> None:
    """Write a research digest narrative to the database."""
    db.create_report_narrative(payload)


def _write_user_report(payload: Dict[str, Any]) -> None:
    """Write a user orchestration report narrative to the database."""
    db.create_report_narrative(payload)


def _write_admin_digest(payload: Dict[str, Any]) -> None:
    """Send the admin digest email."""
    # payload: {"subject": str, "html": str, "cfg": dict, "headers": dict}
    from . import digest
    digest.send_email(payload["subject"], payload["html"], payload["cfg"], payload.get("headers"))


def _write_user_digest(payload: Dict[str, Any]) -> None:
    """Send a user digest email."""
    # payload: {"subject": str, "html": str, "cfg": dict, "headers": dict}
    from . import digest
    digest.send_email(payload["subject"], payload["html"], payload["cfg"], payload.get("headers"))


def _write_notification(payload: Dict[str, Any]) -> None:
    """Generate and send daily notifications."""
    # payload: {"book": ..., "users": ..., "narratives": ..., "stance_fn": ...}
    from . import notifications
    notifications.generate_daily(
        book=payload.get("book"),
        users=payload.get("users"),
        narratives=payload.get("narratives"),
        stance_fn=payload.get("stance_fn"),
    )


def _write_tts(payload: Dict[str, Any]) -> None:
    """TTS synthesis - handled by the /api/tts route, not here.
    This is a no-op because TTS goes through the API endpoint which has its own
    whitelist check. The publish() call for TTS is just for A7 compliance logging.
    """
    pass


def _write_assistant(payload: Dict[str, Any]) -> None:
    """Assistant answer - stored by the inbox router after publish().
    This is a no-op because the answer is saved by the caller.
    """
    pass


def _write_inbox(payload: Dict[str, Any]) -> None:
    """Inbox notification - stored by the notifications module.
    This is a no-op because the notification is created by the caller.
    """
    pass


def _write_orchestration_report(payload: Dict[str, Any]) -> None:
    """Write an admin-generated report narrative to the database."""
    db.create_report_narrative(payload)


# Map channel names to their writer functions
_CHANNEL_WRITERS: Dict[str, Callable[[Dict[str, Any]], None]] = {
    "committee_report": _write_committee_report,
    "paper_report": _write_paper_report,
    "research_digest": _write_research_digest,
    "user_report": _write_user_report,
    "admin_digest": _write_admin_digest,
    "user_digest": _write_user_digest,
    "notification": _write_notification,
    "tts": _write_tts,
    "assistant": _write_assistant,
    "inbox": _write_inbox,
    "orchestration_report": _write_orchestration_report,
}


def publish(
    channel: str,
    text: str,
    *,
    run_ids: Tuple[str, ...] = (),
    content_ref: Optional[str] = None,
    is_html: bool = False,
    committee_output: bool = True,
    writer_payload: Optional[Dict[str, Any]] = None,
) -> PublishResult:
    """The single publish exit.

    Args:
        channel: Output channel name (e.g. "committee_report", "user_digest", "assistant", "tts", "paper_report", "research_digest", "orchestration_report", "admin_digest", "inbox", "user_report")
        text: The text to publish (for A7 compliance check)
        run_ids: Committee run IDs this output derives from (for A6 gate)
        content_ref: Reference for the quarantine item (e.g. narrative id, digest date)
        is_html: Whether the text is HTML (affects A7 disclaimer placement)
        committee_output: If False, skips A6 (for assistant answers, "run my report", orchestration reports)
        writer_payload: Full payload for the channel writer (if allowed). If None, no write is performed.

    Returns:
        PublishResult with allowed, text (possibly rewritten by A7), a6_ok, a7_action, held, reasons
    """
    reasons: List[str] = []
    final_text = text

    # Check enforcement flag early (needed for fail-closed behavior)
    enforce = flags.flag("publish.enforce")

    # A6 gate (only for committee outputs)
    a6_ok = True
    if committee_output and run_ids:
        a6_ok, a6_reasons = _a6_check(run_ids, enforce=enforce)
        reasons.extend(a6_reasons)

    # A7 compliance filter - pass first run_id (or None) and enforce flag
    first_run_id = run_ids[0] if run_ids else None
    a7_action, rewritten_text, a7_events = _a7_check(text, channel, first_run_id, enforce=enforce)
    final_text = rewritten_text  # A7 rewrites (disclaimer) always apply

    # Determine if held
    held = not a6_ok or a7_action == "blocked"

    if held:
        if enforce:
            # Enforce mode: block and create pending quarantine item
            stage = "A6" if not a6_ok else "A7"
            _create_quarantine_item(
                channel=channel,
                run_id=run_ids[0] if run_ids else None,
                content_ref=content_ref or "unknown",
                stage=stage,
                status="pending",
                reasons=reasons or (["A7 blocked"] if a7_action == "blocked" else []),
            )
            allowed = False
            # In enforce mode, replace with holding message for user-facing channels
            if channel in ("committee_report", "paper_report", "research_digest", "orchestration_report", "admin_digest", "user_digest", "inbox", "user_report", "assistant"):
                final_text = "This content is being reviewed."
        else:
            # Shadow mode: allow but create shadow quarantine item
            stage = "A6" if not a6_ok else "A7"
            _create_quarantine_item(
                channel=channel,
                run_id=run_ids[0] if run_ids else None,
                content_ref=content_ref or "unknown",
                stage=stage,
                status="shadow",
                reasons=reasons or (["A7 blocked"] if a7_action == "blocked" else []),
            )
            allowed = True
    else:
        allowed = True

    # Ledger append for committee decisions (idempotent)
    if committee_output and run_ids:
        for run_id in run_ids:
            try:
                # Check if already in ledger
                with db.engine.connect() as conn:
                    existing = conn.execute(
                        db.ledger_calls_table.select().where(db.ledger_calls_table.c.call_id == run_id)
                    ).first()
                if not existing:
                    # Get the committee run to extract decision details
                    run_doc = db.get_committee_run(run_id)
                    if run_doc:
                        # The saved committee doc (_run_doc) stores decision, engine_signal, symbol, votes at top level
                        decision = run_doc.get("decision")
                        if decision is None:
                            # If decision is missing, DO NOT append (log an error) - the ledger is append-only,
                            # a wrong row can never be fixed
                            log.error("Ledger append skipped for %s: missing decision field", run_id)
                            continue
                        action = run_doc.get("action")
                        votes = run_doc.get("votes") or {}
                        engine_signal = run_doc.get("engine_signal")
                        # Use the real confidence from the CEO brief (vote share), or compute from votes if missing
                        ceo = run_doc.get("ceo") or {}
                        confidence = ceo.get("consensus")
                        if confidence is None and decision and votes:
                            total = sum(votes.values()) or 1.0
                            confidence = votes.get(decision, 0.0) / total
                        confidence = round(confidence or 0.0, 3)
                        # Get claim snapshot IDs
                        snapshot_ids: List[str] = []
                        with db.engine.connect() as conn:
                            claim_rows = conn.execute(
                                db.claims_table.select().where(db.claims_table.c.run_id == run_id)
                            ).fetchall()
                        for cr in claim_rows:
                            if cr.source_snapshot_id:
                                snapshot_ids.append(cr.source_snapshot_id)

                        ledger.append(
                            call_id=run_id,
                            ticker=run_doc.get("symbol", "UNKNOWN"),
                            call_type="committee_decision",
                            payload={
                                "decision": decision,
                                "confidence": confidence,
                                "engine_signal": engine_signal,
                                "horizon_days": 30,
                                "a6_ok": a6_ok,
                                "a7_action": a7_action,
                            },
                            input_snapshot_ids=snapshot_ids,
                            committee_config_id=None,
                        )
            except Exception as exc:  # noqa: BLE001
                log.error("Ledger append failed for %s: %s", run_id, exc)

    # If allowed and we have a writer payload, call the channel writer
    if allowed and writer_payload is not None:
        writer = _CHANNEL_WRITERS.get(channel)
        if writer:
            try:
                # Update the payload with the potentially rewritten text
                if "text" in writer_payload:
                    writer_payload = dict(writer_payload, text=final_text)
                elif "html" in writer_payload:
                    writer_payload = dict(writer_payload, html=final_text)
                elif "narrative" in writer_payload:
                    writer_payload = dict(writer_payload, narrative=final_text)
                writer(writer_payload)
            except Exception as exc:  # noqa: BLE001
                log.error("Channel writer failed for %s: %s", channel, exc)
                # Don't change allowed status - the content passed checks, write failure is separate

    return PublishResult(
        allowed=allowed,
        text=final_text,
        a6_ok=a6_ok,
        a7_action=a7_action,
        held=held,
        reasons=reasons,
    )


# Convenience function for channels that don't have run_ids
def publish_simple(channel: str, text: str, *, content_ref: Optional[str] = None, is_html: bool = False, writer_payload: Optional[Dict[str, Any]] = None) -> PublishResult:
    """Publish without committee run IDs (skips A6)."""
    return publish(channel, text, run_ids=(), content_ref=content_ref, is_html=is_html, committee_output=False, writer_payload=writer_payload)