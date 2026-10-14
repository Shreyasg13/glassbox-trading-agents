"""The LangGraph committee: fan-out, structured analyst answers, the deterministic vote,
the risk gate, degraded members, the run budget, and the CEO brief. No network: the
router is replaced by a scripted fake and the agent store by a dict."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from app import committee_daily, committee_graph, llm_router, orchestration
from app.committee_graph import AnalystView
from app.models import AgentConfig, OrchestrationConfig

ENGINE_NAMES = ["QuantEngine", "RiskEngine", "ValueEngine"]
LLM_NAMES = [f"Analyst{i}" for i in range(7)]


def agent_row(name, kind):
    if kind == "deterministic":
        return {"id": name, "name": name, "role": "engine", "type": "deterministic"}
    return {"id": name, "name": name, "role": "analyst", "type": "llm", "provider": "gemini", "model": "m", "system_prompt": f"You are {name}."}


def orch(agents=None, agent_timeout_s=5.0, run_budget_s=20.0):
    ids = ENGINE_NAMES + LLM_NAMES if agents is None else agents  # an explicit empty list means an empty committee
    return OrchestrationConfig(name="Investment Committee", mode="committee_vote", agent_ids=ids, agent_timeout_s=agent_timeout_s, run_budget_s=run_budget_s)


@pytest.fixture
def world(monkeypatch):
    """Scripted committee: engines say HOLD@50, analysts answer via `replies[name]` (a JSON dict, raw text, or an Exception)."""
    w = type("W", (), {})()
    w.replies, w.calls, w.engine = {}, [], ("HOLD", 50)
    monkeypatch.setattr(orchestration, "_LLM_LAUNCH_STAGGER_S", 0.0)
    monkeypatch.setattr(committee_graph.db, "get_agent", lambda aid: agent_row(aid, "deterministic" if aid in ENGINE_NAMES else "llm"))

    async def fake_engine(agent, ctx):
        return {"agent": agent.name, "type": "deterministic", "symbol": "AAPL", "signal": {"signal": w.engine[0], "confidence": w.engine[1]}}

    async def fake_routed(provider, model, prompt, **kw):
        name = (kw.get("system") or "").replace("You are ", "").rstrip(".")
        w.calls.append({"name": name, "prompt": prompt, "system": kw.get("system")})
        r = w.replies.get(name, {"decision": "HOLD", "confidence": 60, "risk_level": "MEDIUM", "rationale": f"{name} sees no edge."})
        if isinstance(r, Exception):
            raise r
        text = r if isinstance(r, str) else json.dumps(r)
        return llm_router.RoutedResult(text=text, provider=kw.get("_provider", "gemini"), model="m")

    monkeypatch.setattr(orchestration, "run_deterministic_agent", fake_engine)
    monkeypatch.setattr(llm_router, "complete_routed", fake_routed)
    return w


async def run(world, **kw):
    return await committee_graph.run_committee_graph(orch(**{k: kw.pop(k) for k in ("agents", "agent_timeout_s", "run_budget_s") if k in kw}), "AAPL — Apple\nData through the close of 2026-09-18.", **kw)


# ------------------------------------------------------------ the pipeline --


async def test_all_ten_members_answer_and_come_back_in_committee_order(world):
    out = await run(world)
    assert out["engine"] == "langgraph" and out["mode"] == "committee_vote"
    assert [a["agent"] for a in out["agents"]] == ENGINE_NAMES + LLM_NAMES
    assert out["committee_decision"]["decision"] == "HOLD"
    assert all("_idx" not in a for a in out["agents"])
    assert len(world.calls) == 7  # only the analysts touch a model


async def test_the_analyst_prompt_carries_the_facts_the_json_ask_and_the_analysts_own_system_prompt(world):
    await run(world)
    c = world.calls[0]
    assert "Data through the close of 2026-09-18" in c["prompt"] and "ONLY one JSON object" in c["prompt"]
    assert c["system"] == "You are Analyst0."


async def test_the_debate_is_off_by_default_and_adds_no_calls(world, monkeypatch):
    monkeypatch.delenv("COMMITTEE_DEBATE_ENABLED", raising=False)
    out = await run(world)
    assert out.get("debate") is None and len(world.calls) == 7  # unchanged: no Bull/Bear calls made


async def test_the_debate_runs_when_enabled_and_reaches_every_analyst(world, monkeypatch):
    monkeypatch.setenv("COMMITTEE_DEBATE_ENABLED", "1")

    async def fake_side(context, system, label, timeout_s, allow_failover):
        return f"{label} argues from the numbers above."

    monkeypatch.setattr(committee_graph, "_debate_side", fake_side)
    out = await run(world)
    assert out["debate"] == "Research debate (for your own reasoning, not a vote):\nBull case: Bull argues from the numbers above.\nBear case: Bear argues from the numbers above."
    assert len(world.calls) == 7  # the debate itself is faked here; every analyst still only answers once
    assert all("Research debate" in c["prompt"] and "Bull case" in c["prompt"] for c in world.calls)


async def test_the_debate_survives_one_side_failing(world, monkeypatch):
    monkeypatch.setenv("COMMITTEE_DEBATE_ENABLED", "1")

    async def flaky_side(context, system, label, timeout_s, allow_failover):
        return None if label == "Bull" else "Bear argues from the numbers above."

    monkeypatch.setattr(committee_graph, "_debate_side", flaky_side)
    out = await run(world)
    assert "Bull case: unavailable." in out["debate"] and "Bear case: Bear argues" in out["debate"]


async def test_the_debate_never_calls_a_model_for_an_engine_only_or_empty_committee(monkeypatch):
    monkeypatch.setenv("COMMITTEE_DEBATE_ENABLED", "1")
    called = []
    monkeypatch.setattr(committee_graph, "_debate_side", lambda *a, **kw: called.append(1))
    engines_only = [AgentConfig(**agent_row(n, "deterministic")) for n in ENGINE_NAMES]
    for agents_cfg in ([], engines_only):
        out = await committee_graph._debate({"agents_cfg": agents_cfg, "deadline": time.monotonic() + 60, "context": "x", "allow_failover": True})
        assert out == {"debate": None}
    assert called == []


def test_the_debate_flag_defaults_to_off(monkeypatch):
    monkeypatch.delenv("COMMITTEE_DEBATE_ENABLED", raising=False)
    assert committee_graph._debate_enabled() is False
    monkeypatch.setenv("COMMITTEE_DEBATE_ENABLED", "1")
    assert committee_graph._debate_enabled() is True


async def test_a_reflection_line_reaches_only_the_agent_it_is_about(world):
    """Reflection memory is per agent, not shared context: only Analyst0's prompt should carry its
    own line, and it must never reach a deterministic engine (which has no prompt at all)."""
    await run(world, reflections={"Analyst0": "Your own recent record (5 scored calls): 80% were right."})
    by_name = {c["name"]: c["prompt"] for c in world.calls}
    assert "Your own recent record" in by_name["Analyst0"]
    assert "Your own recent record" not in by_name["Analyst1"]


async def test_structured_answers_carry_their_own_confidence_into_the_vote(world):
    for n in LLM_NAMES[:5]:
        world.replies[n] = {"decision": "BUY", "confidence": 80, "risk_level": "LOW", "rationale": "Trend and momentum line up."}
    out = await run(world)
    cd = out["committee_decision"]
    # 5 confident BUYs (5 x 0.8) against 3 engine HOLDs (3 x 0.5) and 2 default analysts (2 x 0.6)
    assert cd["votes"]["BUY"] == pytest.approx(4.0) and cd["votes"]["HOLD"] == pytest.approx(1.5 + 1.2)
    assert cd["decision"] == "BUY"


async def test_self_reported_confidence_is_clamped_so_one_overconfident_analyst_cannot_dominate(world):
    world.replies["Analyst0"] = {"decision": "SELL", "confidence": 100, "risk_level": "HIGH", "rationale": "Certain."}
    world.replies["Analyst1"] = {"decision": "SELL", "confidence": 1, "risk_level": "HIGH", "rationale": "Unsure."}
    out = await run(world)
    row = {a["agent"]: a for a in out["agents"]}
    assert row["Analyst0"]["view"]["confidence"] == 100  # recorded as said
    weights = {t["agent"]: t["weight"] for t in out["committee_decision"]["tally"]}
    assert weights["Analyst0"] == orchestration.VIEW_WEIGHT_MAX and weights["Analyst1"] == orchestration.VIEW_WEIGHT_MIN


async def test_a_reply_that_is_not_json_still_votes_through_the_old_text_rule(world):
    world.replies["Analyst0"] = "Decision: SELL\nToo stretched here."
    world.replies["Analyst1"] = "HOLD -- I would not buy at this level."
    out = await run(world)
    rows = {a["agent"]: a for a in out["agents"]}
    assert rows["Analyst0"]["structured"] is False and "view" not in rows["Analyst0"]
    leans = {t["agent"]: (t["lean"], t["weight"]) for t in out["committee_decision"]["tally"]}
    assert leans["Analyst0"] == ("SELL", 0.5) and leans["Analyst1"] == ("HOLD", 0.5)  # earliest word wins, flat weight


async def test_a_failing_or_slow_analyst_is_degraded_and_the_rest_still_vote(world):
    world.replies["Analyst2"] = RuntimeError("HTTP 429: quota exhausted")
    out = await run(world)
    bad = next(a for a in out["agents"] if a["agent"] == "Analyst2")
    assert bad["degraded"] and "error" in bad
    assert len(out["committee_decision"]["tally"]) == 9


async def test_the_run_budget_degrades_late_starters_but_keeps_finished_results(world, monkeypatch):
    monkeypatch.setattr(orchestration, "_LLM_LAUNCH_STAGGER_S", 0.5)
    out = await run(world, run_budget_s=1.2)  # analysts 0-2 launch at 0 / 0.5 / 1.0 s; 3-6 would launch after the deadline
    late = [a for a in out["agents"] if a.get("degraded")]
    done = [a for a in out["agents"] if not a.get("degraded")]
    assert late and all("budget" in a["error"] for a in late)
    assert len(done) >= 3  # the three engine agents (and the early analysts) still count


async def test_an_agent_that_exceeds_its_own_timeout_is_degraded_not_fatal(world, monkeypatch):
    async def slow(provider, model, prompt, **kw):
        await asyncio.sleep(1.0)

    monkeypatch.setattr(llm_router, "complete_routed", slow)
    out = await run(world, agent_timeout_s=0.05)
    assert all(a.get("degraded") for a in out["agents"] if a["agent"] in LLM_NAMES)
    assert out["committee_decision"]["decision"] == "HOLD"  # the engine trio alone still produces a vote


async def test_an_empty_committee_returns_a_hold_instead_of_hanging(world):
    out = await run(world, agents=[])
    assert out["agents"] == [] and out["committee_decision"]["decision"] == "HOLD"


# ---------------------------------------------------------------- risk gate --


async def test_a_high_risk_regime_holds_back_a_buy_but_keeps_the_vote_as_recorded(world):
    for n in LLM_NAMES:
        world.replies[n] = {"decision": "BUY", "confidence": 70, "risk_level": "MEDIUM", "rationale": "Strong."}
    out = await run(world, risk={"level": "HIGH", "score": 80.0})
    cd = out["committee_decision"]
    assert cd["decision"] == "BUY" and cd["action"] == "HOLD" and "HIGH" in cd["gate"]


async def test_the_gate_never_blocks_a_sell_or_a_buy_in_a_calmer_regime(world, monkeypatch):
    for n in LLM_NAMES:
        world.replies[n] = {"decision": "SELL", "confidence": 70, "risk_level": "HIGH", "rationale": "Weak."}
    out = await run(world, risk={"level": "HIGH", "score": 90.0})
    assert out["committee_decision"]["action"] == "SELL" and out["committee_decision"]["gate"] is None
    for n in LLM_NAMES:
        world.replies[n] = {"decision": "BUY", "confidence": 70, "risk_level": "LOW", "rationale": "Good."}
    out = await run(world, risk={"level": "MEDIUM", "score": 50.0})
    assert out["committee_decision"]["action"] == "BUY"


async def test_the_gate_can_be_switched_off(world, monkeypatch):
    monkeypatch.setenv("COMMITTEE_RISK_GATE", "0")
    for n in LLM_NAMES:
        world.replies[n] = {"decision": "BUY", "confidence": 70, "risk_level": "LOW", "rationale": "Good."}
    out = await run(world, risk={"level": "HIGH", "score": 90.0})
    assert out["committee_decision"]["action"] == "BUY"


# --------------------------------------------------------------- the schema --


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"decision": "buy", "confidence": "75%", "risk_level": "low", "rationale": "x"}', ("BUY", 75, "LOW")),
        ('```json\n{"decision": "SELL", "confidence": 0.8, "risk_level": "HIGH", "rationale": "x"}\n```', ("SELL", 80, "HIGH")),
        ('{"decision": "HOLD", "confidence": 250, "rationale": "x"}', ("HOLD", 100, "MEDIUM")),
    ],
)
def test_the_parser_accepts_the_usual_ways_models_bend_the_format(raw, expected):
    v = committee_graph.PARSER.parse(raw)
    assert (v.decision, v.confidence, v.risk_level) == expected


def test_the_parser_rejects_an_answer_with_no_valid_decision():
    with pytest.raises(Exception):
        committee_graph.PARSER.parse('{"decision": "MAYBE", "confidence": 50}')
    assert AnalystView(decision="BUY", confidence=5).risk_level == "MEDIUM"


# ---------------------------------------------------------------- CEO brief --


def test_the_ceo_brief_reports_consensus_dissent_and_the_engine_vs_panel_split():
    agents = [
        {"agent": "E1", "type": "deterministic", "ok": True}, {"agent": "E2", "type": "deterministic", "ok": True},
        {"agent": "A1", "type": "llm", "ok": True}, {"agent": "A2", "type": "llm", "ok": True}, {"agent": "A3", "type": "llm", "ok": True},
    ]
    tally = [
        {"agent": "E1", "lean": "HOLD", "weight": 0.5}, {"agent": "E2", "lean": "HOLD", "weight": 0.5},
        {"agent": "A1", "lean": "BUY", "weight": 0.7}, {"agent": "A2", "lean": "BUY", "weight": 0.7}, {"agent": "A3", "lean": "HOLD", "weight": 0.4},
    ]
    votes = {"BUY": 1.4, "SELL": 0.0, "HOLD": 1.4}
    b = committee_daily.ceo_brief("BUY", "HOLD", "risk regime is HIGH", votes, agents, tally)
    assert b["label"] == "split" and b["engine_trio"] == "HOLD" and b["analyst_panel"] == "BUY" and b["trio_panel_agree"] is False
    assert b["dissenters"] == ["E1", "E2", "A3"] and b["call"] == "HOLD" and "DISAGREE" in b["headline"]
    assert b["headline"].startswith("vote BUY - split (50% of the weight), held to HOLD by the risk check")  # the % belongs to the vote, not the call
    strong = committee_daily.ceo_brief("HOLD", "HOLD", None, {"BUY": 0.0, "SELL": 0.0, "HOLD": 5.0}, agents, [{"agent": "E1", "lean": "HOLD", "weight": 1.0}])
    assert strong["label"] == "strong consensus" and strong["dissenters"] == [] and strong["consensus"] == 1.0
    assert committee_daily.ceo_brief(None, None, None, None, [], []) is None


async def test_the_stored_decision_keeps_what_the_inspector_needs(world):
    out = await run(world, risk={"level": "MEDIUM", "score": 50.0, "vol_pct": 40.0, "drawdown": -0.05, "below_ma200": False})
    book = _book()
    doc = committee_daily._run_doc(book.latest_date, {"symbol": "AAPL", "why": "test", "engine_signal": "HOLD", "engine_confidence": 50.0}, book, out, None, 12.0, 6, "THE PROMPT")
    assert doc["engine"] == "langgraph" and doc["context"] == "THE PROMPT" and doc["action"] == "HOLD" and doc["ceo"]["headline"]
    a0 = next(a for a in doc["agents"] if a["agent"] == "Analyst0")
    assert a0["confidence"] == 60 and a0["risk_level"] == "MEDIUM" and a0["structured"] is True and a0["raw"].startswith("{") and "latency_s" in a0
    assert doc["analyst_risk"] == {"LOW": 0, "MEDIUM": 7, "HIGH": 0}


def _book():
    from tests.test_committee_daily import make_book

    return make_book()


def test_analyst_view_p_beat_spy_is_optional_and_normalised():
    assert AnalystView(decision="HOLD", confidence=60).p_beat_spy is None
    assert AnalystView(decision="HOLD", confidence=60, p_beat_spy="55%").p_beat_spy == 55
    assert AnalystView(decision="HOLD", confidence=60, p_beat_spy=0.62).p_beat_spy == 62
