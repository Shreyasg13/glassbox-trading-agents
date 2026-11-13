"""The daily admin digest: content curation (pure), HTML rendering (pure), and the never-fatal
send path. No real SMTP, no real DB -- see app/digest.py's own docstring for the design rules
this is checking."""
from __future__ import annotations

import pytest

from app import digest


def overview(**over):
    base = {
        "data_date": "2026-09-22",
        "latest_review_date": "2026-09-22",
        "pipeline": {
            "status": "ok",
            "message": "",
            "stages": {"free_data": {"ok": True}, "committee": {"ok": True}, "paper_cycle": {"ok": True}},
            "sync": {"coverage": 1.0},
        },
        "data_quality": {"ok": True, "gaps": []},
        "capital": [
            {"id": "ctl_engine", "name": "Signal engine", "total_return": 0.135, "live_return": 0.02, "sharpe": 1.1, "max_drawdown": -0.08, "live_days": 3},
        ],
    }
    base.update(over)
    return base


def run_row(symbol="AAPL", decision="BUY", gate=None, engine="BUY"):
    return {"date": "2026-09-22", "symbol": symbol, "decision": decision, "action": decision if not gate else "HOLD", "engine_signal": engine, "gate": gate, "ceo": {"label": "strong consensus"}, "answered": 10, "total": 10}


FREE_STATUS = {"treasury": {"ok": True, "detail": "through 2026-09-22"}, "sec": {"ok": False, "detail": "not configured: set SEC_USER_AGENT"}}
BOARD_INSUFFICIENT = {"agents": [{"agent": "Analyst0", "ranked": False}], "note": "An agent is ranked only after 30 scored BUY/SELL calls."}


# ------------------------------------------------------------------ build_digest --


def test_build_digest_curates_the_overview_without_recomputing_anything():
    d = digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, [run_row(), run_row("NVDA", "SELL", engine="SELL")])
    assert d["date"] == "2026-09-22" and d["pipeline"]["status"] == "ok" and d["pipeline"]["sync_coverage"] == 1.0
    assert d["pipeline"]["stages"] == {"free_data": True, "committee": True, "paper_cycle": True}
    assert [x["symbol"] for x in d["decisions"]] == ["AAPL", "NVDA"]  # sorted
    assert d["capital"][0]["name"] == "Signal engine" and d["capital"][0]["total_return"] == 0.135
    assert d["ranked_agents"] == [] and "30 scored" in d["leaderboard_note"]
    assert d["free_data"]["sec"]["ok"] is False


def test_a_risk_gated_buy_is_flagged_in_the_curated_decisions():
    d = digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, [run_row("TSLA", "BUY", gate="risk regime is HIGH")])
    assert d["decisions"][0]["gated"] is True and d["decisions"][0]["action"] == "HOLD"


def test_missing_data_quality_and_pipeline_never_crash_the_curation():
    d = digest.build_digest({"data_date": None}, {}, {"agents": []}, [])
    assert d["data_quality"] == {"ok": True, "gaps": []} and d["pipeline"]["stages"] == {} and d["decisions"] == []


# ------------------------------------------------------------------------ HTML --


def test_render_html_is_self_contained_and_covers_every_section():
    d = digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, [run_row()])
    html = digest.render_html(d)
    assert html.startswith("<!doctype html>") and "<style" not in html  # inline styles only: safest across mail clients
    assert "2026-09-22" in html and "Simulated research, not investment advice." in html
    assert "AAPL" in html and "Signal engine" in html and "sec" in html
    assert "30 scored" in html  # the leaderboard's own gate note, not a fabricated verdict
    assert digest.LIVE_SITE in html


def test_render_html_flags_a_data_quality_gap():
    d = digest.build_digest(overview(data_quality={"ok": False, "gaps": [{"from": "2025-12-29", "to": "2026-08-20", "days": 234}]}), FREE_STATUS, BOARD_INSUFFICIENT, [])
    html = digest.render_html(d)
    assert "Data quality warning" in html and "234d" in html


def test_render_html_shows_a_ranked_agent_once_the_gate_opens():
    board = {"agents": [{"agent": "Analyst0", "ranked": True, "hit_rate": 0.6, "mean_edge": 0.011, "directional_calls": 34}], "note": "..."}
    d = digest.build_digest(overview(), FREE_STATUS, board, [])
    html = digest.render_html(d)
    assert "Analyst0" in html and "60%" in html and "34" in html


# ----------------------------------------------------------------- smtp config --


def test_smtp_config_needs_all_three_values(monkeypatch):
    for k in ("DIGEST_SMTP_USER", "DIGEST_SMTP_APP_PASSWORD", "DIGEST_TO_EMAIL", "DIGEST_SMTP_HOST", "DIGEST_SMTP_PORT"):
        monkeypatch.delenv(k, raising=False)
    assert digest.smtp_config() is None
    monkeypatch.setenv("DIGEST_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("DIGEST_SMTP_APP_PASSWORD", "x" * 16)
    assert digest.smtp_config() is None  # still missing the recipient
    monkeypatch.setenv("DIGEST_TO_EMAIL", "admin@example.com")
    cfg = digest.smtp_config()
    assert cfg == {"host": "smtp.gmail.com", "port": 587, "user": "bot@example.com", "password": "x" * 16, "to": "admin@example.com"}


# --------------------------------------------------------------------------- run --


def test_run_reports_not_configured_but_is_not_a_failure(monkeypatch):
    """Not-yet-turned-on is the same convention as SEC/insiders without SEC_USER_AGENT: it must never
    mark the whole pipeline day 'partial' just because nobody added a credential yet."""
    monkeypatch.setattr(digest, "gather", lambda book=None: digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, []))
    monkeypatch.delenv("DIGEST_SMTP_USER", raising=False)
    out = digest.run()
    assert out["ok"] is True and out["sent"] is False and "not configured" in out["detail"]


def test_run_sends_through_the_injected_runner_and_reports_success(monkeypatch):
    monkeypatch.setattr(digest, "gather", lambda book=None: digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, []))
    monkeypatch.setenv("DIGEST_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("DIGEST_SMTP_APP_PASSWORD", "x" * 16)
    monkeypatch.setenv("DIGEST_TO_EMAIL", "admin@example.com")
    seen = {}

    def fake_send(subject, html, cfg):
        seen.update(subject=subject, html=html, cfg=cfg)

    out = digest.run(runner=fake_send)
    assert out == {"ok": True, "sent": True, "detail": "sent to admin@example.com"}
    assert "2026-09-22" in seen["subject"] and seen["cfg"]["to"] == "admin@example.com" and "<!doctype html>" in seen["html"]


def test_run_never_raises_when_sending_fails(monkeypatch):
    monkeypatch.setattr(digest, "gather", lambda book=None: digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, []))
    monkeypatch.setenv("DIGEST_SMTP_USER", "bot@example.com")
    monkeypatch.setenv("DIGEST_SMTP_APP_PASSWORD", "x" * 16)
    monkeypatch.setenv("DIGEST_TO_EMAIL", "admin@example.com")

    def boom(subject, html, cfg):
        raise ConnectionError("smtp.gmail.com refused the connection")

    out = digest.run(runner=boom)
    assert out["ok"] is False and out["sent"] is False and "ConnectionError" in out["detail"]


def test_run_never_raises_when_gathering_fails(monkeypatch):
    def boom(book=None):
        raise RuntimeError("no price data")

    monkeypatch.setattr(digest, "gather", boom)
    out = digest.run()
    assert out["ok"] is False and "build failed" in out["detail"]


# ---------------------------------------------------------------------- whatsapp --


def _email_env(monkeypatch, on=True):
    for k in ("DIGEST_SMTP_USER", "DIGEST_SMTP_APP_PASSWORD", "DIGEST_TO_EMAIL"):
        monkeypatch.delenv(k, raising=False)
    if on:
        monkeypatch.setenv("DIGEST_SMTP_USER", "bot@example.com")
        monkeypatch.setenv("DIGEST_SMTP_APP_PASSWORD", "x" * 16)
        monkeypatch.setenv("DIGEST_TO_EMAIL", "admin@example.com")


def _wa_env(monkeypatch, on=True):
    monkeypatch.delenv("CALLMEBOT_PHONE", raising=False)
    monkeypatch.delenv("CALLMEBOT_APIKEY", raising=False)
    if on:
        monkeypatch.setenv("CALLMEBOT_PHONE", "+15551234567")
        monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")


def test_whatsapp_config_needs_both_values(monkeypatch):
    _wa_env(monkeypatch, on=False)
    assert digest.whatsapp_config() is None
    monkeypatch.setenv("CALLMEBOT_PHONE", "+15551234567")
    assert digest.whatsapp_config() is None
    monkeypatch.setenv("CALLMEBOT_APIKEY", "123456")
    assert digest.whatsapp_config() == {"phone": "+15551234567", "apikey": "123456"}


def test_render_text_is_short_and_flags_gated_calls():
    rows = [run_row(f"S{i:02d}") for i in range(20)] + [run_row("ZZZ", gate={"reason": "risk"})]
    text = digest.render_text(digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, rows))
    assert text.startswith("GlassBox · Daily Committee Digest · 2026-09-22")
    assert "S00: BUY" in text and "…and 6 more" in text and "/admin/strategy" in text
    assert "<" not in text  # plain text, no HTML
    assert len(text) < 1500


def test_run_sends_whatsapp_only_when_email_is_not_configured(monkeypatch):
    monkeypatch.setattr(digest, "gather", lambda book=None: digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, [run_row()]))
    _email_env(monkeypatch, on=False)
    _wa_env(monkeypatch)
    seen = {}
    out = digest.run(wa_runner=lambda text, cfg: seen.update(text=text, cfg=cfg))
    assert out == {"ok": True, "sent": True, "detail": "whatsapp sent"}
    assert "AAPL: BUY" in seen["text"] and seen["cfg"]["phone"] == "+15551234567"


def test_run_whatsapp_failure_does_not_stop_email_and_hides_the_key(monkeypatch):
    monkeypatch.setattr(digest, "gather", lambda book=None: digest.build_digest(overview(), FREE_STATUS, BOARD_INSUFFICIENT, []))
    _email_env(monkeypatch)
    _wa_env(monkeypatch)
    emailed = []

    def boom(text, cfg):
        raise ConnectionError(f"https://api.callmebot.com/whatsapp.php?apikey={cfg['apikey']}")

    out = digest.run(runner=lambda s, h, c: emailed.append(s), wa_runner=boom)
    assert emailed and out["ok"] is False and out["sent"] is True
    assert "sent to admin@example.com" in out["detail"] and "whatsapp: ConnectionError" in out["detail"]
    assert "123456" not in out["detail"]


def test_send_whatsapp_raises_on_a_rejected_key(monkeypatch):
    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return b"APIKey is invalid. Please check it."

    urls = []
    monkeypatch.setattr(digest.urllib.request, "urlopen", lambda url, timeout: urls.append(url) or Resp())
    with pytest.raises(ValueError):
        digest.send_whatsapp("hi there", {"phone": "+15551234567", "apikey": "123456"})
    assert urls[0].startswith("https://api.callmebot.com/whatsapp.php?") and "text=hi+there" in urls[0]
