"""Admin master view of the paper-trading system: leaderboard, per-account
detail (equity curve vs benchmark, holdings, trades), the signal scorecard and
a manual "run the cycle now" button. Admin-only, like the rest of /api/admin."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .. import db, paper_cycle
from ..auth import TokenPayload, require_admin
from ..rate_limit import rate_limit_admin_mutations

router = APIRouter(
    prefix="/api/admin/paper",
    tags=["paper-trading"],
    dependencies=[Depends(require_admin), Depends(rate_limit_admin_mutations)],
)


class PaperRunRequest(BaseModel):
    bootstrap: bool = False
    start: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    dry_run: bool = False


@router.get("/overview")
async def overview(since: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Every account (profiles, their policy benchmarks, controls) with return,
    alpha, drawdown, Sharpe, cost and turnover -- the leaderboard. `since` measures it all from that date."""
    return await run_in_threadpool(paper_cycle.overview, since)


@router.get("/accounts/{account_id}")
async def account(account_id: str, points: int = Query(400, ge=20, le=2000), since: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    detail = await run_in_threadpool(paper_cycle.account_detail, account_id, points, since)
    if detail is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return detail


@router.get("/scorecard")
async def scorecard(refresh: bool = False):
    """Hit-rate and edge of the engine's BUY/SELL/HOLD signals at 1/5/20 days."""
    try:
        return await run_in_threadpool(paper_cycle.scorecard, refresh)
    except Exception as exc:  # noqa: BLE001 -- e.g. no price data on this machine
        raise HTTPException(status_code=503, detail=f"Scorecard unavailable: {type(exc).__name__}") from None


@router.get("/signals")
async def signals(limit: int = Query(20, ge=1, le=200)):
    """The engine's recorded signal for every symbol on each live day."""
    return await run_in_threadpool(db.list_paper_signals, limit)


@router.post("/run", status_code=200)
async def run(body: PaperRunRequest, user: TokenPayload = Depends(require_admin)):
    """Run the daily cycle now (same code the cron job runs). Use
    bootstrap=true exactly once to create accounts and backfill history."""
    try:
        result = await run_in_threadpool(paper_cycle.run_cycle, bootstrap=body.bootstrap, start=body.start, dry_run=body.dry_run)
    except paper_cycle.CycleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if not body.dry_run:
        db.log_audit(
            user.sub, "paper.run", "paper", None,
            {"bootstrap": body.bootstrap, "new_live_days": len(result["new_live_days"]), "reports_written": result["reports_written"]},
        )
    return result
