"""Paper-trading engine: signal parity with the live endpoint, execution
timing (no look-ahead), costs, sizing rules, rebalancing, idempotent/incremental
replay, placebo control, metrics and the signal scorecard. Everything runs on
small synthetic price frames with hand-placed signals so each expectation can
be checked by eye."""
from __future__ import annotations

import pandas as pd
import pytest

from app import data_source as ds
from app import paper

N = 80
DATES = pd.bdate_range("2024-01-02", periods=N)  # naive dates; the real data is tz-aware, tested separately
PARAMS = {"rsi_low": 30, "rsi_high": 70, "fast_ma": 20, "slow_ma": 50}


def frame(close=100.0, buy=(), sell=(), n=N, dates=None):
    """Price frame with hand-placed signals: BUY = RSI 20 + bullish cross,
    SELL = RSI 80 + bearish cross, otherwise HOLD (RSI 50, neutral)."""
    closes = list(close) if isinstance(close, (list, tuple)) else [float(close)] * n
    rsi, fast, slow = [], [], []
    for i in range(n):
        if i in buy:
            rsi.append(20.0), fast.append(101.0), slow.append(100.0)
        elif i in sell:
            rsi.append(80.0), fast.append(99.0), slow.append(100.0)
        else:
            rsi.append(50.0), fast.append(100.0), slow.append(100.0)
    return pd.DataFrame({"Close": closes, "RSI": rsi, "MA_20": fast, "MA_50": slow}, index=dates if dates is not None else DATES[:n])


def book_of(frames):
    return paper.PriceBook.from_frames(frames, {s: PARAMS for s in frames})


def run(acct, book, **kw):
    kw.setdefault("live_from", "2999-01-01")
    return paper.advance(acct, book, **kw)


def acct(strategy="static_hold", weights=None, invested=1.0, **kw):
    return paper.new_account("t", "test", "control", strategy, weights or {"AAA": 1.0}, invested=invested, **kw)


def day(i):
    return DATES[i].strftime("%Y-%m-%d")


# ---------------------------------------------------------------- signals --


@pytest.mark.parametrize(
    "rsi,cross,expected",
    [
        (20, "BULLISH", "BUY"),
        (29.9, "BULLISH", "BUY"),
        (30, "BULLISH", "HOLD"),  # boundary: strictly below rsi_low
        (20, "BEARISH", "HOLD"),  # oversold but downtrend: no buy
        (80, "BEARISH", "SELL"),
        (70, "BEARISH", "HOLD"),
        (80, "BULLISH", "HOLD"),
        (50, "NEUTRAL", "HOLD"),
    ],
)
def test_signal_rule(rsi, cross, expected):
    assert ds.signal_from_indicators(rsi, cross, PARAMS)[0] == expected


def test_signal_confidence_scales_with_how_extreme_the_rsi_is():
    assert ds.signal_from_indicators(0, "BULLISH", PARAMS)[1] == 90  # 60 + 30 lands exactly on the cap
    assert ds.signal_from_indicators(25, "BULLISH", PARAMS)[1] == 65
    assert ds.signal_from_indicators(100, "BEARISH", PARAMS)[1] == 80  # 50 + 30; RSI<=100 can never reach the 85 cap
    assert ds.signal_from_indicators(50, "NEUTRAL", PARAMS)[1] == 50


def test_pricebook_signals_match_the_live_signals_endpoint(monkeypatch):
    df = frame(buy={N - 1})
    df["Volume"] = 1_000_000.0  # get_live_signals also reads volume
    df["Volume_MA"] = 1_000_000.0
    monkeypatch.setattr(ds, "STOCK_INFO", {"AAA": {"name": "A", "sector": "T", "beta": 1.0}})
    monkeypatch.setattr(ds, "_load_trained_params", lambda: {"AAA": PARAMS})
    monkeypatch.setattr(ds, "_load_parquet_row", lambda s: df)
    live = ds.get_live_signals()["signals"][0]
    book = book_of({"AAA": df})
    assert live["signal"] == book.signal_at("AAA", day(N - 1))[0] == "BUY"
    assert live["confidence"] == book.signal_at("AAA", day(N - 1))[1]


# -------------------------------------------------------------- price book --


def test_pricebook_handles_tz_aware_indexes_like_the_real_parquet():
    tz = pd.bdate_range("2024-01-02", periods=5, tz="America/New_York")
    book = book_of({"AAA": frame(n=5, dates=tz)})
    assert book.dates[0] == "2024-01-02" and len(book.dates) == 5  # date not shifted by the timezone


def test_warmup_nan_indicators_are_hold_not_a_crash():
    df = frame(n=5)
    df.loc[df.index[:2], ["RSI", "MA_20", "MA_50"]] = float("nan")
    book = book_of({"AAA": df})
    assert book.signal_at("AAA", day(0))[0] == "HOLD"


def test_close_on_falls_back_to_the_last_known_price():
    df = frame(close=[10, 11, 12, 13, 14], n=5).drop(DATES[2])
    book = book_of({"AAA": df, "BBB": frame(n=5)})
    assert book.close_on("AAA", day(2)) == 11.0  # halted that day: last print
    assert book.close_on("AAA", "2000-01-01") is None


def test_frames_without_close_or_empty_are_skipped():
    book = book_of({"AAA": frame(n=5), "BAD": pd.DataFrame({"Open": [1]}, index=DATES[:1]), "EMPTY": frame(n=0)})
    assert book.symbols == ["AAA"]


# --------------------------------------------------------------- execution --


def test_orders_execute_the_next_day_never_the_same_day():
    closes = [100.0] * 5 + [200.0] * (N - 5)
    a = acct(weights={"AAA": 1.0}, invested=1.0)
    run(a, book_of({"AAA": frame(close=closes)}), upto=day(2))
    assert a["curve"][0][1] == a["start_cash"]  # day 0: only decided, nothing owned
    (trade,) = a["trades"]
    assert trade["date"] == day(1) and trade["price"] == 100.0  # filled at day 1's close, not day 0's


def test_no_lookahead_fill_uses_the_execution_days_price_not_the_signal_days():
    closes = [100.0, 150.0] + [150.0] * (N - 2)
    a = acct(weights={"AAA": 1.0}, invested=1.0)
    run(a, book_of({"AAA": frame(close=closes)}), upto=day(1))
    assert a["trades"][0]["price"] == 150.0


def test_commission_is_charged_on_traded_notional(monkeypatch):
    monkeypatch.setattr(paper, "COMMISSION_BPS", 10.0)
    a = acct(weights={"AAA": 1.0}, invested=1.0)
    run(a, book_of({"AAA": frame()}), upto=day(2))
    assert a["cost_paid"] == pytest.approx(a["trades"][0]["shares"] * 100 * 0.001, rel=1e-6)
    assert a["curve"][-1][1] == pytest.approx(100_000 - a["cost_paid"], abs=0.01)
    assert a["cash"] >= -1e-6


def test_static_hold_buys_once_and_never_trades_again():
    a = acct(weights={"AAA": 0.5, "BBB": 0.5}, invested=0.8)
    run(a, book_of({"AAA": frame(buy={10, 20}), "BBB": frame(sell={30})}))
    assert a["trade_count"] == 2  # one buy per symbol, on day 1
    assert {t["date"] for t in a["trades"]} == {day(1)}


def test_cash_strategy_stays_flat():
    a = acct("cash", weights={})
    run(a, book_of({"AAA": frame(buy={5})}))
    assert a["trade_count"] == 0 and a["curve"][-1][1] == a["start_cash"]


# ------------------------------------------------------------------ sizing --


def test_buy_signal_tilts_weight_up_and_sell_tilts_it_down():
    a = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.6, risk_level="moderate")
    book = book_of({"AAA": frame(buy=set(range(3, N))), "BBB": frame(sell=set(range(3, N)))})
    run(a, book)
    w = paper.current_weights(a, book, day(N - 1))
    assert w["AAA"] == pytest.approx(0.6 * 0.5 * 1.5, abs=0.02)
    assert w["BBB"] == pytest.approx(0.6 * 0.5 * 0.5, abs=0.02)


def test_sell_signals_raise_cash_and_buy_signals_deploy_it():
    neutral = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.6, risk_level="moderate")
    riskoff = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.6, risk_level="moderate")
    riskon = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.6, risk_level="moderate")
    hold = lambda: frame()
    run(neutral, book_of({"AAA": hold(), "BBB": hold()}))
    b_off = book_of({"AAA": hold(), "BBB": frame(sell=set(range(3, N)))})
    run(riskoff, b_off)
    b_on = book_of({"AAA": hold(), "BBB": frame(buy=set(range(3, N)))})
    run(riskon, b_on)
    cash = lambda a, b: a["cash"] / paper.equity(a, b, day(N - 1))
    assert cash(neutral, b_off) == pytest.approx(0.40, abs=0.01)
    assert cash(riskoff, b_off) > 0.50 > 0.40 > cash(riskon, b_on)  # 55% cash risk-off, 32.5% risk-on


def test_total_target_never_exceeds_100_percent():
    a = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.95, risk_level="aggressive")
    book = book_of({"AAA": frame(buy=set(range(0, N))), "BBB": frame(buy=set(range(0, N)))})
    run(a, book)
    assert sum(paper.current_weights(a, book, day(N - 1)).values()) <= 1.0 + 1e-9
    assert a["cash"] >= -1e-6


def test_position_cap_binds_but_never_cuts_below_the_strategic_weight():
    book = book_of({"AAA": frame(buy=set(range(0, N))), "BBB": frame(), "CCC": frame()})
    tilted = acct("engine_tilt", weights={"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3}, invested=0.95, risk_level="conservative")
    run(tilted, book)
    assert paper.current_weights(tilted, book, day(N - 1))["AAA"] <= 0.35 + 0.01  # conservative cap
    one = acct("engine_tilt", weights={"AAA": 1.0}, invested=0.95, risk_level="aggressive")
    run(one, book_of({"AAA": frame()}))
    assert paper.current_weights(one, book_of({"AAA": frame()}), day(N - 1))["AAA"] == pytest.approx(0.95, abs=0.01)


def test_cash_is_never_overspent_even_with_costs_and_heavy_rebalancing(monkeypatch):
    monkeypatch.setattr(paper, "COMMISSION_BPS", 50.0)
    closes = [100 + 15 * (i % 2) for i in range(N)]  # violent day-to-day swings
    a = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.95, risk_level="aggressive")
    book = book_of({"AAA": frame(close=closes, buy=set(range(0, N, 3))), "BBB": frame(close=closes[::-1], sell=set(range(1, N, 4)))})
    for _ in range(1):
        run(a, book)
    assert a["cash"] >= -1e-6 and all(v > 0 for v in a["positions"].values())


# ------------------------------------------------------------- rebalancing --


def test_small_price_drift_alone_does_not_trigger_trades_inside_the_window():
    closes = [100.0] * 10 + [100.0 + 2 * i for i in range(10)] + [120.0] * (N - 20)  # AAA drifts up vs BBB
    a = acct("static_rebalanced", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.8, risk_level="moderate")
    run(a, book_of({"AAA": frame(close=closes), "BBB": frame()}), upto=day(15))
    assert {t["date"] for t in a["trades"]} == {day(1)}  # only the initial build


def test_drift_beyond_the_band_rebalances_once_the_window_has_elapsed():
    closes = [100.0] * 5 + [300.0] * (N - 5)  # AAA triples: weights drift far past the band
    a = acct("static_rebalanced", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.8, risk_level="moderate")
    run(a, book_of({"AAA": frame(close=closes), "BBB": frame()}))
    later = [t for t in a["trades"] if t["date"] > day(1)]
    assert later and min(t["date"] for t in later) >= day(paper.REBALANCE_DAYS)  # not before the monthly check


def test_a_signal_change_trades_immediately_without_waiting_for_the_window():
    a = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.6, risk_level="moderate")
    run(a, book_of({"AAA": frame(buy={10}), "BBB": frame()}), upto=day(13))
    assert any(t["date"] == day(11) and t["symbol"] == "AAA" and t["side"] == "BUY" for t in a["trades"])  # decided day 10, filled day 11


def test_halted_symbol_cannot_be_traded_and_keeps_its_last_price_for_valuation():
    gap = frame(close=[100.0] * N).drop(DATES[1])
    a = acct("static_hold", weights={"AAA": 0.5, "BBB": 0.5}, invested=1.0)
    book = book_of({"AAA": gap, "BBB": frame()})
    run(a, book, upto=day(2))
    assert {t["symbol"] for t in a["trades"] if t["date"] == day(1)} == {"BBB"}  # AAA didn't print on day 1
    assert paper.equity(a, book, day(1)) > 0


# ----------------------------------------------------------- replay logic --


def test_advance_is_idempotent():
    a, book = acct("engine_tilt", weights={"AAA": 1.0}, invested=0.8, risk_level="moderate"), book_of({"AAA": frame(buy={10}, sell={30})})
    assert run(a, book) == N
    snapshot = repr(a)
    assert run(a, book) == 0
    assert repr(a) == snapshot


def test_incremental_daily_runs_equal_one_full_replay():
    frames = {"AAA": frame(close=[100 + (i % 7) for i in range(N)], buy={12, 40}, sell={25}), "BBB": frame(close=[50 + (i % 5) for i in range(N)], buy={30})}
    book = book_of(frames)
    full = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.8, risk_level="moderate")
    run(full, book)
    inc = acct("engine_tilt", weights={"AAA": 0.5, "BBB": 0.5}, invested=0.8, risk_level="moderate")
    for cut in (15, 16, 40, 41, 60, N - 1):
        run(inc, book, upto=day(cut))
    assert inc["curve"] == full["curve"] and inc["trades"] == full["trades"] and inc["cash"] == pytest.approx(full["cash"])


def test_days_are_tagged_backtest_before_live_from_and_live_after():
    a = acct()
    run(a, book_of({"AAA": frame()}), live_from=day(10))
    modes = [p[2] for p in a["curve"]]
    assert modes[:10] == ["backtest"] * 10 and set(modes[10:]) == {"live"}


def test_start_date_skips_earlier_history():
    a = acct()
    run(a, book_of({"AAA": frame()}), start=day(20))
    assert a["inception"] == day(20) and a["curve"][0][0] == day(20)


def test_unknown_strategy_is_rejected_and_weights_are_normalised():
    with pytest.raises(ValueError):
        acct("moon_shot")
    a = paper.new_account("x", "x", "control", "static_hold", {"A": 3, "B": 1, "C": 0, "D": -2}, invested=1.0)
    assert a["weights"] == {"A": 0.75, "B": 0.25}


# ------------------------------------------------------------ placebo --


def test_placebo_never_maps_a_symbol_to_itself_and_is_a_permutation():
    syms = ["A", "B", "C", "D", "E", "F"]
    for seed in ("acct1", "acct2", "ctl_random15"):
        mapped = [paper._placebo_symbol(seed, s, syms) for s in syms]
        assert all(m != s for m, s in zip(mapped, syms))
        assert sorted(mapped) == syms
    assert paper._placebo_symbol("s", "A", syms) == paper._placebo_symbol("s", "A", syms)  # deterministic


def test_placebo_account_trades_on_another_symbols_signal_history():
    # AAA carries the only BUY. With just two symbols the shift is forced, so a
    # random_tilt account holding only BBB must react to AAA's BUY, never BBB's own.
    book = book_of({"AAA": frame(buy={10}), "BBB": frame()})
    a = paper.new_account("p", "p", "control", "random_tilt", {"BBB": 1.0}, invested=0.6)
    run(a, book, upto=day(13))
    assert any(t["symbol"] == "BBB" and t["date"] == day(11) and "placebo BUY" in t["reason"] for t in a["trades"])


# ------------------------------------------------------------- metrics --


def test_max_drawdown():
    assert paper.max_drawdown([100, 120, 90, 110]) == pytest.approx(-0.25)
    assert paper.max_drawdown([100, 110, 121]) == 0.0
    assert paper.max_drawdown([]) == 0.0


def test_sharpe_is_zero_for_flat_and_positive_for_steady_gains():
    assert paper.sharpe([100] * 30) == 0.0
    steady = [100.0]
    for i in range(60):
        steady.append(steady[-1] * (1.001 + 0.0005 * (i % 2)))  # +0.10% / +0.15% alternating: tiny variance, real drift
    assert paper.sharpe(steady) > 5
    assert paper.sharpe(list(reversed(steady))) < -5


def test_summary_alpha_and_live_alpha_against_the_benchmark():
    book = book_of({"AAA": frame(close=[100.0 + i for i in range(N)])})
    prof = paper.new_account("p", "p", "profile", "static_hold", {"AAA": 1.0}, invested=1.0, benchmark_id="b")
    half = paper.new_account("b", "b", "benchmark", "static_hold", {"AAA": 1.0}, invested=0.5)
    live_from = day(40)
    for x in (prof, half):
        run(x, book, live_from=live_from)
    s = paper.summarize(prof, half)
    assert s["total_return"] > s["benchmark_return"] > 0
    assert s["alpha"] == pytest.approx(s["total_return"] - s["benchmark_return"])
    assert s["live_return"] is not None and s["live_alpha"] is not None and s["live_alpha"] > 0
    assert s["live_days"] == N - 40


def test_since_measures_return_profit_and_alpha_from_the_close_before_that_day():
    book = book_of({"AAA": frame(close=[100.0 + i for i in range(N)])})
    prof = paper.new_account("p", "p", "profile", "static_hold", {"AAA": 1.0}, invested=1.0, benchmark_id="b")
    half = paper.new_account("b", "b", "benchmark", "static_hold", {"AAA": 1.0}, invested=0.5)
    for x in (prof, half):
        run(x, book, live_from=day(40))
    d = day(30)
    base = [p for p in prof["curve"] if p[0] < d][-1][1]
    w = paper.since(prof, d)
    s = paper.summarize(w, paper.since(half, d))
    assert w["curve"][0][1] == base and w["curve"][1][0] == d and prof["curve"][0][0] < d  # original untouched
    assert s["start_equity"] == pytest.approx(base, abs=0.01)
    assert s["profit"] == pytest.approx(prof["curve"][-1][1] - base, abs=0.01)
    assert s["total_return"] == pytest.approx(prof["curve"][-1][1] / base - 1)
    assert s["trade_count"] == 0  # the static hold bought on day 1, before the window
    assert s["alpha"] == pytest.approx(s["total_return"] - s["benchmark_return"])


def test_summary_has_no_live_metrics_before_any_live_day():
    a = acct()
    run(a, book_of({"AAA": frame()}))
    s = paper.summarize(a)
    assert s["live_return"] is None and s["alpha"] is None and s["live_days"] == 0


# ------------------------------------------------------------ scorecard --


def test_scorecard_measures_forward_returns_from_the_next_close():
    # BUY on day 10; price jumps on day 11 -- entry is day 11's close so the jump must NOT count.
    closes = [100.0] * 11 + [110.0] * (N - 11)
    sc = paper.signal_scorecard(book_of({"AAA": frame(close=closes, buy={10})}), horizons=(1,))
    assert sc["signals"]["BUY"]["1"]["n"] == 1
    assert sc["signals"]["BUY"]["1"]["mean_return"] == pytest.approx(0.0)  # entered after the gap, day11->day12 flat
    assert sc["in_sample"] is True


def test_scorecard_edge_positive_when_buys_precede_gains_and_sells_precede_losses():
    closes = [100.0 * (1.0 + 0.0001 * i) for i in range(N)]
    buy_days, sell_days = {10, 20, 30}, {15, 25, 35}
    for d in buy_days:
        for j in range(d + 1, d + 6):
            closes[j] = closes[d] * 1.04 ** (j - d)  # strong run after each BUY
    for d in sell_days:
        for j in range(d + 1, d + 6):
            closes[j] = closes[d] * 0.96 ** (j - d)  # slide after each SELL
    sc = paper.signal_scorecard(book_of({"AAA": frame(close=closes, buy=buy_days, sell=sell_days)}), horizons=(3,))
    assert sc["edge_vs_average"]["BUY"]["3"] > 0 and sc["edge_vs_average"]["SELL"]["3"] > 0
    assert sc["signals"]["BUY"]["3"]["n"] == 3 and sc["signals"]["SELL"]["3"]["n"] == 3


def test_scorecard_ignores_signals_too_close_to_the_end_to_have_a_forward_return():
    sc = paper.signal_scorecard(book_of({"AAA": frame(buy={N - 2, N - 1})}), horizons=(5,))
    assert sc["signals"]["BUY"]["5"]["n"] == 0 and sc["signals"]["BUY"]["5"]["hit_rate"] is None
