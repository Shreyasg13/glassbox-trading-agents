"""The Committee Casebook: the public blog built from the daily committee runs.

One post per trading day the committee sat. Each case shows what was reviewed, every
analyst's labelled lean and reasoning, the vote, and -- once 5 trading days have passed --
how the stock actually did against SPY. Losses are shown exactly like wins.

Only public-safe fields leave this module: no providers, models, raw errors or timings.
Every analyst summary goes through the A7 compliance filter; anything it flags or blocks
is withheld rather than published."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from sqlalchemy.exc import SQLAlchemyError

from . import committee_daily, db, disclaimer, paper_cycle
from .compliance import filter as cf

log = logging.getLogger("glassbox.casebook")

HORIZON = 5  # trading days, the same horizon the track record scores
BENCH = "SPY"
SUMMARY_CAP = 260  # matches the cut in committee_daily._agent_rows
_BOOK_TTL_S = 3600.0
_book_cache: Dict[str, Any] = {"at": 0.0, "book": None}


def _book() -> Optional[Any]:
    """Prices for the outcome line. Read-only and slow to build, so cached per worker for an hour;
    None (no outcomes shown) on a machine without price data."""
    if _book_cache["book"] is None or time.monotonic() - _book_cache["at"] > _BOOK_TTL_S:
        try:
            _book_cache.update({"book": paper_cycle.load_book(), "at": time.monotonic()})
        except Exception:  # noqa: BLE001 -- the casebook still reads fine without outcomes
            return None
    return _book_cache["book"]


def _outcome(book: Optional[Any], symbol: str, d: str, call: Optional[str]) -> Optional[Dict[str, Any]]:
    if book is None:
        return None
    ret = committee_daily._agent_fwd_return(book, symbol, d, HORIZON)
    if ret is None:
        return None
    spy = committee_daily._agent_fwd_return(book, BENCH, d, HORIZON)
    excess = ret - spy if spy is not None else None
    right = None
    if call in ("BUY", "SELL") and excess is not None:
        right = excess > 0 if call == "BUY" else excess < 0
    return {"horizon": HORIZON, "return": round(ret, 4), "spy_return": None if spy is None else round(spy, 4),
            "excess": None if excess is None else round(excess, 4), "right": right}


def _tidy(text: str) -> str:
    """Summaries are stored cut at a fixed length (committee_daily._squash); end them on a word, with an ellipsis."""
    if len(text) < SUMMARY_CAP:
        return text
    return text[: text.rfind(" ")].rstrip(",;:-") + "…" if " " in text else text


def _safe_summary(text: str, symbol: str) -> Optional[str]:
    """None when the compliance filter would flag or block this text on a public page, or cannot run (fail closed)."""
    try:
        res = cf.check(text, channel="blog", tickers=[symbol])
    except Exception:  # noqa: BLE001 -- e.g. rules file missing: publish nothing unchecked
        log.exception("casebook: compliance filter unavailable; withholding summary")
        return None
    return None if res.action in ("flagged", "blocked") else text


def _case(r: Dict[str, Any], book: Optional[Any]) -> Dict[str, Any]:
    analysts, silent, withheld = [], 0, 0
    for a in r.get("agents", []):
        if not a.get("ok"):
            silent += 1
            continue
        summary = _safe_summary(_tidy(a.get("summary", "")), r["symbol"])
        withheld += summary is None
        analysts.append({"agent": a.get("agent"), "type": a.get("type"), "lean": a.get("lean"),
                         "summary": summary, "confidence": a.get("confidence")})
    call = r.get("action") or r.get("decision")
    return {
        "symbol": r["symbol"],
        "why": r.get("why"),
        "engine_signal": r.get("engine_signal"),
        "decision": r.get("decision"),
        "acted_on": r.get("action"),
        "votes": r.get("votes"),
        "headline": (r.get("ceo") or {}).get("headline"),
        "agrees_with_engine": r.get("agrees_with_engine"),
        "quorum_ok": r.get("quorum_ok"),
        "answered": r.get("answered", 0),
        "total": r.get("total", 0),
        "analysts": analysts,
        "did_not_answer": silent,
        "withheld": withheld,
        "outcome": _outcome(book, r["symbol"], r["date"], call if r.get("decision") else None),
    }


def _by_date() -> Dict[str, List[Dict[str, Any]]]:
    days: Dict[str, List[Dict[str, Any]]] = {}
    for r in db.list_all_committee_runs():
        if r.get("date") and r.get("symbol"):
            days.setdefault(r["date"], []).append(r)
    return days


_posts_cache: Dict[str, Any] = {"at": 0.0, "key": None, "value": None}


def _teasers() -> List[Dict[str, Any]]:
    """Every day as a feed card: the least routine thing that happened as the title, a quote as the excerpt.
    Built without prices (no outcomes needed here) and cached like the feed."""
    days = _by_date()
    key = (sum(len(v) for v in days.values()), max(days, default=""))
    if _posts_cache["value"] is not None and _posts_cache["key"] == key and time.monotonic() - _posts_cache["at"] < _FEED_TTL_S:
        return _posts_cache["value"]
    out = []
    for d in sorted(days, reverse=True):
        cases = [_case(r, None) for r in sorted(days[d], key=lambda r: r["symbol"])]
        over = [c["symbol"] for c in cases if c["agrees_with_engine"] is False]
        debate = most_debated(cases)
        if over:
            kicker, title = "Overrule", f"The AI committee overruled the engine on {', '.join(over)}"
        elif debate:
            s = _split(debate)
            kicker, title = "Debate", f"{debate['symbol']} split the room: {s['BUY']} BUY, {s['SELL']} SELL, {s['HOLD']} HOLD"
        else:
            kicker, title = "Casebook", f"The committee reviewed {len(cases)} stocks"
        quote = _quote(debate) if debate else next((q for c in cases if (q := _quote(c))), None)
        words = sum(len((a["summary"] or "").split()) for c in cases for a in c["analysts"])
        out.append({
            "date": d,
            "symbols": [c["symbol"] for c in cases],
            "decisions": {c["symbol"]: c["decision"] for c in cases},
            "disagreements": over,
            "title": title,
            "kicker": kicker,
            "dek": _day_headline(cases),
            "excerpt": quote,
            "focus": over[0] if over else (debate["symbol"] if debate else None),
            "read_minutes": max(1, round(words / 230)),
        })
    _posts_cache.update({"value": out, "key": key, "at": time.monotonic()})
    return out


def posts(limit: int = 30) -> Dict[str, Any]:
    """Newest first: one teaser per day for the blog index and the homepage. A day the editorial board's
    pick was published for (Admin -> Studio) carries that title, dek and tags instead of the template's."""
    from . import editorial  # editorial imports this module

    try:
        picks = editorial.published()
    except SQLAlchemyError:  # no studio_drafts table (migration 0009 not run): template titles only
        log.exception("casebook: editorial picks unavailable")
        picks = {}
    out = []
    for t in _teasers()[:limit]:
        pick = picks.get(t["date"])
        out.append({**t, "title": pick["title"], "dek": pick["dek"], "tags": pick["tags"] or [], "editors_pick": (pick["promote"] or 0) >= 4}
                   if pick else {**t, "tags": [], "editors_pick": False})
    return {"posts": out, "disclaimer": disclaimer.text()}


def post(d: str) -> Optional[Dict[str, Any]]:
    runs = _by_date().get(d)
    if not runs:
        return None
    book = _book()
    return {"date": d, "cases": [_case(r, book) for r in sorted(runs, key=lambda r: r["symbol"])],
            "horizon": HORIZON, "disclaimer": disclaimer.text()}


# ------------------------------------------------------------------ feed --
# The homepage/blog feed: a handful of typed cards built from the most recent days, so the page
# changes every trading day on its own. Every card is derived from stored committee runs; nothing
# is generated here, and quotes reuse the compliance-filtered summaries above.

FEED_DAYS = 10
_FEED_TTL_S = 600.0
_feed_cache: Dict[str, Any] = {"at": 0.0, "key": None, "value": None}
_LEANS = ("BUY", "SELL", "HOLD")


def _day_headline(cases: List[Dict[str, Any]]) -> str:
    n = len(cases)
    ai = len({a["agent"] for c in cases for a in c["analysts"] if a["type"] == "llm"})
    quant = len({a["agent"] for c in cases for a in c["analysts"] if a["type"] != "llm"})
    who = f"{ai} AI analysts" + (f" and {quant} quant models" if quant else "")
    count = {lean: sum(1 for c in cases if c["decision"] == lean) for lean in _LEANS}
    if count["HOLD"] == n:
        return f"{who} looked at {n} stocks. On every single one, their verdict was: wait."
    moves = " and ".join(f"{count[k]} {k}" for k in ("BUY", "SELL") if count[k])
    return f"{who} looked at {n} stocks. They made {moves}, and said wait on the other {count['HOLD']}."


def _split(c: Dict[str, Any]) -> Dict[str, int]:
    return {lean: sum(1 for a in c["analysts"] if a["lean"] == lean and a["type"] == "llm") for lean in _LEANS}


def _sides(c: Dict[str, Any]) -> int:
    return sum(1 for v in _split(c).values() if v)


def most_debated(cases: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The case whose LLM analysts split across the most sides, most evenly (None if every room agreed)."""
    split = [c for c in cases if _sides(c) >= 2]
    return max(split, key=lambda c: (_sides(c), -max(_split(c).values()))) if split else None


def _quote(c: Dict[str, Any], lean: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The fullest compliant LLM reasoning on this case (optionally from one side)."""
    pool = [a for a in c["analysts"] if a["type"] == "llm" and a["summary"] and (lean is None or a["lean"] == lean)]
    if not pool:
        return None
    a = max(pool, key=lambda a: (a.get("confidence") or 0, len(a["summary"])))
    return {"agent": a["agent"], "lean": a["lean"], "text": a["summary"]}


def _scoreboard(days: Dict[str, List[Dict[str, Any]]], book: Optional[Any]) -> Dict[str, Any]:
    calls = [r for runs in days.values() for r in runs]
    directional = right = 0
    for r in calls:
        call = r.get("action") or r.get("decision")
        if call in ("BUY", "SELL"):
            o = _outcome(book, r["symbol"], r["date"], call)
            if o and o["right"] is not None:
                directional += 1
                right += bool(o["right"])
    holds = sum(1 for r in calls if r.get("decision") == "HOLD")
    return {"type": "scoreboard", "days": len(days), "calls": len(calls), "hold_share": round(holds / len(calls), 3) if calls else None,
            "scored": directional, "right": right, "overrule_days": sum(1 for runs in days.values() if any(r.get("agrees_with_engine") is False for r in runs))}


def _build_feed() -> Dict[str, Any]:
    days = _by_date()
    if not days:
        return {"updated": None, "cards": [], "disclaimer": disclaimer.text()}
    book = _book()
    recent = sorted(days, reverse=True)[:FEED_DAYS]
    full = {d: [_case(r, book) for r in sorted(days[d], key=lambda r: r["symbol"])] for d in recent}
    latest = recent[0]
    cards: List[Dict[str, Any]] = []

    today = full[latest]
    cards.append({"type": "day", "date": latest, "href": f"/blog/{latest}", "headline": _day_headline(today), "stocks": len(today),
                  "decisions": {c["symbol"]: c["decision"] for c in today}, "overruled": [c["symbol"] for c in today if c["agrees_with_engine"] is False],
                  "opinions": {lean: sum(1 for c in today for a in c["analysts"] if a["type"] == "llm" and a["lean"] == lean) for lean in _LEANS}})

    # Most debated stock of the recent days: the case whose LLM analysts split across the most sides, most evenly.
    debates = [(d, c) for d in recent for c in full[d] if _sides(c) >= 2]
    if debates:
        d, c = max(debates, key=lambda dc: (_sides(dc[1]), -max(_split(dc[1]).values()), dc[0]))
        sides = sorted((lean for lean, v in _split(c).items() if v), key=lambda lean: -_split(c)[lean])[:2]
        cards.append({"type": "debate", "date": d, "symbol": c["symbol"], "href": f"/blog/{d}#{c['symbol']}", "split": _split(c),
                      "decision": c["decision"], "quotes": [q for q in (_quote(c, s) for s in sides) if q]})

    over = next(((d, c) for d in recent for c in full[d] if c["agrees_with_engine"] is False), None)
    if over:
        d, c = over
        cards.append({"type": "overrule", "date": d, "symbol": c["symbol"], "href": f"/blog/{d}#{c['symbol']}", "engine": c["engine_signal"],
                      "decision": c["decision"], "outcome": c["outcome"], "quote": _quote(c, c["decision"])})

    # The latest day whose outcome is in: its best and worst stock versus SPY, and how the calls fared.
    scored_day = next((d for d in recent if any(c["outcome"] and c["outcome"]["excess"] is not None for c in full[d])), None)
    if scored_day:
        rows = [c for c in full[scored_day] if c["outcome"] and c["outcome"]["excess"] is not None]
        best, worst = max(rows, key=lambda c: c["outcome"]["excess"]), min(rows, key=lambda c: c["outcome"]["excess"])
        judged = [c for c in rows if c["outcome"]["right"] is not None]
        cards.append({"type": "verdict", "date": scored_day, "href": f"/blog/{scored_day}", "horizon": HORIZON,
                      "best": {"symbol": best["symbol"], "decision": best["decision"], "excess": best["outcome"]["excess"]},
                      "worst": {"symbol": worst["symbol"], "decision": worst["decision"], "excess": worst["outcome"]["excess"]},
                      "judged": len(judged), "right": sum(1 for c in judged if c["outcome"]["right"])})

    used = {card.get("symbol") for card in cards}
    spot = next(((latest, c, q) for c in today if c["symbol"] not in used and (q := _quote(c))), None)
    if spot:
        d, c, q = spot
        cards.append({"type": "quote", "date": d, "symbol": c["symbol"], "href": f"/blog/{d}#{c['symbol']}", "quote": q, "decision": c["decision"]})

    cards.append(_scoreboard(days, book) | {"href": "/blog"})
    updated = max((r.get("created_at") or "") for r in days[latest]) or None
    return {"updated": updated, "latest": latest, "cards": cards, "disclaimer": disclaimer.text()}


def feed() -> Dict[str, Any]:
    """Cached per worker for ten minutes, and rebuilt at once when a new trading day lands."""
    days = db.list_all_committee_runs()
    key = (len(days), max((r.get("date") or "" for r in days), default=""))
    if _feed_cache["value"] is None or _feed_cache["key"] != key or time.monotonic() - _feed_cache["at"] > _FEED_TTL_S:
        _feed_cache.update({"value": _build_feed(), "key": key, "at": time.monotonic()})
    return _feed_cache["value"]
