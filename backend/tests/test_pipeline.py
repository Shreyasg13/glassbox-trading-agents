"""The event-driven daily pipeline: waits for the FINAL bar, idempotent, one at a time, honest about holidays."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app import pipeline as PL

SYMS = [f"S{i}" for i in range(10)]


def utc(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start):
        self.t = start
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += timedelta(seconds=s)


def synced(target, share=1.0, failed=()):
    """A stand-in price sync in which `share` of the universe already has the target bar."""
    have = int(round(len(SYMS) * share))
    latest = {s: (target if i < have else "1999-01-01") for i, s in enumerate(SYMS)}
    return {"ok": list(SYMS), "failed": list(failed), "new_bars": have, "rejected": [], "latest": target, "latest_by_symbol": latest}


class Calls:
    def __init__(self):
        self.order = []

    def stage(self, name, result=None, boom=None):
        def fn():
            self.order.append(name)
            if boom:
                raise boom
            return result

        return fn


def stages(calls, **over):
    base = {n: calls.stage(n) for n in PL.STAGE_ORDER}
    base.update(over)
    return base


# --------------------------------------------------------------------- calendar --


def test_nyse_holidays_match_the_published_2026_and_2027_schedules():
    h26 = {d.isoformat() for d in PL.nyse_holidays(2026)}
    assert h26 == {"2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25"}
    h27 = {d.isoformat() for d in PL.nyse_holidays(2027)}
    assert "2027-07-05" in h27 and "2027-07-04" not in h27  # Sunday July 4 -> observed Monday
    assert "2027-12-24" in h27  # Saturday Christmas -> observed Friday
    assert "2027-12-31" not in {d.isoformat() for d in PL.nyse_holidays(2027)} | {d.isoformat() for d in PL.nyse_holidays(2028)}  # Saturday New Year: NYSE stays open Dec 31
    assert PL.is_trading_day(date(2026, 9, 21)) and not PL.is_trading_day(date(2026, 9, 5)) and not PL.is_trading_day(date(2026, 9, 7))


def test_the_close_is_final_at_different_utc_times_in_summer_and_winter():
    assert PL.ready_at(date(2026, 7, 15)) == utc(2026, 7, 15, 20, 20)
    assert PL.ready_at(date(2026, 12, 1)) == utc(2026, 12, 1, 21, 20)


def test_plan_target_waits_for_todays_close_only_when_it_is_close():
    winter_20_35 = PL.plan_target(utc(2026, 12, 1, 20, 35))
    assert winter_20_35["target"] == "2026-12-01" and winter_20_35["wait_s"] == 45 * 60
    summer_20_35 = PL.plan_target(utc(2026, 9, 21, 20, 35))  # the close was final at 20:20 UTC
    assert summer_20_35["target"] == "2026-09-21" and summer_20_35["wait_s"] == 0
    morning = PL.plan_target(utc(2026, 12, 1, 14, 0))  # 09:00 ET: far too early to wait for
    assert morning["target"] == "2026-11-30" and morning["wait_s"] == 0
    assert PL.plan_target(utc(2026, 12, 1, 20, 35), wait=False)["target"] == "2026-11-30"


def test_weekends_and_holidays_target_the_last_trading_day():
    assert PL.plan_target(utc(2026, 9, 19, 15, 0))["target"] == "2026-09-18"  # Saturday
    assert PL.plan_target(utc(2026, 9, 7, 20, 35))["target"] == "2026-09-04"  # Labor Day: market closed all day
    assert PL.plan_target(utc(2026, 9, 8, 13, 0))["target"] == "2026-09-04"  # Tuesday 09:00 ET, Monday was a holiday


# --------------------------------------------------------------------- the run --


def test_a_normal_day_runs_every_stage_in_order_and_records_it(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    out = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls))
    assert out["status"] == "ok" and out["exit_code"] == 0 and out["target"] == "2026-09-21"
    assert calls.order == ["free_data", "committee", "paper_cycle", "score_ledger", "weekly_research", "snapshot", "mirror", "notifications", "digest_email", "user_digests", "weekly_report"]
    assert clock.slept == []  # the close was already final: no waiting, no polling
    rec = PL.last_status()
    assert rec["target"] == "2026-09-21" and all(v["ok"] for v in rec["stages"].values()) and rec["sync"]["coverage"] == 1.0
    assert PL._get(PL.LOCK_BLOB)["held"] is False


def test_it_sleeps_until_the_close_is_final_in_winter(real_db):
    clock, calls = Clock(utc(2026, 12, 1, 20, 35)), Calls()
    PL.run(clock.now, clock.sleep, lambda: synced("2026-12-01"), stages(calls))
    assert sum(clock.slept) == 45 * 60 and max(clock.slept) <= 600
    assert clock.now() >= PL.ready_at(date(2026, 12, 1))
    assert calls.order[0] == "free_data"


def test_it_polls_until_the_provider_publishes_the_bar(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    answers = iter([synced("2026-09-21", 0.0), synced("2026-09-21", 0.3), synced("2026-09-21", 1.0)])
    out = PL.run(clock.now, clock.sleep, lambda: next(answers), stages(calls), poll_s=300)
    assert out["sync"]["attempts"] == 3 and clock.slept == [300, 300] and out["status"] == "ok"


def test_no_bar_by_the_deadline_is_a_no_op_that_says_why(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    out = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21", 0.0), stages(calls), poll_s=600)
    assert out["status"] == "no_bar" and out["exit_code"] == 0 and calls.order == []
    assert clock.now() >= PL.ready_at(date(2026, 9, 21)) + timedelta(seconds=PL.DEADLINE_AFTER_READY_S)
    assert "holiday or provider delay" in PL.last_status()["message"]


def test_a_partial_bar_at_the_deadline_still_runs_and_is_flagged(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    out = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21", 0.6), stages(calls), poll_s=900)
    assert out["status"] == "ok" and out["partial"] is True and "paper_cycle" in calls.order


def test_a_market_holiday_needs_no_polling_at_all(real_db):
    clock, calls = Clock(utc(2026, 9, 7, 20, 35)), Calls()
    out = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-04"), stages(calls))
    assert out["target"] == "2026-09-04" and clock.slept == []


# ------------------------------------------------------------ idempotence / failures --


def test_the_safety_net_rerun_does_nothing_when_everything_succeeded(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls))
    calls.order.clear()
    again = PL.run(Clock(utc(2026, 9, 21, 23, 45)).now, lambda s: None, lambda: synced("2026-09-21"), stages(calls))
    assert calls.order == [] and again["status"] == "ok"


def test_a_rerun_retries_only_the_stage_that_failed(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    first = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls, committee=calls.stage("committee", boom=RuntimeError("quota"))))
    assert first["status"] == "partial" and first["exit_code"] == 2 and first["failed_stages"] == ["committee"]
    assert "paper_cycle" in calls.order  # a committee failure never blocks the paper cycle
    calls.order.clear()
    second = PL.run(Clock(utc(2026, 9, 21, 23, 45)).now, lambda s: None, lambda: synced("2026-09-21"), stages(calls))
    assert calls.order == ["committee"] and second["status"] == "ok"


def test_a_failed_paper_cycle_is_fatal_and_stops_the_rest(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    out = PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls, paper_cycle=calls.stage("paper_cycle", result=1)))
    assert out["status"] == "failed" and out["exit_code"] == 1
    assert "snapshot" not in calls.order and "mirror" not in calls.order


def test_nonzero_exit_codes_and_ok_false_dicts_count_as_failure(real_db):
    assert PL._ok(None) and PL._ok(0) and PL._ok({"ok": True}) and PL._ok({"anything": 1})
    assert not PL._ok(1) and not PL._ok(False) and not PL._ok({"ok": False})


def test_force_reruns_everything(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls))
    calls.order.clear()
    PL.run(Clock(utc(2026, 9, 21, 23, 45)).now, lambda s: None, lambda: synced("2026-09-21"), stages(calls), force=True)
    assert len(calls.order) == len(PL.STAGE_ORDER)


def test_a_price_source_that_always_fails_is_a_fatal_error_not_a_silent_skip(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()

    def down():
        raise ConnectionError("provider down")

    out = PL.run(clock.now, clock.sleep, down, stages(calls), poll_s=900)
    assert out["status"] == "failed" and out["exit_code"] == 1 and calls.order == []


def test_every_symbol_failing_to_update_is_fatal(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    dead = {"ok": [], "failed": list(SYMS), "new_bars": 0, "rejected": [], "latest": None, "latest_by_symbol": {}}
    out = PL.run(clock.now, clock.sleep, lambda: dead, stages(calls), poll_s=900)
    assert out["exit_code"] == 1 and calls.order == []


# --------------------------------------------------------------------------- lock --


def test_a_second_run_while_one_is_active_backs_off(real_db):
    now = utc(2026, 9, 21, 20, 35)
    PL._put(PL.LOCK_BLOB, {"held": True, "beat": (now - timedelta(minutes=5)).isoformat()}, now)
    calls = Calls()
    out = PL.run(lambda: now, lambda s: None, lambda: synced("2026-09-21"), stages(calls))
    assert out["status"] == "locked" and calls.order == []


def test_a_crashed_run_does_not_block_forever(real_db):
    now = utc(2026, 9, 21, 20, 35)
    PL._put(PL.LOCK_BLOB, {"held": True, "beat": (now - timedelta(hours=2)).isoformat()}, now)
    calls = Calls()
    out = PL.run(lambda: now, lambda s: None, lambda: synced("2026-09-21"), stages(calls))
    assert out["status"] == "ok" and calls.order


def test_the_lock_is_released_even_when_the_run_fails(real_db):
    clock, calls = Clock(utc(2026, 9, 21, 20, 35)), Calls()
    PL.run(clock.now, clock.sleep, lambda: synced("2026-09-21"), stages(calls, paper_cycle=calls.stage("paper_cycle", boom=RuntimeError("x"))))
    assert PL._get(PL.LOCK_BLOB)["held"] is False


# --------------------------------------------------------------------- real stages --


def test_the_weekly_digest_stage_runs_on_fridays_only(monkeypatch):
    from app import research

    ran = []
    monkeypatch.setattr(research, "run_weekly", lambda: ran.append(1) or {"written": True})
    PL.default_stages("2026-09-18")["weekly_research"]()  # a Friday
    PL.default_stages("2026-09-17")["weekly_research"]()  # a Thursday
    assert ran == [1]


def test_the_digest_email_stage_calls_digest_run(monkeypatch):
    from app import digest

    seen = []
    monkeypatch.setattr(digest, "run", lambda: seen.append(1) or {"ok": True, "sent": False, "detail": "not configured"})
    result = PL.default_stages("2026-09-18")["digest_email"]()
    assert seen == [1] and result == {"ok": True, "sent": False, "detail": "not configured"}


def test_the_weekly_report_stage_runs_mondays_only(monkeypatch):
    from app import weekly_report as wr

    calls = []
    monkeypatch.setattr(wr, "create_draft_for_week", lambda week_start: calls.append(week_start) or {"id": "r1"})
    assert PL.default_stages("2026-09-18")["weekly_report"]() == {"skipped": "not monday"}  # a Friday
    assert calls == []
    out = PL.default_stages("2026-09-21")["weekly_report"]()  # a Monday
    assert out == {"ok": True, "created": True} and calls == [wr.previous_week_start(date(2026, 9, 21))]


def test_the_weekly_report_stage_never_raises(monkeypatch):
    from app import weekly_report as wr

    def boom(week_start):
        raise RuntimeError("db not migrated")

    monkeypatch.setattr(wr, "create_draft_for_week", boom)
    out = PL.default_stages("2026-09-21")["weekly_report"]()  # a Monday: would run, but the DB call blows up
    assert out == {"ok": False, "error": "RuntimeError: db not migrated"}


def test_status_history_keeps_one_record_per_day_and_caps_its_length(real_db):
    now = utc(2026, 9, 21, 20, 35)
    for i in range(40):
        PL._record({"target": (date(2026, 1, 1) + timedelta(days=i)).isoformat(), "status": "ok"}, now)
    PL._record({"target": "2026-02-09", "status": "partial"}, now)  # same day again replaces, not appends
    hist = PL.status_history()
    assert len(hist) == PL.HISTORY_KEEP and hist[-1]["status"] == "partial" and len({h["target"] for h in hist}) == len(hist)
