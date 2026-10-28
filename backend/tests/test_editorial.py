"""The editorial board: model picks only survive compliance; the owner publishes; the blog index uses only published picks."""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from app import editorial, studio
from app.main import app
from tests.test_studio import _hdr, studio_db  # noqa: F401 -- fixture reuse


def _llm(editor: dict, marketer: dict):
    async def call(prompt: str, system: str) -> str:
        return json.dumps(marketer if system == editorial.MARKETER else editor)
    return call


def test_template_leads_with_the_debate_and_passes_checks(studio_db):
    f = editorial.day_facts("2026-10-16")
    t = editorial.template(f)
    assert t["title"].startswith("The AI committee overruled the engine on NVDA")  # NVDA: engine BUY, committee HOLD
    assert editorial._reasons(t, f) == []


def test_marketer_pick_wins_when_it_passes(studio_db):
    f = editorial.day_facts("2026-10-16")
    out = asyncio.run(editorial.review(f, _llm(
        {"title": "NVDA: the committee said wait", "dek": "The engine said BUY. The AI room did not agree.", "tags": ["NVDA"]},
        {"title": "Why the AI room overruled the engine on NVDA", "dek": "The engine said BUY. The AI room did not agree.", "promote": 4, "channel": "linkedin", "note": "A real disagreement."})))
    assert out["source"] == "marketer" and out["title"].startswith("Why the AI room") and out["promote"] == 4 and out["tags"] == ["NVDA"]


def test_invented_number_falls_back_to_editor_then_template(studio_db):
    f = editorial.day_facts("2026-10-16")
    ok_editor = {"title": "NVDA: the committee said wait", "dek": "The engine said BUY; the AI room did not agree.", "tags": ["NVDA"]}
    bad = {"title": "NVDA up 42% after the committee said wait", "dek": "x y z", "promote": 5, "channel": "x", "note": ""}
    out = asyncio.run(editorial.review(f, _llm(ok_editor, bad)))
    assert out["source"] == "editor" and "42" in out["members"]["marketer"]["rejected"][0]
    out = asyncio.run(editorial.review(f, _llm({**ok_editor, "title": "NVDA fell 42%"}, bad)))
    assert out["source"] == "template"


def test_only_published_picks_reach_the_blog(studio_db):
    async def down(prompt: str, system: str) -> str:
        raise TimeoutError
    assert asyncio.run(editorial.run("2026-10-16", down))["source"] == "template"
    with TestClient(app) as client:
        post = client.get("/api/public/casebook").json()["posts"][0]
        assert post["date"] == "2026-10-16" and post["kicker"] == "Overrule" and post["tags"] == []
        r = client.post("/api/admin/studio/drafts/2026-10-16:editorial/status", json={"status": "published"}, headers=_hdr("admin"))
        assert r.status_code == 200
        post = client.get("/api/public/casebook").json()["posts"][0]
    assert post["tags"] == ["NVDA"] and post["title"] == studio.get("2026-10-16:editorial")["title"]
    assert asyncio.run(editorial.run("2026-10-16", down))["reason"] == "already published"


def test_editorial_run_route_is_admin_only(studio_db):
    with TestClient(app) as client:
        assert client.post("/api/admin/studio/editorial/run", json={}, headers=_hdr("viewer")).status_code == 403
