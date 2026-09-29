"""Ported read routes from DASHBOARD_PRO.py (see app/data_source.py).

Phase 6: hot reads go through `cache.hot_read_cache` -- a short TTL cache
keyed by the underlying source file(s)' mtime, per
docs/PERFORMANCE_AND_ORCHESTRATION.md section 1. This also moves the
actual (blocking, parquet/pandas-touching) read off the event loop via
`asyncio.to_thread` inside `get_or_compute_async`, closing a gap where
these handlers previously ran that I/O directly inline.

`/api/track1/agents` and `/api/track2/agents` return static in-memory
constants with no I/O -- there's nothing to cache there, so they're left
as-is. `/api/agent-performance` used to be a third one (a hardcoded
3-agent mock) but now queries real data via db.get_agent_performance()
-- see that function's docstring for what it does and doesn't measure.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends

from .. import data_source as ds
from .. import db
from ..auth import TokenPayload, get_current_user_optional, require_role
from ..cache import hot_read_cache
from ..models import (
    AgentPerformance,
    DailySummary,
    HistoricalReport,
    HoldingsResponse,
    LiveSignalsResponse,
    PortfolioStats,
    TrackAgentsResponse,
    TrackDataPoint,
)

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/data", response_model=List[TrackDataPoint], dependencies=[Depends(require_role("public"))])
async def api_data():
    result = await hot_read_cache.get_or_compute_async(
        "api_data", ds.report_source_paths(), ds.load_latest_data
    )
    return result or []


@router.get("/track1/data", response_model=List[TrackDataPoint], dependencies=[Depends(require_role("public"))])
async def api_track1_data():
    return await hot_read_cache.get_or_compute_async(
        "track1_data", ds.track_source_paths("track1_performance.json"), ds.get_track1_data
    )


@router.get("/track2/data", response_model=List[TrackDataPoint], dependencies=[Depends(require_role("public"))])
async def api_track2_data():
    return await hot_read_cache.get_or_compute_async(
        "track2_data", ds.track_source_paths("track2_performance.json"), ds.get_track2_data
    )


@router.get("/track1/agents", response_model=TrackAgentsResponse, dependencies=[Depends(require_role("public"))])
async def api_track1_agents():
    return {"agents": ds.TRACK1_AGENTS, "vn_score": 0.85, "total_agents": 3}


@router.get("/track2/agents", response_model=TrackAgentsResponse, dependencies=[Depends(require_role("public"))])
async def api_track2_agents():
    return {"agents": ds.TRACK2_AGENTS, "vn_score": 0.92, "total_agents": 7}


@router.get("/agent-performance", response_model=List[AgentPerformance], dependencies=[Depends(require_role("public"))])
async def api_agent_performance():
    return db.get_agent_performance()


@router.get("/holdings", response_model=HoldingsResponse)
async def api_holdings(user: Optional[TokenPayload] = Depends(get_current_user_optional)):
    """Public (no auth required, same as every other endpoint in this
    file) -- but personalizes to the caller's own watchlist when a
    valid token IS present and that user has one set (see
    app/scripts/seed_demo_users.py for how those get populated).
    Anonymous/demo requests, and any account with no watchlist set
    (every real signup today), see the same full shared universe this
    endpoint always returned -- this is additive, not a behavior change
    for existing callers."""
    user_tickers: Optional[List[str]] = None
    if user is not None:
        row = db.get_user_by_username(user.sub)
        if row:
            user_tickers = row.get("tickers") or None

    cache_key = "holdings:" + (",".join(sorted(user_tickers)) if user_tickers else "global")
    return await hot_read_cache.get_or_compute_async(
        cache_key, ds.live_signals_source_paths(), lambda: ds.get_holdings(tickers=user_tickers)
    )


@router.get("/live-signals", response_model=LiveSignalsResponse, dependencies=[Depends(require_role("public"))])
async def api_live_signals():
    return await hot_read_cache.get_or_compute_async(
        "live_signals", ds.live_signals_source_paths(), ds.get_live_signals
    )


@router.get("/historical-reports", response_model=List[HistoricalReport], dependencies=[Depends(require_role("public"))])
async def api_historical_reports():
    return await hot_read_cache.get_or_compute_async(
        "historical_reports", ds.report_source_paths(), ds.get_historical_reports
    )


@router.get("/daily-summary", response_model=DailySummary | dict, dependencies=[Depends(require_role("public"))])
async def api_daily_summary():
    return await hot_read_cache.get_or_compute_async(
        "daily_summary", ds.report_source_paths(), ds.get_daily_summary
    )


@router.get("/portfolio-stats", response_model=PortfolioStats, dependencies=[Depends(require_role("public"))])
async def api_portfolio_stats():
    return await hot_read_cache.get_or_compute_async(
        "portfolio_stats", ds.portfolio_source_paths(), ds.get_portfolio_stats
    )
