"""The evidence gate, proposals and the weekly digest: strict, deterministic, and never self-applying."""
from __future__ import annotations

import inspect
import random

import pytest

from app import research
from tests.test_paper_cycle import make_book
from tests.test_paper_tax_committee import D


def acct(aid, daily_returns, backtest_days=3):
    """An account whose curve has `backtest_days` backtest points then one LIVE point per return."""
    curve, eq = [], 100_000.0
    for i in range(backtest_days):
        curve.append([f"2026-01-{i + 1:02d}", eq, "backtest"])
    for i, r in enumerate(daily_returns):
        eq *= 1 + r
        curve.append([f"2026-06-{(i % 28) + 1:02d}-{i}", eq, "live"])  # unique, sortable labels
    return {"id": aid, "name": aid, "kind": "control", "strategy": "engine_tilt", "start_cash": 100_000.0, "curve": curve, "cash": 0.0, "positions": {},
            "trade_count": 0, "cost_paid": 0.0, "traded_notional": 0.0, "inception": "2026-01-01", "last_date": curve[-1][0], "tax": None}


def noise(n, mean, sd, seed):
    rnd = random.Random(seed)
    return [rnd.gauss(mean, sd) for _ in range(n)]


def pair(n, excess_mean, seed=1, sd=0.004):
    base = noise(n, 0.0004, 0.01, seed)
    cand = [b + e for b, e in zip(base, noise(n, excess_mean, sd, seed + 1))]
    return acct("cand", cand), acct("base", base)


# ------------------------------------------------------------------------ gate --


def test_live_returns_use_only_live_days_and_measure_the_first_from_the_last_backtest_close():
    a = acct("x", [0.01, 0.02], backtest_days=2)
    r = research.live_returns(a)
    assert list(r.values()) == [pytest.approx(0.01), pytest.approx(0.02)] and len(r) == 2


def test_the_gate_refuses_to_judge_before_enough_live_days():
    cand, base = pair(research.MIN_LIVE_DAYS - 1, 0.01)  # a huge edge, but too few days
    g = research.gate(cand, base)
    assert g["verdict"] == "insufficient" and g["n_days"] == research.MIN_LIVE_DAYS - 1 and g["ci_low_bps"] is None
    assert research.gate(acct("a", []), acct("b", []))["verdict"] == "insufficient"  # no live days at all


def test_a_consistent_edge_is_recognised_a_zero_edge_is_not_and_a_consistent_loss_is_flagged():
    n = 250
    assert research.gate(*pair(n, 0.002))["verdict"] == "edge"
    assert research.gate(*pair(n, 0.0, seed=5))["verdict"] == "no edge yet"
    assert research.gate(*pair(n, -0.002, seed=9))["verdict"] == "worse"
    g = research.gate(*pair(n, 0.002))
    assert g["ci_low_bps"] > 0 and g["ci_low_bps"] < g["mean_excess_bps"] < g["ci_high_bps"]


def test_a_lucky_looking_edge_is_not_enough_after_correcting_for_how_many_strategies_were_compared():
    cand, base = pair(120, 0.0004, seed=3)  # a small edge: visible at 5%, not at 5%/10
    single = research.gate(cand, base, n_comparisons=1)
    many = research.gate(cand, base, n_comparisons=50)
    assert many["ci_low_bps"] < single["ci_low_bps"]  # the correction widens the interval
    assert many["verdict"] != "edge" or single["verdict"] == "edge"  # it can only make the verdict harder to reach


def test_the_verdict_is_deterministic():
    a, b = pair(150, 0.001, seed=11)
    assert research.gate(a, b) == research.gate(a, b)


def test_evidence_table_compares_each_candidate_with_both_yardsticks_and_skips_missing_accounts():
    base = noise(80, 0.0004, 0.01, 1)
    accounts = [acct("ctl_engine", noise(80, 0.0004, 0.01, 2)), acct("ctl_equal", base), acct("ctl_placebo", noise(80, 0.0004, 0.01, 3)), acct("ctl_trend", base)]
    rows = {r["id"]: r for r in research.evidence_table(accounts)}
    assert set(rows) == {"ctl_engine", "ctl_trend"}  # the others are not there
    assert set(rows["ctl_engine"]["vs"]) == {"ctl_equal", "ctl_placebo"}
    assert rows["ctl_trend"]["vs"]["ctl_equal"]["mean_excess_bps"] == pytest.approx(0.0)  # identical returns: zero excess
    assert rows["ctl_engine"]["live_days"] == 80


# ------------------------------------------------------------------- proposals --


def board(*agents):
    return {"min_ranked": 30, "agents": list(agents)}


def agent(name, edge, n, ranked=True):
    return {"agent": name, "mean_edge": edge, "directional_calls": n, "ranked": ranked}


def test_no_agent_proposal_without_enough_scored_calls():
    assert research.agent_proposals(board(agent("A", 0.05, 12, ranked=False))) == []


def test_reweighting_is_shrunk_bounded_and_ignores_trivial_differences():
    props = research.agent_proposals(board(agent("Good", 0.01, 40), agent("Bad", -0.05, 40), agent("Meh", 0.001, 40)))
    text = " ".join(props)
    assert "raising Good" in text and "1.25x" in text  # +1% edge -> +25%
    assert "lowering Bad" in text and "0.50x" in text  # capped at -50% however bad
    assert "Meh" not in text and len(props) == 2


def test_strategy_proposals_only_appear_when_the_gate_says_edge_or_worse():
    def row(name, eq, pl):
        gate = lambda v: {"verdict": v, "mean_excess_bps": 5.0, "n_days": 90}  # noqa: E731
        return {"id": name, "name": name, "live_days": 90, "live_return": 0.0, "vs": {"ctl_equal": gate(eq), "ctl_placebo": gate(pl)}}

    assert research.build_proposals([row("A", "insufficient", "insufficient"), row("B", "no edge yet", "no edge yet")], board()) == []
    props = research.build_proposals([row("Winner", "edge", "edge"), row("Loser", "no edge yet", "worse")], board())
    assert any("Winner has beaten equal-weight" in p for p in props) and any("Loser is doing worse than the placebo" in p for p in props)


def test_there_is_no_code_path_that_applies_a_proposal():
    src = inspect.getsource(research)
    assert "update_agent" not in src and "create_agent" not in src and "update_orchestration" not in src and "save_paper_account" not in src
    assert "for a human to decide" in src


# ---------------------------------------------------------------------- digest --


@pytest.fixture
def fake_db(monkeypatch):
    class F:
        narratives, audit = [], []

    f = F()
    f.narratives, f.audit = [], []
    monkeypatch.setattr(research.db, "list_paper_accounts", lambda: [])
    monkeypatch.setattr(research.db, "list_all_committee_runs", lambda: [])
    monkeypatch.setattr(research.db, "list_report_narratives", lambda: list(f.narratives))
    monkeypatch.setattr(research.db, "create_report_narrative", lambda n: f.narratives.append(n) or n)
    monkeypatch.setattr(research.db, "log_audit", lambda *a, **k: f.audit.append(a))
    return f


def test_the_digest_says_plainly_when_there_is_nothing_to_propose(fake_db):
    out = research.run_weekly(book=make_book(80))
    text = out["digest"]["text"]
    assert out["written"] and out["proposals"] == 0
    for header in ("1. What was analysed", "2. What the live paper strategies delivered", "3. How the stocks move together", "4. Committee members", "5. Proposals"):
        assert header in text
    assert "Nothing in this digest changes anything automatically" in text and "the system is not suggesting any change" in text
    assert "'insufficient'" in text


def test_the_digest_is_idempotent_per_week_and_a_dry_run_writes_nothing(fake_db):
    book = make_book(80)
    assert research.run_weekly(book=book, dry_run=True)["written"] is False and fake_db.narratives == []
    assert research.run_weekly(book=book)["written"] is True
    assert research.run_weekly(book=book)["written"] is False  # same week: no duplicate
    assert len(fake_db.narratives) == 1 and fake_db.narratives[0]["title"] == research.DIGEST_TITLE and fake_db.narratives[0]["profile"].startswith("research:")
    assert fake_db.audit and fake_db.audit[0][1] == "research.weekly_digest"


def test_the_digest_counts_reviews_disagreements_and_risk_holds(fake_db, monkeypatch):
    book = make_book(80)
    d = book.latest_date
    runs = [
        {"symbol": "AAA", "date": d, "quorum_ok": True, "engine_signal": "HOLD", "decision": "BUY", "action": "HOLD", "gate": "risk regime is HIGH", "agents": []},
        {"symbol": "BBB", "date": d, "quorum_ok": True, "engine_signal": "BUY", "decision": "SELL", "action": "SELL", "gate": None, "agents": []},
        {"symbol": "SPY", "date": d, "quorum_ok": False, "engine_signal": "HOLD", "decision": "HOLD", "action": None, "gate": None, "agents": []},
    ]
    monkeypatch.setattr(research.db, "list_all_committee_runs", lambda: runs)
    text = research.run_weekly(book=book, dry_run=True)["digest"]["text"]
    assert "made 3 review(s) this week (2 reliable); it disagreed with the plain engine on 1 and the risk check held back 1 BUY(s)" in text


def test_the_view_bundles_associations_evidence_and_the_latest_digest(fake_db):
    book = make_book(80)
    research.run_weekly(book=book)
    v = research.view(book)
    assert v["min_live_days"] == research.MIN_LIVE_DAYS and v["associations"]["symbols"] == 3 and v["evidence"] == [] and v["proposals"] == []
    assert v["latest_digest"]["title"] == research.DIGEST_TITLE and "Weekly research digest" in v["latest_digest"]["narrative"]


def test_deflated_sharpe_penalises_many_trials():
    import random as _r
    rnd = _r.Random(1)
    xs = [0.001 + rnd.gauss(0, 0.01) for _ in range(250)]
    few = research.deflated_sharpe(xs, [0.0, 0.01])
    many = research.deflated_sharpe(xs, [rnd.gauss(0, 0.05) for _ in range(50)])
    assert 0 <= many["dsr"] < few["dsr"] <= 1
    assert many["expected_max_sharpe"] > few["expected_max_sharpe"]
    assert research.deflated_sharpe(xs[:2], [0.1, 0.2]) is None and research.deflated_sharpe(xs, [0.1]) is None


def test_evidence_table_deflated_sharpe_is_on_excess_over_placebo():
    # rising market, no skill: candidates and placebo share the market move and differ only by noise
    market = noise(120, 0.003, 0.01, 7)
    no_skill = lambda s: [m + e for m, e in zip(market, noise(120, 0.0, 0.002, s))]
    accounts = [acct("ctl_placebo", no_skill(1)), acct("ctl_engine", no_skill(2)), acct("ctl_trend", no_skill(3)),
                acct("ctl_committee", [m + e for m, e in zip(market, noise(120, 0.002, 0.002, 4))])]  # real edge over placebo
    rows = {r["id"]: r for r in research.evidence_table(accounts)}
    assert research.deflated_sharpe(list(research.live_returns(accounts[1]).values()), [0.0, 0.01])["dsr"] > 0.95  # raw: passes by riding the market
    assert rows["ctl_engine"]["deflated_sharpe"]["vs"] == "ctl_placebo"
    assert rows["ctl_engine"]["deflated_sharpe"]["dsr"] < 0.95 and rows["ctl_trend"]["deflated_sharpe"]["dsr"] < 0.95
    assert rows["ctl_committee"]["deflated_sharpe"]["dsr"] > 0.95


def test_evidence_table_deflated_sharpe_none_without_placebo():
    rows = research.evidence_table([acct("ctl_engine", noise(80, 0.001, 0.01, 2)), acct("ctl_equal", noise(80, 0.0, 0.01, 3))])
    assert rows[0]["deflated_sharpe"] is None
