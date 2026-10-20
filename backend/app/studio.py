"""The Daily Studio: turns each trading day's Committee Casebook into share-ready drafts.

Runs right after the daily committee (app/scripts/run_committee_daily.py). For the latest casebook
day it writes three drafts -- a LinkedIn post, an X thread and a short caption -- and, on the week's
last trading day, a weekly recap article. Nothing here posts anywhere: social drafts wait on the
admin Studio page for the owner to copy and post by hand, and a recap reaches /blog only once the
owner approves it.

Every draft is built from a FACTS dict taken from the casebook feed, and must pass two checks that
do not depend on a model's judgement:
  1. every number in the text appears in FACTS (so a model cannot invent a statistic), and
  2. the A7 compliance filter does not flag or block it.
A model draft that fails either check is replaced by a deterministic template draft, which passes
by construction; the rejection reasons are kept on the draft so the owner can see what happened.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import select

from . import casebook, db, llm_router
from .compliance import filter as cf
from .migrated_tables import studio_drafts_table

log = logging.getLogger("glassbox.studio")

SITE = os.environ.get("PUBLIC_SITE_URL", "https://glassbox-portfolio-review.duckdns.org").rstrip("/")
SOCIAL = ("linkedin", "x_thread", "caption")
LLM_CHAIN = ["gemini-3.1-flash-lite", "gemini-flash-latest"]
LLM_TIMEOUT_S = 60
LIMITS = {"linkedin": 1300, "x_post": 280 - 24, "caption": 220}  # x_post leaves room for the link (23 chars + space)
EVIDENCE_DAYS = 60  # the live-evidence bar the site states everywhere

Llm = Callable[[str, str], Awaitable[str]]  # (prompt, system) -> text

_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?")
_URL = re.compile(r"https?://\S+")
UTM_SOURCE = {"linkedin": "linkedin", "x_thread": "x", "caption": "other"}  # matches the platforms on Admin -> Traction


def link_for(link: str, channel: str) -> str:
    """The casebook link tagged so Admin -> Traction can tell which post brought a visitor."""
    return f"{link}?utm_source={UTM_SOURCE[channel]}&utm_medium=social&utm_campaign=casebook"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pct(x: Optional[float]) -> Optional[str]:
    return None if x is None else f"{'+' if x >= 0 else ''}{x * 100:.1f}%"


# ------------------------------------------------------------------ facts --

def facts_from_feed(feed: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The only material a draft may draw on, flattened from the casebook feed's cards."""
    cards = {c["type"]: c for c in feed.get("cards", [])}
    day = cards.get("day")
    if not day:
        return None
    d = day["date"]
    decisions = [v for v in day["decisions"].values() if v]
    f: Dict[str, Any] = {
        "date": d,
        "link": f"{SITE}/blog/{d}",
        "headline": day["headline"],
        "stocks_reviewed": day["stocks"],
        "final_calls": {k: decisions.count(k) for k in ("BUY", "SELL", "HOLD")},
        "ai_opinions": day.get("opinions"),
        "overruled_engine_on": day["overruled"],
        "evidence_bar_days": EVIDENCE_DAYS,
        "horizon_days": casebook.HORIZON,
    }
    if (c := cards.get("debate")):
        f["most_debated"] = {"date": c["date"], "symbol": c["symbol"], "split": c["split"], "final_call": c["decision"],
                             "quotes": [{"agent": q["agent"], "lean": q["lean"], "text": q["text"]} for q in c["quotes"]]}
    if (c := cards.get("overrule")):
        o = c.get("outcome") or {}
        f["latest_overrule"] = {"date": c["date"], "symbol": c["symbol"], "engine": c["engine"], "committee": c["decision"],
                                "result_vs_spy": _pct(o.get("excess")), "right": o.get("right")}
    if (c := cards.get("verdict")):
        f["five_day_verdict"] = {"date": c["date"], "best": {"symbol": c["best"]["symbol"], "vs_spy": _pct(c["best"]["excess"])},
                                 "worst": {"symbol": c["worst"]["symbol"], "vs_spy": _pct(c["worst"]["excess"])},
                                 "buy_sell_calls_judged": c["judged"], "right": c["right"]}
    if (c := cards.get("scoreboard")):
        f["scoreboard"] = {"trading_days": c["days"], "public_calls": c["calls"],
                           "hold_share": None if c["hold_share"] is None else f"{round(c['hold_share'] * 100)}%",
                           "buy_sell_calls_scored": c["scored"], "right": c["right"]}
    return f


def _norm(n: str) -> str:
    return str(float(n)).rstrip("0").rstrip(".") if "." in n else str(int(n))


def allowed_numbers(facts: Dict[str, Any]) -> set:
    """Every number that appears anywhere in FACTS (the link and quotes included), normalised."""
    return {_norm(n) for n in _NUM.findall(json.dumps(facts))}


def unsupported_numbers(text: str, facts: Dict[str, Any]) -> List[str]:
    ok = allowed_numbers(facts)
    body = _URL.sub("", text)
    return sorted({n for n in _NUM.findall(body) if _norm(n) not in ok})


def check(text: str, facts: Dict[str, Any], limit: int) -> List[str]:
    """Reasons this text may not be published (empty = passes)."""
    reasons = []
    if not text.strip():
        return ["empty"]
    if (bad := unsupported_numbers(text, facts)):
        reasons.append(f"numbers not in the day's data: {', '.join(bad)}")
    symbols = [facts.get("most_debated", {}).get("symbol"), facts.get("latest_overrule", {}).get("symbol")]
    try:
        res = cf.check(text, channel="social", tickers=[s for s in symbols if s])
        if res.action in ("flagged", "blocked"):
            reasons.append(f"compliance filter: {res.action} ({', '.join(e['rule_id'] for e in res.events if e['action'] in ('flagged', 'blocked'))})")
    except Exception:  # noqa: BLE001 -- fail closed: no filter, no publish
        reasons.append("compliance filter unavailable")
    if len(text) > limit:
        reasons.append(f"too long ({len(text)} > {limit} chars)")
    return reasons


# -------------------------------------------------------------- templates --
# Deterministic drafts built only from FACTS. They are the fallback when no model is available or a
# model draft fails a check, so they must always pass check() themselves (see tests).

def _hook(f: Dict[str, Any]) -> str:
    return f["headline"]


def template_social(f: Dict[str, Any]) -> Dict[str, Any]:
    o = f.get("ai_opinions") or {}
    lines_li = [_hook(f), ""]
    if o:
        lines_li.append(f"Behind that: {o.get('BUY', 0)} individual BUY opinions, {o.get('SELL', 0)} SELL and {o.get('HOLD', 0)} HOLD.")
    if (m := f.get("most_debated")):
        s = m["split"]
        lines_li.append(f"The most debated stock was {m['symbol']}: {s['BUY']} BUY, {s['SELL']} SELL, {s['HOLD']} HOLD.")
    if (v := f.get("five_day_verdict")):
        lines_li.append(f"From an earlier review, {f['horizon_days']} trading days on: {v['best']['symbol']} did best at {v['best']['vs_spy']} vs SPY, {v['worst']['symbol']} worst at {v['worst']['vs_spy']}.")
    lines_li += ["", "Every call is published before the outcome and scored against SPY afterwards, wrong ones included. "
                 "Simulated research, not investment advice.", "", f["link"]]
    thread = [_hook(f)]
    if (m := f.get("most_debated")):
        s = m["split"]
        thread.append(f"Most debated: {m['symbol']}. {s['BUY']} BUY, {s['SELL']} SELL, {s['HOLD']} HOLD. The arguments are all public.")
    if (r := f.get("latest_overrule")):
        tail = f" Result: {r['result_vs_spy']} vs SPY." if r.get("result_vs_spy") else " Result pending."
        thread.append(f"The AI overruled the plain rule on {r['symbol']}: engine {r['engine']}, committee {r['committee']}.{tail}")
    thread.append(f"Every call is scored against SPY, wrong ones included. Simulated research, not advice. {f['link']}")
    caption = f"{_hook(f)} Every analyst's argument, public: {f['link']}"
    if len(caption) > LIMITS["caption"]:
        caption = f"Every AI analyst's argument today, public and scored against SPY: {f['link']}"
    return {"linkedin": "\n".join(lines_li), "x_thread": thread, "caption": caption}


# ------------------------------------------------------------------- model --

SYSTEM = (
    "You write social posts for GlassBox Research, an independent, non-commercial research project where AI analysts "
    "argue over stocks daily and every call is published and scored against SPY. Voice: curious, plain-English, human, "
    "honest about uncertainty; no hype, no emojis beyond one at most, no hashtags spam (two at most). Hard rules: use ONLY "
    "numbers that appear in FACTS (do not compute new ones, do not number thread posts); never tell anyone to buy, sell or "
    "hold anything, never predict prices; never use the words recommendation, recommend, signal, opportunity, should or "
    "must; call them the committee's calls. Never state anything FACTS does not say; if a field is missing or null, leave "
    "that topic out entirely. Say it is simulated research. Do not include any URL (it is added for you). "
    "Shape: the first line is a hook a busy reader would stop for (a surprising fact from FACTS, stated plainly); short "
    "paragraphs; name one concrete disagreement between analysts with their reasoning; end the LinkedIn post with one "
    "honest question that invites discussion. Reply with JSON only: "
    '{"linkedin": "<=1100 characters", "x_thread": ["3 to 5 posts, each <=250 characters"], "caption": "<=180 characters"}'
)


async def _default_llm(prompt: str, system: str) -> str:
    routed = await asyncio.wait_for(
        llm_router.complete_routed("gemini", LLM_CHAIN[0], prompt, system=system, fallback_models=LLM_CHAIN[1:], max_tokens=900, temperature=0.6),
        LLM_TIMEOUT_S,
    )
    return routed.text or ""


def _parse_json(text: str) -> Dict[str, Any]:
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0)) if m else {}


async def draft_social(f: Dict[str, Any], llm: Optional[Llm] = None) -> Dict[str, Dict[str, Any]]:
    """One draft per channel: the model's version if it passes every check, otherwise the template's."""
    tmpl = template_social(f)
    model: Dict[str, Any] = {}
    model_error = None
    try:
        model = _parse_json(await (llm or _default_llm)(f"FACTS:\n{json.dumps(f, indent=1)}", SYSTEM))
    except Exception as exc:  # noqa: BLE001 -- no model today: templates only
        model_error = f"model unavailable ({type(exc).__name__})"
    out: Dict[str, Dict[str, Any]] = {}
    for ch in SOCIAL:
        cand = model.get(ch)
        if ch == "x_thread":
            posts = [p.strip() for p in cand if isinstance(p, str) and p.strip()] if isinstance(cand, list) else []
            if posts:
                posts[-1] = f"{posts[-1]} {f['link']}"
            reasons = [r for i, p in enumerate(posts) for r in (f"post {i + 1}: {x}" for x in check(p, f, LIMITS["x_post"] + 24))] if posts else ["no draft"]
            if posts and not reasons:
                out[ch] = {"source": "model", "posts": posts, "rejected": []}
            else:
                out[ch] = {"source": "template", "posts": tmpl[ch], "rejected": [model_error] if model_error else reasons}
            continue
        text = cand.strip() if isinstance(cand, str) else ""
        if text and ch == "linkedin":
            text = f"{text}\n\n{f['link']}"
        elif text and ch == "caption":
            text = f"{text} {f['link']}"
        reasons = check(text, f, LIMITS[ch] + 30) if text else ["no draft"]
        if text and not reasons:
            out[ch] = {"source": "model", "text": text, "rejected": []}
        else:
            out[ch] = {"source": "template", "text": tmpl[ch], "rejected": [model_error] if model_error else reasons}
    for ch, d in out.items():
        tagged = link_for(f["link"], ch)
        if "posts" in d:
            d["posts"] = [p.replace(f["link"], tagged) for p in d["posts"]]
        else:
            d["text"] = d["text"].replace(f["link"], tagged)
    return out


# ------------------------------------------------------------------ recap --

def _week_key(d: str) -> str:
    y, w, _ = date_cls.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def recap_facts(end: str) -> Optional[Dict[str, Any]]:
    """The week (Mon..end) as facts: per-day headlines, overrules, the week's most debated stock, verdicts, scoreboard."""
    end_d = date_cls.fromisoformat(end)
    start = (end_d - timedelta(days=end_d.weekday())).isoformat()
    days = sorted(d for d in casebook._by_date() if start <= d <= end)
    if not days:
        return None
    week: List[Dict[str, Any]] = []
    for d in days:
        p = casebook.post(d)
        if not p:
            continue
        cases = p["cases"]
        week.append({"date": d, "headline": casebook._day_headline(cases),
                     "calls": {k: sum(1 for c in cases if c["decision"] == k) for k in ("BUY", "SELL", "HOLD")},
                     "overruled_engine_on": [c["symbol"] for c in cases if c["agrees_with_engine"] is False],
                     "best_vs_spy": max(((c["symbol"], _pct(c["outcome"]["excess"])) for c in cases if c["outcome"] and c["outcome"]["excess"] is not None), key=lambda t: float(t[1].rstrip("%")), default=None),
                     "worst_vs_spy": min(((c["symbol"], _pct(c["outcome"]["excess"])) for c in cases if c["outcome"] and c["outcome"]["excess"] is not None), key=lambda t: float(t[1].rstrip("%")), default=None)})
    sb = next((c for c in casebook.feed()["cards"] if c["type"] == "scoreboard"), None)
    return {"week": _week_key(end), "week_number": date_cls.fromisoformat(end).isocalendar()[1], "from": start, "to": end, "link": f"{SITE}/blog", "days": week,
            "scoreboard": None if not sb else {"trading_days": sb["days"], "public_calls": sb["calls"], "buy_sell_calls_scored": sb["scored"], "right": sb["right"]},
            "evidence_bar_days": EVIDENCE_DAYS}


def template_recap(f: Dict[str, Any]) -> Dict[str, Any]:
    n = len(f["days"])
    paras = [f"This week the GlassBox committee met on {n} trading day{'' if n == 1 else 's'}. Here is what it argued, and how its earlier calls played out."]
    for d in f["days"]:
        line = f"{d['date']}: {d['headline']}"
        if d["overruled_engine_on"]:
            line += f" It overruled the plain engine on {', '.join(d['overruled_engine_on'])}."
        if d["best_vs_spy"]:
            line += f" Five trading days on, the best stock was {d['best_vs_spy'][0]} ({d['best_vs_spy'][1]} vs SPY) and the worst {d['worst_vs_spy'][0]} ({d['worst_vs_spy'][1]})."
        paras.append(line)
    if (sb := f.get("scoreboard")):
        paras.append(f"Running total: {sb['public_calls']} public calls over {sb['trading_days']} trading days; "
                     f"{sb['right']} of {sb['buy_sell_calls_scored']} scored BUY/SELL calls were right.")
    paras.append(f"That is still far too little to call skill: the bar is {f['evidence_bar_days']} live trading days against simple rules and SPY. "
                 "This is simulated research on a fixed list of stocks and funds, not investment advice.")
    return {"title": f"The week in the Casebook: {f['from']} to {f['to']}", "paragraphs": paras}


RECAP_SYSTEM = (
    "You write the weekly recap article for GlassBox Research's public blog. Same hard rules as always: use ONLY numbers in "
    "FACTS, never advise or predict, never use the words recommendation, recommend, signal, opportunity, should or must "
    "(say 'call'), never state anything FACTS does not say (a missing or null field means: leave that topic out), say it is "
    "simulated research, no URLs. Warm, plain, specific, like a thoughtful analyst's weekly note; "
    "a title that says what happened, then 4 to 7 short paragraphs. "
    'Reply with JSON only: {"title": "...", "paragraphs": ["...", "..."]}'
)


async def draft_recap(f: Dict[str, Any], llm: Optional[Llm] = None) -> Dict[str, Any]:
    tmpl = template_recap(f)
    try:
        cand = _parse_json(await (llm or _default_llm)(f"FACTS:\n{json.dumps(f, indent=1)}", RECAP_SYSTEM))
        title, paras = cand.get("title", ""), [p for p in cand.get("paragraphs", []) if isinstance(p, str) and p.strip()]
        reasons = check("\n\n".join([title, *paras]), f, 6000) if title and paras else ["no draft"]
    except Exception as exc:  # noqa: BLE001
        title, paras, reasons = "", [], [f"model unavailable ({type(exc).__name__})"]
    if not reasons:
        return {"source": "model", "title": title, "paragraphs": paras, "rejected": []}
    return {"source": "template", **tmpl, "rejected": reasons}


# ---------------------------------------------------------------- storage --

def _save(item_id: str, kind: str, d: str, status: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    row = {"id": item_id, "kind": kind, "date": d, "status": status, "payload_json": json.dumps(payload), "updated_at": now}
    with db.engine.begin() as conn:
        exists = conn.execute(select(studio_drafts_table.c.id).where(studio_drafts_table.c.id == item_id)).first()
        if exists:
            conn.execute(studio_drafts_table.update().where(studio_drafts_table.c.id == item_id).values(**row))
        else:
            conn.execute(studio_drafts_table.insert().values(**row, created_at=now))
    return get(item_id)


def _row(r: Any) -> Dict[str, Any]:
    return {"id": r.id, "kind": r.kind, "date": r.date, "status": r.status, "created_at": r.created_at, "updated_at": r.updated_at, **json.loads(r.payload_json)}


def get(item_id: str) -> Optional[Dict[str, Any]]:
    with db.engine.connect() as conn:
        r = conn.execute(select(studio_drafts_table).where(studio_drafts_table.c.id == item_id)).first()
    return _row(r) if r else None


def list_drafts(limit: int = 60, kind: Optional[str] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
    q = select(studio_drafts_table).order_by(studio_drafts_table.c.date.desc(), studio_drafts_table.c.id)
    if kind:
        q = q.where(studio_drafts_table.c.kind == kind)
    if status:
        q = q.where(studio_drafts_table.c.status == status)
    with db.engine.connect() as conn:
        return [_row(r) for r in conn.execute(q.limit(limit))]


def set_status(item_id: str, status: str) -> Optional[Dict[str, Any]]:
    """Only a recap moves through review (pending -> published | rejected); social drafts are never published from here."""
    item = get(item_id)
    if not item or item["kind"] != "recap" or status not in ("published", "rejected", "pending"):
        return None
    payload = {k: v for k, v in item.items() if k not in ("id", "kind", "date", "status", "created_at", "updated_at")}
    if status == "published":
        payload["published_at"] = _now()
    return _save(item_id, "recap", item["date"], status, payload)


def published_recaps() -> List[Dict[str, Any]]:
    return [{k: r[k] for k in ("id", "date", "title", "paragraphs", "published_at") if k in r} for r in list_drafts(52, kind="recap", status="published")]


# -------------------------------------------------------------------- run --

def _is_friday(d: str) -> bool:
    """Recaps are written on Fridays; a holiday Friday is covered by the admin "write recap now" button."""
    return date_cls.fromisoformat(d).weekday() == 4


async def run(force_recap: bool = False, llm: Optional[Llm] = None) -> Dict[str, Any]:
    """Write today's drafts (idempotent: re-running replaces them) and, on Fridays, the weekly recap."""
    f = facts_from_feed(casebook.feed())
    if not f:
        return {"ran": False, "reason": "no casebook day yet"}
    social = await draft_social(f, llm)
    for ch, draft in social.items():
        _save(f"{f['date']}:{ch}", ch, f["date"], "ready", {**draft, "facts_date": f["date"], "link": f["link"]})
    result: Dict[str, Any] = {"ran": True, "date": f["date"], "social": {ch: d["source"] for ch, d in social.items()}}
    if force_recap or _is_friday(f["date"]):
        rf = recap_facts(f["date"])
        if rf:
            rid = f"recap:{rf['week']}"
            existing = get(rid)
            if existing and existing["status"] == "published" and not force_recap:
                result["recap"] = "already published"
            else:
                rec = await draft_recap(rf, llm)
                _save(rid, "recap", f["date"], "pending", {**rec, "week": rf["week"], "from": rf["from"], "to": rf["to"]})
                result["recap"] = rec["source"]
    return result
