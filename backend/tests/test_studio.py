"""The Daily Studio: drafts only from the day's facts, deterministic checks gate model output, recaps need approval."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app import casebook, studio
from app.main import app
from app.migrated_tables import migrated_metadata


def _hdr(role: str) -> dict:
    from app.auth import TokenPayload, create_access_token
    return {"Authorization": "Bearer " + create_access_token(TokenPayload(sub="owner", role=role))}


def _agent(name, lean, summary, conf=0.6):
    return {"agent": name, "type": "llm", "ok": True, "lean": lean, "summary": summary, "confidence": conf}


def _run(d, sym, decision="HOLD", engine="HOLD", agents=None):
    return {"id": f"{d}:{sym}", "date": d, "symbol": sym, "why": "mover", "engine_signal": engine, "decision": decision, "action": decision,
            "votes": {"BUY": 1.0, "SELL": 1.0, "HOLD": 3.0}, "agrees_with_engine": decision == engine,
            "agents": agents or [_agent("Value Investor", decision, "Fairly valued.")], "answered": 1, "total": 1, "quorum_ok": True}


@pytest.fixture
def studio_db(real_db, monkeypatch):
    migrated_metadata.create_all(real_db.engine)
    monkeypatch.setattr(casebook, "_book", lambda: None)
    casebook._feed_cache.update({"value": None, "key": None})
    debated = _run("2026-10-16", "NVDA", "HOLD", "BUY", [_agent("Value Investor", "BUY", "Cash flow is strong."),
                                                         _agent("Risk Manager", "SELL", "Volatility is high."), _agent("Global Macro", "HOLD", "Rates unclear.")])
    for r in (_run("2026-10-12", "MSFT"), debated, _run("2026-10-16", "AAPL")):
        real_db.save_committee_run(r)
    return real_db


def _facts():
    return studio.facts_from_feed(casebook.feed())


def test_number_check_normalises_and_catches_invented_stats(studio_db):
    f = _facts()
    assert studio.unsupported_numbers("3 AI analysts, 1 BUY and 2 stocks", f) == []
    assert studio.unsupported_numbers("Up 40% this year across 12 stocks", f) == ["12", "40"]
    assert studio.unsupported_numbers(f"See {f['link']}", f) == []  # the link's date never counts


def test_template_drafts_always_pass_their_own_checks(studio_db):
    f = _facts()
    t = studio.template_social(f)
    assert studio.check(t["linkedin"], f, studio.LIMITS["linkedin"]) == []
    assert studio.check(t["caption"], f, studio.LIMITS["caption"]) == []
    for post in t["x_thread"]:
        assert studio.check(post, f, 280) == []
    assert f["link"] in t["linkedin"] and f["link"] in t["x_thread"][-1] and f["link"] in t["caption"]


def test_model_draft_with_invented_number_falls_back_to_template(studio_db):
    f = _facts()

    async def llm(prompt, system):
        assert "FACTS" in prompt and "never tell anyone to buy" in system
        return json.dumps({"linkedin": "The AI beat the market by 40% this month.", "x_thread": ["Calm day, 3 analysts.", "All public."], "caption": "Quiet day."})

    out = asyncio.run(studio.draft_social(f, llm))
    assert out["linkedin"]["source"] == "template" and "40" in out["linkedin"]["rejected"][0]
    assert out["x_thread"]["source"] == "model" and out["x_thread"]["posts"][-1].endswith(f"{f['link']}?utm_source=x&utm_medium=social&utm_campaign=casebook")
    assert out["caption"]["source"] == "model" and out["caption"]["text"] == f"Quiet day. {studio.link_for(f['link'], 'caption')}"
    assert "utm_source=linkedin" in out["linkedin"]["text"]


def test_compliance_flag_rejects_model_draft(studio_db, monkeypatch):
    f = _facts()
    monkeypatch.setattr(studio.cf, "check", lambda text, **kw: type("R", (), {"action": "flagged" if "should buy" in text else "pass", "events": [{"rule_id": "advice", "action": "flagged"}]})())

    async def llm(prompt, system):
        return json.dumps({"linkedin": "You should buy NVDA.", "x_thread": ["ok"], "caption": "ok"})

    out = asyncio.run(studio.draft_social(f, llm))
    assert out["linkedin"]["source"] == "template" and "compliance filter: flagged" in out["linkedin"]["rejected"][0]


def test_no_model_means_templates(studio_db):
    async def llm(prompt, system):
        raise TimeoutError

    out = asyncio.run(studio.draft_social(_facts(), llm))
    assert {d["source"] for d in out.values()} == {"template"}
    assert out["linkedin"]["rejected"] == ["model unavailable (TimeoutError)"]


def test_run_saves_drafts_and_friday_recap_needs_approval(studio_db):
    async def llm(prompt, system):
        raise TimeoutError

    result = asyncio.run(studio.run(llm=llm))  # 2026-10-16 is a Friday
    assert result["date"] == "2026-10-16" and result["recap"] == "template"
    ids = {d["id"]: d for d in studio.list_drafts()}
    assert {"2026-10-16:linkedin", "2026-10-16:x_thread", "2026-10-16:caption", "recap:2026-W42"} <= set(ids)
    assert ids["recap:2026-W42"]["status"] == "pending"
    assert studio.published_recaps() == []
    assert studio.set_status("2026-10-16:linkedin", "published") is None  # social drafts are never published from here

    with TestClient(app) as client:
        assert client.get("/api/admin/studio/drafts").status_code in (401, 403)
        r = client.post("/api/admin/studio/drafts/recap:2026-W42/status", json={"status": "published"}, headers=_hdr("admin"))
        assert r.status_code == 200 and r.json()["status"] == "published"
        pub = client.get("/api/public/recaps").json()["recaps"]
        assert [p["id"] for p in pub] == ["recap:2026-W42"] and pub[0]["paragraphs"]
        assert client.get("/api/public/recaps/recap:2026-W42").status_code == 200
        assert client.get("/api/public/recaps/recap:2026-W01").status_code == 404


def test_rerun_replaces_drafts_but_keeps_a_published_recap(studio_db):
    async def llm(prompt, system):
        raise TimeoutError

    asyncio.run(studio.run(llm=llm))
    studio.set_status("recap:2026-W42", "published")
    again = asyncio.run(studio.run(llm=llm))
    assert again["recap"] == "already published"
    assert studio.get("recap:2026-W42")["status"] == "published"
