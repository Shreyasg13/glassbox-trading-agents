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


def posts(limit: int = 30) -> Dict[str, Any]:
    """Newest first: one teaser per day, enough for the blog index and the homepage cards."""
    days = _by_date()
    out = []
    for d in sorted(days, reverse=True)[:limit]:
        runs = sorted(days[d], key=lambda r: r["symbol"])
        split = [r["symbol"] for r in runs if r.get("agrees_with_engine") is False]
        out.append({
            "date": d,
            "symbols": [r["symbol"] for r in runs],
            "decisions": {r["symbol"]: r.get("decision") for r in runs},
            "disagreements": split,
            "title": f"Committee Casebook, {d}: " + ", ".join(r["symbol"] for r in runs),
        })
    return {"posts": out, "disclaimer": disclaimer.text()}


def post(d: str) -> Optional[Dict[str, Any]]:
    runs = _by_date().get(d)
    if not runs:
        return None
    book = _book()
    return {"date": d, "cases": [_case(r, book) for r in sorted(runs, key=lambda r: r["symbol"])],
            "horizon": HORIZON, "disclaimer": disclaimer.text()}
