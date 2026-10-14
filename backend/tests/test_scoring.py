"""Tests for scoring.py pure functions (S3 T12a).

All tests use hand-computed numbers in comments.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.scoring import (
    PRE_LEDGER_CUTOFF,
    baseline_calls,
    eligible,
    entry_date,
    metrics,
    outcome,
)


# ----------------------------------------------------------------------
# Fixtures / test data
# ----------------------------------------------------------------------


def toy_closes() -> dict[str, float]:
    """5 trading days: Mon-Fri, prices 100, 101, 102, 103, 104."""
    return {
        "2026-09-28": 100.0,  # Monday
        "2026-09-29": 101.0,  # Tuesday
        "2026-09-30": 102.0,  # Wednesday
        "2026-10-01": 103.0,  # Thursday
        "2026-10-02": 104.0,  # Friday
    }


def bench_closes() -> dict[str, float]:
    """Benchmark (SPY) prices for same dates: 400, 402, 404, 406, 408."""
    return {
        "2026-09-28": 400.0,
        "2026-09-29": 402.0,
        "2026-09-30": 404.0,
        "2026-10-01": 406.0,
        "2026-10-02": 408.0,
    }


def weekend_closes() -> dict[str, float]:
    """Friday -> Monday entry test: Fri 25th, Mon 28th, Tue 29th, Wed 30th."""
    return {
        "2026-09-25": 100.0,  # Friday
        "2026-09-28": 101.0,  # Monday
        "2026-09-29": 102.0,  # Tuesday
        "2026-09-30": 103.0,  # Wednesday
    }


def weekend_bench() -> dict[str, float]:
    return {
        "2026-09-25": 400.0,
        "2026-09-28": 402.0,
        "2026-09-29": 404.0,
        "2026-09-30": 406.0,
    }


# ----------------------------------------------------------------------
# entry_date tests
# ----------------------------------------------------------------------


def test_entry_date_basic() -> None:
    """Next trading day after call date."""
    closes = toy_closes()
    assert entry_date("2026-09-28", closes) == "2026-09-29"  # Mon -> Tue
    assert entry_date("2026-09-29", closes) == "2026-09-30"  # Tue -> Wed
    assert entry_date("2026-10-02", closes) is None  # Fri -> no next day


def test_entry_date_weekend() -> None:
    """Friday call -> Monday entry."""
    closes = weekend_closes()
    assert entry_date("2026-09-25", closes) == "2026-09-28"  # Fri -> Mon


def test_entry_date_missing_symbol() -> None:
    """Date not in closes returns None."""
    closes = toy_closes()
    assert entry_date("2026-09-27", closes) == "2026-09-28"  # Sun -> Mon (next trading day)
    assert entry_date("2026-10-05", closes) is None  # beyond data


# ----------------------------------------------------------------------
# eligible tests (acceptance test from spec)
# ----------------------------------------------------------------------


def test_eligible_recorded_after_market_open_not_eligible() -> None:
    """A call recorded after 09:30 ET on its entry day is NOT eligible."""
    closes = toy_closes()
    # Call date Mon 28th, entry is Tue 29th. Market open Tue 09:30 ET = 13:30 UTC.
    # Recorded at 14:00 UTC on Tue (after market open) -> not eligible.
    recorded_at = "2026-09-29T14:00:00+00:00"
    call_date = "2026-09-28"
    assert eligible(recorded_at, call_date, closes) is False


def test_eligible_recorded_evening_before_eligible() -> None:
    """A call recorded the evening before entry day IS eligible."""
    closes = toy_closes()
    # Call date Mon 28th, entry is Tue 29th. Recorded Mon evening (20:00 ET = 00:00 UTC Tue).
    # 00:00 UTC Tue < 13:30 UTC Tue (market open) -> eligible.
    recorded_at = "2026-09-29T00:00:00+00:00"
    call_date = "2026-09-28"
    assert eligible(recorded_at, call_date, closes) is True


def test_eligible_before_pre_ledger_cutoff_not_eligible() -> None:
    """A call before PRE_LEDGER_CUTOFF is not eligible."""
    closes = toy_closes()
    # PRE_LEDGER_CUTOFF = 2026-09-27T00:00:00+00:00
    # Recorded at 2026-09-26 (before cutoff) -> not eligible even if before market open.
    recorded_at = "2026-09-26T12:00:00+00:00"
    call_date = "2026-09-25"
    assert eligible(recorded_at, call_date, closes) is False


def test_eligible_at_cutoff_is_eligible() -> None:
    """A call at exactly PRE_LEDGER_CUTOFF is eligible (>= cutoff)."""
    closes = toy_closes()
    recorded_at = "2026-09-27T00:00:00+00:00"  # exactly at cutoff
    call_date = "2026-09-26"
    assert eligible(recorded_at, call_date, closes) is True


def test_eligible_no_entry_date_false() -> None:
    """If no entry date (call date beyond data), not eligible."""
    closes = toy_closes()
    recorded_at = "2026-10-01T12:00:00+00:00"
    call_date = "2026-10-05"  # beyond data
    assert eligible(recorded_at, call_date, closes) is False


def test_eligible_weekend_entry() -> None:
    """Friday call -> Monday entry, recorded Sunday evening is eligible."""
    closes = weekend_closes()
    # Call Fri 25th, entry Mon 28th. Market open Mon 09:30 ET = 13:30 UTC.
    # Recorded Sun 27th 20:00 ET = Mon 00:00 UTC -> before market open.
    recorded_at = "2026-09-28T00:00:00+00:00"
    call_date = "2026-09-25"
    assert eligible(recorded_at, call_date, closes) is True


# ----------------------------------------------------------------------
# outcome tests
# ----------------------------------------------------------------------


def test_outcome_buy_5day_toy() -> None:
    """BUY score on 5-day toy series.

    Call date: 2026-09-28 (Mon), entry: 2026-09-29 (Tue) @ 101, exit: 2026-10-02 (Fri) @ 104
    horizon=3 trading days (Tue->Wed->Thu->Fri = 3 steps)
    forward_return = 104/101 - 1 = 0.02970297...
    benchmark: entry 402, exit 408 -> bench_return = 408/402 - 1 = 0.014925...
    excess = 0.029703 - 0.014925 = 0.014778
    score = +1 * excess = 0.014778
    """
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "BUY",
        "confidence": 80,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    closes = toy_closes()
    bench = bench_closes()
    oc = outcome(call, horizon=3, closes=closes, bench_closes=bench)

    assert oc is not None
    assert oc["entry_date"] == "2026-09-29"
    assert oc["exit_date"] == "2026-10-02"
    assert oc["entry_price"] == 101.0
    assert oc["exit_price"] == 104.0
    # 104/101 - 1 = 3/101 = 0.02970297...
    assert oc["forward_return"] == round(3 / 101, 6)
    # 408/402 - 1 = 6/402 = 0.01492537...
    assert oc["benchmark_return"] == round(6 / 402, 6)
    excess = 3 / 101 - 6 / 402
    assert oc["excess_return"] == round(excess, 6)
    assert oc["score"] == round(excess, 6)  # BUY -> +1 * excess


def test_outcome_sell_5day_toy() -> None:
    """SELL score on 5-day toy series.

    Same prices, SELL decision -> score = -1 * excess_return.
    """
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "SELL",
        "confidence": 70,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    closes = toy_closes()
    bench = bench_closes()
    oc = outcome(call, horizon=3, closes=closes, bench_closes=bench)

    assert oc is not None
    excess = 3 / 101 - 6 / 402
    assert oc["score"] == round(-excess, 6)  # SELL -> -1 * excess


def test_outcome_hold_5day_toy() -> None:
    """HOLD score on 5-day toy series.

    HOLD decision -> score = 0 * excess_return = 0.
    """
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "HOLD",
        "confidence": 50,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    closes = toy_closes()
    bench = bench_closes()
    oc = outcome(call, horizon=3, closes=closes, bench_closes=bench)

    assert oc is not None
    assert oc["score"] == 0.0


def test_outcome_window_not_closed_returns_none() -> None:
    """If horizon extends beyond available data, returns None."""
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "BUY",
        "confidence": 80,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    closes = toy_closes()  # only 5 days
    bench = bench_closes()
    # horizon=10 needs 10 trading days after entry, but we only have 3
    oc = outcome(call, horizon=10, closes=closes, bench_closes=bench)
    assert oc is None


def test_outcome_missing_benchmark_date_returns_none() -> None:
    """If benchmark missing entry or exit date, returns None."""
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "BUY",
        "confidence": 80,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    closes = toy_closes()
    # Benchmark missing exit date (2026-10-02)
    bench = {
        "2026-09-28": 400.0,
        "2026-09-29": 402.0,
        "2026-09-30": 404.0,
        "2026-10-01": 406.0,
        # missing 2026-10-02
    }
    oc = outcome(call, horizon=3, closes=closes, bench_closes=bench)
    assert oc is None


def test_outcome_missing_symbol_date_returns_none() -> None:
    """If symbol missing entry or exit date, returns None."""
    call = {
        "call_id": "2026-09-28:AAPL",
        "ticker": "AAPL",
        "decision": "BUY",
        "confidence": 80,
        "recorded_at": "2026-09-28T20:00:00+00:00",
    }
    # Symbol missing entry date (2026-09-29)
    closes = {
        "2026-09-28": 100.0,
        "2026-09-30": 102.0,
        "2026-10-01": 103.0,
        "2026-10-02": 104.0,
    }
    bench = bench_closes()
    oc = outcome(call, horizon=3, closes=closes, bench_closes=bench)
    assert oc is None


def test_outcome_weekend_entry() -> None:
    """Friday call -> Monday entry, horizon=1 -> Tuesday exit."""
    call = {
        "call_id": "2026-09-25:AAPL",
        "ticker": "AAPL",
        "decision": "BUY",
        "confidence": 80,
        "recorded_at": "2026-09-25T20:00:00+00:00",
    }
    closes = weekend_closes()
    bench = weekend_bench()
    # Entry Mon 28th @ 101, exit Tue 29th @ 102 (horizon=1)
    # forward = 102/101 - 1 = 1/101
    # bench: 404/402 - 1 = 2/402
    oc = outcome(call, horizon=1, closes=closes, bench_closes=bench)

    assert oc is not None
    assert oc["entry_date"] == "2026-09-28"
    assert oc["exit_date"] == "2026-09-29"
    assert oc["entry_price"] == 101.0
    assert oc["exit_price"] == 102.0
    excess = 1 / 101 - 2 / 402
    assert oc["excess_return"] == round(excess, 6)
    assert oc["score"] == round(excess, 6)


# ----------------------------------------------------------------------
# metrics tests
# ----------------------------------------------------------------------


def test_metrics_too_few_calls() -> None:
    """Fewer directional calls than min_n -> every metric None, with the reason."""
    scored = [
        {"decision": "BUY", "confidence": 80, "excess_return": 0.01, "forward_return": 0.02, "horizon": 5},
        {"decision": "SELL", "confidence": 70, "excess_return": -0.01, "forward_return": -0.02, "horizon": 5},
    ]
    result = metrics(scored, min_n=10)
    assert result["count"] == 2 and result["directional_count"] == 2
    for k in ("hit_rate", "mean_signed_excess", "rank_ic", "rank_ic_t", "brier", "calibration"):
        assert result[k] is None
    assert result["reason"] == "too few BUY/SELL calls (2)"


def test_metrics_rank_ic_and_brier_10_calls() -> None:
    """Hand example; expected values recomputed independently (pandas rank + Pearson)."""
    rows = [("BUY", 90, 0.05), ("BUY", 80, 0.04), ("BUY", 80, -0.01), ("SELL", 70, -0.02), ("SELL", 60, 0.01),
            ("BUY", 40, -0.02), ("BUY", 30, 0.03), ("SELL", 20, -0.03), ("SELL", 10, 0.02), ("BUY", 50, 0.0)]
    scored = [{"decision": d, "confidence": c, "excess_return": e, "forward_return": e, "horizon": 5} for d, c, e in rows]
    scored.append({"decision": "HOLD", "confidence": 50, "excess_return": 0.005, "forward_return": 0.0, "horizon": 5})
    result = metrics(scored, min_n=10)
    assert result["count"] == 11 and result["directional_count"] == 10
    assert result["hit_rate"] is None  # pre-registered: needs 30 calls
    assert result["by_decision"]["BUY"] == {"n": 6, "scorable": 6, "hit_rate": None}
    assert result["mean_signed_excess"] == 0.011  # SELL excess sign-flipped, HOLD left out
    assert result["rank_ic"] == pytest.approx(0.5305, abs=1e-4)  # signed confidence vs excess, BUY/SELL only
    assert result["rank_ic_t"] == pytest.approx(1.77, abs=1e-2)
    assert result["brier"] == pytest.approx(0.2673, abs=1e-4)  # confidence vs pre-registered right, HOLD judged at 5d
    assert sum(b["n"] for b in result["calibration"]) == 11

def test_metrics_calibration_bins_sum_to_n() -> None:
    """Calibration bins sum to n."""
    scored = [
        {"decision": "BUY", "confidence": 95, "score": 0.05, "excess_return": 0.05, "forward_return": 0.06},
        {"decision": "BUY", "confidence": 85, "score": 0.04, "excess_return": 0.04, "forward_return": 0.05},
        {"decision": "BUY", "confidence": 75, "score": 0.03, "excess_return": 0.03, "forward_return": 0.04},
        {"decision": "BUY", "confidence": 65, "score": 0.02, "excess_return": 0.02, "forward_return": 0.03},
        {"decision": "BUY", "confidence": 55, "score": 0.01, "excess_return": 0.01, "forward_return": 0.02},
        {"decision": "BUY", "confidence": 45, "score": -0.01, "excess_return": -0.01, "forward_return": 0.00},
        {"decision": "BUY", "confidence": 35, "score": -0.02, "excess_return": -0.02, "forward_return": -0.01},
        {"decision": "BUY", "confidence": 25, "score": -0.03, "excess_return": -0.03, "forward_return": -0.02},
        {"decision": "BUY", "confidence": 15, "score": -0.04, "excess_return": -0.04, "forward_return": -0.03},
        {"decision": "BUY", "confidence": 5, "score": -0.05, "excess_return": -0.05, "forward_return": -0.04},
    ]
    result = metrics(scored, min_n=10)

    calibration = result["calibration"]
    assert calibration is not None
    total_n = sum(bin_["n"] for bin_ in calibration)
    assert total_n == 10  # sum of bin counts equals total n

    # Check bin ranges
    assert calibration[0]["confidence_range"] == "0-20"
    assert calibration[1]["confidence_range"] == "20-40"
    assert calibration[2]["confidence_range"] == "40-60"
    assert calibration[3]["confidence_range"] == "60-80"
    assert calibration[4]["confidence_range"] == "80-100"

    # Each call falls in exactly one bin
    for bin_ in calibration:
        if bin_["n"] > 0:
            assert bin_["mean_confidence"] is not None
            assert bin_["observed_hit_rate"] is not None


def test_metrics_calibration_empty_bins() -> None:
    """Empty bins have None mean_confidence and observed_hit_rate."""
    scored = [
        {"decision": "BUY", "confidence": 95, "score": 0.05, "excess_return": 0.05, "forward_return": 0.06},
        {"decision": "BUY", "confidence": 85, "score": 0.04, "excess_return": 0.04, "forward_return": 0.05},
    ] * 5  # 10 calls, all in 80-100 bin
    result = metrics(scored, min_n=10)

    calibration = result["calibration"]
    # bins 0-3 should be empty
    for i in range(4):
        assert calibration[i]["n"] == 0
        assert calibration[i]["mean_confidence"] is None
        assert calibration[i]["observed_hit_rate"] is None
    # bin 4 (80-100) has all 10
    assert calibration[4]["n"] == 10


# ----------------------------------------------------------------------
# baseline_calls tests
# ----------------------------------------------------------------------


def test_baseline_calls_skips_missing_signals_keeps_order() -> None:
    """baseline_calls skips missing signals and keeps the order."""
    scored_call_dates = [
        ("AAPL", "2026-09-28"),
        ("MSFT", "2026-09-28"),
        ("AAPL", "2026-09-29"),
        ("GOOG", "2026-09-28"),  # no signal for GOOG
        ("MSFT", "2026-09-29"),
    ]
    engine_signals = {
        "AAPL": {
            "2026-09-28": ("BUY", 80.0),
            "2026-09-29": ("SELL", 70.0),
        },
        "MSFT": {
            "2026-09-28": ("HOLD", 50.0),
            "2026-09-29": ("BUY", 90.0),
        },
        # GOOG has no signals
    }
    result = baseline_calls(scored_call_dates, engine_signals)

    # Should have 4 calls (GOOG skipped)
    assert len(result) == 4

    # Order preserved
    assert result[0]["call_id"] == "2026-09-28:AAPL"
    assert result[0]["decision"] == "BUY"
    assert result[0]["confidence"] == 80.0

    assert result[1]["call_id"] == "2026-09-28:MSFT"
    assert result[1]["decision"] == "HOLD"
    assert result[1]["confidence"] == 50.0

    assert result[2]["call_id"] == "2026-09-29:AAPL"
    assert result[2]["decision"] == "SELL"
    assert result[2]["confidence"] == 70.0

    assert result[3]["call_id"] == "2026-09-29:MSFT"
    assert result[3]["decision"] == "BUY"
    assert result[3]["confidence"] == 90.0


def test_baseline_calls_empty_signals() -> None:
    """Empty engine_signals returns empty list."""
    scored_call_dates = [("AAPL", "2026-09-28")]
    engine_signals = {}
    result = baseline_calls(scored_call_dates, engine_signals)
    assert result == []


def test_baseline_calls_all_missing() -> None:
    """All dates missing signals returns empty list."""
    scored_call_dates = [("AAPL", "2026-09-28"), ("MSFT", "2026-09-28")]
    engine_signals = {"GOOG": {"2026-09-28": ("BUY", 80.0)}}
    result = baseline_calls(scored_call_dates, engine_signals)
    assert result == []


# ----------------------------------------------------------------------
# PRE_LEDGER_CUTOFF constant test
# ----------------------------------------------------------------------


def test_pre_ledger_cutoff_is_correct() -> None:
    """PRE_LEDGER_CUTOFF is 2026-09-27 00:00:00 UTC."""
    assert PRE_LEDGER_CUTOFF == datetime(2026, 9, 27, tzinfo=timezone.utc)
    assert PRE_LEDGER_CUTOFF.tzinfo == timezone.utc