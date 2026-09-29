"""Strategy views: the admin's one-page answer to "is it working?" and the users' calm,
read-only view of the same system (their stance for the day, and the simulated track record).

Admin (/api/admin/strategy/*): decisions with the full per-agent inspector, today's risk table,
capital results incl. tax, accuracy of the engine / risk signal / committee, the agent leaderboard.
Users (/api/me/stance, /api/me/track-record, /api/me/ledger-track-record): only reference strategies
and their own watchlist -- never another user's paper account.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from .. import db, pipeline, portfolio_analytics, portfolio_view, research, scoring, snapshots, strategy
from ..auth import TokenPayload, get_current_user, require_admin, require_role
from ..rate_limit import rate_limit_admin_mutations

admin_router = APIRouter(prefix="/api/admin/strategy", tags=["strategy"], dependencies=[Depends(require_admin), Depends(rate_limit_admin_mutations)])
me_router = APIRouter(prefix="/api/me", tags=["strategy"])


def _book_or_503():
    try:
        return strategy.cached_book()
    except Exception as exc:  # noqa: BLE001 -- e.g. no price data on this machine
        raise HTTPException(status_code=503, detail=f"Price data unavailable: {type(exc).__name__}") from None


@admin_router.get("/overview")
async def overview() -> Dict[str, Any]:
    return await run_in_threadpool(lambda: strategy.overview(_book_or_503()))


@admin_router.get("/accuracy")
async def accuracy() -> Dict[str, Any]:
    """Whole-history scorecards (cached per data version) plus the live committee scorecard and leaderboard."""
    return await run_in_threadpool(lambda: strategy.accuracy(_book_or_503()))


@admin_router.get("/research")
async def research_view() -> Dict[str, Any]:
    """Today's correlations / clusters / cohesion / lead-lag, the live-evidence gate for every strategy,
    standing proposals (advice only) and the latest weekly digest."""
    return await run_in_threadpool(lambda: research.view(_book_or_503()))


@admin_router.get("/data-sources")
async def data_sources() -> Dict[str, Any]:
    return await run_in_threadpool(lambda: strategy.data_sources(_book_or_503()))


@admin_router.get("/matrix")
async def matrix(
    fields: str = Query("close,ret,signal,risk_score", description="comma-separated panel fields"),
    symbols: Optional[str] = Query(None, description="comma-separated tickers (default: all)"),
    days: int = Query(120, ge=1, le=800),
    inference: bool = Query(False, description="also include the committee's stored decisions as matrices"),
) -> Dict[str, Any]:
    """Prices, returns, signals, risk (and optionally committee decisions) as labelled dates x symbols arrays."""
    names = [f.strip() for f in fields.split(",") if f.strip()]
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    try:
        return await run_in_threadpool(lambda: strategy.matrix(_book_or_503(), names, syms, days, inference))
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=f"Unknown field or symbol: {exc.args[0]}") from None


@admin_router.get("/correlation")
async def correlation(window: int = Query(60, ge=10, le=500)) -> Dict[str, Any]:
    """The correlation matrix of daily returns over the last `window` trading days, and its clusters."""
    return await run_in_threadpool(lambda: strategy.correlation_matrix(_book_or_503(), window))


@admin_router.get("/snapshots")
async def snapshot_history(field: str = Query("committee"), days: int = Query(120, ge=1, le=400)) -> Dict[str, Any]:
    """What the system believed on each stored day, one field at a time, as a labelled matrix."""
    try:
        return await run_in_threadpool(lambda: strategy.snapshot_matrix(field, days))
    except KeyError:
        raise HTTPException(status_code=422, detail=f"Unknown snapshot field: {field}. Choose from {sorted(snapshots.SNAPSHOT_FIELDS)}") from None


@admin_router.get("/pipeline")
async def pipeline_runs() -> Dict[str, Any]:
    """The recent daily-pipeline runs: target day, per-stage result and timing, price coverage."""
    return {"runs": await run_in_threadpool(lambda: list(reversed(pipeline.status_history()))[:14])}


@me_router.get("/track-record")
async def track_record(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    return await run_in_threadpool(strategy.track_record)


@me_router.get("/ledger-track-record", dependencies=[Depends(require_role("user"))])
async def ledger_track_record() -> Dict[str, Any]:
    """Forward-only scored ledger calls with per-horizon metrics (user-facing).

    Returns stored rows from ledger_calls and call_outcomes joined by call_id.
    Never recomputes prices -- only reads what the scoring job already stored.
    """
    def _compute() -> Dict[str, Any]:
        # Join ledger calls with outcomes
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

        # Group by horizon for metrics
        by_horizon: dict[int, list[dict[str, Any]]] = {}
        for sc in scored_calls:
            h = sc["horizon"]
            by_horizon.setdefault(h, []).append(sc)

        # Compute metrics per horizon (only 1, 5, 20 as those are the scoring horizons)
        metrics_by_horizon = {}
        for h in (1, 5, 20):
            horizon_calls = by_horizon.get(h, [])
            metrics_by_horizon[str(h)] = scoring.metrics(horizon_calls)

        ledger_calls_count = len(scoring.load_ledger_calls())

        return {
            "scored_calls": scored_calls,
            "metrics": metrics_by_horizon,
            "ledger_calls": ledger_calls_count,
            "pre_ledger_note": "Calls before 2026-09-27 are pre-ledger and not scored.",
        }

    return await run_in_threadpool(_compute)


@me_router.get("/stance")
async def stance(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    def _compute() -> Dict[str, Any]:
        row = db.get_user_by_username(user.sub)
        tickers = (row.get("tickers") or None) if row else None
        return strategy.stance(_book_or_503(), tickers)

    return await run_in_threadpool(_compute)


# ------------------------------------------------- committee-run user portfolios --


def _accounts() -> Dict[str, Dict[str, Any]]:
    return {a["id"]: a for a in db.list_paper_accounts()}


@me_router.get("/portfolio")
async def my_portfolio(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    """The caller's OWN committee-run paper portfolio (growth, Monte Carlo estimate, agent record, reasons).
    No account yet -> the shared model portfolio, labelled as such. Never another user's numbers."""

    def _compute() -> Dict[str, Any]:
        out = portfolio_view.user_portfolio(user.sub, _book_or_503(), db.list_all_committee_runs(), _accounts())
        if out is None:
            raise HTTPException(status_code=404, detail="No paper portfolio yet: the daily paper cycle creates it")
        return out

    return await run_in_threadpool(_compute)


@admin_router.get("/users")
async def portfolio_users() -> Dict[str, Any]:
    """Every user an admin can inspect (anyone with a committee-run paper account)."""
    return {"users": await run_in_threadpool(lambda: portfolio_view.selectable_users(db.list_users(), _accounts()))}


@admin_router.get("/user-portfolio/{username}")
async def user_portfolio(username: str) -> Dict[str, Any]:
    def _compute() -> Dict[str, Any]:
        out = portfolio_view.user_portfolio(username, _book_or_503(), db.list_all_committee_runs(), _accounts(), allow_fallback=False)
        if out is None:
            raise HTTPException(status_code=404, detail="That user has no committee-run paper account")
        return out

    return await run_in_threadpool(_compute)


@admin_router.get("/aggregate")
async def aggregate() -> Dict[str, Any]:
    """Cohort roll-up: every user's committee vs engine vs benchmark, plus pooled scored reviews."""
    return await run_in_threadpool(lambda: portfolio_analytics.aggregate(db.list_users(), _book_or_503(), db.list_all_committee_runs(), _accounts()))


@admin_router.get("/validation-dataset")
async def validation_dataset(format: str = Query("json", pattern="^(json|csv)$")):
    """One row per (user, symbol, committee review): engine vs committee, whether the account acted, forward returns."""
    rows = await run_in_threadpool(lambda: portfolio_analytics.validation_rows(db.list_users(), _book_or_503(), db.list_all_committee_runs(), _accounts()))
    if format == "csv":
        return Response(portfolio_analytics.to_csv(rows), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="glassbox-validation-dataset.csv"'})
    return {"rows": rows, "count": len(rows)}
