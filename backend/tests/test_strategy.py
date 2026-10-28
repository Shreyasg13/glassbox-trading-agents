"""Strategy read models (leaderboard, stance, track record), their endpoints, the admin ask
console, and the verified account rebuild. Prices and the database are synthetic/in-memory."""
from __future__ import annotations

import copy
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import auth, committee_daily, paper, paper_cycle, strategy
from app.main import app
from tests.test_committee_daily import FakeDB as CommitteeFakeDB
from tests.test_committee_daily import agents_result
from tests.test_paper_cycle import FakeDB as PaperFakeDB
from tests.test_paper_cycle import N_FULL, N_SHORT, PARAMS, USERS, _frame, day, fake, make_book  # noqa: F401  (fake is a fixture)


def _hdr(sub, role):
    return {"Authorization": "Bearer " + auth.create_access_token(auth.TokenPayload(sub=sub, role=role))}


ADMIN, VIEWER = _hdr("admin", "admin"), _hdr("viewer", "viewer")


def agent(name, lean, kind="llm", ok=True, confidence=None):
    a = {"agent": name, "type": kind, "ok": ok, "lean": lean}
    if confidence is not None:
        a["confidence"] = confidence
    if not ok:
        a.pop("lean")
        a["error"] = "HTTP 429"
    return a


# --------------------------------------------------------------- leaderboard --


def test_the_leaderboard_scores_directional_calls_and_ignores_holds_failures_and_unreliable_runs():
    book = make_book(N_FULL)
    d = day(20)
    fwd = strategy._fwd_return(book, "AAA", d, 5)
    assert fwd is not None and fwd != 0
    runs = [
        {"symbol": "AAA", "date": d, "quorum_ok": True, "decision": "BUY", "engine_signal": "HOLD",
         "agents": [agent("Bull", "BUY", confidence=80), agent("Bear", "SELL", confidence=60), agent("Fence", "HOLD"), agent("Eng", "HOLD", "deterministic"), agent("Down", None, ok=False)]},
        {"symbol": "AAA", "date": day(30), "quorum_ok": False, "decision": "BUY", "engine_signal": "HOLD", "agents": [agent("Bull", "BUY")]},  # unreliable: not counted
    ]
    lb = strategy.agent_leaderboard(book, runs, horizon=5, min_ranked=1)
    rows = {r["agent"]: r for r in lb["agents"]}
    assert set(rows) == {"Bull", "Bear", "Fence", "Eng"}  # the failed agent and the unreliable run are absent
    assert rows["Bull"]["answers"] == 1 and rows["Bull"]["mean_edge"] == pytest.approx(fwd) and rows["Bear"]["mean_edge"] == pytest.approx(-fwd)
    assert {rows["Bull"]["hit_rate"], rows["Bear"]["hit_rate"]} == {0.0, 1.0}  # exactly one of a BUY and a SELL on the same stock is right
    assert rows["Bull"]["avg_confidence"] == 80 and rows["Fence"]["avg_confidence"] is None
    assert rows["Bull"]["agrees_with_committee"] == 1.0 and rows["Bear"]["agrees_with_committee"] == 0.0 and rows["Eng"]["agrees_with_engine"] == 1.0
    assert rows["Fence"]["directional_calls"] == 0 and rows["Fence"]["hit_rate"] is None and rows["Fence"]["ranked"] is False
    order = [r["agent"] for r in lb["agents"]]
    assert set(order[:2]) == {"Bull", "Bear"} and order[:2] == sorted(order[:2], key=lambda n: -rows[n]["mean_edge"])  # ranked first, best edge first


def test_the_leaderboard_finds_pivotal_agents_scores_calibration_and_counts_failures():
    book = make_book(N_FULL)
    d = day(20)
    fwd = strategy._fwd_return(book, "AAA", d, 5)
    spy = strategy._fwd_return(book, "SPY", d, 5)
    assert fwd is not None and spy is not None and fwd != spy
    swing = dict(agent("Swing", "BUY", confidence=80), structured=True, p_beat_spy=70, model="m1")
    runs = [
        {"symbol": "AAA", "date": d, "quorum_ok": True, "decision": "BUY", "engine_signal": "HOLD", "engine_confidence": 20,
         # votes: BUY 0.8 + 0.5 = 1.3 vs HOLD 0.5 + 0.2 = 0.7 -> without Swing HOLD wins (0.5 < 0.7), without Echo BUY still wins (0.8 > 0.7)
         "agents": [swing, agent("Echo", "BUY"), agent("Fence", "HOLD"), agent("Eng", "HOLD", "deterministic"), dict(agent("Down", None, ok=False), failed_over_from="gemini")]},
        {"symbol": "AAA", "date": d, "quorum_ok": True, "decision": "SELL", "engine_signal": "HOLD", "agents": [agent("Swing", "BUY")]},  # stored votes don't reproduce: no pivots
    ]
    rows = {r["agent"]: r for r in strategy.agent_leaderboard(book, runs, horizon=5, min_ranked=1)["agents"]}
    assert rows["Swing"]["pivotal"] == 1 and rows["Swing"]["pivotal_edge"] == pytest.approx(fwd)  # BUY instead of HOLD
    assert rows["Echo"]["pivotal"] == 0 and rows["Echo"]["pivotal_edge"] is None
    beat = float(fwd > spy)
    assert rows["Swing"]["calibration_n"] == 1 and rows["Swing"]["brier"] == pytest.approx((0.7 - beat) ** 2) and rows["Swing"]["beat_rate"] == beat
    assert rows["Swing"]["models"] == {"m1": 1} and rows["Echo"]["brier"] is None and rows["Echo"]["answer_rate"] == 1.0
    assert "Down" not in rows  # it never answered, so it has no row


def test_nobody_is_ranked_until_they_have_enough_scored_calls():
    runs = [{"symbol": "AAA", "date": day(20), "quorum_ok": True, "decision": "BUY", "engine_signal": "HOLD", "agents": [agent("Bull", "BUY")]}]
    lb = strategy.agent_leaderboard(make_book(N_FULL), runs)  # default MIN_RANKED = 30
    assert lb["min_ranked"] == 30 and all(r["ranked"] is False for r in lb["agents"]) and "ranked only after 30" in lb["note"]


def test_a_decision_too_recent_to_have_a_forward_return_is_not_scored_yet():
    book = make_book(N_FULL)
    runs = [{"symbol": "AAA", "date": book.latest_date, "quorum_ok": True, "decision": "BUY", "engine_signal": "HOLD", "agents": [agent("Bull", "BUY")]}]
    row = strategy.agent_leaderboard(book, runs, min_ranked=1)["agents"][0]
    assert row["answers"] == 1 and row["directional_calls"] == 0 and row["mean_edge"] is None


# -------------------------------------------------------------------- stance --


def _committee_doc(sym, d, action, engine="HOLD", label="majority"):
    return {"symbol": sym, "date": d, "action": action, "decision": action, "quorum_ok": True, "engine_signal": engine, "ceo": {"label": label, "headline": f"{action} - {label}"}}


def test_stance_puts_what_needs_a_look_first_and_collapses_the_rest_into_quiet(monkeypatch):
    book = make_book(N_FULL)
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [_committee_doc("AAA", day(N_FULL - 2), "BUY"), _committee_doc("BBB", day(N_FULL - 25), "SELL")])
    out = strategy.stance(book)
    by = {r["symbol"]: r for r in out["rows"]}
    assert out["as_of"] == book.latest_date and out["rows"][0]["symbol"] == "AAA"
    assert by["AAA"]["attention"] and "committee says BUY" in by["AAA"]["summary"] and "engine and committee disagree" in by["AAA"]["summary"]
    assert by["AAA"]["committee"]["consensus"] == "majority"
    assert by["BBB"]["committee"] is None and not by["BBB"]["attention"]  # a stale committee view is ignored
    assert by["SPY"]["summary"].startswith("No action suggested")
    assert out["attention"] == 1 and out["quiet"] == 2


def test_stance_flags_an_engine_signal_and_respects_the_users_watchlist(monkeypatch):
    frames = {"AAA": _frame(N_FULL, lambda i: 50 + i * 0.1, buy={N_FULL - 1}), "BBB": _frame(N_FULL, lambda i: 60 + i * 0.1)}
    book = paper.PriceBook.from_frames(frames, {s: PARAMS for s in frames})
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [])
    out = strategy.stance(book, ["AAA", "ZZZ"])  # ZZZ is not tracked: dropped, not an error
    assert [r["symbol"] for r in out["rows"]] == ["AAA"] and out["rows"][0]["attention"] and "engine says BUY" in out["rows"][0]["summary"]


def test_stance_with_no_price_data_is_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [])
    assert strategy.stance(paper.PriceBook()) == {"as_of": None, "rows": [], "attention": 0, "watch": 0, "quiet": 0}


def test_high_risk_alone_goes_to_watch_not_attention_but_rides_along_with_a_real_signal(monkeypatch):
    from tests.test_risk import book_from, regime_series

    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [])
    book = book_from({"WILD": regime_series(), "CALM": [100 + i * 0.05 for i in range(520)]})  # WILD ends in a violent slump: HIGH risk; every engine signal is HOLD
    out = strategy.stance(book)
    by = {r["symbol"]: r for r in out["rows"]}
    assert by["WILD"]["risk"]["level"] == "HIGH" and by["WILD"]["watch"] and not by["WILD"]["attention"]
    assert "risk is HIGH" in by["WILD"]["summary"] and by["WILD"]["reasons"] == []
    assert not by["CALM"]["watch"] and not by["CALM"]["attention"]
    assert (out["attention"], out["watch"], out["quiet"]) == (0, 1, 1)
    assert [r["symbol"] for r in out["rows"]] == ["WILD", "CALM"]  # watch ranks above quiet
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [_committee_doc("WILD", book.latest_date, "SELL")])
    row = next(r for r in strategy.stance(book)["rows"] if r["symbol"] == "WILD")
    assert row["attention"] and not row["watch"] and "committee says SELL" in row["summary"] and "risk is HIGH" in row["summary"]


# ------------------------------------------------------------- track record --


def test_the_track_record_shows_only_reference_strategies_never_a_users_account(fake):
    paper_cycle.run_cycle(bootstrap=True, start=day(0), book=make_book(N_SHORT))
    tr = strategy.track_record()
    ids = [s["id"] for s in tr["strategies"]]
    assert ids and all(i in strategy.CONTROL_ORDER for i in ids) and not any(i.startswith(("profile:", "bench:")) for i in ids)
    assert ids == [i for i in strategy.CONTROL_ORDER if i in ids]  # in the fixed, meaningful order
    assert tr["initialised"] and tr["live_from"] and set(tr["curves"]) == set(ids)
    assert all("username" not in s and "profile" not in s for s in tr["strategies"])
    assert {"est_tax", "after_tax_return", "tax_drag", "turnover"} <= set(tr["strategies"][0])


def test_the_track_record_before_bootstrap_is_an_empty_shell(fake):
    tr = strategy.track_record()
    assert tr["initialised"] is False and tr["strategies"] == [] and tr["curves"] == {}


# ----------------------------------------------------------------- endpoints --


@pytest.fixture
def api(fake, monkeypatch):
    paper_cycle.run_cycle(bootstrap=True, start=day(0), book=make_book(N_SHORT))
    monkeypatch.setattr(strategy, "cached_book", lambda: make_book(N_FULL))
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [])
    monkeypatch.setattr(strategy.db, "list_paper_accounts", lambda: [copy.deepcopy(a) for a in fake.accounts.values()])
    monkeypatch.setattr(strategy.db, "get_paper_meta", lambda: dict(fake.meta))
    monkeypatch.setattr(strategy.db, "get_user_by_username", lambda u: next((x for x in fake.users if x["username_lower"] == u.lower()), None))
    return TestClient(app)


@pytest.mark.parametrize("path", ["/api/admin/strategy/overview", "/api/admin/strategy/accuracy"])
def test_admin_strategy_endpoints_are_admin_only(api, path):
    assert api.get(path).status_code == 401 and api.get(path, headers=VIEWER).status_code == 403


@pytest.mark.parametrize("path", ["/api/me/stance", "/api/me/track-record"])
def test_user_strategy_endpoints_need_a_login_but_not_admin(api, path):
    assert api.get(path).status_code == 401
    assert api.get(path, headers=VIEWER).status_code == 200


def test_the_admin_overview_carries_capital_including_tax_and_the_risk_table(api):
    r = api.get("/api/admin/strategy/overview", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert {"decisions", "history", "risk_today", "capital", "curves", "tax_assumptions"} <= set(body)
    assert body["capital"] and {"after_tax_return", "tax_drag", "avg_holding_days"} <= set(body["capital"][0])
    assert body["tax_assumptions"] == {"short_term": paper.TAX_ST, "long_term": paper.TAX_LT}


def test_the_accuracy_endpoint_bundles_all_four_scorecards(api):
    body = api.get("/api/admin/strategy/accuracy", headers=ADMIN).json()
    assert {"signal", "risk", "committee", "leaderboard"} <= set(body)
    assert body["risk"]["horizons"] == [5, 20] and body["leaderboard"]["min_ranked"] == strategy.MIN_RANKED


def test_a_users_stance_follows_their_own_watchlist(api, fake):
    fake.users.append({"username": "viewer", "username_lower": "viewer", "role": "viewer", "tickers": ["BBB"]})
    rows = api.get("/api/me/stance", headers=VIEWER).json()["rows"]
    assert [r["symbol"] for r in rows] == ["BBB"]


def test_strategy_endpoints_report_503_when_price_data_is_missing(api, monkeypatch):
    def boom():
        raise FileNotFoundError("no parquet")

    monkeypatch.setattr(strategy, "cached_book", boom)
    r = api.get("/api/admin/strategy/overview", headers=ADMIN)
    assert r.status_code == 503 and "FileNotFoundError" in r.json()["detail"]


# ------------------------------------------------------------------- ask ----


class AskDB:
    """Just enough of the committee tables for the ask flow."""

    def __init__(self):
        self.asks = {}

    def install(self, mp):
        d = committee_daily.db
        mp.setattr(d, "save_committee_ask", lambda doc: self.asks.__setitem__(doc["id"], copy.deepcopy(doc)) or doc)
        mp.setattr(d, "get_committee_ask", lambda i: copy.deepcopy(self.asks.get(i)))
        mp.setattr(d, "list_committee_asks", lambda limit=15: sorted((copy.deepcopy(a) for a in self.asks.values()), key=lambda a: a["created_at"], reverse=True)[:limit])


@pytest.fixture
def ask_env(monkeypatch):
    from tests.test_committee_daily import make_book as committee_book

    cf = CommitteeFakeDB()
    cf.install(monkeypatch)
    adb = AskDB()
    adb.install(monkeypatch)
    monkeypatch.setattr(committee_daily.ds, "get_live_signals", lambda: {"signals": []})
    return type("E", (), {"cf": cf, "adb": adb, "book": committee_book()})


async def test_an_ask_stores_the_whole_exchange_but_is_never_a_committee_decision(ask_env):
    seen = {}

    async def runner(orch, ctx, **kw):
        seen["ctx"], seen["risk"] = ctx, kw.get("risk")
        return agents_result()

    doc = committee_daily.new_ask_doc("AAPL", "  Is   the volume spike   a concern?  ")
    out = await committee_daily.run_ask(doc, book=ask_env.book, runner=runner)
    assert out["status"] == "done" and out["id"].startswith("ask:") and out["question"] == "Is the volume spike a concern?"
    assert "A specific question from the committee chair" in seen["ctx"] and "Is the volume spike a concern?" in out["prompt"] and "ONLY one JSON object" in out["prompt"]
    assert out["decision"] == "HOLD" and out["ceo"]["headline"] and len(out["agents"]) == 10
    assert ask_env.adb.asks[out["id"]]["status"] == "done"
    assert ask_env.cf.runs == {}  # nothing landed in the decisions table


async def test_a_failed_ask_records_the_error_instead_of_raising(ask_env):
    async def boom(orch, ctx, **kw):
        raise RuntimeError("model down")

    out = await committee_daily.run_ask(committee_daily.new_ask_doc("AAPL", ""), book=ask_env.book, runner=boom)
    assert out["status"] == "error" and "model down" in out["error"]
    bad = await committee_daily.run_ask(committee_daily.new_ask_doc("NOPE", ""), book=ask_env.book, runner=boom)
    assert bad["status"] == "error" and "no current price data" in bad["error"]


def test_only_one_recent_ask_may_run_at_a_time(ask_env):
    assert committee_daily.ask_in_flight() is False
    running = committee_daily.new_ask_doc("AAPL", "")
    ask_env.adb.asks[running["id"]] = running
    assert committee_daily.ask_in_flight() is True
    old = dict(running, created_at=(datetime.now(timezone.utc) - timedelta(seconds=committee_daily.ASK_STALE_S + 5)).isoformat())
    ask_env.adb.asks[running["id"]] = old
    assert committee_daily.ask_in_flight() is False  # a dead ask does not block the console forever
    ask_env.adb.asks[running["id"]] = dict(running, status="done")
    assert committee_daily.ask_in_flight() is False


def test_the_ask_api_validates_starts_conflicts_and_returns_the_stored_exchange(ask_env, monkeypatch):
    monkeypatch.setattr(committee_daily.db, "log_audit", lambda *a, **k: None)
    monkeypatch.setattr(committee_daily.ds, "STOCK_INFO", {"AAPL": {"name": "Apple", "sector": "Tech", "beta": 1.0}, "GOOGL": {"name": "Alphabet", "sector": "Tech", "beta": 1.0}})
    from app.routers import committee as committee_router

    monkeypatch.setattr(committee_router.ds, "STOCK_INFO", committee_daily.ds.STOCK_INFO)
    monkeypatch.setattr(committee_router.db, "save_committee_ask", committee_daily.db.save_committee_ask)
    monkeypatch.setattr(committee_router.db, "list_committee_asks", committee_daily.db.list_committee_asks)
    monkeypatch.setattr(committee_router.db, "get_committee_ask", committee_daily.db.get_committee_ask)
    monkeypatch.setattr(committee_router.db, "log_audit", lambda *a, **k: None)

    async def fake_run_ask(doc, **kw):
        ask_env.adb.asks[doc["id"]] = dict(doc, status="done", decision="HOLD", action="HOLD", agents=[])
        return doc

    monkeypatch.setattr(committee_daily, "run_ask", fake_run_ask)
    with TestClient(app) as c:
        assert c.post("/api/admin/committee/ask", json={"symbol": "ZZZZ"}, headers=ADMIN).status_code == 422
        assert c.post("/api/admin/committee/ask", json={"symbol": "AAPL"}).status_code == 401
        assert c.post("/api/admin/committee/ask", json={"symbol": "AAPL"}, headers=VIEWER).status_code == 403
        started = c.post("/api/admin/committee/ask", json={"symbol": "aapl", "question": "why?"}, headers=ADMIN)
        assert started.status_code == 202
        ask_id = started.json()["id"]
        for _ in range(100):
            got = c.get(f"/api/admin/committee/asks/{ask_id}", headers=ADMIN).json()
            if got["status"] == "done":
                break
            time.sleep(0.05)
        assert got["status"] == "done" and got["decision"] == "HOLD"
        assert [a["id"] for a in c.get("/api/admin/committee/asks", headers=ADMIN).json()] == [ask_id]
        assert c.get("/api/admin/committee/asks/ask:missing", headers=ADMIN).status_code == 404
        assert c.get("/api/admin/committee/asks/2026-09-18:GOOGL", headers=ADMIN).status_code == 404  # a real decision id is not readable as an ask
        stuck = committee_daily.new_ask_doc("GOOGL", "")
        ask_env.adb.asks[stuck["id"]] = stuck
        assert c.post("/api/admin/committee/ask", json={"symbol": "GOOGL"}, headers=ADMIN).status_code == 409


# ------------------------------------------------------------------- rebuild --


def _old_style(acct):
    """What an account created before tax tracking looks like."""
    acct = copy.deepcopy(acct)
    acct.pop("tax", None)
    acct.pop("lots", None)
    return acct


def test_rebuild_reproduces_every_curve_and_only_then_adds_the_tax_lots(fake):
    book = make_book(N_FULL)
    paper_cycle.run_cycle(bootstrap=True, start=day(0), book=book)
    for aid in list(fake.accounts):
        fake.accounts[aid] = _old_style(fake.accounts[aid])
    assert paper.summarize(fake.accounts["ctl_engine"])["tax_tracked"] is False
    before = {aid: copy.deepcopy(a["curve"]) for aid, a in fake.accounts.items()}

    dry = paper_cycle.rebuild_accounts(book=book)
    assert dry["applied"] is False and dry["differs"] == [] and dry["replaced"] == 0
    assert paper.summarize(fake.accounts["ctl_engine"])["tax_tracked"] is False  # a dry run writes nothing

    done = paper_cycle.rebuild_accounts(apply=True, book=book)
    assert done["replaced"] == done["identical"] == len(fake.accounts) and done["differs"] == []
    for aid, acct in fake.accounts.items():
        assert acct["curve"] == before[aid]  # history untouched, to the cent
    eng = paper.summarize(fake.accounts["ctl_engine"])
    assert eng["tax_tracked"] is True and eng["est_tax"] is not None and eng["unrealized_st"] is not None


def test_an_account_whose_replay_does_not_match_is_reported_and_left_alone(fake):
    book = make_book(N_FULL)
    paper_cycle.run_cycle(bootstrap=True, start=day(0), book=book)
    fake.accounts["ctl_engine"]["curve"][5][1] += 123.45  # history that no honest replay would produce
    tampered = copy.deepcopy(fake.accounts["ctl_engine"])
    out = paper_cycle.rebuild_accounts(apply=True, book=book)
    assert "ctl_engine" in out["differs"] and out["accounts"]["ctl_engine"] == "DIFFERS"
    assert fake.accounts["ctl_engine"]["curve"] == tampered["curve"]  # not overwritten
    assert out["replaced"] == len(fake.accounts) - 1


def test_rebuild_needs_an_initialised_paper_trader(fake):
    with pytest.raises(paper_cycle.CycleError):
        paper_cycle.rebuild_accounts(book=make_book(N_SHORT))


# ------------------------------------------------------------------ coverage --


def test_coverage_lists_every_symbol_and_marks_which_ones_the_committee_reviewed(monkeypatch):
    book = make_book(N_FULL)
    runs = [{"symbol": "AAA", "date": book.latest_date, "action": "HOLD", "decision": "BUY", "quorum_ok": True}]
    c = strategy.coverage(book, runs, book.latest_date)
    assert c["total"] == 3 and c["reviewed"] == 1 and c["data_date"] == book.latest_date
    assert {r["symbol"] for r in c["rows"]} == {"SPY", "AAA", "BBB"}
    first = c["rows"][0]
    assert first["symbol"] == "AAA" and first["reviewed"] and first["committee_action"] == "HOLD"  # reviewed rows lead
    assert all(not r["reviewed"] and r["committee_action"] is None for r in c["rows"][1:])
    assert all({"engine_signal", "engine_confidence", "risk", "name"} <= set(r) for r in c["rows"])


def test_a_review_from_an_earlier_day_does_not_count_as_reviewed_today():
    book = make_book(N_FULL)
    stale = [{"symbol": "AAA", "date": day(N_FULL - 5), "action": "BUY", "decision": "BUY", "quorum_ok": True}]
    c = strategy.coverage(book, stale, day(N_FULL - 5))
    assert c["reviewed"] == 0 and c["review_date"] == day(N_FULL - 5) and all(not r["reviewed"] for r in c["rows"])


def test_the_overview_carries_the_coverage_block(monkeypatch):
    monkeypatch.setattr(strategy.db, "list_all_committee_runs", lambda: [])
    monkeypatch.setattr(strategy.db, "list_paper_accounts", lambda: [])
    monkeypatch.setattr(strategy.db, "get_paper_meta", lambda: None)
    out = strategy.overview(make_book(N_FULL))
    assert out["coverage"]["total"] == 3 and out["coverage"]["reviewed"] == 0


# ------------------------------------------------------------------ research --


def test_the_committee_prompt_carries_the_peer_context_line_when_there_is_one():
    from tests.test_committee_daily import make_book as committee_book

    book = committee_book()
    with_peers = committee_daily.build_context("AAPL", book.latest_date, book, None, ask=False, peers="Moves most with: NVDA (correlation +0.80, engine HOLD) over the last 60 days.")
    without = committee_daily.build_context("AAPL", book.latest_date, book, None, ask=False)
    assert "Moves most with: NVDA" in with_peers and "Moves most with" not in without


def test_the_research_endpoint_is_admin_only_and_returns_the_gate_and_associations(api, monkeypatch):
    from app import research

    monkeypatch.setattr(research.db, "list_paper_accounts", lambda: [])
    monkeypatch.setattr(research.db, "list_all_committee_runs", lambda: [])
    monkeypatch.setattr(research.db, "list_report_narratives", lambda: [])
    assert api.get("/api/admin/strategy/research").status_code == 401
    assert api.get("/api/admin/strategy/research", headers=VIEWER).status_code == 403
    body = api.get("/api/admin/strategy/research", headers=ADMIN).json()
    assert body["min_live_days"] == research.MIN_LIVE_DAYS and body["associations"]["symbols"] == 3 and body["proposals"] == [] and body["latest_digest"] is None


# --------------------------------------------- labelled matrices and pipeline status --


def test_matrix_endpoints_are_admin_only_and_labelled(api, real_db):
    for path in ("/api/admin/strategy/matrix", "/api/admin/strategy/correlation", "/api/admin/strategy/snapshots", "/api/admin/strategy/pipeline"):
        assert api.get(path).status_code == 401 and api.get(path, headers=VIEWER).status_code == 403
    body = api.get("/api/admin/strategy/matrix?fields=close,ret&symbols=aaa&days=5", headers=ADMIN).json()
    assert body["symbols"] == ["AAA"] and len(body["dates"]) == 5 and len(body["fields"]["close"]) == 5 and "risk_score" in body["available_fields"]
    assert api.get("/api/admin/strategy/matrix?fields=nope", headers=ADMIN).status_code == 422
    assert api.get("/api/admin/strategy/matrix?symbols=ZZZ", headers=ADMIN).status_code == 422
    inf = api.get("/api/admin/strategy/matrix?fields=committee_code&inference=true&days=3", headers=ADMIN)
    assert inf.status_code == 200 and "committee_consensus" in inf.json()["available_fields"]


def test_correlation_endpoint_returns_a_symmetric_labelled_matrix(api):
    body = api.get("/api/admin/strategy/correlation?window=60", headers=ADMIN).json()
    n = len(body["symbols"])
    assert len(body["values"]) == n and all(len(r) == n for r in body["values"]) and body["values"][0][0] == 1.0
    assert body["as_of"] and body["window"] == 60


def test_snapshot_and_pipeline_endpoints_read_the_stored_history(api, real_db):
    from app import snapshots

    snapshots.take(strategy.cached_book())
    m = api.get("/api/admin/strategy/snapshots?field=close", headers=ADMIN).json()
    assert m["field"] == "close" and len(m["dates"]) == 1 and m["symbols"]
    assert api.get("/api/admin/strategy/snapshots?field=nope", headers=ADMIN).status_code == 422
    assert api.get("/api/admin/strategy/pipeline", headers=ADMIN).json() == {"runs": []}
    assert api.get("/api/admin/strategy/overview", headers=ADMIN).json()["pipeline"] is None
