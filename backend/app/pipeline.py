"""The daily pipeline: start when the close is FINAL, not at a fixed clock time.

    wait for the final bar -> sync prices -> free data -> committee -> paper cycle -> (Fri) weekly digest -> snapshot -> mirror

Why event-driven. The old schedule (sync 22:00, committee 22:15, paper 22:45 UTC) waited 2h after the US close
"to be safe" in summer, and in winter the close moves an hour later so the same times were tighter. Now one
process starts at 20:35 UTC, works out when today's session is final in New York time (16:15 ET + a buffer,
so 20:20 UTC in summer, 21:20 UTC in winter), sleeps until then, then polls the data provider until the day's
bar is actually there. Signals and the committee's review therefore land ~1.5h sooner in summer, and never
run on a half-day price.

"Real time" here means: as soon as the day's close is final. It is deliberately not intraday. This strategy
holds for months, trades on daily closes and defers taxes; a tick-level feed (none is free and licensed for a
product) would add cost, noise and short-term-gain tax without adding the edge the platform is built on.

Design rules
  * Idempotent. A run for a target date remembers which stages succeeded; the 23:45 safety-net run (and any
    manual re-run) does only what is still missing, so it is a no-op on a healthy day.
  * One at a time. A database lock with a heartbeat (stale after 75 minutes) stops two runs overlapping.
  * Non-fatal where it can be. Committee, free data, weekly digest, snapshot and mirror failing never stop the
    paper cycle (the committee account just follows the engine that day). No price data or a failed paper cycle
    is fatal and exits non-zero.
  * Holidays are a graceful no-op: the NYSE calendar is built in, so a closed day targets the last trading day
    (already done, nothing runs). If the provider never publishes an open day's bar by the deadline, nothing runs
    either, and the record says so.
  * Everything time-related is injectable (clock, sleep), so the whole flow is tested without waiting.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time as _time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import db, price_store

log = logging.getLogger("glassbox.pipeline")

READY_AFTER_ET = time(16, 20)  # the final-bar cutoff (16:15) plus a few minutes for the provider to publish
MAX_START_WAIT_S = 90 * 60  # never sleep longer than this waiting for the close (a manual 9am run just processes yesterday)
POLL_S = 300  # how often to ask the provider once the close is final
DEADLINE_AFTER_READY_S = 3 * 3600  # give up on today's bar this long after the close
READY_COVERAGE = 0.90  # share of the universe that must have today's bar before we proceed at once
PARTIAL_COVERAGE = 0.50  # at the deadline we proceed with at least this much, and flag it
LOCK_STALE_S = 75 * 60
STATUS_BLOB = "state/pipeline.json"
LOCK_BLOB = "state/pipeline.lock"
HISTORY_KEEP = 30

FATAL_STAGES = {"paper_cycle"}
STAGE_ORDER = ["free_data", "committee", "paper_cycle", "score_ledger", "weekly_research", "snapshot", "mirror", "notifications", "digest_email", "user_digests", "weekly_report"]


# ------------------------------------------------------------------- time --


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday_of(year: int, month: int, weekday: int) -> date:
    d = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: date) -> date:
    """A fixed-date holiday on a Saturday closes the market the Friday before, on a Sunday the Monday after."""
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def nyse_holidays(year: int) -> Dict[date, str]:
    """Full-day NYSE closures for a year (early 13:00 closes still produce a normal daily bar, so they are not listed)."""
    from dateutil.easter import easter

    out = {
        _nth_weekday(year, 1, 0, 3): "Martin Luther King Jr. Day",
        _nth_weekday(year, 2, 0, 3): "Presidents Day",
        easter(year) - timedelta(days=2): "Good Friday",
        _last_weekday_of(year, 5, 0): "Memorial Day",
        _observed(date(year, 6, 19)): "Juneteenth",
        _observed(date(year, 7, 4)): "Independence Day",
        _nth_weekday(year, 9, 0, 1): "Labor Day",
        _nth_weekday(year, 11, 3, 4): "Thanksgiving",
        _observed(date(year, 12, 25)): "Christmas",
    }
    ny = date(year, 1, 1)
    if ny.weekday() == 6:
        out[ny + timedelta(days=1)] = "New Year's Day"  # observed Monday
    elif ny.weekday() != 5:
        out[ny] = "New Year's Day"  # on a Saturday NYSE stays open the Friday before
    return out


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def last_trading_day(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def ready_at(et_date: date) -> datetime:
    """When that New York session's bar is safe to read, as an aware UTC datetime."""
    return datetime.combine(et_date, READY_AFTER_ET, tzinfo=price_store.ET).astimezone(timezone.utc)


def plan_target(now: datetime, wait: bool = True) -> Dict[str, Any]:
    """Which trading day this run is for, and how long to sleep until its close is final.

    On a weekday, before that day's close is final, and within MAX_START_WAIT_S of it: the target is TODAY and we
    wait. Otherwise (after the close, weekend, or a manual early run): the most recent completed weekday."""
    et_today = now.astimezone(price_store.ET).date()
    if wait and is_trading_day(et_today):
        r = ready_at(et_today)
        gap = (r - now).total_seconds()
        if 0 < gap <= MAX_START_WAIT_S:
            return {"target": et_today.isoformat(), "wait_s": gap, "ready_at": r}
    tgt = last_trading_day(date.fromisoformat(price_store.final_cutoff(now)))
    return {"target": tgt.isoformat(), "wait_s": 0.0, "ready_at": ready_at(tgt)}


# ------------------------------------------------------------------ state --


def _put(name: str, obj: Any, now: datetime) -> None:
    text = json.dumps(obj, sort_keys=True, default=str)
    db.put_blob(name, text, hashlib.sha256(text.encode()).hexdigest(), now.isoformat())


def _get(name: str) -> Optional[Dict[str, Any]]:
    b = db.get_blob(name)
    try:
        return json.loads(b["content"]) if b else None
    except ValueError:
        return None


def last_status() -> Optional[Dict[str, Any]]:
    """The most recent run's record ({target, stages, ...}) or None -- for the admin data-quality view."""
    s = _get(STATUS_BLOB)
    return s["runs"][-1] if s and s.get("runs") else None


def status_history() -> List[Dict[str, Any]]:
    s = _get(STATUS_BLOB)
    return list(s["runs"]) if s and s.get("runs") else []


def _acquire(now: datetime) -> bool:
    held = _get(LOCK_BLOB)
    if held and held.get("beat"):
        age = (now - datetime.fromisoformat(held["beat"])).total_seconds()
        if age < LOCK_STALE_S and held.get("held"):
            return False
    _put(LOCK_BLOB, {"held": True, "beat": now.isoformat()}, now)
    return True


def _beat(now: datetime) -> None:
    _put(LOCK_BLOB, {"held": True, "beat": now.isoformat()}, now)


def _release(now: datetime) -> None:
    _put(LOCK_BLOB, {"held": False, "beat": now.isoformat()}, now)


def _record(run: Dict[str, Any], now: datetime) -> None:
    prior = (_get(STATUS_BLOB) or {}).get("runs", [])
    runs = [r for r in prior if r.get("target") != run["target"]] + [run]
    _put(STATUS_BLOB, {"runs": runs[-HISTORY_KEEP:]}, now)


# ---------------------------------------------------------------- stages --


def _ok(result: Any) -> bool:
    if result is None or result is True:
        return True
    if isinstance(result, bool):
        return result
    if isinstance(result, int):
        return result == 0
    if isinstance(result, dict) and result.get("ok") is False:
        return False
    return True


def default_sync() -> Dict[str, Any]:
    from .scripts import update_daily_data

    return update_daily_data.run()


def default_stages(target: str) -> Dict[str, Callable[[], Any]]:
    """The real stage implementations (imported lazily so importing this module stays cheap)."""

    def free_data() -> Any:
        from .scripts import refresh_free_data

        return refresh_free_data.main([])

    def committee() -> Any:
        from .scripts import run_committee_daily

        return run_committee_daily.main([])

    def paper_cycle() -> Any:
        from .scripts import run_paper_cycle

        return run_paper_cycle.main([])

    def score_ledger() -> Any:
        from .scripts import score_ledger

        return score_ledger.main([])

    def weekly_research() -> Any:
        from . import research

        if date.fromisoformat(target).weekday() != 4:
            return None  # the digest is weekly: Fridays only
        return {"ok": True, "written": research.run_weekly().get("written")}

    def snapshot() -> Any:
        from . import paper_cycle as pc
        from . import snapshots

        book = pc.load_book()
        d = target if target in book.dates else book.latest_date
        if d != target:
            log.warning("snapshot: book ends %s, not the target %s", d, target)
        snapshots.take(book, d)

    def mirror() -> Any:
        from . import artifacts

        return artifacts.mirror()

    def digest_email() -> Any:
        from . import digest

        return digest.run()

    def notifications_stage() -> Any:
        from . import notifications

        return notifications.generate_daily()

    def user_digests() -> Any:
        from . import user_digest

        return user_digest.run()

    def weekly_report() -> Any:
        """The weekly discrepancy report draft (S3 T13): Mondays only, and this stage must never raise -- a failure
        here (e.g. the DB not migrated yet) must never hold up the rest of the pipeline."""
        if date.fromisoformat(target).weekday() != 0:
            return {"skipped": "not monday"}
        from . import weekly_report as wr

        try:
            row = wr.create_draft_for_week(wr.previous_week_start(date.fromisoformat(target)))
        except Exception as exc:  # noqa: BLE001 -- see docstring
            log.error("weekly_report stage failed: %s", exc)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "created": row is not None}

    return {
        "free_data": free_data, "committee": committee, "paper_cycle": paper_cycle, "score_ledger": score_ledger,
        "weekly_research": weekly_research, "snapshot": snapshot, "mirror": mirror, "notifications": notifications_stage, "digest_email": digest_email, "user_digests": user_digests,
        "weekly_report": weekly_report,
    }


# ------------------------------------------------------------------- run --


def _coverage(sync_result: Dict[str, Any], target: str) -> float:
    latest = sync_result.get("latest_by_symbol") or {}
    total = len(latest) + len(sync_result.get("failed", []))
    if not total:
        return 0.0
    return sum(1 for d in latest.values() if d and d >= target) / total


def run(
    now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    sleep: Callable[[float], None] = _time.sleep,
    sync: Callable[[], Dict[str, Any]] = default_sync,
    stages: Optional[Dict[str, Callable[[], Any]]] = None,
    wait: bool = True,
    force: bool = False,
    poll_s: float = POLL_S,
) -> Dict[str, Any]:
    """Run one pipeline pass. Returns {status, target, exit_code, stages, ...}. Never raises for a stage failure."""
    from . import flags

    if not flags.flag("pipeline.daily"):
        log.warning("pipeline.daily is switched off: skipping this run")
        return {"status": "disabled", "target": None, "exit_code": 0, "message": "the pipeline.daily flag is off", "stages": {}, "sync": None, "partial": False}
    started = now_fn()
    plan = plan_target(started, wait=wait)
    target = plan["target"]
    if not _acquire(started):
        log.info("another pipeline run is active (heartbeat fresh); exiting")
        return {"status": "locked", "target": target, "exit_code": 0}
    try:
        return _run_locked(started, plan, now_fn, sleep, sync, stages or default_stages(target), force, poll_s)
    finally:
        _release(now_fn())


def _run_locked(started: datetime, plan: Dict[str, Any], now_fn, sleep, sync, stages, force: bool, poll_s: float) -> Dict[str, Any]:
    target = plan["target"]
    prior = next((r for r in status_history() if r.get("target") == target), None)
    already_ok = set() if force or not prior else {k for k, v in (prior.get("stages") or {}).items() if v.get("ok")}
    run_rec: Dict[str, Any] = {"target": target, "started": started.isoformat(), "stages": dict((prior or {}).get("stages") or {}) if not force else {}, "status": "running"}

    if plan["wait_s"] > 0:
        log.info("waiting %.0f min for the %s close to be final (ready %s UTC)", plan["wait_s"] / 60, target, plan["ready_at"].strftime("%H:%M"))
        remaining = plan["wait_s"]
        while remaining > 0:
            step = min(remaining, 600)
            sleep(step)
            remaining -= step
            _beat(now_fn())

    # ---- prices: poll until the target day's bar is there
    deadline = plan["ready_at"] + timedelta(seconds=DEADLINE_AFTER_READY_S)
    sync_res: Dict[str, Any] = {}
    coverage = 0.0
    attempts = 0
    while True:
        attempts += 1
        try:
            sync_res = sync()
        except Exception as exc:  # noqa: BLE001
            log.error("price sync raised %s: %s", type(exc).__name__, exc)
            sync_res = {"ok": [], "failed": ["*"], "latest_by_symbol": {}}
        coverage = _coverage(sync_res, target)
        _beat(now_fn())
        if coverage >= READY_COVERAGE:
            break
        if now_fn() >= deadline:
            break
        log.info("target bar %s not there yet (coverage %.0f%%); polling again in %.0f s", target, coverage * 100, poll_s)
        sleep(poll_s)
    run_rec["sync"] = {"attempts": attempts, "coverage": round(coverage, 3), "new_bars": sync_res.get("new_bars"), "failed": sync_res.get("failed"), "rejected": len(sync_res.get("rejected") or [])}

    if not sync_res.get("ok") and sync_res.get("failed"):
        return _finish(run_rec, "failed", "price sync failed for every symbol", 1, now_fn)
    if coverage < PARTIAL_COVERAGE:
        # Nothing new to trade on: a market holiday, or the provider is late/down. Either way, do not run the model stages.
        return _finish(run_rec, "no_bar", f"no final bar for {target} by the deadline (holiday or provider delay)", 0, now_fn)
    run_rec["partial"] = coverage < READY_COVERAGE

    # ---- the rest, in order
    exit_code, failed_stages = 0, []
    for name in STAGE_ORDER:
        fn = stages.get(name)
        if fn is None:
            continue
        if name in already_ok:
            log.info("stage %s already done for %s; skipping", name, target)
            continue
        t0 = _time.monotonic()
        try:
            res = fn()
            ok, detail = _ok(res), (res if isinstance(res, (dict, int, str)) else None)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
            log.error("stage %s failed: %s", name, detail)
        run_rec["stages"][name] = {"ok": ok, "seconds": round(_time.monotonic() - t0, 1), "detail": _small(detail)}
        _beat(now_fn())
        if not ok:
            failed_stages.append(name)
            if name in FATAL_STAGES:
                return _finish(run_rec, "failed", f"{name} failed", 1, now_fn, failed_stages)
            exit_code = 2
    return _finish(run_rec, "partial" if failed_stages else "ok", "" if not failed_stages else "non-fatal stage(s) failed: " + ", ".join(failed_stages), exit_code, now_fn, failed_stages)


def _small(detail: Any) -> Any:
    text = json.dumps(detail, default=str) if not isinstance(detail, str) else detail
    return text[:300] if detail is not None else None


def _finish(run_rec: Dict[str, Any], status: str, message: str, code: int, now_fn, failed: Optional[List[str]] = None) -> Dict[str, Any]:
    now = now_fn()
    run_rec.update({"status": status, "message": message, "finished": now.isoformat(), "failed_stages": failed or []})
    try:
        _record(run_rec, now)
    except Exception as exc:  # noqa: BLE001 -- bookkeeping must never mask the run's real outcome
        log.warning("could not record pipeline status (%s)", type(exc).__name__)
    log.info("pipeline %s for %s: %s", status, run_rec["target"], message or "all stages done")
    return dict(run_rec, exit_code=code)
