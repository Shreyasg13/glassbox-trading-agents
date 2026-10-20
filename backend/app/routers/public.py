"""Public endpoints that require no authentication."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.concurrency import run_in_threadpool

from .. import casebook, disclaimer, scoring, studio
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
                    "score": oc["score"],
                    "p_beat_spy": call.get("p_beat_spy"),
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
                "p_beat_spy": {k: m["p_beat_spy"][k] for k in ("count", "brier", "base_rate", "rank_ic")},
            }
        return summary

    return await run_in_threadpool(_compute)


@router.get("/casebook", dependencies=[Depends(require_role("public"))])
async def casebook_posts(limit: int = Query(30, ge=1, le=120)) -> dict[str, Any]:
    """The Committee Casebook blog index: one post per day the committee sat, newest first."""
    return await run_in_threadpool(casebook.posts, limit)


@router.get("/casebook/feed", dependencies=[Depends(require_role("public"))])
async def casebook_feed() -> dict[str, Any]:
    """The daily feed: typed cards (the day, the most debated stock, an overrule, the latest verdict, a quote, the scoreboard)."""
    return await run_in_threadpool(casebook.feed)


@router.get("/casebook/{day}", dependencies=[Depends(require_role("public"))])
async def casebook_post(day: str = Path(..., pattern=r"^\d{4}-\d{2}-\d{2}$")) -> dict[str, Any]:
    """One day's casebook: each analyst's labelled lean and reasoning, the vote, and the 5-day outcome."""
    doc = await run_in_threadpool(casebook.post, day)
    if doc is None:
        raise HTTPException(status_code=404, detail="No committee review on that day")
    return doc


@router.get("/recaps", dependencies=[Depends(require_role("public"))])
async def recaps() -> dict[str, Any]:
    """Weekly recap articles the owner has approved for the blog, newest first."""
    return {"recaps": await run_in_threadpool(studio.published_recaps), "disclaimer": disclaimer.text()}


@router.get("/recaps/{recap_id}", dependencies=[Depends(require_role("public"))])
async def recap(recap_id: str = Path(..., pattern=r"^recap:\d{4}-W\d{2}$")) -> dict[str, Any]:
    item = next((r for r in await run_in_threadpool(studio.published_recaps) if r["id"] == recap_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="No published recap with that id")
    return {**item, "disclaimer": disclaimer.text()}
