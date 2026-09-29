from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from .. import data_source as ds
from ..models import MonteCarloResult
from ..auth import require_role
from ..rate_limit import rate_limit_monte_carlo

router = APIRouter(prefix="/api", tags=["monte-carlo"])


# Public endpoint, so every input is bounded: unbounded `simulations`/`days`
# would let one request allocate gigabytes or pin a core for minutes. The
# dashboard itself asks for simulations=500 and a short horizon.
@router.post("/monte-carlo", response_model=MonteCarloResult, dependencies=[Depends(require_role("public")), Depends(rate_limit_monte_carlo)])
async def api_monte_carlo(
    days: int = Query(7, ge=1, le=365),
    simulations: int = Query(1000, ge=100, le=5000),
    confidence: float = Query(0.95, ge=0.5, le=0.999),
):
    # CPU-bound numpy work, run off the event loop so it can't stall
    # every other request (websocket pushes, logins) on this worker.
    result = await run_in_threadpool(ds.run_monte_carlo, days=days, simulations=simulations, confidence=confidence)
    if result is None:
        raise HTTPException(status_code=404, detail="No data available")
    return result
