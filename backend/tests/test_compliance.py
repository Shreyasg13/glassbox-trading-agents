"""A7 compliance filter (S3 T8): every rule's positive and negative (neutral research language) side, the disclaimer rewrite,
severity, rule loading, `record()` against a migrated temp database, and the admin routes."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select

from app import compliance, db, disclaimer, migrate
from app.compliance import filter as cf
from app.migrated_tables import compliance_events_table

CH = "committee_report"


@pytest.fixture(autouse=True)
def _fresh_rules_and_disclaimer(monkeypatch):
    monkeypatch.delenv("GLASSBOX_COMPLIANCE_RULES_PATH", raising=False)
    monkeypatch.delenv("GLASSBOX_DISCLAIMER_PATH", raising=False)
    cf.clear_cache()
    disclaimer.clear_cache()
    yield
    cf.clear_cache()
    disclaimer.clear_cache()


@pytest.fixture
def events_db(tmp_path, monkeypatch):
    """A throwaway database migrated to head (compliance_events included), swapped in for app.db.engine."""
    eng = create_engine(f"sqlite:///{tmp_path / 'c.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)
    with eng.begin() as conn:
        migrate.upgrade(conn)
    monkeypatch.setattr(db, "engine", eng)
    yield eng
    eng.dispose()


def D() -> str:
    return disclaimer.text()


def run(body: str, channel: str = CH) -> cf.FilterResult:
    """Check `body` with the disclaimer already present, so only the rule under test can fire."""
    return compliance.check(f"{body}\n\n{D()}", channel=channel)


def hits(res: cf.FilterResult, rule_id: str) -> list[str]:
    return [e["matched_text"] for e in res.events if e["rule_id"] == rule_id]


# ---- Banned advisory phrases (block) ----

@pytest.mark.parametrize("body,phrase", [
    ("This trade is guaranteed to double.", "guaranteed to double"),
    ("Enjoy guaranteed returns with this pick.", "guaranteed returns"),
    ("With this setup you can't lose.", "can't lose"),
    ("With this setup you can’t lose.", "can’t lose"),  # curly apostrophe
    ("An index fund that cannot lose.", "cannot lose"),
    ("A risk-free way to grow.", "risk-free"),
    ("A risk free way to grow.", "risk free"),
    ("NVDA is a sure thing.", "sure thing"),
    ("This is how to double your money.", "double your money"),
    ("Buying the dip is a no-brainer.", "no-brainer"),
    ("This stock can't go down.", "can't go down"),
    ("Time to load up on TSLA.", "load up on"),
    ("The trade carries zero risk.", "zero risk"),
    ("Index arbitrage is free money.", "free money"),
    ("Back up the truck on NVDA.", "Back up the truck"),
])
def test_banned_phrase_blocks(body, phrase):
    res = run(body)
    assert res.action == "blocked"
    assert hits(res, "advice.banned_phrase") == [phrase]
    assert res.events[0]["action"] == "blocked" and res.events[0]["channel"] == CH


@pytest.mark.parametrize("body", [
    "YOU SHOULD BUY this now.",
    "GUARANTEED RETURNS here.",
    "You Can't Lose here.",
])
def test_banned_phrases_are_case_insensitive(body):
    assert run(body).action == "blocked"


@pytest.mark.parametrize("body", [
    "The committee voted to buy AAPL.",
    "The committee's lean is to sell; the risk officer disagreed.",
    "The 10-year Treasury, the usual risk-free rate, yields 4.2%.",
    "Returns are not guaranteed and the stock can lose value.",
    "The dividend is government-guaranteed in name only.",
    "Management has to act on margins before the next quarter.",
    "Analysts should revisit the thesis if guidance slips.",
    # review round 1: these were blocked
    "The bond pays a guaranteed income stream from its coupon.",
    "Treasuries are guaranteed by the US government.",
    "There is no guaranteed outcome here, and no guaranteed returns either.",
    "This is not a sure thing.",
    "Earnings are no sure thing this quarter.",
])
def test_neutral_research_language_is_not_a_banned_phrase(body):
    res = run(body)
    assert res.events == [] and res.action == "pass"


def test_act_now_is_flagged_not_blocked():
    res = run("Companies should act now on the tax change.")
    assert res.action == "flagged" and hits(res, "advice.pressure_phrase") == ["act now"]
    assert run("Act now before the earnings call.").action == "flagged"


@pytest.mark.parametrize("body", [
    "The board will react now that rates moved.",  # "act now" inside "react now"
    "Unguaranteed returns rose.",  # "guaranteed returns" inside a longer word
    "They can act nowhere near fast enough.",  # "act now" + a longer word
])
def test_phrases_match_whole_words_only(body):
    res = run(body)
    assert res.events == [] and res.action == "pass"


def test_a_repeated_phrase_is_one_event():
    res = run("Risk-free. Really, risk-free. RISK-FREE.")
    assert hits(res, "advice.banned_phrase") == ["Risk-free"]


def test_a_real_hit_next_to_an_allowed_phrase_still_blocks():
    res = run("Above the risk-free rate, and totally risk-free for you.")
    assert res.action == "blocked" and hits(res, "advice.banned_phrase") == ["risk-free"]


# ---- Personalized instructions on the reader's own holdings (block) ----

@pytest.mark.parametrize("body", [
    "Sell your AAPL before earnings.",
    "It is time to trim your $NVDA.",
    "You should consider this: sell your shares of TSLA.",
    "Your portfolio should hold more cash.",
    "Your holdings need to be rebalanced.",
    "You need to buy MSFT this week.",
    "You must sell before Friday.",
    "We recommend that you buy GOOG.",
    "Add to your position on weakness.",
])
def test_personal_holding_instructions_block(body):
    res = run(body)
    assert res.action == "blocked"
    assert hits(res, "advice.personal_holdings")


@pytest.mark.parametrize("body", [
    "GlassBox: 2 of your 5 stocks need a look.",  # the real user digest subject line
    "Nothing needs your attention today.",
    "Open your dashboard to see the full report.",
    "Your portfolio holds AAPL and MSFT.",
    "The committee voted to buy AAPL; the analysts sell-side consensus is Hold.",
    "Sell your car? Not a question this report answers.",  # not a holding: lower-case word, no ticker
    "Investors who need to buy protection use puts.",
])
def test_neutral_second_person_text_is_not_personal_advice(body):
    res = run(body)
    assert hits(res, "advice.personal_holdings") == [] and res.action == "pass"


@pytest.mark.parametrize("body", [
    "Before you sell your AI thesis short, read the capex numbers.",
    "Markets may reduce your US exposure indirectly through the dollar.",
    "Economists trim your CPI estimates when energy falls.",
])
def test_an_all_caps_word_that_is_not_a_ticker_is_not_a_holding(body):
    res = run(body)
    assert res.events == [] and res.action == "pass"


@pytest.mark.parametrize("body", ["Sell your tsla now.", "sell your Aapl.", "Trim your $abcd."])
def test_real_tickers_match_in_any_case(body):
    assert hits(run(body), "advice.personal_holdings")


def test_the_ticker_universe_can_be_passed_in():
    body = f"Sell your ABCD before earnings.\n\n{D()}"
    assert compliance.check(body, channel=CH).action == "pass"  # not in the app's universe
    assert compliance.check(body, channel=CH, tickers={"abcd"}).action == "blocked"
    assert compliance.check(f"Sell your AAPL.\n\n{D()}", channel=CH, tickers=[]).action == "pass"  # an empty universe


# ---- Direct buy/sell advice (block) ----

@pytest.mark.parametrize("body,matched", [
    ("Given the setup, you should buy AAPL.", "you should buy"),
    ("At these levels you should sell MSFT.", "you should sell"),
    ("You should definitely buy NVDA.", "You should definitely buy"),
    ("You should really just sell.", "You should really just sell"),
    ("I'd buy AAPL now.", "I'd buy"),
    ("Honestly, we would sell here.", "we would sell"),
    ("I recommend buying the dip.", "I recommend buying"),
    ("Buy AAPL now!", "Buy AAPL now"),
    ("Strong quarter. Sell TSLA today.", "Sell TSLA today"),
])
def test_direct_instructions_block(body, matched):
    res = run(body)
    assert res.action == "blocked" and hits(res, "advice.direct_instruction") == [matched]


@pytest.mark.parametrize("body", [
    "The committee voted to buy AAPL today.",
    "Trades executed today:\nBUY AAPL 10 shares.",  # a trade log line, not an imperative
    "You should read the 10-K before the call.",
    "We recommend caution; sell-side analysts are split.",
])
def test_neutral_text_is_not_a_direct_instruction(body):
    res = run(body)
    assert hits(res, "advice.direct_instruction") == [] and res.action == "pass"


# ---- Reported speech and quotes are flagged, not blocked ----

@pytest.mark.parametrize("body,rule", [
    ("The CEO said 'you should buy what you know'.", "advice.direct_instruction"),
    ("The CEO said “you should buy what you know”.", "advice.direct_instruction"),
    ("Management said you need to buy more capacity.", "advice.personal_holdings"),
    ('A reader wrote in: "this stock is a sure thing".', "advice.banned_phrase"),
    ("According to the promoter, it is risk-free.", "advice.banned_phrase"),
])
def test_quoted_or_reported_advice_is_flagged_not_blocked(body, rule):
    res = run(body)
    assert res.action == "flagged" and hits(res, rule) and all(e["action"] == "flagged" for e in res.events)


def test_a_reported_sentence_does_not_excuse_the_next_one():
    res = run("The CEO said growth is strong. You should buy AAPL.")
    assert res.action == "blocked"


# ---- HTML ----

@pytest.mark.parametrize("body", [
    "<p>you <b>should</b> buy AAPL</p>",
    "<p>you should&nbsp;buy AAPL</p>",
    "<p>it&#x27;s a sure&#32;thing</p>",
])
def test_html_is_normalised_before_matching(body):
    assert run(body).action == "blocked"


def test_html_disclaimer_detection_sees_through_tags_and_entities():
    d = D()
    marked = d.replace("not", "<b>not</b>", 1).replace(" ", "&nbsp;", 1).replace(",", "&#44;")
    doc = f"<html><body><p>Hold AAPL.</p><footer>{marked}</footer></body></html>"
    res = compliance.check(doc, channel="user_digest")
    assert res.action == "pass" and res.text == doc


def test_html_disclaimer_is_inserted_inside_the_body_escaped(tmp_path, monkeypatch):
    p = tmp_path / "disclaimer.md"
    p.write_text("Research only & <not> advice.\n", encoding="utf-8")
    monkeypatch.setenv("GLASSBOX_DISCLAIMER_PATH", str(p))
    disclaimer.clear_cache()
    doc = "<html><body><p>Hold AAPL.</p></BODY></html>"
    res = compliance.check(doc, channel="user_digest")
    assert res.action == "rewritten"
    assert res.text == "<html><body><p>Hold AAPL.</p><p>Research only &amp; &lt;not&gt; advice.</p></BODY></html>"
    assert compliance.check(res.text, channel="user_digest").action == "pass"  # idempotent on HTML too


def test_comparison_signs_are_not_mistaken_for_tags():
    res = run("RSI < 30 and you should buy > 70? No.")
    assert res.action == "blocked"


# ---- Performance ----

def test_a_long_digit_run_is_checked_in_linear_time():
    import time

    for text in ("1" * 200_000, ("9" * 1000 + ".") * 200, "+1.5 % " * 30_000):
        start = time.perf_counter()
        compliance.check(text, channel=CH)
        assert time.perf_counter() - start < 1.0


# ---- Performance mentions must reference the ledger (flag) ----

PERF = [
    ("The strategy returned 12% last year.", "returned 12%"),
    ("The picks beat the market by 4 points.", "beat the market by"),
    ("It has compounded +23% a year since launch.", "+23% a year"),
    ("That is a 15% annualized return.", "15% annualized return"),
    ("Total returns of 8.5% since January.", "returns of 8.5%"),
    ("A win rate of 62% on buy calls.", "win rate of 62%"),
]


@pytest.mark.parametrize("body,matched", PERF)
def test_performance_mention_without_a_ledger_reference_is_flagged(body, matched):
    res = run(body)
    assert res.action == "flagged"
    assert hits(res, "performance.needs_ledger") == [matched]
    assert all(e["action"] == "flagged" for e in res.events)


@pytest.mark.parametrize("body,matched", PERF)
def test_performance_mention_with_a_ledger_reference_passes(body, matched):
    res = run(f"{body} Source: {{{{ledger:call-2026-09-26-AAPL}}}}.")
    assert res.action == "pass" and res.events == []


@pytest.mark.parametrize("body", [
    "Revenue grew 12% a year over five years.",
    "The company earns a 15% return on equity.",
    "Operating margin rose to 31%.",
    "The stock fell 3% on Tuesday.",
    "The 10-year yield rose 0.2% this week.",
    "The stock returned to its 200-day average, 4% below the high.",
    "Services revenue returned 12% growth.",
])
def test_neutral_numbers_are_not_performance_mentions(body):
    assert hits(run(body), "performance.needs_ledger") == []


def test_an_empty_ledger_reference_does_not_count():
    assert run("The strategy returned 12%. {{ledger:}}").action == "flagged"


# ---- The disclaimer (the only rewrite) ----

def test_missing_disclaimer_is_appended_exactly_once():
    res = compliance.check("The committee voted to hold AAPL.", channel="user_digest")
    assert res.action == "rewritten"
    assert res.text == f"The committee voted to hold AAPL.\n\n{D()}"
    assert res.text.count(D()) == 1
    assert res.events == [{"channel": "user_digest", "rule_id": "disclaimer.required", "matched_text": "", "action": "rewritten"}]


def test_the_disclaimer_rewrite_is_idempotent():
    once = compliance.check("Hold AAPL.   \n", channel=CH)
    twice = compliance.check(once.text, channel=CH)
    assert twice.action == "pass" and twice.events == [] and twice.text == once.text
    assert twice.text.count(D()) == 1


def test_a_present_disclaimer_passes_even_rewrapped_or_recased():
    wrapped = " \n ".join(D().upper().split())
    res = compliance.check(f"Hold AAPL. {wrapped}", channel=CH)
    assert res.action == "pass" and res.text == f"Hold AAPL. {wrapped}"


def test_empty_text_becomes_just_the_disclaimer():
    res = compliance.check("", channel=CH)
    assert res.action == "rewritten" and res.text == D()


def test_the_disclaimer_comes_from_the_config_file(tmp_path, monkeypatch):
    p = tmp_path / "disclaimer.md"
    p.write_text("<!-- PENDING LEGAL REVIEW: test -->\nTest wording only.\n", encoding="utf-8")
    monkeypatch.setenv("GLASSBOX_DISCLAIMER_PATH", str(p))
    disclaimer.clear_cache()
    res = compliance.check("Hold AAPL.", channel=CH)
    assert res.text == "Hold AAPL.\n\nTest wording only." and res.action == "rewritten"
    assert compliance.check(res.text, channel=CH).action == "pass"


# ---- Severity and result shape ----

def test_most_severe_action_wins_blocked_over_flagged_over_rewritten():
    res = compliance.check("You should buy it: the strategy returned 12% last year.", channel=CH)
    assert res.action == "blocked"
    assert {e["action"] for e in res.events} == {"blocked", "flagged", "rewritten"}
    assert res.text.endswith(D())  # the rewrite still happens; a blocked text is simply not published


def test_flagged_beats_rewritten():
    res = compliance.check("The strategy returned 12% last year.", channel=CH)
    assert res.action == "flagged" and {e["action"] for e in res.events} == {"flagged", "rewritten"}


def test_check_is_pure_it_never_touches_the_database(monkeypatch):
    class NoDb:
        def __getattr__(self, name):
            raise AssertionError("check() touched the database")

    monkeypatch.setattr(db, "engine", NoDb())
    assert compliance.check("You should buy.", channel=CH).action == "blocked"


def test_channel_is_required():
    with pytest.raises(ValueError):
        compliance.check("text", channel="")


# ---- Rules as data ----

def test_the_shipped_rules_load_and_cover_the_plan():
    ids = [r.id for r in compliance.load_rules()]
    assert ids == ["advice.banned_phrase", "advice.direct_instruction", "advice.personal_holdings", "advice.pressure_phrase",
                   "performance.needs_ledger", "disclaimer.required"]
    assert compliance.load_rules() is compliance.load_rules()  # loaded once


def _write_rules(tmp_path, rules) -> str:
    p = tmp_path / "rules.json"
    p.write_text(json.dumps({"rules": rules}), encoding="utf-8")
    return str(p)


def test_the_rules_path_can_be_overridden(tmp_path, monkeypatch):
    path = _write_rules(tmp_path, [{"id": "t.moon", "description": "", "kind": "phrase", "patterns": ["to the moon"], "action": "flag"}])
    monkeypatch.setenv("GLASSBOX_COMPLIANCE_RULES_PATH", path)
    res = compliance.check("AAPL to the moon. You should buy.", channel=CH)
    assert res.action == "flagged" and [e["rule_id"] for e in res.events] == ["t.moon"]  # the shipped rules are not loaded


@pytest.mark.parametrize("rule", [
    {"id": "x", "kind": "phrase", "patterns": ["a"], "action": "rewrite", "replacement": "b"},  # rewrite only for requires
    {"id": "x", "kind": "phrase", "patterns": ["a"], "action": "block", "replacement": "b"},  # replacement without rewrite
    {"id": "x", "kind": "requires", "pattern": "a", "action": "rewrite"},  # rewrite without replacement
    {"id": "x", "kind": "regex", "patterns": ["(unclosed"], "action": "flag"},
    {"id": "x", "kind": "llm", "patterns": ["a"], "action": "flag"},
    {"id": "x", "kind": "phrase", "patterns": [], "action": "flag"},
    {"id": "x", "kind": "phrase", "patterns": ["a"], "action": "delete"},
])
def test_an_invalid_rule_fails_loudly(tmp_path, monkeypatch, rule):
    monkeypatch.setenv("GLASSBOX_COMPLIANCE_RULES_PATH", _write_rules(tmp_path, [rule]))
    with pytest.raises(ValueError):
        compliance.check("text", channel=CH)


def test_a_missing_rules_file_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("GLASSBOX_COMPLIANCE_RULES_PATH", str(tmp_path / "nope.json"))
    with pytest.raises(ValueError):
        compliance.load_rules()


# ---- record() ----

def _rows(eng):
    with eng.connect() as c:
        return c.execute(select(compliance_events_table).order_by(compliance_events_table.c.rule_id)).fetchall()


def test_record_writes_one_row_per_event(events_db):
    res = compliance.check("You should buy: it returned 12%.", channel=CH)
    assert compliance.record(res, run_id="2026-09-26:AAPL") == 3
    rows = _rows(events_db)
    assert [(r.rule_id, r.action, r.matched_text) for r in rows] == [
        ("advice.direct_instruction", "blocked", "You should buy"),
        ("disclaimer.required", "rewritten", ""),
        ("performance.needs_ledger", "flagged", "returned 12%"),
    ]
    assert {r.run_id for r in rows} == {"2026-09-26:AAPL"} and {r.channel for r in rows} == {CH}
    assert len({r.id for r in rows}) == 3
    ts = rows[0].created_at
    assert len(ts) == len("2026-09-26T00:00:00.000000+00:00") and ts.endswith("+00:00")


def test_record_without_a_run_id_and_with_nothing_to_record(events_db):
    assert compliance.record(compliance.check(f"Fine. {D()}", channel="assistant")) == 0
    assert compliance.record(compliance.check("Fine.", channel="assistant")) == 1
    assert _rows(events_db)[0].run_id is None


def test_record_truncates_matched_text_to_300_chars(events_db):
    res = cf.FilterResult(text="", action="flagged", events=[{"channel": CH, "rule_id": "r", "matched_text": "x" * 1000, "action": "flagged"}])
    assert compliance.record(res) == 1
    assert len(_rows(events_db)[0].matched_text) == 300


def test_record_survives_a_database_error(tmp_path, monkeypatch, caplog):
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")  # never migrated: no compliance_events table
    monkeypatch.setattr(db, "engine", eng)
    res = compliance.check("You should buy.", channel=CH)
    assert compliance.record(res, run_id="r1") == 0
    assert "compliance record failed" in caplog.text
    eng.dispose()


def test_record_survives_a_broken_engine(monkeypatch):
    class Broken:
        def begin(self):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "engine", Broken())
    assert compliance.record(compliance.check("x", channel=CH)) == 0


# ---- Admin API ----

def _seed(events_db):
    t = lambda d: datetime(2026, 9, d, 12, 0, tzinfo=timezone.utc)
    compliance.record(compliance.check("You should buy.", channel=CH), run_id="a", now=t(24))  # blocked + rewritten
    compliance.record(compliance.check(f"It returned 12%. {D()}", channel="user_digest"), now=t(25))  # flagged
    compliance.record(compliance.check("Hold.", channel="assistant"), now=t(26))  # rewritten


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app

    return TestClient(app), app


def test_admin_compliance_routes_require_admin(client, events_db):
    from app.auth import TokenPayload, get_current_user

    c, app = client
    try:
        app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub="v", role="viewer")
        assert c.get("/api/admin/compliance/events").status_code == 403
        assert c.get("/api/admin/compliance/rules").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub="a", role="admin")
        assert c.get("/api/admin/compliance/events").status_code == 200
        assert c.get("/api/admin/compliance/rules").status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_compliance_routes_reject_anonymous(client):
    c, _ = client
    assert c.get("/api/admin/compliance/events").status_code == 401
    assert c.get("/api/admin/compliance/rules").status_code == 401


def test_admin_events_filters_newest_first_and_clamps_the_limit(client, events_db):
    from app.auth import TokenPayload, get_current_user

    _seed(events_db)
    c, app = client
    try:
        app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub="a", role="admin")
        get = lambda **p: c.get("/api/admin/compliance/events", params=p)
        items = get().json()
        assert len(items) == 4 and [i["created_at"][:10] for i in items] == ["2026-09-26", "2026-09-25", "2026-09-24", "2026-09-24"]
        assert [i["rule_id"] for i in get(action="flagged").json()] == ["performance.needs_ledger"]
        assert {i["channel"] for i in get(action="rewritten").json()} == {CH, "assistant"}
        assert [i["created_at"][:10] for i in get(**{"from": "2026-09-25"}).json()] == ["2026-09-26", "2026-09-25"]
        assert [i["created_at"][:10] for i in get(to="2026-09-25").json()] == ["2026-09-25", "2026-09-24", "2026-09-24"]
        assert len(get(**{"from": "2026-09-25", "to": "2026-09-25"}).json()) == 1
        assert len(get(to="2026-09-25T11:59:59Z").json()) == 2
        assert len(get(limit=0).json()) == 1 and len(get(limit=100000).json()) == 4
        assert get(action="deleted").status_code == 400
        assert get(**{"from": "yesterday"}).status_code == 400
        blocked = get(action="blocked").json()[0]
        assert blocked["run_id"] == "a" and blocked["matched_text"] == "You should buy"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_rules_lists_the_loaded_rules(client):
    from app.auth import TokenPayload, get_current_user

    c, app = client
    try:
        app.dependency_overrides[get_current_user] = lambda: TokenPayload(sub="a", role="admin")
        rules = c.get("/api/admin/compliance/rules").json()
        assert [r["id"] for r in rules] == [r.id for r in compliance.load_rules()]
        by_id = {r["id"]: r for r in rules}
        assert by_id["advice.banned_phrase"]["action"] == "block" and "can't lose" in by_id["advice.banned_phrase"]["patterns"]
        assert by_id["advice.banned_phrase"]["reported_action"] == "flag"
        assert by_id["disclaimer.required"] == {
            "id": "disclaimer.required", "description": by_id["disclaimer.required"]["description"], "kind": "requires",
            "action": "rewrite", "pattern": "{{disclaimer}}", "replacement": "{{disclaimer}}",
        }
    finally:
        app.dependency_overrides.pop(get_current_user, None)
