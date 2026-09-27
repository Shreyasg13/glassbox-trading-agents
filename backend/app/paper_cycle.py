"""Daily paper-trading cycle + the read models the admin API serves.

run_cycle() is what cron calls once per weekday after the market-data sync
(app/scripts/run_paper_cycle.py) and what POST /api/admin/paper/run triggers:

  1. Load prices + engine signals for every tracked symbol (PriceBook).
  2. Make sure every account exists: one per demo profile (+ a same-universe
     "policy" benchmark for each) and a fixed set of controls (SPY buy&hold,
     equal-weight, the engine on all symbols, a placebo, cash).
  3. Replay every trading day each account has not yet processed. The very
     first run (--bootstrap) backfills history from BACKTEST_START, tagged
     "backtest"; every day after live_from is tagged "live" -- genuine
     out-of-sample paper trading (see app/paper.py).
  4. Persist accounts, record the live days' signals, and write one daily
     report per profile into report_narratives (provider "system") so the admin
     Reports page shows what each simulated portfolio did and why.

Accounts are frozen at creation: changing a user's watchlist or profile later
does NOT rewrite their history. (It would corrupt the equity curve.) Reset an
account by deleting its row and re-running.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from . import data_source as ds
from . import db, paper, paper_profiles

log = logging.getLogger("glassbox.paper")

BACKTEST_START = os.environ.get("PAPER_BACKTEST_START", "2021-01-04")  # includes the 2022 bear market
DEFAULT_CASH = 100_000.0

# The lite Gemini models carry the highest free-tier daily quota (500/day vs 20
# for the flash models -- see providers/gemini_quota.py), so the optional daily
# narration tries them first.
LLM_NOTE_CHAIN = ["gemini-3.1-flash-lite", "gemini-flash-latest"]

_cycle_lock = threading.Lock()


class CycleError(RuntimeError):
    """A cycle could not run (not initialised, no data, already running)."""


# ------------------------------------------------------------------ setup --


def committee_views() -> Dict[str, Dict[str, str]]:
    """symbol -> decision date -> the committee's risk-checked action, for every reliable
    review on record (a low-quorum call carries no action and is left out, so the
    committee account falls back to the engine there). Best-effort: a database hiccup must
    never stop the paper cycle -- the account then simply follows the engine that day."""
    try:
        runs = db.list_all_committee_runs()
    except Exception as exc:  # noqa: BLE001
        log.warning("committee views unavailable (%s); committee_tilt follows the engine", type(exc).__name__)
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for r in runs:
        action = r.get("action") or (r.get("decision") if r.get("quorum_ok") else None)
        if action in ("BUY", "SELL", "HOLD") and r.get("date") and r.get("symbol"):
            out.setdefault(r["symbol"], {})[r["date"]] = action
    return out


def challenger_views() -> Dict[str, Dict[str, Dict[str, str]]]:
    """source -> symbol -> decision date -> action, for every outside challenger with recorded calls."""
    try:
        rows = db.list_challenger_decisions()
    except Exception as exc:  # noqa: BLE001 -- the arena must never stop the paper cycle
        log.warning("challenger decisions unavailable (%s)", type(exc).__name__)
        return {}
    out: Dict[str, Dict[str, Dict[str, str]]] = {}
    for r in rows:
        if r.get("action") in ("BUY", "SELL", "HOLD") and r.get("source") and r.get("date") and r.get("symbol"):
            out.setdefault(r["source"], {}).setdefault(r["symbol"], {})[r["date"]] = r["action"]
    return out


def _challenger_accounts(book: paper.PriceBook) -> List[Dict[str, Any]]:
    equal = {s: 1.0 for s in book.symbols}
    moderate = paper.RISK_POLICY["moderate"]["invested"]
    return [
        paper.new_account(
            f"chal_{src}", f"Challenger: {src}", "control", "external_tilt", equal, invested=moderate, start_cash=DEFAULT_CASH, risk_level="moderate", source=src,
            note=f"Trades exactly on {src}'s recorded daily BUY/SELL/HOLD calls, on the same stocks and rules as 'Engine on all symbols'. A stock it made no fresh call on is neutral, not backed by our engine.",
        )
        for src in sorted(book.external)
        if equal
    ]


def load_book() -> paper.PriceBook:
    params = ds._load_trained_params()
    frames = {}
    for sym in ds.STOCK_INFO:
        df = ds._load_parquet_row(sym)
        if df is not None:
            frames[sym] = df
    book = paper.PriceBook.from_frames(frames, params)
    book.set_committee(committee_views())
    for src, decisions in challenger_views().items():
        book.set_external(src, decisions)
    return book


def _control_accounts(book: paper.PriceBook) -> List[Dict[str, Any]]:
    equal = {s: 1.0 for s in book.symbols}
    moderate = paper.RISK_POLICY["moderate"]["invested"]
    controls = [  # (id, name, strategy, weights, invested, risk, note[, tax_status[, placebo seed]])
        ("ctl_spy", "SPY buy & hold", "static_hold", {"SPY": 1.0}, 1.0, None, "The market: 100% SPY, never traded."),
        ("ctl_equal", "Equal-weight buy & hold", "static_hold", equal, 1.0, None, "Every tracked symbol, equal weight, never traded."),
        ("ctl_engine", "Engine on all symbols", "engine_tilt", equal, moderate, "moderate", "The quant engine's own signals across the whole universe."),
        ("ctl_placebo", "Placebo (shifted signals)", "random_tilt", equal, moderate, "moderate",
         "Same as the engine, but each symbol acts on a DIFFERENT symbol's signals. If the engine can't beat this its signals carry no information."),
        ("ctl_committee", "Committee on all symbols", "committee_tilt", equal, moderate, "moderate",
         "Trades exactly like 'Engine on all symbols', except a symbol the Investment Committee reviewed follows the committee's risk-checked call. "
         "The gap between the two is what the committee added -- and it can only differ on live days."),
        ("ctl_taxaware", "Tax-aware engine", "engine_taxaware", equal, moderate, "moderate",
         "The engine built for a TAXABLE account: sells losses and long-term gains first, holds short-term gains unless risk turns HIGH, acts only on signals that persist "
         "5 days, rebalances less often, and never buys into a HIGH-risk regime. Judge it by its after-tax result."),
        ("ctl_engine_ira", "Engine in a tax-sheltered account", "engine_tilt", equal, moderate, "moderate",
         "The same trades as 'Engine on all symbols', but inside an IRA / 401k / Roth-style account: no tax on realised gains, no wash-sale rule, costs only.", "sheltered"),
        ("ctl_placebo_ira", "Placebo in a tax-sheltered account", "random_tilt", equal, moderate, "moderate",
         "The placebo (same shifted signals as the taxable placebo) inside a sheltered account: the bar the sheltered engine has to clear.", "sheltered", "ctl_placebo"),
        ("ctl_trend", "Trend filter (200-day)", "trend_filter", equal, 1.0, None,
         "No engine signals. Holds each stock only while its close at the end of last month was above its 200-day average, otherwise cash. Checked monthly, so it trades rarely."),
        ("ctl_trend_ira", "Trend filter in a tax-sheltered account", "trend_filter", equal, 1.0, None, "The same trades as the taxable trend filter, inside an IRA / 401k / Roth-style account.", "sheltered"),
        ("ctl_voltarget", "Volatility-targeted", "vol_target", equal, 1.0, None,
         "No engine signals. Holds the same stocks but shrinks the whole position when the basket's 60-day volatility exceeds a 15% target and restores it when calm. Never uses leverage."),
        ("ctl_voltarget_ira", "Volatility-targeted in a tax-sheltered account", "vol_target", equal, 1.0, None, "The same trades as the taxable volatility-targeted account, inside an IRA / 401k / Roth-style account.", "sheltered"),
        ("ctl_cash", "Cash", "cash", {}, 0.0, None, "Sits in cash. The floor any strategy must beat."),
    ]
    out = []
    for cid, name, strat, weights, invested, risk, note, *extra in controls:
        weights = {s: w for s, w in weights.items() if s in book.close}
        if strat != "cash" and not weights:
            continue
        out.append(
            paper.new_account(
                cid, name, "control", strat, weights, invested=invested, start_cash=DEFAULT_CASH, risk_level=risk, note=note,
                tax_status=extra[0] if extra else "taxable", seed=extra[1] if len(extra) > 1 else None,
            )
        )
    return out


def _cohort(users: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Who gets a paper account: every profile in the registry (no login needed),
    plus any real user row that carries a `profile` the registry doesn't already
    cover. Registry entries win on a username clash."""
    cohort = [
        {"username": p["username"], "username_lower": p["username"].lower(), "role": "viewer", "tickers": p["tickers"], "profile": p["profile"]}
        for p in paper_profiles.DEMO_PROFILES
    ]
    seen = {c["username_lower"] for c in cohort}
    for u in users:
        if u.get("username_lower") not in seen:
            cohort.append(u)
    return cohort


def _profile_accounts(users: Iterable[Dict[str, Any]], book: paper.PriceBook) -> List[Dict[str, Any]]:
    out = []
    for u in _cohort(users):
        prof = u.get("profile")
        if not prof or u.get("role") == "admin":
            continue
        tickers = [t for t in (u.get("tickers") or []) if t in book.close]
        if not tickers:
            continue
        risk = prof.get("risk_level") if prof.get("risk_level") in paper.RISK_POLICY else "moderate"
        invested = paper.RISK_POLICY[risk]["invested"]
        weights = {t: w for t, w in (prof.get("strategic_weights") or {}).items() if t in tickers} or {t: 1.0 for t in tickers}
        cash = float(prof.get("starting_cash") or DEFAULT_CASH)
        base = u["username_lower"]
        common = dict(invested=invested, start_cash=cash, risk_level=risk, username=u["username"], profile=prof)
        out.append(paper.new_account(f"profile:{base}", u["username"], "profile", "engine_tilt", weights, benchmark_id=f"bench:{base}", **common))
        out.append(
            paper.new_account(
                f"committee:{base}", f"{u['username']} (committee-run)", "twin", "committee_tilt", weights, benchmark_id=f"profile:{base}",
                note="Same stocks, weights and cash as the profile, but follows the Investment Committee's risk-checked call where it has one. "
                "Its alpha is measured against the engine-only profile, so the gap is what the committee added for this user.", **common,
            )
        )
        out.append(
            paper.new_account(
                f"bench:{base}", f"{u['username']} policy benchmark", "benchmark", "static_rebalanced", weights,
                note="Same weights and rebalancing as the profile, signals ignored -- the profile's alpha is measured against this.", **common,
            )
        )
    return out


def ensure_accounts(existing: Dict[str, Dict[str, Any]], users: Iterable[Dict[str, Any]], book: paper.PriceBook) -> Dict[str, Dict[str, Any]]:
    """Existing accounts are kept exactly as stored; only missing ones are created."""
    accounts = dict(existing)
    for acct in _control_accounts(book) + _challenger_accounts(book) + _profile_accounts(users, book):
        accounts.setdefault(acct["id"], acct)
    return accounts


def _refresh_holdings(acct: Dict[str, Any], book: paper.PriceBook) -> None:
    d = acct["last_date"]
    if d is None:
        return
    eq = paper.equity(acct, book, d)
    holdings = {}
    for sym, shares in acct["positions"].items():
        px = book.close_on(sym, d) or 0.0
        holdings[sym] = {"shares": round(shares, 6), "price": round(px, 4), "value": round(shares * px, 2), "weight": round(shares * px / eq, 4) if eq else 0.0}
    acct["holdings"] = holdings
    acct["equity"] = round(eq, 2)
    paper.refresh_unrealized(acct, book, d)


# ----------------------------------------------------------------- reports --


def _day_change(acct: Dict[str, Any]) -> Optional[float]:
    c = acct["curve"]
    return c[-1][1] / c[-2][1] - 1 if len(c) > 1 and c[-2][1] else None


def build_report_text(acct: Dict[str, Any], bench: Optional[Dict[str, Any]], book: paper.PriceBook, d: str) -> str:
    s = paper.summarize(acct, bench)
    prof = acct.get("profile") or {}
    lines = [
        f"{acct['name']} -- daily paper-trading report for {d}",
        f"Profile: {prof.get('archetype', 'n/a')} · {acct.get('risk_level') or 'n/a'} risk · {prof.get('horizon_years', '?')}-year horizon. "
        "SIMULATED portfolio -- not real money, not investment advice.",
        "",
    ]
    chg = _day_change(acct)
    lines.append(f"Equity ${s['equity']:,.0f}" + (f" ({chg:+.2%} today)" if chg is not None else "") + f" · {s['total_return']:+.1%} since {s['inception']}")
    if s["alpha"] is not None:
        lines.append(f"Versus its policy benchmark ({s['benchmark_return']:+.1%}): alpha {s['alpha']:+.1%} over the whole history (the backtest part is in-sample).")
    if s["live_return"] is not None:
        la = f", alpha {s['live_alpha']:+.1%}" if s["live_alpha"] is not None else ""
        lines.append(f"Live (out-of-sample) so far: {s['live_return']:+.1%} over {s['live_days']} trading day(s){la}.")
    lines.append(f"Risk: max drawdown {s['max_drawdown']:.1%}, Sharpe {s['sharpe']:.2f}, {s['trade_count']} trades, ${s['cost_paid']:,.0f} in costs.")
    lines.append("")
    hold = acct.get("holdings") or {}
    if hold:
        lines.append("Holdings: " + ", ".join(f"{sym} {h['weight']:.0%}" for sym, h in sorted(hold.items(), key=lambda kv: -kv[1]["weight"])) + f", cash {s['cash_weight']:.0%}.")
    else:
        lines.append(f"Holdings: all cash ({s['cash_weight']:.0%}).")
    todays = [t for t in acct["trades"] if t["date"] == d]
    lines.append("Trades executed today: " + ("; ".join(f"{t['side']} {t['shares']:.2f} {t['symbol']} @ {t['price']:.2f} ({t['reason']})" for t in todays) if todays else "none."))
    sigs = book.signals_on(d, list(acct["weights"]))
    if sigs:
        lines.append("Engine signals on this watchlist: " + ", ".join(f"{x['symbol']} {x['signal']} (RSI {x['rsi']:.0f})" for x in sigs) + ".")
    pend = acct.get("pending")
    if pend:
        lines.append("Queued for the next close: " + ", ".join(f"{sym} -> {w:.0%}" for sym, w in sorted(pend["weights"].items())) + ".")
    return "\n".join(lines)


def _llm_note(context: str) -> Optional[str]:
    """Optional 2-3 sentence commentary. Off unless PAPER_LLM_NARRATIVES=1, and
    any failure (quota, 404, timeout) just means the report goes out without
    it -- the factual report never depends on an LLM."""
    if os.environ.get("PAPER_LLM_NARRATIVES") != "1":
        return None
    try:
        from . import llm_router

        prompt = (
            "You are reviewing a SIMULATED paper-trading portfolio. In 2-3 plain sentences, say what drove today's result "
            "and one thing worth watching. Do not give investment advice.\n\n" + context
        )
        # Routed: if Gemini's free quota is spent, any configured failover provider answers.
        routed = asyncio.run(
            asyncio.wait_for(
                llm_router.complete_routed("gemini", LLM_NOTE_CHAIN[0], prompt, fallback_models=LLM_NOTE_CHAIN[1:], max_tokens=200, temperature=0.3), 90
            )
        )
        return routed.text.strip() or None
    except Exception as exc:  # noqa: BLE001 -- deliberately best-effort
        log.warning("paper llm note skipped: %s", type(exc).__name__)
        return None


def _write_reports(accounts: Dict[str, Dict[str, Any]], book: paper.PriceBook, d: str) -> int:
    have = {(n.get("profile"), n.get("date")) for n in db.list_report_narratives() if n.get("provider") == "system"}
    stamp = d.replace("-", "")
    written = 0
    for acct in accounts.values():
        if acct["kind"] != "profile" or (acct["id"], stamp) in have:
            continue
        text = build_report_text(acct, accounts.get(acct.get("benchmark_id") or ""), book, d)
        note = _llm_note(text)
        if note:
            text += "\n\nAnalyst note (AI-generated, may be wrong):\n" + note

        # Publish through the single exit (paper reports don't have committee run_ids)
        from . import publish
        provider_model = "paper-engine" + ("+llm" if note else "")
        result = publish.publish_simple(
            channel="paper_report",
            text=text,
            content_ref=acct["id"],
            is_html=False,
            writer_payload={
                "id": str(uuid.uuid4()),
                "date": stamp,
                "provider": "system",
                "model": provider_model,
                "title": f"{acct['name']} · paper-trading report",
                "profile": acct["id"],
                "narrative": text,
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # In enforce mode, held reports are not written
        if not result.allowed:
            log.info("Paper report for %s held by publish (enforce mode)", acct["id"])
            continue

        written += 1
    return written


# ---------------------------------------------------------------- rebuild --


def rebuild_accounts(*, apply: bool = False, book: Optional[paper.PriceBook] = None, allow_differences: bool = False) -> Dict[str, Any]:
    """Replay every stored account from scratch, from ITS OWN stored definition (not the
    users' current profiles, which may have changed since -- accounts are frozen).

    The engine is deterministic, so a fresh replay must reproduce the stored equity curve to
    the cent. That is what makes this safe: it is how accounts created before tax tracking get
    their lots, and the check proves nothing else changed. An account whose replay does not
    match (e.g. a committee account after a forced re-review) is reported and left alone.
    With apply=False nothing is written. `allow_differences` is for when the PRICES themselves were corrected
    (e.g. a data gap was backfilled): every account is then replayed from scratch and replaced, and the result
    lists each account's old and new total return so the change is visible rather than silent."""
    meta = db.get_paper_meta()
    if meta is None:
        raise CycleError("paper trading is not initialised")
    book = book or load_book()
    report: Dict[str, str] = {}
    changes: Dict[str, Dict[str, Any]] = {}
    replaced = 0
    for old in db.list_paper_accounts():
        fresh = paper.new_account(
            old["id"], old["name"], old["kind"], old["strategy"], old["weights"], invested=old["invested"], start_cash=old["start_cash"],
            risk_level=old.get("risk_level"), username=old.get("username"), benchmark_id=old.get("benchmark_id"), profile=old.get("profile"), note=old.get("note", ""),
            tax_status=old.get("tax_status", "taxable"), seed=old.get("seed"), source=old.get("source"),
        )
        paper.advance(fresh, book, live_from=meta["live_from"], start=meta["start"], upto=old["last_date"])
        _refresh_holdings(fresh, book)
        n = len(old["curve"])
        same = len(fresh["curve"]) == n and all(a[0] == b[0] and abs(a[1] - b[1]) < 0.01 and a[2] == b[2] for a, b in zip(old["curve"], fresh["curve"]))
        report[old["id"]] = "identical" if same else "DIFFERS"
        changes[old["id"]] = {"old_total": (old["curve"][-1][1] / old["start_cash"] - 1) if old["curve"] else None, "new_total": (fresh["curve"][-1][1] / fresh["start_cash"] - 1) if fresh["curve"] else None}
        if apply and (same or allow_differences):
            db.save_paper_account(fresh)
            replaced += 1
    return {"applied": apply, "identical": sum(v == "identical" for v in report.values()), "differs": sorted(k for k, v in report.items() if v != "identical"), "replaced": replaced, "accounts": report, "changes": changes}


# ------------------------------------------------------------------ cycle --


def run_cycle(
    *,
    bootstrap: bool = False,
    start: Optional[str] = None,
    dry_run: bool = False,
    book: Optional[paper.PriceBook] = None,
    users: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if not _cycle_lock.acquire(blocking=False):
        raise CycleError("a paper-trading cycle is already running")
    try:
        book = book or load_book()
        if not book.dates:
            raise CycleError("no price data available (is TRADING_STORAGE_PATH populated?)")
        meta = db.get_paper_meta()
        if meta is None:
            if not bootstrap:
                raise CycleError("paper trading is not initialised -- run once with --bootstrap")
            first_live = date.fromisoformat(book.latest_date) + timedelta(days=1)
            meta = {"live_from": first_live.isoformat(), "start": start or BACKTEST_START, "bootstrapped_at": datetime.now(timezone.utc).isoformat(), "last_date": None}
        elif bootstrap:
            raise CycleError("already bootstrapped -- run without --bootstrap for the daily cycle")

        users = db.list_users() if users is None else users
        existing = {a["id"]: a for a in db.list_paper_accounts()}
        accounts = ensure_accounts(existing, users, book)
        created = sorted(set(accounts) - set(existing))

        processed = {aid: paper.advance(a, book, live_from=meta["live_from"], start=meta["start"]) for aid, a in accounts.items()}
        prev_last = meta.get("last_date")
        new_live = [d for d in book.dates if d >= meta["live_from"] and (prev_last is None or d > prev_last)]
        for a in accounts.values():
            _refresh_holdings(a, book)

        reports = 0
        if not dry_run:
            for aid, a in accounts.items():
                if processed[aid] or aid in created:
                    db.save_paper_account(a)
            for d in new_live:
                db.save_paper_signals(d, {"mode": "live", "signals": book.signals_on(d)})
            if new_live:
                reports = _write_reports(accounts, book, new_live[-1])
            meta.update({"last_date": book.latest_date, "last_run": datetime.now(timezone.utc).isoformat()})
            db.save_paper_meta(meta)
        result = {
            "bootstrapped": bootstrap,
            "dry_run": dry_run,
            "latest_data_date": book.latest_date,
            "live_from": meta["live_from"],
            "backtest_start": meta["start"],
            "accounts": len(accounts),
            "accounts_created": created,
            "days_processed": {k: v for k, v in processed.items() if v},
            "new_live_days": new_live,
            "reports_written": reports,
        }
        log.info("paper cycle: %s", {k: v for k, v in result.items() if k != "days_processed"})
        return result
    finally:
        _cycle_lock.release()


# ------------------------------------------------------------- read models --


def downsample(curve: List[List[Any]], max_points: int) -> List[List[Any]]:
    if len(curve) <= max_points:
        return curve
    step = (len(curve) - 1) / (max_points - 1)
    idx = sorted({round(i * step) for i in range(max_points)} | {len(curve) - 1})
    return [curve[i] for i in idx]


def overview() -> Dict[str, Any]:
    meta = db.get_paper_meta()
    accounts = db.list_paper_accounts()
    by_id = {a["id"]: a for a in accounts}
    rows = [paper.summarize(a, by_id.get(a.get("benchmark_id") or "")) for a in accounts]
    rows.sort(key=lambda r: (r["kind"] != "profile", -(r["total_return"] or 0)))
    return {"initialised": meta is not None, "meta": meta, "accounts": rows}


def account_detail(account_id: str, max_points: int = 400) -> Optional[Dict[str, Any]]:
    acct = db.get_paper_account(account_id)
    if acct is None:
        return None
    bench = db.get_paper_account(acct["benchmark_id"]) if acct.get("benchmark_id") else None
    curve = downsample(acct["curve"], max_points)
    keep = {p[0] for p in curve}
    return {
        "summary": paper.summarize(acct, bench),
        "weights": acct["weights"],
        "invested": acct["invested"],
        "note": acct.get("note", ""),
        "holdings": acct.get("holdings", {}),
        "cash": round(acct["cash"], 2),
        "last_targets": acct.get("last_targets", {}),
        "pending": acct.get("pending"),
        "curve": curve,
        "benchmark_curve": [p for p in (bench["curve"] if bench else []) if p[0] in keep],
        "recent_trades": list(reversed(acct["trades"][-60:])),
    }


_scorecard_cache: Dict[str, Any] = {}


def scorecard(force: bool = False) -> Dict[str, Any]:
    """Signal hit-rates over the full history. Cached per data date: it
    re-reads every price file, so it should not run on every page view."""
    d = None
    try:
        d = max((p.stat().st_mtime for p in ds.live_signals_source_paths() if p.exists()), default=None)
    except OSError:
        pass
    if not force and _scorecard_cache.get("key") == d and "value" in _scorecard_cache:
        return _scorecard_cache["value"]
    value = paper.signal_scorecard(load_book())
    _scorecard_cache.update({"key": d, "value": value})
    return value
