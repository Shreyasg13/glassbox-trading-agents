"""Forward-only scoring: pure functions for call evaluation (S3 T12a).

Plain data in, plain data out. No PriceBook, no DB, no network.
All logic is testable in isolation.
"""
from __future__ import annotations

import math
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

PRE_LEDGER_CUTOFF = datetime(2026, 9, 27, tzinfo=timezone.utc)


def _sign(decision: str) -> int:
    """+1 for BUY, -1 for SELL, 0 for HOLD."""
    if decision == "BUY":
        return 1
    if decision == "SELL":
        return -1
    return 0


def _iso_to_dt(ts: str) -> datetime:
    """Parse ISO timestamp (with or without microseconds, always UTC)."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _market_open_utc(trading_date: str) -> datetime:
    """Market open (09:30 ET) as aware UTC datetime for a given trading date."""
    ET = ZoneInfo("America/New_York")
    dt_et = datetime.strptime(trading_date, "%Y-%m-%d").replace(hour=9, minute=30, tzinfo=ET)
    return dt_et.astimezone(timezone.utc)


def _next_trading_day(dates: list[str], from_date: str) -> str | None:
    """The next trading day's date after from_date (exclusive)."""
    i = bisect_right(dates, from_date)
    if i < len(dates):
        return dates[i]
    return None


def entry_date(call_date: str, closes: dict[str, float]) -> str | None:
    """First trading date strictly after call_date.

    Args:
        call_date: Date string in YYYY-MM-DD format.
        closes: Dict of date -> close price for ONE symbol; keys are sorted trading dates.

    Returns:
        The next trading date after call_date, or None if no such date exists.
    """
    dates = list(closes.keys())
    return _next_trading_day(dates, call_date)


def eligible(recorded_at: str, call_date: str, closes: dict[str, float]) -> bool:
    """Check if a call is eligible for scoring.

    A call is eligible if:
    1. recorded_at is strictly before 09:30 America/New_York on entry_date
    2. recorded_at >= PRE_LEDGER_CUTOFF
    3. An entry_date exists (next trading day after call_date)

    Args:
        recorded_at: ISO timestamp string (UTC).
        call_date: Date string in YYYY-MM-DD format (from call_id).
        closes: Dict of date -> close price for the symbol; keys are sorted trading dates.

    Returns:
        True if eligible, False otherwise.
    """
    dates = list(closes.keys())
    entry_dt = _next_trading_day(dates, call_date)
    if entry_dt is None:
        return False

    market_open = _market_open_utc(entry_dt)
    recorded_dt = _iso_to_dt(recorded_at)

    if recorded_dt < PRE_LEDGER_CUTOFF:
        return False

    return recorded_dt < market_open


def outcome(
    call: dict[str, Any],
    horizon: int,
    closes: dict[str, float],
    bench_closes: dict[str, float],
) -> dict[str, Any] | None:
    """Compute the outcome for a call at the given horizon.

    Args:
        call: Dict with keys call_id, ticker, decision, confidence, recorded_at.
              call_id format: "YYYY-MM-DD:SYMBOL".
        horizon: Number of trading days forward.
        closes: Dict of date -> close price for the symbol; keys are sorted trading dates.
        bench_closes: Dict of date -> close price for benchmark (SPY); keys are sorted trading dates.

    Returns:
        Dict with entry_date, exit_date, entry_price, exit_price, forward_return,
        benchmark_return, excess_return, score. None if window not closed or data missing.
    """
    call_id = call.get("call_id", "")
    decision = call.get("decision", "HOLD")

    try:
        call_date_str = call_id.split(":")[0]
    except (IndexError, AttributeError):
        return None

    dates = list(closes.keys())
    bench_dates = list(bench_closes.keys())

    entry_dt = _next_trading_day(dates, call_date_str)
    if entry_dt is None:
        return None

    # Find index of entry date
    try:
        entry_idx = dates.index(entry_dt)
    except ValueError:
        return None

    exit_idx = entry_idx + horizon
    if exit_idx >= len(dates):
        return None  # window not closed

    exit_dt = dates[exit_idx]

    # Forward return for the symbol
    entry_price = closes[entry_dt]
    exit_price = closes[exit_dt]
    if entry_price <= 0 or exit_price <= 0:
        return None
    forward_return = exit_price / entry_price - 1.0

    # Benchmark return over SAME entry/exit dates
    if entry_dt not in bench_closes or exit_dt not in bench_closes:
        return None
    bench_entry = bench_closes[entry_dt]
    bench_exit = bench_closes[exit_dt]
    if bench_entry <= 0 or bench_exit <= 0:
        return None
    benchmark_return = bench_exit / bench_entry - 1.0

    excess_return = forward_return - benchmark_return
    score = _sign(decision) * excess_return

    return {
        "entry_date": entry_dt,
        "exit_date": exit_dt,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "forward_return": round(forward_return, 6),
        "benchmark_return": round(benchmark_return, 6),
        "excess_return": round(excess_return, 6),
        "score": round(score, 6),
    }


def _spearman_rho(x: list[float], y: list[float]) -> float | None:
    """Spearman rank correlation coefficient. Returns None if n < 2 or constant."""
    n = len(x)
    if n < 2:
        return None

    def ranks(vals: list[float]) -> list[float]:
        # Average ranks for ties
        sorted_pairs = sorted((v, i) for i, v in enumerate(vals))
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n and sorted_pairs[j][0] == sorted_pairs[i][0]:
                j += 1
            avg_rank = (i + 1 + j) / 2.0  # 1-indexed
            for k in range(i, j):
                r[sorted_pairs[k][1]] = avg_rank
            i = j
        return r

    rx = ranks(x)
    ry = ranks(y)

    # Pearson on ranks
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n
    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((rx[i] - mean_rx) ** 2 for i in range(n))
    var_y = sum((ry[i] - mean_ry) ** 2 for i in range(n))

    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def _spearman_t_stat(rho: float | None, n: int) -> float | None:
    """t-statistic for Spearman rho under H0: rho=0. Returns None if n <= 2."""
    if n <= 2 or rho is None or abs(rho) >= 1.0:
        return None
    return rho * math.sqrt((n - 2) / (1 - rho * rho))


def metrics(scored: list[dict[str, Any]], min_n: int = 10) -> dict[str, Any]:
    """Compute aggregate metrics for a list of scored calls.

    Args:
        scored: List of dicts with keys: decision, confidence (0-100), score,
                excess_return, forward_return.
        min_n: Minimum sample size for metrics to be computed.

    Returns:
        Dict with count, hit_rate, mean_excess_return, rank_ic, rank_ic_t,
        brier, calibration (5 bins of width 20).
        If n < min_n, all metrics are None and "reason": "too few calls".
    """
    n = len(scored)
    if n < min_n:
        return {
            "count": n,
            "hit_rate": None,
            "mean_excess_return": None,
            "rank_ic": None,
            "rank_ic_t": None,
            "brier": None,
            "calibration": None,
            "reason": "too few calls",
        }

    # Hit rate: share where score > 0
    hits = sum(1 for c in scored if c["score"] > 0)
    hit_rate = hits / n

    # Mean excess return
    mean_excess = sum(c["excess_return"] for c in scored) / n

    # Rank IC: Spearman of signed_confidence vs forward_return
    signed_confidences = []
    forward_returns = []
    for c in scored:
        conf = c["confidence"]
        decision = c["decision"]
        if decision == "BUY":
            signed = conf
        elif decision == "SELL":
            signed = -conf
        else:
            signed = 0.0
        signed_confidences.append(signed)
        forward_returns.append(c["forward_return"])

    rho = _spearman_rho(signed_confidences, forward_returns)
    t_stat = _spearman_t_stat(rho, n) if rho is not None else None

    # Brier score: confidence/100 vs "call was right" (score > 0)
    brier_sum = 0.0
    for c in scored:
        p = c["confidence"] / 100.0
        o = 1.0 if c["score"] > 0 else 0.0
        brier_sum += (p - o) ** 2
    brier = brier_sum / n

    # Calibration: 5 bins of width 20
    calibration = []
    for bin_idx in range(5):
        lo = bin_idx * 20
        hi = (bin_idx + 1) * 20
        bin_calls = [c for c in scored if lo <= c["confidence"] < hi]
        if bin_calls:
            mean_conf = sum(c["confidence"] for c in bin_calls) / len(bin_calls)
            obs_hit = sum(1 for c in bin_calls if c["score"] > 0) / len(bin_calls)
            calibration.append({
                "bin": bin_idx,
                "confidence_range": f"{lo}-{hi}",
                "n": len(bin_calls),
                "mean_confidence": round(mean_conf, 1),
                "observed_hit_rate": round(obs_hit, 3),
            })
        else:
            calibration.append({
                "bin": bin_idx,
                "confidence_range": f"{lo}-{hi}",
                "n": 0,
                "mean_confidence": None,
                "observed_hit_rate": None,
            })

    return {
        "count": n,
        "hit_rate": round(hit_rate, 3),
        "mean_excess_return": round(mean_excess, 6),
        "rank_ic": round(rho, 4) if rho is not None else None,
        "rank_ic_t": round(t_stat, 2) if t_stat is not None else None,
        "brier": round(brier, 4),
        "calibration": calibration,
        "reason": None,
    }


def baseline_calls(
    scored_call_dates: list[tuple[str, str]],
    engine_signals: dict[str, dict[str, tuple[str, float]]],
) -> list[dict[str, Any]]:
    """Build baseline calls from engine signals for the same dates as scored calls.

    Args:
        scored_call_dates: List of (symbol, call_date) tuples in order.
        engine_signals: Dict of symbol -> {call_date: (signal, confidence)}.
                        signal is "BUY"/"SELL"/"HOLD", confidence is 0-100.

    Returns:
        List of call dicts in same shape as committee calls: call_id, ticker, decision,
        confidence, recorded_at. Skips dates with no signal. Keeps input order.
    """
    result = []
    for symbol, call_date in scored_call_dates:
        symbol_signals = engine_signals.get(symbol, {})
        signal_data = symbol_signals.get(call_date)
        if signal_data is None:
            continue
        signal, confidence = signal_data
        call_id = f"{call_date}:{symbol}"
        result.append({
            "call_id": call_id,
            "ticker": symbol,
            "decision": signal,
            "confidence": confidence,
            "recorded_at": f"{call_date}T16:00:00+00:00",
        })
    return result