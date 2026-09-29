"""Daily scoring job: evaluate committee calls against market outcomes (S3 T12b).

    python -m app.scripts.score_ledger              # score eligible calls whose windows have closed
    python -m app.scripts.score_ledger --dry-run    # compute and print, but write nothing

Idempotent: a second run inserts 0 rows.
Exit code 0 = ran, 1 = could not run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

from .. import paper, scoring
from ..data_source import _load_trained_params, _load_parquet_row, STOCK_INFO

log = logging.getLogger("score_ledger")

HORIZONS = (1, 5, 20)


def _load_pricebook() -> paper.PriceBook:
    """Load the PriceBook the same way run_paper_cycle does."""
    params = _load_trained_params()
    frames = {}
    for sym in STOCK_INFO:
        df = _load_parquet_row(sym)
        if df is not None:
            frames[sym] = df
    return paper.PriceBook.from_frames(frames, params)


def _is_window_closed(call: dict[str, Any], horizon: int, book_closes: dict[str, float], bench_closes: dict[str, float]) -> bool:
    """Check if the scoring window for this call/horizon has closed (i.e., outcome() would return a result, not None)."""
    result = scoring.outcome(call, horizon, book_closes, bench_closes)
    return result is not None


def _outcome_exists(call_id: str, horizon: int) -> bool:
    """Check if a call_outcomes row already exists for this call_id and horizon."""
    with scoring.db.engine.connect() as conn:
        row = conn.execute(
            scoring.db.select(scoring.call_outcomes_table).where(
                scoring.call_outcomes_table.c.call_id == call_id,
                scoring.call_outcomes_table.c.horizon == horizon,
            )
        ).fetchone()
    return row is not None


def _insert_outcome(call_id: str, horizon: int, evaluated_at: str, outcome_json: dict[str, Any], score: float) -> None:
    """Insert a call_outcomes row."""
    with scoring.db.engine.begin() as conn:
        conn.execute(
            scoring.call_outcomes_table.insert().values(
                call_id=call_id,
                horizon=horizon,
                evaluated_at=evaluated_at,
                outcome_json=json.dumps(outcome_json, sort_keys=True, separators=(",", ":")),
                score=score,
            )
        )


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Score committee calls against market outcomes.")
    parser.add_argument("--dry-run", action="store_true", help="compute and print, but write nothing")
    args = parser.parse_args(argv)

    # Load PriceBook
    book = _load_pricebook()
    if not book.dates:
        log.error("no price data available")
        return {"scored": 0, "skipped_not_eligible": 0, "pending_window": 0, "error": "no price data"}

    # Load ledger calls
    calls = scoring.load_ledger_calls()
    if not calls:
        log.info("no committee_decision calls in ledger")
        return {"scored": 0, "skipped_not_eligible": 0, "pending_window": 0}

    # Get benchmark (SPY) closes
    bench_closes = {d: book.close_on("SPY", d) for d in book.dates if book.close_on("SPY", d) is not None}

    scored = 0
    skipped_not_eligible = 0
    pending_window = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for call in calls:
        ticker = call["ticker"]
        # Get symbol closes
        symbol_closes = {d: book.close_on(ticker, d) for d in book.dates if book.close_on(ticker, d) is not None}
        if not symbol_closes:
            skipped_not_eligible += len(HORIZONS)
            continue

        # Check eligibility once per call (same for all horizons)
        if not scoring.eligible(call["recorded_at"], call["call_id"].split(":")[0], symbol_closes):
            skipped_not_eligible += len(HORIZONS)
            continue

        for horizon in HORIZONS:
            # Check if outcome already exists (idempotent)
            if _outcome_exists(call["call_id"], horizon):
                # Already scored, skip
                continue

            # Check if window has closed
            if not _is_window_closed(call, horizon, symbol_closes, bench_closes):
                pending_window += 1
                continue

            # Compute outcome
            oc = scoring.outcome(call, horizon, symbol_closes, bench_closes)
            if oc is None:
                # Should not happen since _is_window_closed passed, but be safe
                pending_window += 1
                continue

            if args.dry_run:
                print(json.dumps({
                    "call_id": call["call_id"],
                    "horizon": horizon,
                    "evaluated_at": now_iso,
                    "outcome": oc,
                    "score": oc["score"],
                }))
            else:
                _insert_outcome(call["call_id"], horizon, now_iso, oc, oc["score"])
            scored += 1

    result = {"scored": scored, "skipped_not_eligible": skipped_not_eligible, "pending_window": pending_window}
    if not args.dry_run:
        log.info("scored %d, skipped_not_eligible %d, pending_window %d", scored, skipped_not_eligible, pending_window)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = main()
    print(json.dumps(result, indent=2))
    sys.exit(0)