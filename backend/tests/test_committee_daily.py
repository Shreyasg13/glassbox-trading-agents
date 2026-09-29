"""Daily Investment Committee: vote parsing (incl. the old BUY-bias bug), symbol
extraction from a rich prompt, which symbols get reviewed, idempotency/resume,
failure handling, the report, the scorecard and the admin API. Model calls are
faked (a stand-in for orchestration.run_orchestration) and the DB is an in-memory
stand-in, so nothing here touches a network or a database file."""
from __future__ import annotations

import asyncio
import copy
import time
from datetime import datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import auth, committee_daily, data_source as ds, orchestration, paper, paper_cycle, rate_limit
from app.main import app
from app.scripts import run_committee_daily

N = 80
DATES = pd.bdate_range("2024-01-02", periods=N)
PARAMS = {"rsi_low": 30, "rsi_high": 70, "fast_ma": 20, "slow_ma": 50}
LAST = DATES[N - 1].strftime("%Y-%m-%d")
NOW = datetime(2024, 4, 23, 22, 30, tzinfo=timezone.utc)  # the evening after the last bar


def day(i):
    return DATES[i].strftime("%Y-%m-%d")


def frame(close, buy=(), sell=()):
    rsi, fast, slow = [], [], []
    for i in range(N):
        if i in buy:
            rsi.append(20.0), fast.append(101.0), slow.append(100.0)
        elif i in sell:
            rsi.append(80.0), fast.append(99.0), slow.append(100.0)
        else:
            rsi.append(50.0), fast.append(100.0), slow.append(100.0)
    closes = close if isinstance(close, list) else [float(close)] * N
    return pd.DataFrame({"Close": closes, "RSI": rsi, "MA_20": fast, "MA_50": slow}, index=DATES)


def flat(last_move=0.0):
    """Flat prices, then a jump over the final 5 days worth `last_move`."""
    c = [100.0] * (N - 5) + [100.0 * (1 + last_move * (k + 1) / 5) for k in range(5)]
    return c


def make_book(frames=None):
    frames = frames or {
        "AAPL": frame(flat(0.02), buy={N - 1}),            # BUY today
        "NVDA": frame(flat(-0.03), sell={N - 1}),          # SELL today
        "MSFT": frame(flat(0.01), buy={N - 2}),            # BUY yesterday, HOLD today -> "changed"
        "GOOGL": frame(flat(0.08)),                        # biggest quiet mover
        "AMZN": frame(flat(0.04)),
        "JPM": frame(flat(0.005)),
    }
    return paper.PriceBook.from_frames(frames, {s: PARAMS for s in frames})


# ---------------------------------------------------------------- vote parsing --


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Decision: BUY\nStrong momentum.", "BUY"),
        ("decision - sell\nToo stretched.", "SELL"),
        ("**Decision:** HOLD\nNo edge.", "HOLD"),
        ("Decision: `SELL` because ...", "SELL"),
        ("HOLD -- I wouldn't buy here, and wouldn't sell either.", "HOLD"),  # the old code called this BUY
        ("Sell now; consider a buy after the dip.", "SELL"),               # earliest word wins, not tuple order
        ("Some analysis with no recommendation at all.", "HOLD"),
        ("Buying pressure is fading; the buyback ended.", "HOLD"),         # 'buying'/'buyback' are not the word BUY
        ("Long text. Eventually: hold. But buy? no.", "HOLD"),
    ],
)
def test_lean_from_text(text, expected):
    assert orchestration._lean_from_text(text) == expected


def test_the_reducer_no_longer_biases_toward_buy():
    agents = [{"agent": f"a{i}", "output": "HOLD. I would not buy at this level."} for i in range(4)]
    out = orchestration._reduce_committee_vote(agents)
    assert out["decision"] == "HOLD" and out["votes"]["BUY"] == 0


def test_the_reducer_still_uses_engine_signals_with_their_confidence():
    r = orchestration._reduce_committee_vote(
        [{"agent": "engine", "signal": {"signal": "BUY", "confidence": 80}}, {"agent": "llm", "output": "Decision: SELL\nx"}]
    )
    assert r["decision"] == "BUY" and r["votes"] == {"BUY": 0.8, "SELL": 0.5, "HOLD": 0.0}


# ------------------------------------------------------------ symbol extraction --


@pytest.mark.parametrize(
    "text,expected",
    [("AAPL", "AAPL"), ("  nvda ", "NVDA"), ("AAPL — Apple Inc. (Technology)\nData through ...", "AAPL"),
     ("V — Visa Inc.\nfoo", "V"), ("SPY — S&P 500 ETF", "SPY")],
)
def test_extract_symbol(text, expected):
    assert orchestration._extract_symbol(text) == expected


def test_unknown_input_falls_back_to_the_legacy_behaviour():
    assert orchestration._extract_symbol("zzzz") == "ZZZZ"
    assert orchestration._extract_symbol("Tell me about apples") == "TELL ME ABOUT APPLES"


async def test_a_deterministic_agent_reads_the_ticker_from_the_rich_prompt(monkeypatch):
    from app.models import AgentConfig

    monkeypatch.setattr(ds, "get_live_signals", lambda: {"signals": [{"symbol": "AAPL", "signal": "BUY", "confidence": 70}]})
    agent = AgentConfig(id="d1", name="Quant (Engine)", role="r", type="deterministic")
    out = await orchestration.run_deterministic_agent(agent, committee_daily.build_context("AAPL", LAST, make_book()))
    assert out["symbol"] == "AAPL" and out["signal"]["signal"] == "BUY"


# ------------------------------------------------------------------- selection --


def test_selection_orders_signals_then_changes_then_tops_up_with_movers():
    book = make_book()
    picks = committee_daily.select_candidates(book, LAST, min_n=2, max_n=5)
    assert [p["symbol"] for p in picks] == ["AAPL", "NVDA", "MSFT"]  # BUY/SELL by confidence, then the changed one
    assert picks[0]["why"].startswith("engine signal BUY") and "changed BUY -> HOLD" in picks[2]["why"]


def test_selection_tops_up_to_the_minimum_with_the_largest_movers():
    frames = {"AAPL": frame(flat(0.02)), "GOOGL": frame(flat(0.08)), "AMZN": frame(flat(-0.05)), "JPM": frame(flat(0.005))}
    picks = committee_daily.select_candidates(make_book(frames), LAST, min_n=2, max_n=5)
    assert [p["symbol"] for p in picks] == ["GOOGL", "AMZN"]  # |+8%| then |-5%|
    assert all("largest 5-day mover" in p["why"] for p in picks)


def test_selection_never_tops_up_past_what_is_needed_and_respects_the_cap():
    book = make_book()
    assert len(committee_daily.select_candidates(book, LAST, min_n=1, max_n=5)) == 3  # 3 signalled already exceed the minimum
    assert [p["symbol"] for p in committee_daily.select_candidates(book, LAST, min_n=2, max_n=2)] == ["AAPL", "NVDA"]  # capped


def test_selection_is_deterministic_and_skips_symbols_with_no_bar_that_day():
    book = make_book()
    assert committee_daily.select_candidates(book, LAST) == committee_daily.select_candidates(book, LAST)
    assert committee_daily.select_candidates(book, "1999-01-01") == []


def test_manual_symbols_override_the_automatic_picks():
    picks = committee_daily.select_candidates(make_book(), LAST, only=["jpm", "AAPL"])
    assert [p["symbol"] for p in picks] == ["AAPL", "JPM"] and all(p["why"] == "requested manually" for p in picks)


# --------------------------------------------------------------------- context --


def test_the_prompt_is_grounded_in_real_numbers_and_demands_a_decision_line():
    live = {"ma_cross": "BULLISH", "fast_ma": 10, "slow_ma": 100, "volume_ratio": 1.34, "test_sharpe": 0.87, "win_rate": 52.0}
    ctx = committee_daily.build_context("AAPL", LAST, make_book(), live)
    first = ctx.split("\n")[0]
    assert first.startswith("AAPL — Apple Inc.") and LAST in ctx
    for needle in ("Quant engine signal: BUY", "RSI 20.0", "BULLISH", "fast 10-day vs slow 100-day", "1.34x", "Sharpe 0.87", "5-day +", "60-day high", "volatility"):
        assert needle in ctx, needle
    assert "Decision: BUY" in ctx and "ONLY the numbers above" in ctx and "do not invent" in ctx


def test_the_prompt_survives_missing_history_and_missing_live_data():
    frames = {"AAPL": frame(100.0).iloc[-3:]}  # only 3 bars of history
    book = paper.PriceBook.from_frames(frames, {"AAPL": PARAMS})
    ctx = committee_daily.build_context("AAPL", book.latest_date, book, None)
    assert "20-day n/a" in ctx and "n/a" in ctx  # no crash, honest gaps


# ---------------------------------------------------------------------- fakes --


class FakeDB:
    def __init__(self, seeded=True):
        self.runs, self.narratives, self.audit = {}, [], []
        self.lock = None  # owner of the single-flight lock, as the DB row would record it
        self.orchs = (
            [{"id": "o1", "name": "Investment Committee", "mode": "committee_vote", "agent_ids": ["a"], "coordinator": "vn_engine", "schedule": None,
              "agent_timeout_s": 60.0, "run_budget_s": 240.0}]
            if seeded else []
        )

    def _acquire(self, owner, ttl, now=None):
        if self.lock is not None:
            return False
        self.lock = owner
        return True

    def _release(self, owner):
        if self.lock == owner:
            self.lock = None

    def install(self, mp):
        d = committee_daily.db
        mp.setattr(d, "list_committee_runs_for_date", lambda day_: sorted((copy.deepcopy(r) for r in self.runs.values() if r["date"] == day_), key=lambda r: r["symbol"]))
        mp.setattr(d, "save_committee_run", lambda doc: self.runs.__setitem__(doc["id"], copy.deepcopy(doc)) or doc)
        mp.setattr(d, "list_committee_runs", lambda limit=60: sorted((copy.deepcopy(r) for r in self.runs.values()), key=lambda r: (r["date"], r["symbol"]), reverse=True)[:limit])
        mp.setattr(d, "list_all_committee_runs", lambda: [copy.deepcopy(r) for r in self.runs.values()])
        mp.setattr(d, "acquire_committee_lock", self._acquire)
        mp.setattr(d, "release_committee_lock", self._release)
        mp.setattr(d, "committee_lock_active", lambda ttl, now=None: self.lock is not None)
        mp.setattr(d, "list_orchestrations", lambda: copy.deepcopy(self.orchs))
        mp.setattr(d, "list_report_narratives", lambda: list(self.narratives))
        mp.setattr(d, "create_report_narrative", lambda n: self.narratives.append(n) or n)
        mp.setattr(d, "log_audit", lambda *a, **k: self.audit.append(a))

        # Mock snapshot_store to return None (fallback to cache) since test DB has no snapshots table
        import app.snapshot_store as ss
        mp.setattr(ss, "get", lambda *a, **k: None)
        mp.setattr(ss, "put", lambda *a, **k: "mock-snap-id")


@pytest.fixture
def fake(monkeypatch):
    f = FakeDB()
    f.install(monkeypatch)
    for k in ("COMMITTEE_MIN_SYMBOLS", "COMMITTEE_MAX_SYMBOLS", "COMMITTEE_DAILY_BUDGET_S", "COMMITTEE_QUORUM", "COMMITTEE_MAX_DATA_AGE_DAYS"):
        monkeypatch.delenv(k, raising=False)
    rate_limit._admin_limiter._hits.clear()
    return f


def agents_result(engine="HOLD", llm=("HOLD",) * 7, fail=(), providers=("gemini",)):
    """A fake committee result: 3 deterministic engine agents + 7 LLM analysts."""
    rows = [{"agent": f"Engine{i}", "type": "deterministic", "symbol": "X", "signal": {"signal": engine, "confidence": 50}} for i in range(3)]
    for i, lean in enumerate(llm):
        name = f"Analyst{i}"
        if name in fail:
            rows.append({"agent": name, "error": "HTTP 429", "degraded": True})
        else:
            rows.append({"agent": name, "type": "llm", "provider": providers[i % len(providers)], "model": "m", "output": f"Decision: {lean}\nReason number {i} with some detail."})
    return {"mode": "committee_vote", "agents": rows, "committee_decision": orchestration._reduce_committee_vote(rows)}


def make_runner(result_for=None, calls=None, delay=0.0):
    async def runner(orch, ctx, **kw):
        sym = ctx.split(" ")[0]
        if calls is not None:
            calls.append(sym)
        if delay:
            await asyncio.sleep(delay)
        r = (result_for or {}).get(sym, agents_result())
        if isinstance(r, Exception):
            raise r
        return r

    return runner


async def go(**kw):
    kw.setdefault("book", make_book())
    kw.setdefault("now", NOW)
    kw.setdefault("live_rows", {})
    return await committee_daily.run_daily(**kw)


# ------------------------------------------------------------------- daily run --


async def test_a_normal_day_reviews_the_picks_and_saves_each_decision_with_its_evidence(fake):
    calls = []
    out = await go(runner=make_runner(calls=calls, result_for={"AAPL": agents_result("BUY", ("BUY",) * 7), "NVDA": agents_result("SELL", ("SELL",) * 7)}))
    assert calls == ["AAPL", "NVDA", "MSFT"] and out["ran"] == 3 and out["answered_total"] == 30
    a = fake.runs[f"{LAST}:AAPL"]
    assert a["decision"] == "BUY" and a["engine_signal"] == "BUY" and a["agrees_with_engine"] is True
    assert a["answered"] == 10 and a["quorum_ok"] and a["providers"] == {"gemini": 7}
    assert a["price"] == pytest.approx(102.0) and a["why"].startswith("engine signal BUY")
    assert len(a["agents"]) == 10 and all("lean" in x for x in a["agents"])
    assert out["decisions"] == {"AAPL": "BUY", "NVDA": "SELL", "MSFT": "HOLD"}
    assert any(x[1] == "committee.daily_run" for x in fake.audit)


async def test_the_agents_receive_the_grounded_prompt_not_a_bare_ticker(fake):
    seen = []

    async def runner(orch, ctx, **kw):
        seen.append(ctx)
        assert kw["allow_failover"] is True and kw["job_id"] is None
        return agents_result()

    await go(runner=runner, symbols=["AAPL"])
    assert "Quant engine signal" in seen[0] and seen[0].split("\n")[0].startswith("AAPL")


async def test_a_second_run_the_same_day_and_a_holiday_do_nothing(fake):
    calls = []
    await go(runner=make_runner(calls=calls))
    n = len(calls)
    again = await go(runner=make_runner(calls=calls))  # holiday = same latest date, so identical to a same-day re-run
    assert again["ran"] == 0 and again["note"] == "nothing new to review" and len(calls) == n
    assert len(fake.narratives) == 1  # and no duplicate report


async def test_a_partial_day_resumes_with_only_what_is_missing(fake):
    fake.runs[f"{LAST}:AAPL"] = {"id": f"{LAST}:AAPL", "date": LAST, "symbol": "AAPL"}
    calls = []
    out = await go(runner=make_runner(calls=calls))
    assert calls == ["NVDA", "MSFT"] and out["already_done"] == ["AAPL"]


async def test_force_reviews_again(fake):
    calls = []
    await go(runner=make_runner(calls=calls))
    await go(runner=make_runner(calls=calls), force=True)
    assert len(calls) == 6


async def test_dry_run_shows_the_plan_and_changes_nothing(fake):
    calls = []
    out = await go(runner=make_runner(calls=calls), dry_run=True)
    assert out["dry_run"] and [w["symbol"] for w in out["would_run"]] == ["AAPL", "NVDA", "MSFT"] and calls == [] and fake.runs == {} and fake.narratives == []


async def test_stale_price_data_is_refused_loudly(fake):
    with pytest.raises(committee_daily.CommitteeError, match="stale"):
        await go(now=datetime(2024, 5, 20, tzinfo=timezone.utc))
    with pytest.raises(committee_daily.CommitteeError, match="no price data"):
        await committee_daily.run_daily(book=paper.PriceBook(), now=NOW)


async def test_an_unseeded_committee_is_a_clear_error(fake):
    fake.orchs.clear()
    with pytest.raises(committee_daily.CommitteeError, match="not seeded"):
        await go(runner=make_runner())


async def test_one_symbol_failing_is_recorded_and_the_rest_continue(fake):
    out = await go(runner=make_runner(result_for={"NVDA": RuntimeError("Gemini API error: HTTP 429")}))
    bad = fake.runs[f"{LAST}:NVDA"]
    assert bad["decision"] is None and "HTTP 429" in bad["error"] and bad["answered"] == 0 and not bad["quorum_ok"]
    assert out["ran"] == 3 and fake.runs[f"{LAST}:AAPL"]["decision"] is not None and fake.runs[f"{LAST}:MSFT"]["decision"] is not None
    assert "NVDA" in out["low_quorum"] and "FAILED" in fake.narratives[0]["narrative"]


async def test_degraded_agents_lower_the_quorum_and_the_report_says_so(fake):
    weak = agents_result("HOLD", ("HOLD",) * 7, fail=("Analyst0", "Analyst1", "Analyst2", "Analyst3"))
    out = await go(runner=make_runner(result_for={"AAPL": weak}), symbols=["AAPL"])
    run = fake.runs[f"{LAST}:AAPL"]
    assert run["answered"] == 6 and run["quorum_ok"]  # 3 engine + 3 analysts = exactly the quorum
    weaker = agents_result("HOLD", ("HOLD",) * 7, fail=tuple(f"Analyst{i}" for i in range(5)))
    fake.runs.clear()
    out = await go(runner=make_runner(result_for={"AAPL": weaker}), symbols=["AAPL"], force=True)
    assert fake.runs[f"{LAST}:AAPL"]["quorum_ok"] is False and out["low_quorum"] == ["AAPL"]
    assert "LOW QUORUM" in fake.narratives[-1]["narrative"]


async def test_the_report_names_the_decision_the_disagreement_and_the_dissent(fake):
    mixed = agents_result("HOLD", ("BUY", "BUY", "HOLD", "HOLD", "HOLD", "SELL", "HOLD"))
    await go(runner=make_runner(result_for={"AAPL": mixed}), symbols=["AAPL"])
    (n,) = fake.narratives
    text = n["narrative"]
    assert n["provider"] == "system" and n["model"] == "committee-vote" and n["title"].startswith("Investment Committee") and n["date"] == LAST.replace("-", "")
    assert "AAPL — committee: HOLD" in text and "DISAGREES with the engine" in text  # engine said BUY, committee HOLD
    assert "dissent — Analyst0: BUY" in text and "not investment advice" in text
    assert "reasoning (" in text


async def test_a_resumed_day_gets_a_fuller_report_instead_of_a_duplicate(fake):
    await go(runner=make_runner(), symbols=["AAPL"])
    await go(runner=make_runner(), symbols=["AAPL", "NVDA"])  # AAPL already done; NVDA is new
    assert len(fake.narratives) == 2 and "NVDA" in fake.narratives[1]["narrative"]
    await go(runner=make_runner(), symbols=["AAPL", "NVDA"])  # nothing new -> no third
    assert len(fake.narratives) == 2


async def test_the_time_budget_stops_further_symbols_but_never_skips_the_first(fake, monkeypatch):
    monkeypatch.setenv("COMMITTEE_DAILY_BUDGET_S", "0")
    calls = []
    out = await go(runner=make_runner(calls=calls, delay=0.02))
    assert out["ran"] == 1 and calls == ["AAPL"]  # the rest wait for the next run


async def test_the_running_flag_resets_even_after_an_unexpected_error_and_blocks_overlap(fake, monkeypatch):
    monkeypatch.setattr(committee_daily.db, "save_committee_run", lambda doc: (_ for _ in ()).throw(RuntimeError("db down")))
    with pytest.raises(RuntimeError, match="db down"):
        await go(runner=make_runner(), symbols=["AAPL"])
    assert committee_daily.is_running() is False and fake.lock is None  # released despite the crash
    fake.lock = "another-worker"
    assert committee_daily.is_running() is True
    with pytest.raises(committee_daily.CommitteeError, match="already running"):
        await go(runner=make_runner(), symbols=["AAPL"])


async def test_limits_come_from_the_environment(fake, monkeypatch):
    monkeypatch.setenv("COMMITTEE_MAX_SYMBOLS", "1")
    calls = []
    await go(runner=make_runner(calls=calls))
    assert calls == ["AAPL"]


# ----------------------------------------------------------------- scorecard --


def test_the_scorecard_scores_calls_from_the_next_close_and_ignores_unreliable_runs():
    closes = [100.0] * 60 + [100.0 + 2 * k for k in range(N - 60)]  # steady rally from bar 60
    book = paper.PriceBook.from_frames({"AAPL": frame(closes), "NVDA": frame(closes[::-1])}, {"AAPL": PARAMS, "NVDA": PARAMS})
    runs = [
        {"date": day(62), "symbol": "AAPL", "decision": "BUY", "quorum_ok": True, "agrees_with_engine": True},
        {"date": day(63), "symbol": "AAPL", "decision": "SELL", "quorum_ok": True, "agrees_with_engine": False},
        {"date": day(62), "symbol": "AAPL", "decision": "BUY", "quorum_ok": False, "agrees_with_engine": True},  # unreliable: excluded
        {"date": day(N - 1), "symbol": "AAPL", "decision": "BUY", "quorum_ok": True, "agrees_with_engine": True},  # too recent for any forward return
    ]
    sc = committee_daily.committee_scorecard(book, runs, horizons=(1, 5))
    assert sc["runs"] == 4 and sc["reliable_runs"] == 3
    assert sc["by_decision"]["BUY"]["5"]["n"] == 1 and sc["by_decision"]["BUY"]["5"]["hit_rate"] == 1.0  # rally -> BUY right
    assert sc["by_decision"]["SELL"]["5"]["n"] == 1 and sc["by_decision"]["SELL"]["5"]["hit_rate"] == 0.0  # rally -> SELL wrong
    assert sc["agrees_with_engine"] == pytest.approx(2 / 3)


def test_the_scorecard_with_no_runs_is_empty_not_an_error():
    sc = committee_daily.committee_scorecard(make_book(), [])
    assert sc["runs"] == 0 and sc["agrees_with_engine"] is None and sc["by_decision"]["BUY"]["5"]["n"] == 0


# ------------------------------------------------------------- reflection memory --


def _scored_run(d, lean, agent="Analyst0", ok=True, quorum_ok=True):
    return {"date": d, "symbol": "AAPL", "quorum_ok": quorum_ok, "agents": [{"agent": agent, "ok": ok, "lean": lean}]}


def test_agent_reflection_needs_a_minimum_of_scored_calls_and_ignores_everything_else():
    closes = [100.0] * 60 + [100.0 + 2 * k for k in range(N - 60)]  # steady rally from bar 60
    book = paper.PriceBook.from_frames({"AAPL": frame(closes)}, {"AAPL": PARAMS})
    runs = [_scored_run(day(62), "BUY"), _scored_run(day(63), "BUY")]
    assert committee_daily.agent_reflection("Analyst0", book, runs) is None  # only 2 scored calls, min is 3

    runs.append(_scored_run(day(64), "SELL"))  # a rally: the SELL call is wrong
    line = committee_daily.agent_reflection("Analyst0", book, runs)
    assert line is not None and "3 scored BUY/SELL calls" in line and "not an instruction" in line

    # unreliable, degraded, HOLD and another agent's rows must never count
    runs += [
        _scored_run(day(65), "BUY", quorum_ok=False),
        _scored_run(day(66), "BUY", ok=False),
        _scored_run(day(67), "HOLD"),
        _scored_run(day(68), "BUY", agent="Analyst1"),
    ]
    assert committee_daily.agent_reflection("Analyst0", book, runs) == line


# ---------------------------------------------------------------- the script --


def test_the_script_maps_outcomes_to_exit_codes(monkeypatch, capsys):
    async def ok(**kw):
        return {"ran": 2, "answered_total": 20}

    async def all_failed(**kw):
        return {"ran": 2, "answered_total": 0}

    async def nothing(**kw):
        return {"ran": 0, "answered_total": 0, "note": "nothing new to review"}

    async def stale(**kw):
        raise committee_daily.CommitteeError("price data is stale")

    for fn, code in ((ok, 0), (nothing, 0), (all_failed, 1), (stale, 1)):
        monkeypatch.setattr(committee_daily, "run_daily", fn)
        assert run_committee_daily.main([]) == code
    seen = {}

    async def spy(**kw):
        seen.update(kw)
        return {"ran": 0}

    monkeypatch.setattr(committee_daily, "run_daily", spy)
    run_committee_daily.main(["--symbols", "aapl, nvda", "--force", "--dry-run"])
    assert seen == {"symbols": ["AAPL", "NVDA"], "dry_run": True, "force": True}


# ------------------------------------------------------------------------ API --


def _hdr(role, sub):
    return {"Authorization": "Bearer " + auth.create_access_token(auth.TokenPayload(sub=sub, role=role))}


ADMIN, VIEWER = _hdr("admin", "admin"), _hdr("viewer", "v")


@pytest.mark.parametrize("method,path", [("get", "/api/admin/committee/runs"), ("get", "/api/admin/committee/scorecard"), ("post", "/api/admin/committee/run")])
def test_committee_endpoints_are_admin_only(fake, method, path):
    c = TestClient(app)
    kw = {"json": {}} if method == "post" else {}
    assert getattr(c, method)(path, **kw).status_code == 401
    assert getattr(c, method)(path, headers=VIEWER, **kw).status_code == 403


def test_the_api_lists_runs_previews_picks_and_starts_a_background_review(fake, monkeypatch):
    monkeypatch.setattr(paper_cycle, "load_book", make_book)
    monkeypatch.setattr(committee_daily, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    monkeypatch.setattr(committee_daily.orchestration, "run_orchestration", make_runner())
    monkeypatch.setattr(committee_daily.ds, "get_live_signals", lambda: {"signals": []})
    with TestClient(app) as c:  # a persistent event loop, so the background task can finish
        assert c.get("/api/admin/committee/runs", headers=ADMIN).json() == {"running": False, "runs": []}
        prev = c.post("/api/admin/committee/run", json={"dry_run": True}, headers=ADMIN)
        assert prev.status_code == 200 and [w["symbol"] for w in prev.json()["would_run"]] == ["AAPL", "NVDA", "MSFT"]
        assert fake.runs == {}
        started = c.post("/api/admin/committee/run", json={"symbols": ["AAPL"]}, headers=ADMIN)
        assert started.status_code == 202 and started.json() == {"started": True}
        for _ in range(100):
            runs = c.get("/api/admin/committee/runs", headers=ADMIN).json()
            if runs["runs"] and not runs["running"]:
                break
            time.sleep(0.05)
        assert [r["symbol"] for r in runs["runs"]] == ["AAPL"] and runs["runs"][0]["decision"] == "HOLD"
        assert any(a[1] == "committee.run" for a in fake.audit)


def test_the_api_rejects_a_second_concurrent_run_and_bad_input(fake, monkeypatch):
    fake.lock = "another-worker"
    c = TestClient(app)
    assert c.post("/api/admin/committee/run", json={}, headers=ADMIN).status_code == 409
    assert c.post("/api/admin/committee/run", json={"symbols": ["A"] * 16}, headers=ADMIN).status_code == 422


def test_the_scorecard_endpoint_reports_503_when_price_data_is_unavailable(fake, monkeypatch):
    def boom():
        raise FileNotFoundError("no parquet here")

    monkeypatch.setattr(paper_cycle, "load_book", boom)
    r = TestClient(app).get("/api/admin/committee/scorecard", headers=ADMIN)
    assert r.status_code == 503 and "FileNotFoundError" in r.json()["detail"]


# ------------------------------------------------- the lock itself (a real database) --


@pytest.fixture
def real_db(tmp_path, monkeypatch):
    """The actual lock SQL against a throwaway SQLite file, shared across threads."""
    from sqlalchemy import create_engine

    from app import db as real

    eng = create_engine(f"sqlite:///{tmp_path / 'lock.db'}", connect_args={"check_same_thread": False, "timeout": 30})
    real.metadata.create_all(eng)
    monkeypatch.setattr(real, "engine", eng)
    return real


def test_the_lock_is_exclusive_and_only_its_owner_can_release_it(real_db):
    assert real_db.committee_lock_active(60) is False
    assert real_db.acquire_committee_lock("a", 60) is True
    assert real_db.acquire_committee_lock("b", 60) is False
    assert real_db.committee_lock_active(60) is True
    real_db.release_committee_lock("b")  # not the holder: no effect
    assert real_db.committee_lock_active(60) is True
    real_db.release_committee_lock("a")
    assert real_db.committee_lock_active(60) is False
    assert real_db.acquire_committee_lock("b", 60) is True


def test_an_abandoned_lock_is_taken_over_after_the_ttl_but_not_before(real_db):
    assert real_db.acquire_committee_lock("dead", 100, now=1_000.0) is True
    assert real_db.acquire_committee_lock("late", 100, now=1_099.0) is False  # still fresh
    assert real_db.committee_lock_active(100, now=1_099.0) is True
    assert real_db.committee_lock_active(100, now=1_101.0) is False  # stale = not running
    assert real_db.acquire_committee_lock("late", 100, now=1_101.0) is True
    real_db.release_committee_lock("dead")  # the crashed holder waking up must not free the new owner's lock
    assert real_db.committee_lock_active(100, now=1_102.0) is True


def test_many_simultaneous_callers_produce_exactly_one_winner(real_db):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=12) as pool:
        wins = list(pool.map(lambda i: real_db.acquire_committee_lock(f"w{i}", 60), range(12)))
    assert wins.count(True) == 1


def test_the_lock_row_never_shows_up_as_a_committee_decision(real_db):
    real_db.save_committee_run({"id": "2026-09-18:GOOGL", "date": "2026-09-18", "symbol": "GOOGL", "decision": "HOLD"})
    real_db.acquire_committee_lock("a", 60)
    assert [r["symbol"] for r in real_db.list_committee_runs()] == ["GOOGL"]
    assert [r["symbol"] for r in real_db.list_all_committee_runs()] == ["GOOGL"]


# ------------------------------------------------------------ claims step on/off --
# The spec requires: "A test must prove the saved decision is identical with the
# claims step on and off." The claims step attaches structured claims and an LLM
# narrative but must NOT modify the frozen committee decision logic.
def test_saved_decision_identical_with_claims_step_on_and_off(tmp_path, monkeypatch):
    """Run the committee with pipeline.claims flag ON and OFF; decisions must match."""
    from app import db, flags, migrate
    from sqlalchemy import create_engine
    import importlib
    importlib.reload(flags)

    # Seed the orchestration like the fake fixture does
    monkeypatch.setattr(db, "list_orchestrations", lambda: [{
        "id": "o1", "name": "Investment Committee", "mode": "committee_vote",
        "agent_ids": ["a"], "coordinator": "vn_engine", "schedule": None,
        "agent_timeout_s": 60.0, "run_budget_s": 240.0
    }])

    # Ensure deterministic runner
    monkeypatch.setattr(paper_cycle, "load_book", make_book)
    monkeypatch.setattr(committee_daily.orchestration, "run_orchestration", make_runner(calls={"AAPL": 2}))
    monkeypatch.setattr(committee_daily.ds, "get_live_signals", lambda: {"signals": []})
    # Freeze time
    monkeypatch.setattr(committee_daily, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))

    def _run(claims_enabled: bool, db_path: str):
        # Set up a fresh database for each run
        eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        db.metadata.create_all(eng)
        with eng.begin() as conn:
            migrate.upgrade(conn)
        monkeypatch.setattr(db, "engine", eng)
        # Set the flag - save original to avoid recursion
        orig_flag = flags.flag
        monkeypatch.setattr(flags, "flag", lambda key: claims_enabled if key == "pipeline.claims" else orig_flag(key))

        async def _go():
            return await committee_daily.run_daily(symbols=["AAPL"], force=True, dry_run=False)

        import asyncio
        result = asyncio.run(_go())
        # Return just the decisions (what matters for "frozen decision logic")
        decisions = result.get("decisions", {})
        if isinstance(decisions, dict):
            return {s: d for s, d in decisions.items()}
        return {}

    db_path_off = tmp_path / "test_claims_off.db"
    db_path_on = tmp_path / "test_claims_on.db"
    decisions_off = _run(False, db_path_off)
    decisions_on = _run(True, db_path_on)

    # The decisions must be identical - claims step is purely additive
    assert decisions_off == decisions_on, f"Decisions differ! OFF={decisions_off}, ON={decisions_on}"


# --- T4 (reviewer-added): the A6 gate is wired into run_daily but can never change or break a decision ---
def _run_daily_with(monkeypatch, db_path, gate_impl):
    from app import db, migrate, verification
    from sqlalchemy import create_engine
    import asyncio

    monkeypatch.setattr(db, "list_orchestrations", lambda: [{
        "id": "o1", "name": "Investment Committee", "mode": "committee_vote",
        "agent_ids": ["a"], "coordinator": "vn_engine", "schedule": None,
        "agent_timeout_s": 60.0, "run_budget_s": 240.0
    }])
    monkeypatch.setattr(paper_cycle, "load_book", make_book)
    monkeypatch.setattr(committee_daily.orchestration, "run_orchestration", make_runner(calls={"AAPL": 2}))
    monkeypatch.setattr(committee_daily.ds, "get_live_signals", lambda: {"signals": []})
    monkeypatch.setattr(committee_daily, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: NOW)}))
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        migrate.upgrade(conn)
    monkeypatch.setattr(db, "engine", eng)
    if gate_impl is not None:
        monkeypatch.setattr(verification.runner, "run_gate", gate_impl)
    return asyncio.run(committee_daily.run_daily(symbols=["AAPL"], force=True, dry_run=False)), db


def test_saved_decision_identical_with_gate_wired_in_and_patched_out(tmp_path, monkeypatch):
    async def no_gate(*a, **k):
        return None
    with_gate, _ = _run_daily_with(monkeypatch, tmp_path / "gate_on.db", None)
    without_gate, _ = _run_daily_with(monkeypatch, tmp_path / "gate_off.db", no_gate)
    assert with_gate.get("decisions") == without_gate.get("decisions")
    assert with_gate.get("decisions")  # the comparison is not vacuous


def test_an_exception_inside_run_gate_does_not_break_run_daily(tmp_path, monkeypatch):
    calls = []

    async def exploding_gate(run_id, *a, **k):
        calls.append(run_id)
        raise RuntimeError("gate blew up")

    result, _ = _run_daily_with(monkeypatch, tmp_path / "gate_boom.db", exploding_gate)
    assert calls, "run_gate was not called at all"
    assert result.get("decisions")  # the committee result still came back
