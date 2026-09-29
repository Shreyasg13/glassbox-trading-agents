"""Weekly discrepancy report: admin review/publish, and the public transparency page (S3 T13).
See app/weekly_report.py for the draft's contents and the (idempotent) job that creates it."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from .. import weekly_report as wr
from ..auth import TokenPayload, require_role
from ..rate_limit import rate_limit_admin_mutations

admin_router = APIRouter(prefix="/api/admin/weekly-reports", tags=["weekly-reports"], dependencies=[Depends(rate_limit_admin_mutations)])
public_router = APIRouter(prefix="/api/public/weekly-reports", tags=["weekly-reports"])


class WeeklyReportSummary(BaseModel):
    id: str
    week_start: str
    status: str  # draft | published
    created_at: str
    published_at: Optional[str] = None
    published_by: Optional[str] = None


class WeeklyReportDetail(WeeklyReportSummary):
    body: Dict[str, Any]  # build_draft()'s output: runs checked, A6 pass rate, top failing checks/metrics, A7 by action


def _summary(row: Dict[str, Any]) -> WeeklyReportSummary:
    return WeeklyReportSummary(
        id=row["id"], week_start=row["week_start"], status=row["status"], created_at=row["created_at"],
        published_at=row["published_at"], published_by=row["published_by"],
    )


def _detail(row: Dict[str, Any]) -> WeeklyReportDetail:
    return WeeklyReportDetail(**_summary(row).model_dump(), body=json.loads(row["body_json"]))


@admin_router.get("", response_model=List[WeeklyReportDetail], dependencies=[Depends(require_role("admin"))])
async def list_reports_admin() -> List[WeeklyReportDetail]:
    """Every report, drafts and published, newest week first -- with its body, so admin can review a draft's
    content before publishing it."""
    return [_detail(r) for r in await run_in_threadpool(wr.list_reports)]


@admin_router.post("/{report_id}/publish", response_model=WeeklyReportDetail)
async def publish_report(report_id: str, user: TokenPayload = Depends(require_role("admin"))) -> WeeklyReportDetail:
    """draft -> published, recording who and when. A published report can never be edited (there is no edit route);
    publishing an already-published report is a 409."""
    try:
        row = await run_in_threadpool(wr.publish, report_id, user.sub)
    except wr.NotFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekly report not found") from None
    except wr.AlreadyPublished:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This report has already been published") from None
    return _detail(row)


@public_router.get("", response_model=List[WeeklyReportSummary], dependencies=[Depends(require_role("public"))])
async def list_reports_public() -> List[WeeklyReportSummary]:
    """Published reports only, newest week first. Drafts never appear here."""
    return [_summary(r) for r in await run_in_threadpool(wr.list_reports, "published")]


@public_router.get("/{report_id}", response_model=WeeklyReportDetail, dependencies=[Depends(require_role("public"))])
async def get_report_public(report_id: str) -> WeeklyReportDetail:
    """404 for a draft or an unknown id -- only a published report is ever visible here."""
    row = await run_in_threadpool(wr.get, report_id)
    if row is None or row["status"] != "published":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Weekly report not found")
    return _detail(row)
