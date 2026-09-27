"""The weekly research loop: measure everything, say what the evidence supports, propose nothing it cannot back.

This is the safe form of a "self-improving" system. The tempting version -- agents rewriting their own
prompts, weights or strategies every day from what they just saw -- fits noise: with a few committee
reviews a day and a few weeks of live data there is nowhere near enough evidence to tell skill from luck,
and with 15 stocks there are hundreds of relationships to "discover", some of which look real by chance.
So the loop is deliberately split:

  * DAILY  -- data, correlations, risk and the committee's peer context are recomputed (associations.py).
  * WEEKLY -- this module writes a digest: what was analysed, what the live paper strategies actually
              delivered against their yardsticks, what changed among the stocks, how each committee
              member is doing, and any PROPOSALS.
  * GATE   -- a strategy may only be called an improvement if its LIVE (out-of-sample) daily excess return
              over a yardstick has a bootstrap confidence interval that is above zero after correcting for
              how many strategies were compared, over at least MIN_LIVE_DAYS trading days. Until then the
              verdict is "insufficient", and that is the correct and expected answer for weeks.
  * HUMAN  -- proposals are text in a report. No code path applies them: nothing here edits an agent, a
              prompt, a weight or a strategy. An admin decides.
"""
from __future__ import annotations

import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import associations, db, paper, paper_cycle, strategy, disclaimer

log = logging.getLogger("glassbox.research")

MIN_LIVE_DAYS = 60  # about 3 months of live paper trading before any verdict other than "insufficient"
BOOTSTRAP_N = 2000
ALPHA = 0.05
DIGEST_TITLE = "Weekly research digest"

CANDIDATES = {
    "ctl_engine": "Signal engine",
    "ctl_taxaware": "Tax-aware engine",
    "ctl_trend": "Trend filter",
    "ctl_voltarget": "Volatility-targeted",
    "ctl_committee": "Engine + committee",
}
BASELINES = {"ctl_equal": "equal-weight hold of the same stocks", "ctl_placebo": "the scrambled-signal placebo"}


# ------------------------------------------------------------------ evidence gate --


def live_returns(acct: Dict[str, Any]) -> Dict[str, float]:
    """Daily returns on the LIVE days only (the first live day is measured from the last backtest close)."""
    curve = acct.get("curve") or []
    return {curve[i][0]: curve[i][1] / curve[i - 1][1] - 1 for i in range(1, len(curve)) if curve[i][2] == "live" and curve[i - 1][1] > 0}


def bootstrap_mean_ci(xs: List[float], lo_q: float, hi_q: float, n: int = BOOTSTRAP_N, seed: int = 20260921) -> Tuple[float, float]:
    """Percentile bootstrap of the mean, with a fixed seed so the same data always gives the same verdict."""
    rnd = random.Random(seed)
    m = len(xs)
    means = sorted(sum(rnd.choice(xs) for _ in range(m)) / m for _ in range(n))
    return means[int(lo_q * n)], means[min(n - 1, int(hi_q * n))]


def gate(candidate: Dict[str, Any], baseline: Dict[str, Any], n_comparisons: int = 1) -> Dict[str, Any]:
    """Has `candidate` beaten `baseline` out-of-sample? 'edge' only when the lower confidence bound of the mean
    daily excess return is above zero at a level corrected for `n_comparisons`, after MIN_LIVE_DAYS days."""
    rc, rb = live_returns(candidate), live_returns(baseline)
    days = sorted(set(rc) & set(rb))
    excess = [rc[d] - rb[d] for d in days]
    out: Dict[str, Any] = {
        "n_days": len(excess),
        "needed_days": MIN_LIVE_DAYS,
        "mean_excess_bps": (sum(excess) / len(excess) * 1e4) if excess else None,
        "ci_low_bps": None,
        "ci_high_bps": None,
        "verdict": "insufficient",
    }
    if len(excess) < MIN_LIVE_DAYS:
        return out
    alpha = ALPHA / max(1, n_comparisons)
    lo, hi = bootstrap_mean_ci(excess, alpha, 1 - alpha)
    out["ci_low_bps"], out["ci_high_bps"] = lo * 1e4, hi * 1e4
    out["verdict"] = "edge" if lo > 0 else "worse" if hi < 0 else "no edge yet"
    return out


def evidence_table(accounts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by = {a["id"]: a for a in accounts}
    # our own strategies plus every outside challenger in the arena: all judged by the same gate on the same days
    candidates = dict(CANDIDATES)
    candidates.update({a["id"]: a["name"] for a in accounts if a.get("strategy") == "external_tilt"})
    n_tests = len(candidates) * len(BASELINES)  # every candidate vs every yardstick: correct for all of them
    rows = []
    for cid, name in candidates.items():
        c = by.get(cid)
        if c is None:
            continue
        live = paper.summarize(c)
        rows.append(
            {
                "id": cid,
                "name": name,
                "challenger": c.get("strategy") == "external_tilt",
                "live_days": live["live_days"],
                "live_return": live["live_return"],
                "vs": {bid: gate(c, by[bid], n_tests) for bid in BASELINES if bid in by and bid != cid},
            }
        )
    return rows


# ---------------------------------------------------------------------- proposals --


def agent_proposals(leaderboard: Dict[str, Any]) -> List[str]:
    """Suggested committee re-weighting, only for agents with enough scored calls to rank. Advice for a human,
    shrunk toward equal weight: an edge of +/-1% per call moves a weight by +/-25%, never more than 50%."""
    out = []
    for a in leaderboard.get("agents", []):
        if not a.get("ranked") or a.get("mean_edge") is None:
            continue
        mult = 1 + max(-0.5, min(0.5, a["mean_edge"] / 0.04))  # +1% edge -> +25%, capped at +/-50%
        if abs(mult - 1) >= 0.10:
            out.append(
                f"Consider {'raising' if mult > 1 else 'lowering'} {a['agent']}'s vote weight to about {mult:.2f}x "
                f"(mean edge {a['mean_edge']:+.2%} per call over {a['directional_calls']} scored calls)."
            )
    return out


def build_proposals(evidence: List[Dict[str, Any]], leaderboard: Dict[str, Any]) -> List[str]:
    props: List[str] = []
    for row in evidence:
        eq = row["vs"].get("ctl_equal")
        pl = row["vs"].get("ctl_placebo")
        if eq and eq["verdict"] == "edge":
            props.append(f"{row['name']} has beaten equal-weight hold out-of-sample ({eq['mean_excess_bps']:+.1f} bps/day, {eq['n_days']} live days): review it for a larger paper allocation.")
        if pl and pl["verdict"] == "worse":
            props.append(f"{row['name']} is doing worse than the placebo out-of-sample over {pl['n_days']} live days: review whether to retire it.")
    props += agent_proposals(leaderboard)
    return props


# --------------------------------------------------------------------- the digest --


def _pairs(rep: Dict[str, Any]) -> set:
    return {frozenset((p["a"], p["b"])) for p in rep["strongest_pairs"]}


def build_digest(book: paper.PriceBook, accounts: List[Dict[str, Any]], runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    d = book.latest_date
    if not d:
        return {"date": None, "text": "No price data available.", "proposals": []}
    idx = book.dates.index(d)
    week = book.dates[max(0, idx - 4): idx + 1]
    prev = book.dates[max(0, idx - 5)]
    reviews = [r for r in runs if r["date"] in week]
    reliable = [r for r in reviews if r.get("quorum_ok")]
    differ = [r for r in reliable if r.get("engine_signal") != (r.get("action") or r.get("decision"))]
    gated = [r for r in reliable if r.get("gate")]
    now, before = associations.association_report(book, d), associations.association_report(book, prev)
    evidence = evidence_table(accounts)
    board = strategy.agent_leaderboard(book, runs)
    proposals = build_proposals(evidence, board)

    L: List[str] = [f"{DIGEST_TITLE} — week ending {d}", f"{disclaimer.text()} Nothing in this digest changes anything automatically.", ""]
    gaps = book.gaps()
    if gaps:
        L += ["DATA QUALITY WARNING: the price history has hole(s): " + "; ".join(f"{g['from']} -> {g['to']} ({g['days']} calendar days)" for g in gaps[:5]) +
              ". Returns across a hole are not daily returns, so results that span it (backtests, volatility, correlations) are unreliable.", ""]

    L.append("1. What was analysed")
    L.append(f"   {now['symbols']} symbols read by the engine and the risk model every day. The committee made {len(reviews)} review(s) this week "
             f"({len(reliable)} reliable); it disagreed with the plain engine on {len(differ)} and the risk check held back {len(gated)} BUY(s).")
    L.append("")

    L.append("2. What the live paper strategies delivered (out-of-sample only)")
    live_days = max((r["live_days"] for r in evidence), default=0)
    L.append(f"   Live trading days so far: {live_days}. A verdict needs {MIN_LIVE_DAYS}; before that the honest answer is 'insufficient'.")
    for r in evidence:
        v = "; ".join(f"vs {BASELINES[b]}: {g['verdict']}" + (f" ({g['mean_excess_bps']:+.1f} bps/day)" if g["mean_excess_bps"] is not None else "") for b, g in r["vs"].items())
        lr = "n/a" if r["live_return"] is None else f"{r['live_return']:+.2%}"
        L.append(f"   - {r['name']}: live return {lr} over {r['live_days']} day(s) — {v or 'no yardstick available'}")
    L.append("")

    L.append("3. How the stocks move together")
    c, c0 = now["cohesion"], before["cohesion"]
    if c["value"] is not None:
        chg = "" if c0["value"] is None else f" (was {c0['value']:.2f} a week ago)"
        pct = "" if c["percentile"] is None else f", {c['percentile']:.0f}th percentile of its own history — {c['label']}"
        L.append(f"   Average pairwise correlation {c['value']:.2f}{chg}{pct}.")
    grp, grp0 = {tuple(g) for g in now["clusters"]}, {tuple(g) for g in before["clusters"]}
    L.append("   Groups moving as one bet: " + (", ".join("+".join(g) for g in now["clusters"]) if now["clusters"] else "none above the threshold") + ".")
    if grp != grp0:
        L.append(f"   Changed since last week: formed {sorted('+'.join(g) for g in grp - grp0) or 'none'}, dissolved {sorted('+'.join(g) for g in grp0 - grp) or 'none'}.")
    ll = now["lead_lag"]
    L.append(f"   Lead-lag: {ll['tests']} pair-and-lag combinations tested with a multiple-testing correction; " +
             (f"{len(ll['findings'])} survived: " + ", ".join(f"{f['leader']} leads {f['follower']} by {f['lag_days']}d (r={f['r']:+.2f})" for f in ll['findings'][:3]) if ll["findings"]
              else "none survived — no name reliably moves before another, which is the normal result for liquid large caps."))
    L.append("")

    L.append("4. Committee members")
    ranked = [a for a in board["agents"] if a["ranked"]]
    L.append(f"   {len(ranked)} of {len(board['agents'])} agents have the {board['min_ranked']} scored BUY/SELL calls needed to be ranked." +
             ("" if ranked else " Until then the leaderboard is context, not a verdict."))
    L.append("")

    L.append("5. Proposals (for a human to decide)")
    if proposals:
        L += [f"   - {p}" for p in proposals]
    else:
        L.append("   None. No strategy has cleared the evidence gate and no agent has enough scored calls, so the system is not suggesting any change. "
                 "It will keep measuring and say so here when that changes.")
    return {"date": d, "text": "\n".join(L), "proposals": proposals, "evidence": evidence, "associations": now}


def write_digest(digest: Dict[str, Any]) -> bool:
    key = f"research:{digest['date']}"
    if any(n.get("profile") == key for n in db.list_report_narratives()):
        return False

    # Publish through the single exit (research digest doesn't have committee run_ids)
    from . import publish
    result = publish.publish_simple(
        channel="research_digest",
        text=digest["text"],
        content_ref=key,
        is_html=False,
        writer_payload={
            "id": str(uuid.uuid4()),
            "date": digest["date"].replace("-", ""),
            "provider": "system",
            "model": "research-digest",
            "title": DIGEST_TITLE,
            "profile": key,
            "narrative": digest["text"],
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    # In enforce mode, held digests are not written
    if not result.allowed:
        log.info("Research digest for %s held by publish (enforce mode)", digest["date"])
        return False

    return True


def run_weekly(*, dry_run: bool = False, book: Optional[paper.PriceBook] = None) -> Dict[str, Any]:
    book = book or paper_cycle.load_book()
    digest = build_digest(book, db.list_paper_accounts(), db.list_all_committee_runs())
    if digest["date"] is None:
        return {"written": False, "digest": digest}
    written = False if dry_run else write_digest(digest)
    if written:
        db.log_audit("system", "research.weekly_digest", "research", None, {"date": digest["date"], "proposals": len(digest["proposals"])})
    return {"written": written, "dry_run": dry_run, "date": digest["date"], "proposals": len(digest["proposals"]), "digest": digest}


def view(book: paper.PriceBook) -> Dict[str, Any]:
    """Everything the admin's Research tab shows: today's associations, the evidence gate, standing proposals
    and the latest weekly digest."""
    accounts, runs = db.list_paper_accounts(), db.list_all_committee_runs()
    evidence = evidence_table(accounts)
    digests = [n for n in db.list_report_narratives() if n.get("title") == DIGEST_TITLE]
    latest = max(digests, key=lambda n: n.get("date", ""), default=None)
    d = book.latest_date
    return {
        "as_of": d,
        "associations": strategy._heavy("association_report", lambda: associations.association_report(book, d)) if d else None,
        "evidence": evidence,
        "min_live_days": MIN_LIVE_DAYS,
        "baselines": BASELINES,
        "proposals": build_proposals(evidence, strategy.agent_leaderboard(book, runs)),
        "latest_digest": {"title": latest["title"], "date": latest["date"], "narrative": latest["narrative"]} if latest else None,
    }
