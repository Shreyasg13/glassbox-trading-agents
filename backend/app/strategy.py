"""Read models for the admin Strategy page and the user-facing Track record / stance views.

Nothing here trades or calls a model -- it only reads what the paper engine, the risk
signal and the Investment Committee have already stored, and lays it out so the two
questions that matter can be answered at a glance:

  * is each part of the system actually delivering?  (accuracy, leaderboard, capital)
  * what should I look at today, and what can I ignore?  (stance: attention vs quiet)

Every accuracy figure carries its sample size, and the leaderboard refuses to rank an agent
until it has MIN_RANKED scored calls -- a leaderboard built on a dozen decisions would rank
noise. In-sample results (the engine's backtest) are labelled as such wherever they appear.
"""
from __future__ import annotations

import threading
from bisect import bisect_left
from collections import Counter
from typing import Any, Dict, List, Optional

from . import associations, committee_daily, data_source as ds, db, free_data, orchestration, panel as panel_mod, paper, paper_cycle, pipeline, risk, snapshots

MIN_RANKED = 30  # scored directional calls before an agent may be ranked
CONTROL_ORDER = ["ctl_engine", "ctl_taxaware", "ctl_trend", "ctl_voltarget", "ctl_committee", "ctl_placebo", "ctl_spy", "ctl_equal", "ctl_cash", "ctl_engine_ira", "ctl_trend_ira", "ctl_voltarget_ira", "ctl_placebo_ira"]
STALE_VIEW_DAYS = paper.COMMITTEE_MAX_AGE_DAYS

_lock = threading.Lock()
_cache: Dict[str, Any] = {}


def _data_key() -> Optional[float]:
    try:
        return max((p.stat().st_mtime for p in ds.live_signals_source_paths() if p.exists()), default=None)
    except OSError:
        return None


def cached_book() -> paper.PriceBook:
    """The price book, rebuilt only when the data files change (it re-reads every price
    file). The committee's calls are refreshed on every use: they change hourly, prices daily."""
    key = _data_key()
    with _lock:
        if _cache.get("key") != key or "book" not in _cache:
            _cache.clear()
            _cache.update({"key": key, "book": paper_cycle.load_book()})
        book = _cache["book"]
    book.set_committee(paper_cycle.committee_views())
    return book


def _heavy(name: str, compute) -> Any:
    """Whole-history scorecards, cached per data version."""
    key = _data_key()
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == key:
            return hit[1]
    value = compute()
    with _lock:
        _cache[name] = (key, value)
    return value


# --------------------------------------------------------------- leaderboard --


def _fwd_return(book: paper.PriceBook, sym: str, d: str, h: int) -> Optional[float]:
    dates = book._sorted_dates.get(sym) or []
    i = bisect_left(dates, d)
    if i >= len(dates) or dates[i] != d or i + 1 + h >= len(dates):
        return None
    return book.close[sym][dates[i + 1 + h]] / book.close[sym][dates[i + 1]] - 1


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _vote_weight(a: Dict[str, Any], run: Dict[str, Any]) -> float:
    """The weight orchestration._reduce_committee_vote gave this stored answer."""
    if a.get("type") == "deterministic":
        return (run.get("engine_confidence") or 50) / 100
    if a.get("structured") and a.get("confidence") is not None:
        return min(orchestration.VIEW_WEIGHT_MAX, max(orchestration.VIEW_WEIGHT_MIN, a["confidence"] / 100))
    return 0.5


def _winner(votes: Dict[str, float]) -> str:
    return max(votes, key=votes.get) if any(votes.values()) else "HOLD"


def _signed(lean: str, fwd: float) -> float:
    return fwd if lean == "BUY" else -fwd if lean == "SELL" else 0.0


def _leave_one_out(run: Dict[str, Any]) -> Dict[str, str]:
    """agent -> the decision the vote would have reached without it, for the agents whose absence
    changes the call. Empty if the stored votes can't be reproduced (an older run format)."""
    ok = [(a, _vote_weight(a, run)) for a in run.get("agents", []) if a.get("ok") and a.get("lean") in ("BUY", "SELL", "HOLD")]
    votes = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    for a, w in ok:
        votes[a["lean"]] += w
    if _winner(votes) != run.get("decision"):
        return {}
    out = {}
    for a, w in ok:
        alt = _winner({**votes, a["lean"]: votes[a["lean"]] - w})
        if alt != run["decision"]:
            out[a["agent"]] = alt
    return out


def agent_leaderboard(book: paper.PriceBook, runs: List[Dict[str, Any]], horizon: int = 5, min_ranked: int = MIN_RANKED) -> Dict[str, Any]:
    """Per committee member: how often it sided with the final call and with the engine, how
    sure it says it is, and -- once enough decisions have aged -- whether its BUY/SELL calls
    made money. `mean_edge` is the average forward return signed by the call (a BUY is right
    when the stock rises, a SELL when it falls), entered at the next close like the paper
    trader. HOLD is a non-call and is not scored for direction."""
    seen: Dict[str, Counter] = {}
    models: Dict[str, Counter] = {}
    for r in runs:
        for a in r.get("agents", []):
            c = seen.setdefault(a["agent"], Counter())
            c["seen"] += 1
            c["ok"] += bool(a.get("ok"))
            c["failed_over"] += bool(a.get("failed_over_from"))
            if a.get("ok") and a.get("model"):
                models.setdefault(a["agent"], Counter())[a["model"]] += 1
    acc: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        if not r.get("quorum_ok"):
            continue
        fwd = _fwd_return(book, r["symbol"], r["date"], horizon)
        spy = _fwd_return(book, "SPY", r["date"], horizon)
        pivots = _leave_one_out(r)
        for a in r.get("agents", []):
            if not a.get("ok"):
                continue
            row = acc.setdefault(a["agent"], {"type": a.get("type"), "answers": 0, "with_committee": 0, "with_engine": 0, "confs": [], "edges": [], "pivotal": 0, "pivot_edges": [], "briers": [], "ps": [], "beats": []})
            if a["agent"] in pivots:
                row["pivotal"] += 1
                if fwd is not None:  # what the call gained (or lost) because this agent voted
                    row["pivot_edges"].append(_signed(r["decision"], fwd) - _signed(pivots[a["agent"]], fwd))
            if a.get("p_beat_spy") is not None and fwd is not None and spy is not None:
                p, beat = a["p_beat_spy"] / 100, float(fwd > spy)
                row["briers"].append((p - beat) ** 2)
                row["ps"].append(p)
                row["beats"].append(beat)
            row["answers"] += 1
            row["with_committee"] += a.get("lean") == r.get("decision")
            row["with_engine"] += a.get("lean") == r.get("engine_signal")
            if a.get("confidence") is not None:
                row["confs"].append(a["confidence"])
            if fwd is not None and a.get("lean") in ("BUY", "SELL"):
                row["edges"].append(fwd if a["lean"] == "BUY" else -fwd)
    out = []
    for name, row in acc.items():
        n = len(row["edges"])
        out.append(
            {
                "agent": name,
                "type": row["type"],
                "answers": row["answers"],
                "agrees_with_committee": row["with_committee"] / row["answers"],
                "agrees_with_engine": row["with_engine"] / row["answers"],
                "avg_confidence": _mean(row["confs"]),
                "directional_calls": n,
                "hit_rate": (sum(1 for e in row["edges"] if e > 0) / n) if n else None,
                "mean_edge": _mean(row["edges"]),
                "ranked": n >= min_ranked,
                "answer_rate": seen[name]["ok"] / seen[name]["seen"],
                "failover_rate": seen[name]["failed_over"] / seen[name]["seen"],
                "models": dict(models.get(name, Counter()).most_common(3)),
                "pivotal": row["pivotal"],
                "pivotal_edge": _mean(row["pivot_edges"]),
                "calibration_n": len(row["briers"]),
                "brier": _mean(row["briers"]),
                "mean_p_beat_spy": _mean(row["ps"]),
                "beat_rate": _mean(row["beats"]),
            }
        )
    out.sort(key=lambda r: (0, -(r["mean_edge"] or 0.0)) if r["ranked"] else (1, -r["answers"]))
    return {
        "horizon": horizon,
        "min_ranked": min_ranked,
        "agents": out,
        "note": (
            f"An agent is ranked only after {min_ranked} scored BUY/SELL calls at a {horizon}-day horizon; until then the columns are context, not a verdict. "
            "Pivotal = decisions that would have gone the other way without this agent, and what that changed in forward return. "
            "Brier = how well its 'chance of beating SPY' matched what happened (0 is perfect, 0.25 is a coin flip)."
        ),
    }


# ---------------------------------------------------------------- admin views --


def _control_rows(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {a["id"]: a for a in accounts}
    rank = {cid: i for i, cid in enumerate(CONTROL_ORDER)}
    controls = sorted((a for a in accounts if a["kind"] == "control"), key=lambda a: rank.get(a["id"], 99))
    return [paper.summarize(a, by_id.get(a.get("benchmark_id") or "")) for a in controls]


def _curves(accounts: List[Dict[str, Any]], max_points: int = 150) -> Dict[str, List[List[Any]]]:
    return {a["id"]: paper_cycle.downsample([[p[0], p[1], p[2]] for p in a["curve"]], max_points) for a in accounts if a["kind"] == "control" and a["curve"]}


def coverage(book: paper.PriceBook, todays: List[Dict[str, Any]], latest: Optional[str]) -> Dict[str, Any]:
    """Every tracked symbol, not just the few the committee reviewed: the engine and the risk model
    read ALL of them every day; the committee (about 7 model calls each) is a second opinion on a few,
    picked from the engine's BUY/SELL and changed signals and topped up with the biggest movers."""
    d = book.latest_date
    reviewed = {r["symbol"]: r for r in todays} if latest == d else {}
    rows = []
    for sym in book.symbols:
        sig, conf = book.signal_at(sym, d) if d else ("HOLD", 50.0)
        rk = risk.risk_at(book, sym, d) if d else None
        rev = reviewed.get(sym)
        rows.append(
            {
                "symbol": sym,
                "name": ds.STOCK_INFO.get(sym, {}).get("name", sym),
                "engine_signal": sig,
                "engine_confidence": conf,
                "risk": {"level": rk["level"], "score": rk["score"]} if rk else None,
                "reviewed": rev is not None,
                "committee_action": (rev.get("action") or rev.get("decision")) if rev else None,
            }
        )
    rows.sort(key=lambda r: (not r["reviewed"], r["engine_signal"] == "HOLD", -((r["risk"] or {}).get("score") or 0.0), r["symbol"]))
    return {"data_date": d, "review_date": latest, "reviewed": len(reviewed), "total": len(rows), "rows": rows}


def overview(book: paper.PriceBook) -> Dict[str, Any]:
    d = book.latest_date
    runs = db.list_all_committee_runs()
    latest = max((r["date"] for r in runs), default=None)
    todays = sorted((r for r in runs if r["date"] == latest), key=lambda r: r["symbol"])
    history = [
        {"date": r["date"], "symbol": r["symbol"], "decision": r.get("decision"), "action": r.get("action"), "engine_signal": r.get("engine_signal"),
         "consensus": (r.get("ceo") or {}).get("label"), "answered": r.get("answered"), "total": r.get("total")}
        for r in sorted(runs, key=lambda r: (r["date"], r["symbol"]), reverse=True)[:40]
    ]
    accounts = db.list_paper_accounts()
    return {
        "data_date": d,
        "latest_review_date": latest,
        "decisions": todays,
        "coverage": coverage(book, todays, latest),
        "history": history,
        "risk_today": risk.risk_table(book, d) if d else [],
        "capital": _control_rows(accounts),
        "curves": _curves(accounts),
        "meta": db.get_paper_meta(),
        "tax_assumptions": {"short_term": paper.TAX_ST, "long_term": paper.TAX_LT},
        "data_quality": {"gaps": book.gaps(), "ok": not book.gaps()},
        "pipeline": _pipeline_status(),
    }


def _pipeline_status() -> Optional[Dict[str, Any]]:
    """The last daily-pipeline run (which day, which stages passed), or None if it never ran / the store is unreachable."""
    try:
        return pipeline.last_status()
    except Exception:  # noqa: BLE001 -- a status line must never break the overview
        return None


# ------------------------------------------------------------ labelled matrices --

MATRIX_MAX_DAYS = 800


def _panel(book: paper.PriceBook, inference: bool):
    p = panel_mod.from_book(book)
    return p.with_inference(db.list_all_committee_runs()) if inference else p


def matrix(book: paper.PriceBook, fields: List[str], symbols: Optional[List[str]] = None, days: int = 120, inference: bool = False) -> Dict[str, Any]:
    """Labelled (dates x symbols) arrays for the requested fields -- the raw material for path retrieval and custom designs."""
    p = _panel(book, inference)
    unknown = [f for f in fields if f not in p.fields]
    if unknown:
        raise KeyError(", ".join(unknown))
    bad = [s for s in (symbols or []) if s not in p.symbols]
    if bad:
        raise KeyError(", ".join(bad))
    out = p.to_payload(fields, symbols, min(max(days, 1), MATRIX_MAX_DAYS))
    out["available_fields"] = sorted(p.fields)
    out["codes"] = {"signal": panel_mod.SIGNAL_CODE, "risk_level": panel_mod.RISK_CODE, "committee_code": panel_mod.SIGNAL_CODE}
    return out


def correlation_matrix(book: paper.PriceBook, window: int = 60, end: Optional[str] = None) -> Dict[str, Any]:
    """Pairwise return correlation as a labelled matrix, plus the clusters it implies."""
    p = panel_mod.from_book(book)
    d = end or book.latest_date
    C = p.corr(d, window)
    groups = associations.clusters({a: {b: float(C[i, j]) for j, b in enumerate(p.symbols)} for i, a in enumerate(p.symbols)})
    return {"as_of": d, "window": window, "symbols": p.symbols, "values": [[round(float(v), 4) for v in row] for row in C], "clusters": groups}


def snapshot_matrix(field: str, days: int = 120) -> Dict[str, Any]:
    return snapshots.matrix(field, min(max(days, 1), 400))


def accuracy(book: paper.PriceBook) -> Dict[str, Any]:
    runs = db.list_all_committee_runs()
    return {
        "signal": _heavy("signal_scorecard", lambda: paper.signal_scorecard(book)),
        "risk": _heavy("risk_scorecard", lambda: risk.risk_scorecard(book)),
        "committee": committee_daily.committee_scorecard(book, runs),
        "leaderboard": agent_leaderboard(book, runs),
    }


# ----------------------------------------------------------------- user views --


def track_record() -> Dict[str, Any]:
    """The system's own simulated track record: only the fixed reference strategies, never
    another user's account. Live (out-of-sample) and backtest (in-sample) are kept apart."""
    accounts = [a for a in db.list_paper_accounts() if a["id"] in CONTROL_ORDER]  # reference strategies only: never a challenger or a user's account
    meta = db.get_paper_meta() or {}
    rows = _control_rows(accounts)
    keep = ("id", "name", "strategy", "equity", "total_return", "live_return", "max_drawdown", "sharpe", "turnover", "cost_paid", "est_tax", "after_tax_return", "tax_drag", "avg_holding_days", "live_days", "days", "tax_tracked", "tax_status", "wash_disallowed", "deferred_sells", "liquidation_tax", "after_tax_liquidated_return")
    return {
        "initialised": bool(meta),
        "live_from": meta.get("live_from"),
        "strategies": [{k: r.get(k) for k in keep} for r in rows],
        "curves": _curves(accounts),
        "tax_assumptions": {"short_term": paper.TAX_ST, "long_term": paper.TAX_LT},
    }


def data_sources(book: paper.PriceBook) -> Dict[str, Any]:
    """What free public data is live, per source and per symbol (admin)."""
    d = book.latest_date
    if not d:
        return {"as_of": None, "rows": [], "macro": None, "macro_line": None, "status": free_data.read_status(), "sec_configured": free_data.sec_user_agent() is not None}
    return free_data.snapshot(book.symbols, d, {s: book.close_on(s, d) for s in book.symbols})


def _latest_view(runs: List[Dict[str, Any]], book: paper.PriceBook, d: str) -> Dict[str, Dict[str, Any]]:
    """The freshest usable committee review per symbol (within STALE_VIEW_DAYS trading days)."""
    best: Dict[str, Dict[str, Any]] = {}
    for r in runs:
        if not r.get("action") and not (r.get("quorum_ok") and r.get("decision")):
            continue
        age = bisect_left(book.dates, d) - bisect_left(book.dates, r["date"])
        if 0 <= age <= STALE_VIEW_DAYS and (r["symbol"] not in best or r["date"] > best[r["symbol"]]["date"]):
            best[r["symbol"]] = r
    return best


def stance(book: paper.PriceBook, tickers: Optional[List[str]] = None) -> Dict[str, Any]:
    """Today's plain-English position on each symbol a user follows, in three tiers so the page
    stays calm: `attention` (an actionable signal: the engine or committee says BUY/SELL, or they
    disagree), `watch` (risk is HIGH but nothing is suggested -- in a broad sell-off that is most
    symbols at once, so it must not shout), and `quiet` (nothing to do)."""
    d = book.latest_date
    if not d:
        return {"as_of": None, "rows": [], "attention": 0, "watch": 0, "quiet": 0}
    runs = db.list_all_committee_runs()
    views = _latest_view(runs, book, d)
    macro_line = free_data.macro_line(free_data.macro_as_of(d))
    rows = []
    for sym in [t for t in (tickers or book.symbols) if t in book.close]:
        sig, conf = book.signal_at(sym, d)
        rk = risk.risk_at(book, sym, d)
        view = views.get(sym)
        action = (view.get("action") or view.get("decision")) if view else None
        reasons = []
        if sig != "HOLD":
            reasons.append(f"engine says {sig} ({conf:.0f}% confidence)")
        if action and action != "HOLD":
            reasons.append(f"committee says {action}")
        if action and action != sig:
            reasons.append("engine and committee disagree")
        flagged = [e for e in free_data.events_as_of(sym, d, 14) if e["flag"]]  # e.g. a CEO change or an impairment, filed in the last 2 weeks
        if flagged:
            reasons.append(f"SEC filing worth a look ({flagged[0]['filed']}): {flagged[0]['text']}")
        elevated = bool(rk and rk["level"] == "HIGH")
        attention = bool(reasons)
        if attention and elevated:
            reasons.append("risk is HIGH: volatile and under pressure")
        rows.append(
            {
                "symbol": sym,
                "name": ds.STOCK_INFO.get(sym, {}).get("name", sym),
                "price": book.close_on(sym, d),
                "engine": {"signal": sig, "confidence": conf},
                "committee": {"action": action, "consensus": (view.get("ceo") or {}).get("label"), "date": view["date"], "headline": (view.get("ceo") or {}).get("headline")} if view else None,
                "risk": {"level": rk["level"], "score": rk["score"]} if rk else None,
                "fundamentals": {k: fnd.get(k) for k in ("revenue_growth", "net_margin", "pe", "roe")} if (fnd := free_data.fundamentals_as_of(sym, d, book.close_on(sym, d))) else None,
                "attention": attention,
                "watch": elevated and not attention,
                "reasons": reasons,
                "summary": "; ".join(reasons) if attention else ("No action suggested, but risk is HIGH: volatile and under pressure." if elevated else "No action suggested: hold what you have."),
            }
        )
    rows.sort(key=lambda r: (not r["attention"], not r["watch"], -((r["risk"] or {}).get("score") or 0.0), r["symbol"]))
    n_att, n_watch = sum(1 for r in rows if r["attention"]), sum(1 for r in rows if r["watch"])
    return {"as_of": d, "rows": rows, "attention": n_att, "watch": n_watch, "quiet": len(rows) - n_att - n_watch, "macro_line": macro_line}
