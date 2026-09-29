"""Public endpoints that require no authentication."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from .. import disclaimer, scoring
from ..auth import require_role

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/disclaimer")
async def get_disclaimer() -> dict[str, str | bool]:
    """Return the current research disclaimer text and whether it's pending legal review."""
    return {
        "text": disclaimer.text(),
        "pending_legal_review": disclaimer.pending_legal_review(),
    }


@router.get("/track-record/summary", dependencies=[Depends(require_role("public"))])
async def track_record_summary() -> dict[str, Any]:
    """Public track record summary: per-horizon metrics only (no per-call rows, no call_ids, no symbols)."""
    def _compute() -> dict[str, Any]:
        # Join ledger calls with outcomes (same logic as user route)
        calls = scoring.load_ledger_calls()
        outcomes = scoring.load_outcomes()

        outcomes_by_call: dict[str, list[dict[str, Any]]] = {}
        for oc in outcomes:
            outcomes_by_call.setdefault(oc["call_id"], []).append(oc)

        scored_calls = []
        for call in calls:
            call_outcomes = outcomes_by_call.get(call["call_id"], [])
            if not call_outcomes:
                continue
            for oc in call_outcomes:
                ojson = oc["outcome_json"]
                scored_calls.append({
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
                    "right": oc["score"] > 0,
                })

        # Group by horizon
        by_horizon: dict[int, list[dict[str, Any]]] = {}
        for sc in scored_calls:
            h = sc["horizon"]
            by_horizon.setdefault(h, []).append(sc)

        summary = {}
        for h in (1, 5, 20):
            horizon_calls = by_horizon.get(h, [])
            m = scoring.metrics(horizon_calls)
            summary[str(h)] = {
                "count": m["count"],
                "hit_rate": m["hit_rate"],
                "mean_excess_return": m["mean_excess_return"],
                "rank_ic": m["rank_ic"],
            }
        return summary

    return await run_in_threadpool(_compute)
