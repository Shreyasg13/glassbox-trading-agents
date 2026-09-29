"""Feature flags and output kill switches (S3 T1): defaults, safety, audit, and that turning a switch off really stops each channel."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from app import digest, flags, migrate, pipeline, user_digest

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_cache():
    flags.clear_cache()
    yield
    flags.clear_cache()


@pytest.fixture
def migrated(real_db):
    with real_db.engine.begin() as conn:
        migrate.upgrade(conn)
    return real_db


# ------------------------------------------------------------------ the module --


def test_defaults_apply_when_nothing_is_stored_or_the_table_does_not_exist_yet(real_db):
    assert flags.flag("output.speech") is False  # speech is OFF by default (plan T1)
    assert all(flags.flag(k) for k in ("output.email", "output.reports", "pipeline.daily", "output.assistant", "output.user_reports"))


def test_an_unknown_flag_is_off_and_never_raises(real_db):
    assert flags.flag("output.typo") is False
    with pytest.raises(flags.UnknownFlag):
        flags.set_flag("output.typo", True, "admin")


def test_set_and_read_back_and_defaults_are_reported(migrated):
    row = flags.set_flag("output.email", False, "admin", NOW)
    assert row["enabled"] is False and row["default"] is True and row["updated_by"] == "admin" and row["updated_at"].startswith("2026-09-26")
    assert flags.flag("output.email") is False
    flags.set_flag("output.speech", True, "admin", NOW)
    assert flags.flag("output.speech") is True
    listed = {f["key"]: f for f in flags.all_flags()}
    assert set(listed) == set(flags.FLAGS) and listed["output.reports"]["updated_by"] is None and listed["output.reports"]["description"]


def test_every_change_is_written_to_the_audit_log_with_who_and_the_previous_value(migrated):
    flags.set_flag("output.email", False, "root")
    flags.set_flag("output.email", True, "root2")
    entries = [e for e in migrated._list(migrated.audit_log_table) if e["action"] == "flag.set"]
    by_actor = {e["actor"]: e for e in entries}
    assert by_actor["root"]["detail"] == {"enabled": False, "previous": True} and by_actor["root2"]["detail"] == {"enabled": True, "previous": False}
    assert all(e["resource_type"] == "feature_flag" and e["resource_id"] == "output.email" for e in entries)


def test_reads_are_cached_briefly_then_refresh(migrated):
    t = 1000.0
    assert flags.flag("output.email", now=t) is True
    with migrated.engine.begin() as conn:  # changed behind this process's back, as another worker would
        conn.execute(flags._T.insert().values(key="output.email", enabled=False, updated_by="other", updated_at="x"))
    assert flags.flag("output.email", now=t + flags.CACHE_TTL_S - 1) is True  # still cached
    assert flags.flag("output.email", now=t + flags.CACHE_TTL_S + 1) is False  # picked up


def test_a_database_error_falls_back_to_the_default_instead_of_raising(migrated, monkeypatch):
    flags.set_flag("output.email", False, "admin")
    flags.clear_cache()

    class Broken:
        def connect(self):
            from sqlalchemy.exc import OperationalError

            raise OperationalError("select", {}, Exception("database is locked"))

    monkeypatch.setattr(migrated, "engine", Broken())
    assert flags.flag("output.email") is True  # the default, NOT a crash: a broken switch must not take a channel down


# -------------------------------------------------------------- the kill switches --


def test_email_off_stops_the_admin_digest_user_digests_previews_and_confirmations(migrated, monkeypatch):
    monkeypatch.setenv("DIGEST_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("DIGEST_SMTP_APP_PASSWORD", "x")
    monkeypatch.setenv("DIGEST_TO_EMAIL", "admin@example.com")
    sent = []

    def sender(*a, **k):
        sent.append(a)

    flags.set_flag("output.email", False, "admin")
    assert digest.run(runner=sender)["sent"] is False and "flag" in digest.run(runner=sender)["detail"]
    assert user_digest.run(book=object(), sender=sender, users=[])["sent"] == 0
    assert user_digest.send_confirmation({"id": "u", "digest": {"email": "a@b.co"}}, sender=sender)["ok"] is False
    assert user_digest.send_preview({"id": "u", "digest": {"email": "a@b.co"}}, sender=sender)["ok"] is False
    assert sent == []  # nothing left the building


def test_email_on_restores_the_normal_paths(migrated, monkeypatch):
    monkeypatch.delenv("DIGEST_SMTP_USER", raising=False)
    flags.set_flag("output.email", False, "admin")
    flags.set_flag("output.email", True, "admin")
    assert "not configured" in digest.run()["detail"]  # reached the normal path again (no SMTP in tests)


def test_pipeline_off_skips_the_whole_run_and_runs_no_stage(migrated):
    calls = []
    stages = {n: (lambda n=n: calls.append(n)) for n in pipeline.STAGE_ORDER}
    flags.set_flag("pipeline.daily", False, "admin")
    out = pipeline.run(sync=lambda: calls.append("sync") or {}, stages=stages, wait=False)
    assert out["status"] == "disabled" and out["exit_code"] == 0 and calls == []


def _client(*routers, user=None, optional=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.auth import get_current_user, get_current_user_optional

    app = FastAPI()
    for r in routers:
        app.include_router(r)
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_user_optional] = lambda: optional
    return TestClient(app)


def test_speech_is_off_by_default_and_the_switch_turns_it_on(migrated, monkeypatch):
    from app import tts
    from app.routers import tts as tts_routes

    monkeypatch.setattr(tts, "lookup", lambda *a: (b"audio", "audio/mpeg"))
    # Use a whitelisted static line (exact match required after whitespace normalization)
    allowed_text = tts.ALLOWED_STATIC_LINES[0]
    body = {"text": allowed_text, "elevenlabs_voice_id": "A" * 20, "kokoro_voice_id": "am_michael"}
    c = _client(tts_routes.router)
    assert c.post("/api/tts", json=body).status_code == 503  # off by default
    flags.set_flag("output.speech", True, "admin")
    assert c.post("/api/tts", json=body).status_code == 200


def test_reports_off_hides_a_report_from_the_public_but_not_from_an_admin(migrated, monkeypatch):
    from app import db
    from app.auth import TokenPayload
    from app.routers import reports

    monkeypatch.setattr(db, "get_report_narrative", lambda i: {"id": i, "date": "20260925", "provider": "system", "model": "m", "title": "t", "profile": None, "narrative": "n", "created_at": "2026-09-25T00:00:00+00:00"})
    assert _client(reports.router).get("/api/reports/narratives/abc").status_code == 200
    flags.set_flag("output.reports", False, "admin")
    assert _client(reports.router).get("/api/reports/narratives/abc").status_code == 404
    assert _client(reports.router, optional=TokenPayload(sub="u", role="viewer")).get("/api/reports/narratives/abc").status_code == 404
    assert _client(reports.router, optional=TokenPayload(sub="root", role="admin")).get("/api/reports/narratives/abc").status_code == 200


def test_assistant_and_user_reports_switches_answer_503(migrated):
    from app.auth import TokenPayload
    from app.routers import inbox, me

    who = TokenPayload(sub="ann", role="viewer")
    c = _client(inbox.me_router, me.router, user=who)
    flags.set_flag("output.assistant", False, "admin")
    r = c.post("/api/me/ask", json={"question": "hi"})
    assert r.status_code == 503 and "switched off" in r.json()["detail"]
    flags.set_flag("output.user_reports", False, "admin")
    r2 = c.post("/api/me/run-report", json={})
    assert r2.status_code == 503 and "switched off" in r2.json()["detail"]


# ---------------------------------------------------------------------- admin API --


def test_only_admins_can_list_or_change_flags_and_bad_keys_are_rejected(migrated):
    from app.auth import TokenPayload
    from app.routers import flags as flags_routes

    viewer = _client(flags_routes.router, user=TokenPayload(sub="ann", role="viewer"))
    assert viewer.get("/api/admin/flags").status_code == 403 and viewer.post("/api/admin/flags/output.email", json={"enabled": False}).status_code == 403
    assert flags.flag("output.email") is True  # the viewer changed nothing
    admin = _client(flags_routes.router, user=TokenPayload(sub="root", role="admin"))
    assert {f["key"] for f in admin.get("/api/admin/flags").json()["flags"]} == set(flags.FLAGS)
    r = admin.post("/api/admin/flags/output.email", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["updated_by"] == "root"
    assert flags.flag("output.email") is False
    assert admin.post("/api/admin/flags/output.typo", json={"enabled": True}).status_code == 404
    assert admin.post("/api/admin/flags/output.email", json={}).status_code == 422


def test_the_admin_api_says_so_when_the_migration_has_not_been_applied(real_db):
    from app.auth import TokenPayload
    from app.routers import flags as flags_routes

    admin = _client(flags_routes.router, user=TokenPayload(sub="root", role="admin"))
    assert admin.get("/api/admin/flags").status_code == 200  # reading still works (defaults)
    r = admin.post("/api/admin/flags/output.email", json={"enabled": False})
    assert r.status_code == 503 and "migration" in r.json()["detail"]
