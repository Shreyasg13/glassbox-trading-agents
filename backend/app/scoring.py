"""Forward-only scoring: pure functions for call evaluation (S3 T12a).

Plain data in, plain data out. No PriceBook, no DB, no network.
All logic is testable in isolation.
"""
from __future__ import annotations

import json
import math
import random
from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import db
from .migrated_tables import call_outcomes_table, ledger_calls_table

PRE_LEDGER_CUTOFF = datetime(2026, 9, 27, tzinfo=timezone.utc)


def load_ledger_calls() -> list[dict[str, Any]]:
    """Load committee_decision rows from ledger.read, payload parsed.

    Returns:
        List of dicts with keys: call_id, ticker, decision, confidence, recorded_at.
        Only rows with call_type == "committee_decision" are returned.
    """
    with db.engine.connect() as conn:
        rows = conn.execute(
            db.select(ledger_calls_table).where(ledger_calls_table.c.call_type == "committee_decision").order_by(ledger_calls_table.c.seq)
        ).fetchall()

    out = []
    for row in rows:
        payload = json.loads(row.payload_json)
        # Only include rows that have the required decision/confidence fields
        decision = payload.get("decision")
        confidence = payload.get("confidence")
        if decision in ("BUY", "SELL", "HOLD") and confidence is not None:
            out.append({
                "call_id": row.call_id,
                "ticker": row.ticker,
                "decision": decision,
                # publish.py writes the vote share as 0-1; metrics() expects 0-100.
                "confidence": confidence * 100 if confidence <= 1 else confidence,
                "recorded_at": row.recorded_at,
                "p_beat_spy": payload.get("p_beat_spy"),
            })
    return out


def load_outcomes() -> list[dict[str, Any]]:
    """Load rows from call_outcomes with outcome_json parsed.

    Returns:
        List of dicts with keys: call_id, horizon, evaluated_at, outcome_json (parsed), score.
    """
    with db.engine.connect() as conn:
        rows = conn.execute(
            db.select(call_outcomes_table).order_by(call_outcomes_table.c.call_id, call_outcomes_table.c.horizon)
        ).fetchall()

    out = []
    for row in rows:
        out.append({
            "call_id": row.call_id,
            "horizon": row.horizon,
            "evaluated_at": row.evaluated_at,
            "outcome_json": json.loads(row.outcome_json),
            "score": row.score,
        })
    return out


# docs/s3/PREREGISTRATION.md, "Directional hit rate": fixed before any hit rate was computed with them.
HOLD_BAND = 0.01  # a HOLD is right if |5-day excess| <= 1.0%
MIN_PER_DECISION = 30  # a decision type's hit rate is shown only from 30 calls


def is_right(call: dict[str, Any]) -> bool | None:
    """Pre-registered rule: BUY right if excess > 0, SELL if excess < 0, HOLD if |excess| <= HOLD_BAND. The HOLD band
    was registered for the 5-day horizon only, so a HOLD at another horizon is not scored (None), never counted wrong."""
    d, x = call.get("decision"), call.get("excess_return")
    if x is None:
        return None
    if d == "BUY":
        return x > 0
    if d == "SELL":
        return x < 0
    if d == "HOLD" and call.get("horizon") == 5:
        return abs(x) <= HOLD_BAND
    return None


def scored_calls() -> list[dict[str, Any]]:
    """Ledger calls joined to their stored outcomes, one row per (call, horizon). Reads stored rows only."""
    outcomes_by_call: dict[str, list[dict[str, Any]]] = {}
    for oc in load_outcomes():
        outcomes_by_call.setdefault(oc["call_id"], []).append(oc)
    rows = []
    for call in load_ledger_calls():
        for oc in outcomes_by_call.get(call["call_id"], []):
            ojson = oc["outcome_json"]
            row = {
                "call_id": call["call_id"],
                "symbol": call["ticker"],
                "decision": call["decision"],
                "confidence": call["confidence"],
                "recorded_at": call["recorded_at"],
                "recorded_label": "recorded before outcome",
                "horizon": oc["horizon"],
                "entry_date": ojson.get("entry_date"),
                "exit_date": ojson.get("exit_date"),
                "forward_return": ojson.get("forward_return"),
                "benchmark_return": ojson.get("benchmark_return"),
                "excess_return": ojson.get("excess_return"),
                "score": oc["score"],
                "p_beat_spy": call.get("p_beat_spy"),
            }
            row["right"] = is_right(row)
            rows.append(row)
    return rows


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


def _calibration(rows: list[dict[str, Any]], rights: list[bool]) -> list[dict[str, Any]]:
    """5 bins of width 20: stated confidence vs the observed share of right calls."""
    out = []
    for k in range(5):
        lo, hi = k * 20, (k + 1) * 20
        idx = [i for i, c in enumerate(rows) if lo <= c["confidence"] < hi or (hi == 100 and c["confidence"] == 100)]
        out.append({
            "bin": k,
            "confidence_range": f"{lo}-{hi}",
            "n": len(idx),
            "mean_confidence": round(sum(rows[i]["confidence"] for i in idx) / len(idx), 1) if idx else None,
            "observed_hit_rate": round(sum(rights[i] for i in idx) / len(idx), 3) if idx else None,
        })
    return out


def metrics(scored: list[dict[str, Any]], min_n: int = 10) -> dict[str, Any]:
    """Aggregate metrics for scored calls (decision, confidence 0-100, excess_return, horizon).

    Right/wrong always uses the pre-registered rule (`is_right`), so a HOLD is never a silent miss:
    - by_decision: n and hit rate per BUY/SELL/HOLD, the hit rate only from MIN_PER_DECISION scorable calls.
    - hit_rate: BUY+SELL pooled, also only from MIN_PER_DECISION calls.
    - mean_signed_excess: mean of sign(call) * excess over BUY+SELL (what following the calls earned vs SPY).
    - rank_ic / rank_ic_t: Spearman of signed confidence vs excess return over BUY+SELL.
    - brier / calibration: confidence as P(call is right), over calls the rule can judge.
    Each is None when its own subset is smaller than its threshold; `reason` says why.
    """
    directional = [c for c in scored if c["decision"] in ("BUY", "SELL") and c.get("excess_return") is not None]
    judged = [c for c in scored if is_right(c) is not None]
    by_decision = {}
    for d in ("BUY", "SELL", "HOLD"):
        rows = [c for c in scored if c["decision"] == d]
        rs = [r for r in (is_right(c) for c in rows) if r is not None]
        by_decision[d] = {"n": len(rows), "scorable": len(rs), "hit_rate": round(sum(rs) / len(rs), 3) if len(rs) >= MIN_PER_DECISION else None}

    out: dict[str, Any] = {
        "count": len(scored),
        "directional_count": len(directional),
        "by_decision": by_decision,
        "hit_rate": None,
        "mean_signed_excess": None,
        "rank_ic": None,
        "rank_ic_t": None,
        "brier": None,
        "calibration": None,
        "reason": None,
        "p_beat_spy": prob_metrics(scored, min_n),
    }
    if len(directional) >= MIN_PER_DECISION:
        out["hit_rate"] = round(sum(is_right(c) for c in directional) / len(directional), 3)
    if len(directional) >= min_n:
        out["mean_signed_excess"] = round(sum(_sign(c["decision"]) * c["excess_return"] for c in directional) / len(directional), 6)
        signed = [_sign(c["decision"]) * c["confidence"] for c in directional]
        rho = _spearman_rho(signed, [c["excess_return"] for c in directional])
        t_stat = _spearman_t_stat(rho, len(directional)) if rho is not None else None
        out["rank_ic"] = round(rho, 4) if rho is not None else None
        out["rank_ic_t"] = round(t_stat, 2) if t_stat is not None else None
    if len(judged) >= min_n:
        rights = [bool(is_right(c)) for c in judged]
        out["brier"] = round(sum((c["confidence"] / 100.0 - r) ** 2 for c, r in zip(judged, rights)) / len(judged), 4)
        out["calibration"] = _calibration(judged, rights)
    if len(directional) < min_n:
        out["reason"] = f"too few BUY/SELL calls ({len(directional)})"
    return out


def _prob_rows(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for c in scored if c.get("p_beat_spy") is not None and c.get("symbol", c.get("ticker")) != "SPY"]


def shuffle_test(scored: list[dict[str, Any]], n_perm: int = 10000, seed: int = 20261012, min_n: int = 10) -> dict[str, Any]:
    """Does P(beat SPY) know anything? Shuffle the probabilities among calls with the SAME entry date (so a
    market-wide day moves every shuffled call alike) and count how often a shuffle scores a Brier at least as
    good as the real one. p_value = (1 + that count) / (1 + n_perm). Pre-registered in docs/s3/PREREGISTRATION.md.
    """
    rows = [c for c in _prob_rows(scored) if c.get("entry_date")]
    n = len(rows)
    if n < min_n:
        return {"count": n, "brier": None, "null_mean_brier": None, "p_value": None, "reason": "too few calls"}
    outcome = [1.0 if c["excess_return"] > 0 else 0.0 for c in rows]
    probs = [c["p_beat_spy"] / 100.0 for c in rows]
    by_date: dict[str, list[int]] = {}
    for i, c in enumerate(rows):
        by_date.setdefault(c["entry_date"], []).append(i)

    def brier(ps: list[float]) -> float:
        return sum((p - o) ** 2 for p, o in zip(ps, outcome)) / n

    observed = brier(probs)
    rnd = random.Random(seed)
    as_good, total = 0, 0.0
    perm = probs[:]
    for _ in range(n_perm):
        for idx in by_date.values():
            vals = [probs[i] for i in idx]
            rnd.shuffle(vals)
            for i, v in zip(idx, vals):
                perm[i] = v
        b = brier(perm)
        total += b
        as_good += b <= observed
    return {
        "count": n,
        "dates": len(by_date),
        "brier": round(observed, 4),
        "null_mean_brier": round(total / n_perm, 4),
        "p_value": round((1 + as_good) / (1 + n_perm), 4),
        "n_perm": n_perm,
        "reason": None,
    }


def prob_metrics(scored: list[dict[str, Any]], min_n: int = 10) -> dict[str, Any]:
    """Score the committee's P(beat SPY) on every call, HOLD included: outcome = excess_return > 0.

    Rows without p_beat_spy (calls recorded before the field existed) and SPY itself (excess is 0 by
    construction) are left out. Brier, rank IC of p vs excess return, and 5 calibration bins.
    """
    rows = _prob_rows(scored)
    n = len(rows)
    if n < min_n:
        return {"count": n, "brier": None, "base_rate": None, "rank_ic": None, "rank_ic_t": None, "calibration": None, "reason": "too few calls"}
    outcome = [1.0 if c["excess_return"] > 0 else 0.0 for c in rows]
    probs = [c["p_beat_spy"] / 100.0 for c in rows]
    rho = _spearman_rho(probs, [c["excess_return"] for c in rows])
    calibration = []
    for k in range(5):
        lo, hi = k * 0.2, (k + 1) * 0.2
        idx = [i for i, p in enumerate(probs) if lo <= p < hi or (k == 4 and p == 1.0)]
        calibration.append({
            "bin": k,
            "p_range": f"{k * 20}-{(k + 1) * 20}",
            "n": len(idx),
            "mean_p": round(sum(probs[i] for i in idx) / len(idx), 3) if idx else None,
            "observed_rate": round(sum(outcome[i] for i in idx) / len(idx), 3) if idx else None,
        })
    return {
        "count": n,
        "brier": round(sum((p - o) ** 2 for p, o in zip(probs, outcome)) / n, 4),
        "base_rate": round(sum(outcome) / n, 3),  # always-say-base-rate Brier is base_rate*(1-base_rate)
        "rank_ic": round(rho, 4) if rho is not None else None,
        "rank_ic_t": round(_spearman_t_stat(rho, n), 2) if rho is not None and _spearman_t_stat(rho, n) is not None else None,
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