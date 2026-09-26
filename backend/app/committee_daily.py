"""Daily Investment Committee review.

Runs the seeded "Investment Committee" orchestration (3 quant-engine agents + 7
AI analysts, confidence-weighted vote) on a SMALL set of symbols each trading
day, saves every decision with the evidence it was based on, and writes one
report per day to Admin -> Reports. The saved decisions are the raw material for
the "is the committee any better than the plain engine?" question (scorecard()).

WHICH SYMBOLS. Running all 15 every day would be ~105 model calls -- more than a
free tier allows and mostly noise, since the engine says HOLD on ~94% of days.
So each day it reviews:
  1. symbols where the engine's signal is BUY or SELL (highest confidence first),
  2. symbols whose signal just changed (e.g. BUY -> HOLD: an exit worth a look),
     up to COMMITTEE_MAX_SYMBOLS (default 5), then
  3. tops up to COMMITTEE_MIN_SYMBOLS (default 2) with the largest 5-day movers,
     so the committee is exercised every day and builds a track record even in
     quiet markets. Typical cost: 2-3 runs = 14-21 model calls a day.

GROUNDED, NOT GUESSING. The agents used to receive only a ticker, so the AI
analysts answered from general knowledge. Each run now gets the real numbers
(price, engine signal and confidence, RSI, moving-average cross, recent returns,
drawdown, volatility, in-sample backtest quality) and is told to use ONLY those.

IDEMPOTENT. One saved decision per (date, symbol); a re-run the same day only
does what's missing, and on a holiday (no new price bar, so the same "latest
date") it does nothing. Schedule: 22:30 UTC on weekdays, after the data sync
(22:00) and the paper-trading cycle (22:15) -- see docs/DEPLOY_GCP.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import re
import time
import uuid
from bisect import bisect_left
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from . import data_source as ds
from . import associations, claims, committee_graph, db, disclaimer, flags, free_data, narrative, orchestration, paper, paper_cycle, verification
from . import risk as risk_mod
from .models import OrchestrationConfig
from .scripts.seed_agents import ORCHESTRATION_NAME

log = logging.getLogger("glassbox.committee")

QUORUM_DEFAULT = 6  # of 10 agents must answer for a run to count as reliable


class CommitteeError(RuntimeError):
    """The daily review could not run (stale data, committee not seeded, ...)."""


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _lock_ttl_s() -> float:
    # a run can overshoot the budget by one symbol (a few minutes); past this the holder is presumed dead
    return _env_int("COMMITTEE_DAILY_BUDGET_S", 1500) + 900


def is_running() -> bool:
    """True while ANY process (another gunicorn worker, the cron job) is mid-review. The
    lock is a database row: a module flag would only ever be visible to one worker."""
    return db.committee_lock_active(_lock_ttl_s())


# --------------------------------------------------------------- selection --


def _idx(book: paper.PriceBook, sym: str, d: str) -> Optional[int]:
    dates = book._sorted_dates.get(sym) or []
    i = bisect_left(dates, d)
    return i if i < len(dates) and dates[i] == d else None


def _ret(book: paper.PriceBook, sym: str, d: str, n: int) -> Optional[float]:
    i = _idx(book, sym, d)
    if i is None or i - n < 0:
        return None
    dates = book._sorted_dates[sym]
    return book.close[sym][d] / book.close[sym][dates[i - n]] - 1


def select_candidates(
    book: paper.PriceBook, d: str, *, min_n: int = 2, max_n: int = 5, only: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    """Which symbols the committee should review on date `d`, in priority order.
    Deterministic for a given book, so a same-day re-run picks the same symbols."""
    rows = []
    for sym in book.symbols:
        i = _idx(book, sym, d)
        if i is None:  # no price bar that day (halt/holiday for this symbol)
            continue
        sig, conf = book.signal_at(sym, d)
        prev = book._sorted_dates[sym][i - 1] if i > 0 else None
        psig = book.signal_at(sym, prev)[0] if prev else sig
        move5 = _ret(book, sym, d, 5) or 0.0
        if sig != "HOLD":
            group, key, why = 0, -conf, f"engine signal {sig} ({conf:.0f}% confidence)"
        elif psig != sig:
            group, key, why = 1, -abs(move5), f"engine signal changed {psig} -> {sig}"
        else:
            group, key, why = 2, -abs(move5), f"largest 5-day mover ({move5:+.1%})"
        rows.append((group, key, sym, {"symbol": sym, "why": why, "engine_signal": sig, "engine_confidence": conf, "move_5d": move5}))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    if only:
        wanted = {s.upper() for s in only}
        picked = [r[3] for r in rows if r[2] in wanted]
        for p in picked:
            p["why"] = "requested manually"
        return picked
    min_n = min(min_n, max_n)  # the cap wins if the two are configured inconsistently
    signalled = [r[3] for r in rows if r[0] in (0, 1)][:max_n]
    fill = [r[3] for r in rows if r[0] == 2][: max(0, min_n - len(signalled))]
    return signalled + fill


# ----------------------------------------------------------------- context --


def build_context(
    sym: str, d: str, book: paper.PriceBook, live: Optional[Dict[str, Any]] = None, *, risk: Optional[Dict[str, Any]] = None, ask: bool = True, peers: Optional[str] = None, extra: Optional[List[str]] = None
) -> str:
    """The prompt every agent in the committee receives. The FIRST LINE starts with
    the ticker -- the deterministic agents read the symbol from it. `ask=False` returns
    the facts only, for the LangGraph committee which appends its own structured
    (JSON) question; `ask=True` ends with the plain-text 'Decision:' request."""
    info = ds.STOCK_INFO.get(sym, {"name": sym, "sector": "n/a", "beta": None})
    sig, conf = book.signal_at(sym, d)
    close = book.close[sym][d]
    rsi = (book.signal.get(sym, {}).get(d) or (sig, conf, float("nan")))[2]
    live = live or {}
    r1, r5, r20 = (_ret(book, sym, d, n) for n in (1, 5, 20))
    i = _idx(book, sym, d) or 0
    dates = book._sorted_dates[sym]
    window = [book.close[sym][x] for x in dates[max(0, i - 59): i + 1]]
    drawdown = close / max(window) - 1 if window else 0.0
    rets = [b / a - 1 for a, b in zip(window[-21:], window[-20:])]
    vol = (sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / max(len(rets) - 1, 1)) ** 0.5 * math.sqrt(252) if len(rets) > 2 else float("nan")
    pct = lambda v: "n/a" if v is None else f"{v:+.1%}"  # noqa: E731

    lines = [
        f"{sym} — {info['name']} ({info['sector']})",
        f"Data through the close of {d}. Last price {close:.2f}.",
        f"Quant engine signal: {sig} (confidence {conf:.0f}%). RSI {rsi:.1f}; "
        f"moving-average cross {live.get('ma_cross', 'n/a')} (fast {live.get('fast_ma', '?')}-day vs slow {live.get('slow_ma', '?')}-day)"
        + (f"; volume {live['volume_ratio']:.2f}x its average." if live.get("volume_ratio") is not None else "."),
        f"Backtest quality of this engine on {sym} (in-sample, so optimistic): Sharpe {live.get('test_sharpe', float('nan')):.2f}, "
        f"win rate {live.get('win_rate', float('nan')):.0f}%.",
        f"Recent moves: 1-day {pct(r1)}, 5-day {pct(r5)}, 20-day {pct(r20)}; {drawdown:+.1%} from its 60-day high; "
        f"20-day volatility {vol:.0%} annualised; beta {info.get('beta', 'n/a')}.",
    ]
    if risk:
        lines.append(
            f"Risk regime (rule-based, not a forecast): {risk['level']} (score {risk['score']:.0f}/100) -- volatility at the {risk['vol_pct']:.0f}th percentile "
            f"of its own history, {risk['drawdown']:+.1%} from its 252-day high, price {'below' if risk['below_ma200'] else 'above'} its 200-day average."
        )
    if peers:  # who it moves with, what they signal, and whether the whole market is moving as one (associations.py)
        lines.append(peers)
    lines += extra or []  # public fundamentals, recent SEC filings and the macro backdrop (free_data.py); empty when not available
    if ask:
        lines += [
            "",
            "Reply with your recommendation for the next 1-5 trading days. Start with EXACTLY one line: "
            "'Decision: BUY', 'Decision: SELL' or 'Decision: HOLD'. Then give 2-3 sentences of reasoning using ONLY the numbers above -- "
            "do not invent news, earnings, prices or events. If the data does not justify a change, choose HOLD and say what would change your view.",
        ]
    return "\n".join(lines)


# -------------------------------------------------------------------- runs --


def _squash(text: str, n: int) -> str:
    return " ".join((text or "").split())[:n]


def _agent_fwd_return(book: paper.PriceBook, sym: str, d: str, h: int) -> Optional[float]:
    """Same math as strategy._fwd_return, kept local: strategy.py already imports this module, so
    importing strategy here would be circular."""
    dates = book._sorted_dates.get(sym) or []
    i = bisect_left(dates, d)
    if i >= len(dates) or dates[i] != d or i + 1 + h >= len(dates):
        return None
    return book.close[sym][dates[i + 1 + h]] / book.close[sym][dates[i + 1]] - 1


def agent_reflection(agent_name: str, book: paper.PriceBook, runs: List[Dict[str, Any]], horizon: int = 5, min_scored: int = 3, lookback: int = 8) -> Optional[str]:
    """A short, factual line on this agent's OWN recent scored directional calls, fed back into its
    next prompt -- this is the safe form of 'reflection': it tells the agent a fact about itself, it
    does not touch a prompt, weight or strategy automatically (see research.py for why re-weighting
    stays a human decision). None until `min_scored` of its own calls have aged past the horizon --
    a track record of one or two calls is noise, not feedback."""
    scored: List[float] = []
    for r in sorted(runs, key=lambda r: r["date"], reverse=True):
        if len(scored) >= lookback:
            break
        if not r.get("quorum_ok"):
            continue
        row = next((a for a in r.get("agents", []) if a.get("agent") == agent_name and a.get("ok")), None)
        if not row or row.get("lean") not in ("BUY", "SELL"):
            continue
        fwd = _agent_fwd_return(book, r["symbol"], r["date"], horizon)
        if fwd is None:
            continue
        scored.append(fwd if row["lean"] == "BUY" else -fwd)
    if len(scored) < min_scored:
        return None
    hit = sum(1 for e in scored if e > 0) / len(scored)
    mean_edge = sum(scored) / len(scored)
    return (
        f"Your own recent record ({len(scored)} scored BUY/SELL calls, {horizon}-trading-day forward return): "
        f"{hit:.0%} were right, average edge {mean_edge:+.2%}. A fact for you to weigh, not an instruction to change your view."
    )


def _agent_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for a in result.get("agents", []):
        if a.get("degraded"):
            rows.append({"agent": a.get("agent"), "type": a.get("type"), "ok": False, "error": _squash(str(a.get("error", "")), 120)})
        elif "signal" in a:
            s = a["signal"]
            rows.append({"agent": a["agent"], "type": "deterministic", "ok": True, "lean": s["signal"], "summary": f"engine {s['signal']} ({s.get('confidence', 0):.0f}%)"})
        else:
            text = a.get("output", "")
            body = re.sub(r"^\s*decision\s*[:\-].*?(\n|$)", "", text, count=1, flags=re.I)
            view = a.get("view") or {}
            row = {"agent": a["agent"], "type": "llm", "ok": True, "provider": a.get("provider"), "model": a.get("model"),
                   "lean": view.get("decision") or orchestration._lean_from_text(text), "summary": _squash(body or text, 260)}
            if view:
                row.update({"confidence": view["confidence"], "risk_level": view["risk_level"]})
            row["structured"] = bool(view)
            if a.get("failed_over_from"):
                row["failed_over_from"] = a["failed_over_from"]
            if a.get("raw"):
                row["raw"] = a["raw"][:1500]
            if a.get("system_prompt"):
                row["system_prompt"] = a["system_prompt"]
            if a.get("latency_s") is not None:
                row["latency_s"] = a["latency_s"]
            rows.append(row)
    return rows


def ceo_brief(decision: Optional[str], action: Optional[str], gate: Optional[str], votes: Optional[Dict[str, float]], agents: List[Dict[str, Any]], tally: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The CEO view of one decision: how strongly the committee agreed, whether the three
    engine agents and the seven analysts saw it the same way, who dissented, and what the
    risk check did. Pure arithmetic over the stored votes -- deterministic and auditable."""
    if not decision or not votes:
        return None
    total = sum(votes.values()) or 1.0
    share = votes.get(decision, 0.0) / total
    ranked = sorted(votes.values(), reverse=True)
    margin = (ranked[0] - ranked[1]) if len(ranked) > 1 else ranked[0]
    label = "strong consensus" if share >= 0.8 else "majority" if share >= 0.6 else "split"
    types = {a["agent"]: a.get("type") for a in agents if a.get("ok")}

    def group_lean(kind: str) -> Optional[str]:
        w: Dict[str, float] = {}
        for t in tally:
            if types.get(t["agent"]) == kind:
                w[t["lean"]] = w.get(t["lean"], 0.0) + t["weight"]
        return max(w, key=w.get) if w else None

    trio, panel = group_lean("deterministic"), group_lean("llm")
    dissent = [t["agent"] for t in tally if t["lean"] != decision]
    if action and action != decision:  # the risk check overrode the vote: say so, and attribute the percentage to the VOTE
        headline = f"vote {decision} - {label} ({share:.0%} of the weight), held to {action} by the risk check"
    else:
        headline = f"{decision} - {label} ({share:.0%} of the vote weight)"
    if trio and panel:
        headline += "; engine trio and analyst panel " + ("agree" if trio == panel else f"DISAGREE (trio {trio}, panel {panel})")
    if dissent:
        headline += f"; {len(dissent)} dissent"
    return {"call": action or decision, "vote": decision, "consensus": round(share, 3), "label": label, "margin": round(margin, 2), "engine_trio": trio, "analyst_panel": panel,
            "trio_panel_agree": (trio == panel) if trio and panel else None, "dissenters": dissent, "gate": gate, "headline": headline}


def _run_doc(
    d: str, pick: Dict[str, Any], book: paper.PriceBook, result: Optional[Dict[str, Any]], error: Optional[str], seconds: float, quorum: int, context: Optional[str] = None
) -> Dict[str, Any]:
    sym = pick["symbol"]
    agents = _agent_rows(result) if result else []
    ok = [a for a in agents if a.get("ok")]
    cd = (result or {}).get("committee_decision") or {}
    decision = cd.get("decision")
    action = (cd.get("action") or decision) if len(ok) >= quorum else None  # a low-quorum call is not acted on
    return {
        "id": f"{d}:{sym}",
        "date": d,
        "symbol": sym,
        "why": pick["why"],
        "engine_signal": pick["engine_signal"],
        "engine_confidence": pick["engine_confidence"],
        "price": book.close[sym][d],
        "decision": decision,
        "action": action,
        "gate": cd.get("gate"),
        "risk": cd.get("risk"),
        "analyst_risk": cd.get("analyst_risk"),
        "votes": cd.get("votes"),
        "ceo": ceo_brief(decision, action, cd.get("gate"), cd.get("votes"), agents, cd.get("tally") or []),
        "context": context,
        "debate": (result or {}).get("debate"),
        "engine": (result or {}).get("engine", "legacy"),
        "agrees_with_engine": (decision == pick["engine_signal"]) if decision else None,
        "agents": agents,
        "answered": len(ok),
        "total": len(agents),
        "quorum_ok": len(ok) >= quorum,
        "providers": dict(Counter(a.get("provider") for a in ok if a.get("type") == "llm")),
        "error": error,
        "seconds": round(seconds, 1),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def build_report(d: str, docs: List[Dict[str, Any]]) -> str:
    lines = [
        f"Investment Committee — daily review for {d}",
        f"{len(docs)} symbol(s) reviewed. Committee = 3 quant-engine agents + 7 AI analysts, confidence-weighted vote. "
        f"{disclaimer.text()}",
        "",
    ]
    for r in docs:
        v = r.get("votes") or {}
        vote_txt = " / ".join(f"{k} {v.get(k, 0):.1f}" for k in ("BUY", "SELL", "HOLD")) if v else "no vote"
        head = f"{r.get('symbol', '?')} — committee: {r.get('decision') or 'FAILED'} ({vote_txt}) · engine: {r.get('engine_signal', '?')}"
        if r.get("agrees_with_engine") is not None:
            head += " · " + ("agrees with the engine" if r["agrees_with_engine"] else "DISAGREES with the engine")
        lines.append(head)
        lines.append(f"  why reviewed: {r.get('why', '?')}; answered {r.get('answered', 0)}/{r.get('total', 0)}" + ("" if r.get("quorum_ok") else "  ⚠ LOW QUORUM — treat as unreliable"))
        if r.get("error"):
            lines.append(f"  error: {r['error']}")
        dissent = [a for a in r.get("agents", []) if a.get("ok") and r.get("decision") and a.get("lean") != r["decision"]]
        for a in dissent[:3]:
            lines.append(f"  dissent — {a['agent']}: {a['lean']}. {a.get('summary', '')[:150]}")
        lead = next((a for a in r.get("agents", []) if a.get("type") == "llm" and a.get("ok") and a.get("lean") == r.get("decision")), None)
        if lead:
            lines.append(f"  reasoning ({lead['agent']}): {lead.get('summary', '')[:220]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _write_report(d: str, docs: List[Dict[str, Any]]) -> bool:
    # The key fingerprints the OUTCOMES: a resumed day with more decisions, or a forced re-run whose
    # results changed, gets a fresh report; an identical re-run does not create a duplicate.
    sig = "|".join(f"{r.get('symbol')}:{r.get('decision')}:{r.get('answered')}" for r in docs)
    key = f"committee:{d}:{hashlib.md5(sig.encode()).hexdigest()[:10]}"
    if any(n.get("profile") == key for n in db.list_report_narratives()):
        return False
    db.create_report_narrative(
        {
            "id": str(uuid.uuid4()),
            "date": d.replace("-", ""),
            "provider": "system",
            "model": "committee-vote",
            "title": "Investment Committee · daily review",
            "profile": key,
            "narrative": build_report(d, docs),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    return True


Runner = Callable[..., Awaitable[Dict[str, Any]]]


async def attach_claims(doc: Dict[str, Any], book: Any, run_time: str) -> None:
    """Attach structured claims and (if flag on) narrative to a saved committee decision.

    Claims (no LLM) are always stored. Narrative (LLM) only runs if pipeline.claims flag is on.
    Any exception is logged and never breaks the committee run.
    """
    run_id = doc["id"]
    sym = doc["symbol"]
    d = doc["date"]
    try:
        # Build and store claims (deterministic, no LLM)
        claim_list = claims.build_claims(run_id, sym, d, book, run_time)
        log.info("attached %d claims to %s", len(claim_list), run_id)

        # Narrative (LLM) only if flag is on
        if flags.flag("pipeline.claims"):
            nar_result = await narrative.write_narrative(run_id, doc, claim_list)
            # Store narrative row
            from .migrated_tables import committee_narratives_table
            with db.engine.begin() as conn:
                conn.execute(committee_narratives_table.delete().where(committee_narratives_table.c.run_id == run_id))  # re-runs replace
                conn.execute(
                    committee_narratives_table.insert().values(
                        run_id=run_id,
                        narrative=nar_result["narrative"],
                        status=nar_result["status"],
                        attempts=nar_result["attempts"],
                        provider_requested=nar_result["provider_requested"],
                        model_requested=nar_result["model_requested"],
                        provider_answered=nar_result["provider_answered"],
                        model_answered=nar_result["model_answered"],
                        error=nar_result["error"],
                        created_at=nar_result.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    )
                )
            log.info("narrative for %s: status=%s, attempts=%d", run_id, nar_result["status"], nar_result["attempts"])
        else:
            # Store a skipped narrative row so the admin UI knows it wasn't attempted
            from .migrated_tables import committee_narratives_table
            with db.engine.begin() as conn:
                conn.execute(committee_narratives_table.delete().where(committee_narratives_table.c.run_id == run_id))  # re-runs replace
                conn.execute(
                    committee_narratives_table.insert().values(
                        run_id=run_id,
                        narrative=None,
                        status="skipped",
                        attempts=0,
                        provider_requested=None,
                        model_requested=None,
                        provider_answered=None,
                        model_answered=None,
                        error=None,
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                )
    except Exception as exc:  # noqa: BLE001 -- never break the committee run
        log.warning("attach_claims failed for %s: %s", run_id, exc)


async def run_daily(
    *,
    symbols: Optional[List[str]] = None,
    dry_run: bool = False,
    force: bool = False,
    book: Optional[paper.PriceBook] = None,
    live_rows: Optional[Dict[str, Dict[str, Any]]] = None,
    now: Optional[datetime] = None,
    runner: Optional[Runner] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    book = book or await asyncio.to_thread(paper_cycle.load_book)
    d = book.latest_date
    if not d:
        raise CommitteeError("no price data available (is TRADING_STORAGE_PATH populated?)")
    max_age = _env_int("COMMITTEE_MAX_DATA_AGE_DAYS", 5)
    age = (now.date() - date.fromisoformat(d)).days
    if age > max_age:
        raise CommitteeError(f"price data is stale (latest {d}, {age} days old) -- did the data sync run?")

    picks = select_candidates(book, d, min_n=_env_int("COMMITTEE_MIN_SYMBOLS", 2), max_n=_env_int("COMMITTEE_MAX_SYMBOLS", 5), only=symbols)
    done = set() if force else {r["symbol"] for r in db.list_committee_runs_for_date(d)}
    todo = [p for p in picks if p["symbol"] not in done]
    base = {"date": d, "picked": [p["symbol"] for p in picks], "already_done": sorted(done & {p["symbol"] for p in picks})}
    if dry_run:
        return {**base, "dry_run": True, "would_run": [{"symbol": p["symbol"], "why": p["why"]} for p in todo], "ran": 0}
    if not todo:
        return {**base, "ran": 0, "answered_total": 0, "report_written": False, "note": "nothing new to review"}
    orch_row = next((o for o in db.list_orchestrations() if o.get("name") == ORCHESTRATION_NAME), None)
    if orch_row is None:
        raise CommitteeError(f"orchestration {ORCHESTRATION_NAME!r} is not seeded -- run app.scripts.seed_agents")
    orch = OrchestrationConfig(**orch_row)
    run = runner or committee_graph.run_committee_graph
    quorum = _env_int("COMMITTEE_QUORUM", QUORUM_DEFAULT)
    budget = _env_int("COMMITTEE_DAILY_BUDGET_S", 1500)
    live = live_rows if live_rows is not None else {r["symbol"]: r for r in (await asyncio.to_thread(ds.get_live_signals))["signals"]}
    try:
        agent_names = [row["name"] for row in (db.get_agent(aid) for aid in orch.agent_ids) if row and row.get("type") == "llm"]
        history_runs = await asyncio.to_thread(db.list_all_committee_runs)
        reflections = {name: r for name in agent_names if (r := agent_reflection(name, book, history_runs)) is not None}
    except Exception as exc:  # noqa: BLE001 -- reflection memory is a nicety; never let it block a review
        log.warning("could not compute agent reflections: %s", exc)
        reflections = {}

    owner = uuid.uuid4().hex
    if not await asyncio.to_thread(db.acquire_committee_lock, owner, _lock_ttl_s()):
        raise CommitteeError("a committee review is already running")
    started = time.monotonic()
    run_time = datetime.now(timezone.utc).isoformat()
    saved: List[Dict[str, Any]] = []
    try:
        for pick in todo:
            if saved and time.monotonic() - started > budget:
                log.warning("committee time budget (%ss) spent; %d symbol(s) left for the next run", budget, len(todo) - len(saved))
                break
            risk_now = risk_mod.risk_at(book, pick["symbol"], d)
            ctx = build_context(pick["symbol"], d, book, live.get(pick["symbol"]), risk=risk_now, ask=False, peers=associations.peer_context(book, pick["symbol"], d),
                            extra=free_data.context_lines(pick["symbol"], d, book.close[pick["symbol"]][d], run_time=run_time))
            t0 = time.monotonic()
            result: Optional[Dict[str, Any]] = None
            error: Optional[str] = None
            try:
                result = await run(orch, ctx, job_id=None, allow_failover=True, risk=risk_now, reflections=reflections)
            except Exception as exc:  # noqa: BLE001 -- one symbol failing must not stop the rest
                error = f"{type(exc).__name__}: {_squash(str(exc), 160)}"
                log.error("committee run for %s failed: %s", pick["symbol"], error)
            doc = _run_doc(d, pick, book, result, error, time.monotonic() - t0, quorum, ctx)
            db.save_committee_run(doc)
            saved.append(doc)
            log.info("committee %s %s -> %s (%d/%d answered, engine %s)", d, pick["symbol"], doc["decision"], doc["answered"], doc["total"], pick["engine_signal"])
    finally:
        await asyncio.to_thread(db.release_committee_lock, owner)

    # Attach claims (and narrative if flag is on) AFTER the committee lock is released.
    # This ensures the narrative LLM call doesn't consume the committee's time budget or hold its lock.
    for doc in saved:
        await attach_claims(doc, book, run_time)

    # Run the verification gate for each saved decision. Exceptions are logged
    # and never break the committee run. No flag needed (no LLM, no user-visible change).
    for doc in saved:
        try:
            await verification.runner.run_gate(doc["id"], run_time, book)
        except Exception as exc:  # noqa: BLE001 -- never break the committee run
            log.warning("verification gate failed for %s: %s", doc["id"], exc)

    all_today = db.list_committee_runs_for_date(d)
    reported = _write_report(d, all_today) if saved else False
    db.log_audit("system", "committee.daily_run", "committee", None, {"date": d, "ran": len(saved), "answered": sum(s["answered"] for s in saved), "report": reported})
    return {
        **base,
        "ran": len(saved),
        "answered_total": sum(s["answered"] for s in saved),
        "decisions": {s["symbol"]: s["decision"] for s in saved},
        "low_quorum": [s["symbol"] for s in saved if not s["quorum_ok"]],
        "report_written": reported,
    }


# --------------------------------------------------------- ask (sandbox) --

ASK_MAX_QUESTION = 500
ASK_STALE_S = 600  # an ask still "running" after this long is presumed dead


def new_ask_doc(symbol: str, question: str) -> Dict[str, Any]:
    return {"id": f"{db.COMMITTEE_ASK_PREFIX}{uuid.uuid4().hex[:12]}", "status": "running", "symbol": symbol, "question": question, "created_at": datetime.now(timezone.utc).isoformat()}


def ask_in_flight() -> bool:
    """One sandbox ask at a time: each is ~7 model calls against a free-tier quota."""
    now = datetime.now(timezone.utc)
    for a in db.list_committee_asks(5):
        if a.get("status") == "running" and (now - datetime.fromisoformat(a["created_at"])).total_seconds() < ASK_STALE_S:
            return True
    return False


async def run_ask(
    doc: Dict[str, Any], *, book: Optional[paper.PriceBook] = None, live_rows: Optional[Dict[str, Dict[str, Any]]] = None, runner: Optional[Runner] = None
) -> Dict[str, Any]:
    """Put a question about one symbol to the full committee, right now, and store the whole
    exchange (prompt, every agent's raw answer, the vote) for the admin's inspector. This is a
    sandbox: the result is never a committee decision, never feeds the paper account, the
    scorecards or the report -- it lives under a separate id prefix the decision queries skip."""
    sym = doc["symbol"]
    try:
        book = book or await asyncio.to_thread(paper_cycle.load_book)
        d = book.latest_date
        if not d or sym not in book.close or d not in book.close[sym]:
            raise CommitteeError(f"no current price data for {sym}")
        orch_row = next((o for o in db.list_orchestrations() if o.get("name") == ORCHESTRATION_NAME), None)
        if orch_row is None:
            raise CommitteeError(f"orchestration {ORCHESTRATION_NAME!r} is not seeded")
        live = live_rows if live_rows is not None else {r["symbol"]: r for r in (await asyncio.to_thread(ds.get_live_signals))["signals"]}
        sig, conf = book.signal_at(sym, d)
        risk_now = risk_mod.risk_at(book, sym, d)
        run_time = datetime.now(timezone.utc).isoformat()
        ctx = build_context(sym, d, book, live.get(sym), risk=risk_now, ask=False, peers=associations.peer_context(book, sym, d), extra=free_data.context_lines(sym, d, book.close[sym][d], run_time=run_time))
        question = " ".join((doc.get("question") or "").split())[:ASK_MAX_QUESTION]
        if question:
            ctx += f"\n\nA specific question from the committee chair -- answer it in your rationale: {question}"
        t0 = time.monotonic()
        result = await (runner or committee_graph.run_committee_graph)(OrchestrationConfig(**orch_row), ctx, job_id=None, allow_failover=True, risk=risk_now)
        pick = {"symbol": sym, "why": "asked by the admin", "engine_signal": sig, "engine_confidence": conf}
        out = _run_doc(d, pick, book, result, None, time.monotonic() - t0, _env_int("COMMITTEE_QUORUM", QUORUM_DEFAULT), ctx)
        out.update({"id": doc["id"], "status": "done", "question": question, "created_at": doc["created_at"], "prompt": ctx + "\n\n" + committee_graph.FORMAT_INSTRUCTIONS})
    except Exception as exc:  # noqa: BLE001 -- the caller polls this document, so record the failure instead of raising
        log.error("committee ask for %s failed: %s", sym, exc)
        out = {**doc, "status": "error", "error": f"{type(exc).__name__}: {_squash(str(exc), 200)}"}
    db.save_committee_ask(out)
    return out


# ---------------------------------------------------------------- scorecard --


def committee_scorecard(book: paper.PriceBook, runs: List[Dict[str, Any]], horizons: Tuple[int, ...] = (1, 5, 20)) -> Dict[str, Any]:
    """How did the committee's calls do? Forward return from the NEXT close after the
    decision (how the paper trader would act), by decision, next to how often the
    committee simply echoed the engine. Sample sizes are tiny at first -- read n."""
    stats: Dict[str, Dict[int, List[float]]] = {s: {h: [] for h in horizons} for s in ("BUY", "SELL", "HOLD")}
    valid = [r for r in runs if r.get("decision") in stats and r.get("quorum_ok")]
    for r in valid:
        sym, dates = r["symbol"], book._sorted_dates.get(r["symbol"]) or []
        i = bisect_left(dates, r["date"])
        for h in horizons:
            if i < len(dates) and dates[i] == r["date"] and i + 1 + h < len(dates):
                stats[r["decision"]][h].append(book.close[sym][dates[i + 1 + h]] / book.close[sym][dates[i + 1]] - 1)

    def agg(vals: List[float], decision: str) -> Dict[str, Any]:
        if not vals:
            return {"n": 0, "mean_return": None, "hit_rate": None}
        hit = sum(1 for v in vals if (v > 0 if decision != "SELL" else v < 0)) / len(vals)
        return {"n": len(vals), "mean_return": sum(vals) / len(vals), "hit_rate": hit}

    agree = [r for r in valid if r.get("agrees_with_engine") is not None]
    return {
        "horizons": list(horizons),
        "runs": len(runs),
        "reliable_runs": len(valid),
        "agrees_with_engine": (sum(1 for r in agree if r["agrees_with_engine"]) / len(agree)) if agree else None,
        "by_decision": {dec: {str(h): agg(stats[dec][h], dec) for h in horizons} for dec in stats},
        "note": "Forward returns start from the next close after the decision; runs too recent to have one are not counted yet.",
    }
