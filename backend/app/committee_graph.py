"""The Investment Committee as a LangGraph state graph.

    START --fan-out (one Send per agent)--> run_agent x N --> aggregate --> risk_gate --> END

* run_agent   -- one node invocation per committee member, executed concurrently. An engine
                 agent reads the deterministic signal (no model). An analyst is a LangChain
                 chain, `prompt | RoutedChatModel | PydanticOutputParser`, so its answer is a
                 validated {decision, confidence, risk_level, rationale} object instead of
                 free text scraped with a regex. If a provider ignores the JSON instruction
                 the answer still counts: it falls back to reading the BUY/SELL/HOLD word,
                 exactly as before. An analyst that errors or times out becomes a `degraded`
                 row -- one bad call never sinks the committee.
* aggregate   -- the deterministic confidence-weighted vote (orchestration._reduce_committee_vote),
                 never an LLM "judge". Results are put back in the committee's own order.
* risk_gate   -- the risk signal (app/risk.py) can hold back a BUY in a HIGH-risk regime. The
                 vote itself is kept untouched; the gated result is stored as `action`, so the
                 paper account and the scorecards can tell "what the committee thought" from
                 "what it recommended after the risk check".

Why a graph rather than another asyncio.gather: the shape is explicit and testable node by node,
LangGraph runs the fan-out concurrently and merges the branches with a reducer, and the same
structure is where a checkpointer, a human-approval interrupt (for agent re-weighting) and
streaming attach later without rewriting the pipeline.

The model calls still go through app/llm_router.py (failover, cooldowns, the LLM-call ledger)
via llm_chat.RoutedChatModel -- LangChain composes, the router delivers.

Scope: this is the DAILY committee's engine. The admin's manual Orchestrations page keeps its
existing runner (sequential / parallel / committee_vote with live job streaming).
"""
import asyncio
import operator
import os
import re
import time
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict

from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send
from pydantic import BaseModel, Field, field_validator

from . import db, orchestration
from .llm_chat import RoutedChatModel
from .models import AgentConfig, OrchestrationConfig


class AnalystView(BaseModel):
    """What one analyst tells the committee. Tolerant of the small ways models deviate
    (lower case, '75%', 75.0) so a sloppy-but-clear answer is not thrown away."""

    decision: Literal["BUY", "SELL", "HOLD"]
    confidence: int = Field(ge=0, le=100)
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "MEDIUM"
    rationale: str = ""
    # Probability (0-100) this symbol beats SPY over the next 5 trading days. Optional so an answer without it
    # still counts; it is what makes HOLD calls scorable (see docs/s3/PREREGISTRATION.md).
    p_beat_spy: Optional[int] = Field(default=None, ge=0, le=100)

    @field_validator("decision", "risk_level", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("confidence", "p_beat_spy", mode="before")
    @classmethod
    def _number(cls, v: Any) -> Any:
        if isinstance(v, str):
            m = re.search(r"\d+(?:\.\d+)?", v)
            v = float(m.group()) if m else v
        if isinstance(v, float):
            v = round(v * 100) if 0 < v <= 1 else round(v)
        return max(0, min(100, v)) if isinstance(v, int) else v


PARSER = PydanticOutputParser(pydantic_object=AnalystView)
PROMPT = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{context}\n\n{format}")])
FORMAT_INSTRUCTIONS = (
    "Reply with ONLY one JSON object -- no markdown, no text before or after -- with exactly these keys: "
    '"decision" (one of "BUY", "SELL", "HOLD"), "confidence" (an integer 0-100: how sure you are, '
    'where 50 means a coin flip), "risk_level" (one of "LOW", "MEDIUM", "HIGH": how risky holding this '
    'is right now), "p_beat_spy" (an integer 0-100: the probability that this symbol returns more than SPY over the '
    'next 5 trading days, where 50 means no edge; give it even when you choose HOLD) and "rationale" (2-3 sentences using ONLY the numbers above -- do not invent news, '
    "earnings, prices or events). If the data does not justify a change, choose HOLD."
)

class CommitteeState(TypedDict, total=False):
    context: str
    agents_cfg: List[AgentConfig]
    agent_timeout_s: float
    deadline: float  # time.monotonic() value after which no new agent work starts
    allow_failover: bool
    risk: Optional[Dict[str, Any]]
    reflections: Dict[str, str]  # agent name -> its own recent-record line, appended to its own context only
    debate: Optional[str]  # bull/bear case text, appended to every LLM agent's context (see _debate)
    agents: Annotated[List[Dict[str, Any]], operator.add]  # each run_agent branch appends its row
    results: List[Dict[str, Any]]
    committee_decision: Dict[str, Any]


# ------------------------------------------------------------- bull/bear debate --
# A native, lightweight stand-in for TradingAgents' bull/bear researchers (see docs/ARENA.md): TWO
# extra model calls per symbol on the SAME free-tier failover every analyst already uses, not the
# external framework (whose own numbers -- ~11 calls/20+ tool calls per symbol -- need a paid key).
# OFF by default (COMMITTEE_DEBATE_ENABLED=1 to turn on): it adds real LLM-call volume to a nightly
# run that only just got a rebuilt price store and pipeline, so it earns its place on the crontab
# only after running clean on its own for a while.
DEBATE_PROMPT = ChatPromptTemplate.from_messages([("system", "{system}"), ("human", "{context}\n\n{ask}")])
DEBATE_ASK = "State your case in 2-3 sentences, grounded ONLY in the numbers above -- do not invent news, earnings, prices or events."
BULL_SYSTEM = "You are the Bull researcher on an investment committee: build the strongest HONEST case FOR buying."
BEAR_SYSTEM = "You are the Bear researcher on an investment committee: build the strongest HONEST case FOR selling or staying cautious."
DEBATE_MAX_CHARS = 600
DEBATE_TIMEOUT_S = 30.0


def _debate_enabled() -> bool:
    return os.environ.get("COMMITTEE_DEBATE_ENABLED", "0") == "1"


def _debate_model() -> RoutedChatModel:
    return RoutedChatModel(
        provider=os.environ.get("COMMITTEE_DEBATE_PROVIDER", "gemini"),
        model_id=os.environ.get("COMMITTEE_DEBATE_MODEL", "gemini-flash-latest"),
        temperature=0.4,
        max_tokens=220,
    )


async def _debate_side(context: str, system: str, label: str, timeout_s: float, allow_failover: bool) -> Optional[str]:
    model = _debate_model()
    model.agent_id = f"debate-{label.lower()}"
    model.allow_failover = allow_failover
    try:
        msg = await asyncio.wait_for((DEBATE_PROMPT | model).ainvoke({"system": system, "context": context, "ask": DEBATE_ASK}), timeout=timeout_s)
        text = msg.content if isinstance(msg.content, str) else str(msg.content)
        return " ".join(text.split())[:DEBATE_MAX_CHARS] or None
    except Exception as exc:  # noqa: BLE001 -- one side failing must not lose the other or block the committee
        log.warning("debate %s side failed: %s", label, exc)
        return None


async def _debate(state: CommitteeState) -> Dict[str, Any]:
    """Runs before the analysts so its output can be folded into their shared context (see
    _fan_out). A miss on one or both sides never blocks the committee -- see _debate_side."""
    if not _debate_enabled() or not any(a.type == "llm" for a in state.get("agents_cfg", [])):
        return {"debate": None}
    remaining = max(5.0, state["deadline"] - time.monotonic())
    timeout_s = min(DEBATE_TIMEOUT_S, remaining)
    allow_failover = state.get("allow_failover", True)
    bull, bear = await asyncio.gather(
        _debate_side(state["context"], BULL_SYSTEM, "Bull", timeout_s, allow_failover),
        _debate_side(state["context"], BEAR_SYSTEM, "Bear", timeout_s, allow_failover),
    )
    if not bull and not bear:
        return {"debate": None}
    text = "Research debate (for your own reasoning, not a vote):\nBull case: " + (bull or "unavailable.") + "\nBear case: " + (bear or "unavailable.")
    return {"debate": text}


# ------------------------------------------------------------------- nodes --


def _fan_out(state: CommitteeState) -> List[Any]:
    sends: List[Any] = []
    llm_index = 0
    reflections = state.get("reflections") or {}
    debate = state.get("debate")
    for idx, agent in enumerate(state.get("agents_cfg", [])):
        delay = 0.0
        context = state["context"]
        if agent.type == "llm":  # staggered: a same-instant burst trips free-tier rate limits
            delay = llm_index * orchestration._LLM_LAUNCH_STAGGER_S
            llm_index += 1
            if debate:  # shared with every analyst, unlike a reflection line
                context = f"{context}\n\n{debate}"
            refl = reflections.get(agent.name)  # this agent's own recent track record, not shared with the others
            if refl:
                context = f"{context}\n\n{refl}"
        sends.append(
            Send(
                "run_agent",
                {
                    "idx": idx,
                    "agent": agent,
                    "context": context,
                    "delay": delay,
                    "deadline": state["deadline"],
                    "agent_timeout_s": state["agent_timeout_s"],
                    "allow_failover": state.get("allow_failover", True),
                },
            )
        )
    return sends or ["aggregate"]


async def _run_analyst(agent: AgentConfig, context: str, allow_failover: bool) -> Dict[str, Any]:
    if not agent.provider or not agent.model:
        raise ValueError(f"LLM agent '{agent.name}' is missing provider/model")
    model = RoutedChatModel(
        provider=agent.provider,
        model_id=agent.model,
        agent_id=agent.id,
        temperature=agent.params.temperature,
        top_p=agent.params.top_p,
        max_tokens=agent.params.max_tokens,
        fallback_models=list(agent.fallback_models or []),
        allow_failover=allow_failover,
    )
    msg = await (PROMPT | model).ainvoke({"system": agent.system_prompt or "", "context": context, "format": FORMAT_INSTRUCTIONS})
    text = msg.content if isinstance(msg.content, str) else str(msg.content)
    meta = msg.response_metadata or {}
    row: Dict[str, Any] = {"agent": agent.name, "type": "llm", "provider": meta.get("provider"), "model": meta.get("model"), "raw": text, "system_prompt": (agent.system_prompt or "")[:800]}
    if row["provider"] and row["provider"] != agent.provider:
        row["failed_over_from"] = agent.provider  # this answer came from a different provider
    try:
        view = PARSER.parse(text)
    except (OutputParserException, ValueError):
        row["output"] = text  # not valid JSON: the vote falls back to reading the BUY/SELL/HOLD word
        row["structured"] = False
        return row
    row["view"] = view.model_dump()
    row["output"] = f"Decision: {view.decision}\n{view.rationale}"
    row["structured"] = True
    return row


async def _run_agent(task: Dict[str, Any]) -> Dict[str, Any]:
    agent: AgentConfig = task["agent"]

    def remaining() -> float:
        return task["deadline"] - time.monotonic()

    started = time.monotonic()

    try:
        if task["delay"]:
            await asyncio.sleep(max(0.0, min(task["delay"], remaining())))
        started = time.monotonic()  # latency is the agent's own work, not its launch stagger
        budget = min(task["agent_timeout_s"], remaining())
        if budget <= 0:
            raise RuntimeError("run budget exceeded")
        if agent.type == "deterministic":
            row = await asyncio.wait_for(orchestration.run_deterministic_agent(agent, task["context"]), timeout=budget)
        else:
            row = await asyncio.wait_for(_run_analyst(agent, task["context"], task["allow_failover"]), timeout=budget)
    except asyncio.TimeoutError:
        row = {"agent": agent.name, "type": agent.type, "error": f"timed out after {min(task['agent_timeout_s'], max(0.0, remaining())):.0f}s", "degraded": True}
    except Exception as exc:  # noqa: BLE001 -- one failing member must not stop the committee
        row = {"agent": agent.name, "type": agent.type, "error": str(exc) or type(exc).__name__, "degraded": True}
    row["_idx"] = task["idx"]
    row["latency_s"] = round(time.monotonic() - started, 1)
    return {"agents": [row]}


def _aggregate(state: CommitteeState) -> Dict[str, Any]:
    rows = sorted(state.get("agents", []), key=lambda r: r.get("_idx", 0))
    for r in rows:
        r.pop("_idx", None)
    vote = orchestration._reduce_committee_vote(rows)
    risk_votes = {"LOW": 0, "MEDIUM": 0, "HIGH": 0}
    for r in rows:
        if "view" in r:
            risk_votes[r["view"]["risk_level"]] += 1
    vote["analyst_risk"] = risk_votes
    ps = [r["view"]["p_beat_spy"] for r in rows if "view" in r and r["view"].get("p_beat_spy") is not None]
    vote["p_beat_spy"] = round(sum(ps) / len(ps), 1) if ps else None
    vote["p_beat_spy_n"] = len(ps)
    return {"results": rows, "committee_decision": vote}


def _risk_gate(state: CommitteeState) -> Dict[str, Any]:
    cd = dict(state["committee_decision"])
    risk = state.get("risk") or None
    action, gate = cd["decision"], None
    if os.environ.get("COMMITTEE_RISK_GATE", "1") == "1" and risk and risk.get("level") == "HIGH" and action == "BUY":
        action, gate = "HOLD", "risk regime is HIGH: a BUY is held back rather than added"
    cd.update({"action": action, "gate": gate, "risk": risk})
    return {"committee_decision": cd}


def build_graph():
    g = StateGraph(CommitteeState)
    g.add_node("debate", _debate)
    g.add_node("run_agent", _run_agent)
    g.add_node("aggregate", _aggregate)
    g.add_node("risk_gate", _risk_gate)
    g.add_edge(START, "debate")
    g.add_conditional_edges("debate", _fan_out, ["run_agent", "aggregate"])
    g.add_edge("run_agent", "aggregate")
    g.add_edge("aggregate", "risk_gate")
    g.add_edge("risk_gate", END)
    return g.compile()


GRAPH = build_graph()


async def run_committee_graph(
    orch: OrchestrationConfig,
    context: Optional[str],
    *,
    job_id: Optional[str] = None,  # accepted for runner-interface compatibility; the daily run has no live job
    allow_failover: bool = True,
    risk: Optional[Dict[str, Any]] = None,
    reflections: Optional[Dict[str, str]] = None,  # agent name -> its own recent-record line (see committee_daily.agent_reflection)
) -> Dict[str, Any]:
    rows = [db.get_agent(aid) for aid in orch.agent_ids]
    agents = [AgentConfig(**row) for row in rows if row is not None]
    state = await GRAPH.ainvoke(
        {
            "context": context or "",
            "agents_cfg": agents,
            "agent_timeout_s": orch.agent_timeout_s,
            "deadline": time.monotonic() + orch.run_budget_s,
            "allow_failover": allow_failover,
            "risk": risk,
            "reflections": reflections or {},
            "agents": [],
        }
    )
    return {"mode": "committee_vote", "engine": "langgraph", "agents": state["results"], "committee_decision": state["committee_decision"], "debate": state.get("debate")}
