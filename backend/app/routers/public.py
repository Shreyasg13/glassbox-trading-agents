"""Public endpoints that require no authentication."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from .. import disclaimer, scoring
from ..auth import require_role

router = APIRouter(prefix="/api/public", tags=["public"])


@router.get("/disclaimer", dependencies=[Depends(require_role("public"))])
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
        scored_calls = scoring.scored_calls()

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
                "directional_count": m["directional_count"],
                "by_decision": m["by_decision"],
                "hit_rate": m["hit_rate"],
                "mean_signed_excess": m["mean_signed_excess"],
                "rank_ic": m["rank_ic"],
                "p_beat_spy": {k: m["p_beat_spy"][k] for k in ("count", "brier", "base_rate", "rank_ic")},
            }
        return summary

    return await run_in_threadpool(_compute)
