"""The public Committee Casebook (blog): labelled analyst reasoning, no internals, compliance-filtered."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import casebook, db
from app.main import app


def _run(d: str, sym: str, decision: str = "BUY", engine: str = "BUY", summary: str = "Margins are widening and cash flow covers the dividend.") -> dict:
    return {
        "id": f"{d}:{sym}", "date": d, "symbol": sym, "why": "engine BUY", "engine_signal": engine, "engine_confidence": 70,
        "price": 100.0, "decision": decision, "action": decision, "votes": {"BUY": 4.2, "SELL": 1.1, "HOLD": 0.7},
        "ceo": {"headline": f"{decision} - clear majority"}, "agrees_with_engine": decision == engine,
        "agents": [
            {"agent": "Value Investor", "type": "llm", "ok": True, "provider": "groq", "model": "llama-x", "lean": decision, "summary": summary, "confidence": 0.7},
            {"agent": "Risk Manager", "type": "llm", "ok": False, "error": "quota exhausted for key sk-secret"},
        ],
        "answered": 1, "total": 2, "quorum_ok": False, "providers": {"groq": 1}, "error": None, "seconds": 12.3,
    }


def _seed(*runs):
    for r in runs:
        db.save_committee_run(r)


def test_index_lists_days_newest_first(real_db, monkeypatch):
    _seed(_run("2026-10-14", "NVDA"), _run("2026-10-14", "AAPL", decision="SELL"), _run("2026-10-15", "MSFT"))
    with TestClient(app) as client:
        r = client.get("/api/public/casebook")
    assert r.status_code == 200
    posts = r.json()["posts"]
    assert [p["date"] for p in posts] == ["2026-10-15", "2026-10-14"]
    assert posts[1]["symbols"] == ["AAPL", "NVDA"]
    assert posts[1]["disagreements"] == ["AAPL"]


def test_post_has_labelled_reasoning_and_no_internals(real_db, monkeypatch):
    monkeypatch.setattr(casebook, "_book", lambda: None)
    _seed(_run("2026-10-14", "NVDA"))
    with TestClient(app) as client:
        r = client.get("/api/public/casebook/2026-10-14")
    assert r.status_code == 200
    case = r.json()["cases"][0]
    assert case["analysts"] == [{"agent": "Value Investor", "type": "llm", "lean": "BUY",
                                 "summary": "Margins are widening and cash flow covers the dividend.", "confidence": 0.7}]
    assert case["did_not_answer"] == 1 and case["outcome"] is None
    for leak in ("groq", "llama-x", "sk-secret", "provider", "seconds"):
        assert leak not in r.text


def test_compliance_flagged_summary_is_withheld(real_db, monkeypatch):
    monkeypatch.setattr(casebook, "_book", lambda: None)
    monkeypatch.setattr(casebook.cf, "check", lambda text, **kw: type("R", (), {"action": "blocked"})())
    _seed(_run("2026-10-14", "NVDA", summary="You should buy NVDA now, guaranteed returns."))
    with TestClient(app) as client:
        case = client.get("/api/public/casebook/2026-10-14").json()["cases"][0]
    assert case["analysts"][0]["summary"] is None and case["withheld"] == 1
    assert "guaranteed" not in str(case)


def test_unknown_day_404_and_bad_date_422(real_db):
    with TestClient(app) as client:
        assert client.get("/api/public/casebook/2026-01-01").status_code == 404
        assert client.get("/api/public/casebook/not-a-date").status_code == 422


def test_outcome_scores_call_against_spy():
    class Book:
        _sorted_dates = {s: ["d0", "d1", "d2", "d3", "d4", "d5", "d6", "d7"] for s in ("NVDA", "SPY")}
        close = {"NVDA": {f"d{i}": 100 + 2 * i for i in range(8)}, "SPY": {f"d{i}": 100.0 for i in range(8)}}

    out = casebook._outcome(Book(), "NVDA", "d0", "SELL")
    assert out["return"] > 0 and out["spy_return"] == 0 and out["right"] is False
    assert casebook._outcome(Book(), "NVDA", "d3", "BUY") is None  # not 5 trading days old yet


def test_filter_failure_withholds_instead_of_500(real_db, monkeypatch):
    def boom(text, **kw):
        raise FileNotFoundError("compliance_rules.json")
    monkeypatch.setattr(casebook, "_book", lambda: None)
    monkeypatch.setattr(casebook.cf, "check", boom)
    _seed(_run("2026-10-14", "NVDA"))
    with TestClient(app) as client:
        r = client.get("/api/public/casebook/2026-10-14")
    assert r.status_code == 200
    assert r.json()["cases"][0]["analysts"][0]["summary"] is None


def test_backend_image_ships_config():
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY config ./config" in dockerfile


def test_truncated_summary_ends_on_a_word():
    cut = ("word " * 51)[: casebook.SUMMARY_CAP - 5] + "signi"
    assert len(cut) == casebook.SUMMARY_CAP
    out = casebook._tidy(cut)
    assert out.endswith("word…") and "signi" not in out
    assert casebook._tidy("short and complete.") == "short and complete."


def _agent(name, lean, summary, conf=0.6):
    return {"agent": name, "type": "llm", "ok": True, "provider": "groq", "lean": lean, "summary": summary, "confidence": conf}


def test_feed_builds_typed_cards_from_recent_days(real_db, monkeypatch):
    monkeypatch.setattr(casebook, "_book", lambda: None)
    casebook._feed_cache.update({"value": None, "key": None})
    debated = _run("2026-10-15", "NVDA", decision="HOLD", engine="BUY")
    debated["agents"] = [_agent("Value Investor", "BUY", "Cash flow is strong."), _agent("Risk Manager", "SELL", "Volatility is high."),
                         _agent("Global Macro", "HOLD", "Rates are uncertain.")]
    calm = _run("2026-10-15", "AAPL", decision="HOLD", engine="HOLD")
    calm["agents"] = [_agent("Value Investor", "HOLD", "Fairly valued.", 0.9)]
    _seed(_run("2026-10-14", "MSFT"), debated, calm)
    with TestClient(app) as client:
        r = client.get("/api/public/casebook/feed")
    assert r.status_code == 200
    body = r.json()
    cards = {c["type"]: c for c in body["cards"]}
    assert body["latest"] == "2026-10-15"
    assert cards["day"]["headline"] == "3 AI analysts looked at 2 stocks. On every single one, their verdict was: wait."
    assert cards["debate"]["symbol"] == "NVDA" and cards["debate"]["split"] == {"BUY": 1, "SELL": 1, "HOLD": 1}
    assert cards["overrule"]["symbol"] == "NVDA" and cards["overrule"]["engine"] == "BUY"
    assert cards["quote"]["symbol"] == "AAPL"  # never repeats a stock already featured
    assert cards["scoreboard"]["days"] == 2 and cards["scoreboard"]["calls"] == 3
    assert "groq" not in r.text


def test_feed_empty_without_runs(real_db):
    casebook._feed_cache.update({"value": None, "key": None})
    with TestClient(app) as client:
        assert client.get("/api/public/casebook/feed").json()["cards"] == []
