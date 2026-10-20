"""The editorial board: gives each Casebook day a real headline, a one-line dek and tags for the blog feed.

Three members review every day, in order:
  1. Editor (model): drafts a title, a dek and tags from that day's FACTS.
  2. Growth marketer (model): sharpens the editor's draft into a stronger hook, scores how worth
     promoting the day is (1-5) and names the best channel, with a one-line reason.
  3. Compliance (no model): studio.check -- every number must be in FACTS and the A7 filter must not
     flag or block it. The marketer's version wins if it passes, else the editor's, else a template.
The board never publishes: its pick waits on Admin -> Studio as "pending", and a day's card on /blog
uses it only after the owner publishes it. Promotion only -- no ads, no paid placement.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from . import casebook, studio

LIMITS = {"title": 100, "dek": 220}
MAX_TAGS = 4


def day_facts(d: str) -> Optional[Dict[str, Any]]:
    """One day's casebook flattened into the only material the board may use."""
    p = casebook.post(d)
    if not p:
        return None
    cases = p["cases"]
    f: Dict[str, Any] = {
        "date": d,
        "link": f"{studio.SITE}/blog/{d}",
        "default_headline": casebook._day_headline(cases),
        "stocks_reviewed": len(cases),
        "final_calls": {k: sum(1 for c in cases if c["decision"] == k) for k in casebook._LEANS},
        "calls_by_symbol": {c["symbol"]: c["decision"] for c in cases},
        "overruled_engine_on": [{"symbol": c["symbol"], "engine": c["engine_signal"], "committee": c["decision"]}
                                for c in cases if c["agrees_with_engine"] is False],
        "horizon_days": casebook.HORIZON,
    }
    if (c := casebook.most_debated(cases)):
        f["most_debated"] = {"symbol": c["symbol"], "split": casebook._split(c), "final_call": c["decision"],
                             "quotes": [q for q in (casebook._quote(c, s) for s in ("BUY", "SELL", "HOLD")) if q][:2]}
    scored = [c for c in cases if c["outcome"] and c["outcome"]["excess"] is not None]
    if scored:
        best = max(scored, key=lambda c: c["outcome"]["excess"])
        worst = min(scored, key=lambda c: c["outcome"]["excess"])
        f["five_day_result"] = {"best": {"symbol": best["symbol"], "vs_spy": studio._pct(best["outcome"]["excess"])},
                                "worst": {"symbol": worst["symbol"], "vs_spy": studio._pct(worst["outcome"]["excess"])}}
    return f


def template(f: Dict[str, Any]) -> Dict[str, Any]:
    """The deterministic pick: leads with whatever is least routine about the day. Passes check() by construction."""
    if (o := f["overruled_engine_on"]):
        title = f"The AI committee overruled the engine on {', '.join(x['symbol'] for x in o)}"
    elif (m := f.get("most_debated")):
        s = m["split"]
        title = f"{m['symbol']} split the room: {s['BUY']} BUY, {s['SELL']} SELL, {s['HOLD']} HOLD"
    else:
        title = f"The committee reviewed {f['stocks_reviewed']} stocks"
    tags = [x["symbol"] for x in f["overruled_engine_on"]] + ([f["most_debated"]["symbol"]] if f.get("most_debated") else [])
    return {"title": title, "dek": f["default_headline"], "tags": list(dict.fromkeys(tags))[:MAX_TAGS]}


RULES = (
    "Hard rules: use ONLY numbers that appear in FACTS (compute nothing new); never tell anyone to buy, sell or hold, never "
    "predict prices; never use the words recommendation, recommend, signal, opportunity, should or must (say 'call'); "
    "state nothing FACTS does not say; no URLs, no hashtags, no emojis."
)
EDITOR = (
    "You are the editor of GlassBox Research, a non-commercial blog where AI analysts argue over stocks daily and every call "
    "is scored against SPY. Write the headline for one day's post like a good financial newsroom: specific, plain, no "
    "clickbait, under 90 characters, leading with the least routine thing that happened (an overrule, a split room, a "
    "result). The dek is one sentence under 200 characters that adds the context. Tags are 1 to 4 stock symbols or short "
    f"themes from FACTS. {RULES} Reply with JSON only: " + '{"title": "...", "dek": "...", "tags": ["..."]}'
)
MARKETER = (
    "You are the growth marketer on GlassBox Research's editorial board. Given FACTS and the editor's DRAFT, make the "
    "title a stronger hook a busy reader on LinkedIn would stop for, without hype and without changing any fact; keep it "
    "under 90 characters and the dek under 200. Then score how worth promoting this day is from 1 (routine) to 5 (a "
    "story people will discuss), pick the best channel (linkedin, x or none) and give one sentence of reasoning. "
    f"{RULES} Reply with JSON only: " + '{"title": "...", "dek": "...", "promote": 1, "channel": "linkedin", "note": "..."}'
)


def _candidate(raw: Dict[str, Any]) -> Dict[str, Any]:
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    return {"title": str(raw.get("title") or "").strip(), "dek": str(raw.get("dek") or "").strip(),
            "tags": [str(t).strip() for t in tags if str(t).strip()][:MAX_TAGS]}


def _reasons(c: Dict[str, Any], f: Dict[str, Any]) -> List[str]:
    if not c["title"] or not c["dek"]:
        return ["no draft"]
    return [f"title: {r}" for r in studio.check(c["title"], f, LIMITS["title"])] + \
           [f"dek: {r}" for r in studio.check(c["dek"], f, LIMITS["dek"])] + \
           [f"tag: {r}" for t in c["tags"] for r in studio.check(t, f, 40)]


async def review(f: Dict[str, Any], llm: Optional[studio.Llm] = None) -> Dict[str, Any]:
    """Run the board on one day. Always returns a pick that passed compliance, and every member's say."""
    call = llm or studio._default_llm
    facts = f"FACTS:\n{json.dumps(f, indent=1)}"
    members: Dict[str, Any] = {}
    editor: Dict[str, Any] = {}
    try:
        editor = _candidate(studio._parse_json(await call(facts, EDITOR)))
        members["editor"] = {**editor, "rejected": _reasons(editor, f)}
    except Exception as exc:  # noqa: BLE001 -- no model today: the template stands in
        members["editor"] = {"rejected": [f"model unavailable ({type(exc).__name__})"]}
    marketer: Dict[str, Any] = {}
    if editor.get("title"):
        try:
            raw = studio._parse_json(await call(f"{facts}\n\nDRAFT:\n{json.dumps(editor)}", MARKETER))
            marketer = {**_candidate({**raw, "tags": editor["tags"]}), "promote": raw.get("promote"),
                        "channel": raw.get("channel"), "note": str(raw.get("note") or "").strip()}
            members["marketer"] = {**marketer, "rejected": _reasons(marketer, f)}
        except Exception as exc:  # noqa: BLE001
            members["marketer"] = {"rejected": [f"model unavailable ({type(exc).__name__})"]}
    for name, cand in (("marketer", marketer), ("editor", editor)):
        if cand.get("title") and not members[name]["rejected"]:
            pick, source = cand, name
            break
    else:
        pick, source = template(f), "template"
    promote = marketer.get("promote")
    return {
        "source": source, "title": pick["title"], "dek": pick["dek"], "tags": pick["tags"],
        "promote": promote if isinstance(promote, int) and 1 <= promote <= 5 else None,
        "channel": marketer.get("channel") if marketer.get("channel") in ("linkedin", "x", "none") else None,
        "note": marketer.get("note") or None,
        "members": members,
        "rejected": [] if source == "marketer" else [r for m in members.values() for r in m["rejected"]][:3],
    }


async def run(day: Optional[str] = None, llm: Optional[studio.Llm] = None) -> Dict[str, Any]:
    """Review one day (default: the latest). Re-running replaces a pending pick; a published one is kept."""
    days = sorted(casebook._by_date())
    d = day or (days[-1] if days else None)
    f = day_facts(d) if d else None
    if not f:
        return {"ran": False, "reason": "no casebook day"}
    item_id = f"{d}:editorial"
    existing = studio.get(item_id)
    if existing and existing["status"] == "published":
        return {"ran": False, "date": d, "reason": "already published"}
    pick = await review(f, llm)
    studio._save(item_id, "editorial", d, "pending", pick)
    return {"ran": True, "date": d, "source": pick["source"]}


def published() -> Dict[str, Dict[str, Any]]:
    """date -> the owner-approved pick, for the blog index."""
    return {r["date"]: {k: r.get(k) for k in ("title", "dek", "tags", "promote")}
            for r in studio.list_drafts(300, kind="editorial", status="published")}
