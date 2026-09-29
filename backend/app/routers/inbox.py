"""The user's inbox, date-specific signals, the portfolio assistant and feedback (all scoped to the caller), plus the
admin's feedback summary. See app/notifications.py, app/assistant.py and app/feedback.py for the rules."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .. import assistant, db, disclaimer, feedback, flags, notifications, portfolio_view
from ..auth import TokenPayload, get_current_user, require_admin
from ..portfolio_analytics import to_csv
from ..rate_limit import check_user_heavy
from .strategy import _accounts, _book_or_503

me_router = APIRouter(prefix="/api/me", tags=["inbox"])
admin_router = APIRouter(prefix="/api/admin/feedback", tags=["feedback"], dependencies=[Depends(require_admin)])


def _tickers(user: TokenPayload) -> Optional[List[str]]:
    row = db.get_user_by_username(user.sub)
    return (row.get("tickers") or None) if row else None


# ------------------------------------------------------------------- inbox --


@me_router.get("/notifications")
async def list_notifications(kind: Optional[str] = None, unread_only: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0), user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    return await run_in_threadpool(lambda: notifications.list_for(user.sub, kind=kind, unread_only=unread_only, limit=limit, offset=offset))


@me_router.get("/notifications/unread-count")
async def unread_count(user: TokenPayload = Depends(get_current_user)) -> Dict[str, int]:
    return {"unread": await run_in_threadpool(notifications.unread_count, user.sub)}


class ReadBody(BaseModel):
    ids: Optional[List[str]] = Field(default=None, max_length=200)  # None = mark everything read


@me_router.post("/notifications/read")
async def mark_read(body: ReadBody, user: TokenPayload = Depends(get_current_user)) -> Dict[str, int]:
    n = await run_in_threadpool(notifications.mark_read, user.sub, body.ids)
    return {"marked": n, "unread": await run_in_threadpool(notifications.unread_count, user.sub)}


# ----------------------------------------------------------- signals by date --


@me_router.get("/signals")
async def signals(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"), user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    """The caller's stocks as the engine and committee saw them on one trading day (default: the latest), how each
    turned out, and how the committee's calls on these stocks have done."""

    def _compute() -> Dict[str, Any]:
        book, runs, tickers = _book_or_503(), db.list_all_committee_runs(), _tickers(user)
        try:
            out = assistant.signals_on(book, tickers, date, runs)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        out["committee_track"] = assistant.committee_track(book, runs, [r["symbol"] for r in out["rows"]])
        out["watchlist"] = tickers
        return out

    return await run_in_threadpool(_compute)


# ---------------------------------------------------------------- assistant --


class Turn(BaseModel):
    role: str = Field(max_length=12)
    content: str = Field(max_length=600)


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=600)
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    history: List[Turn] = Field(default_factory=list, max_length=12)


@me_router.post("/ask")
async def ask(body: AskBody, user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    if not flags.flag("output.assistant"):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="The assistant is switched off for now. Signals for any date are still available above.")
    check_user_heavy(user.sub)
    if await run_in_threadpool(assistant.remaining_today, user.sub) <= 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f"You've used today's {assistant.DAILY_QUESTIONS} questions. They reset at 00:00 UTC.")

    def _facts() -> Dict[str, Any]:
        book, runs, tickers = _book_or_503(), db.list_all_committee_runs(), _tickers(user)
        try:
            sig = assistant.signals_on(book, tickers, body.date, runs)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        port = portfolio_view.user_portfolio(user.sub, book, runs, _accounts())
        return {"sig": sig, "facts": assistant.build_facts(sig, port, assistant.committee_track(book, runs, [r["symbol"] for r in sig["rows"]]))}

    ctx = await run_in_threadpool(_facts)
    under_cap = await run_in_threadpool(assistant.total_used_today) < assistant.GLOBAL_DAILY_CAP
    result = await assistant.answer(body.question, [t.model_dump() for t in body.history], ctx["facts"], allow_llm=under_cap)

    # Publish the assistant answer through the single exit
    from .. import publish
    content_ref = f"assistant:{user.sub}:{ctx['sig']['as_of']}"
    pub_result = publish.publish_simple(
        channel="assistant",
        text=result["answer"],
        content_ref=content_ref,
        is_html=False,
    )

    # In enforce mode, held answers return a generic message
    if not pub_result.allowed:
        answer_text = "This answer is being reviewed."
    else:
        answer_text = pub_result.text

    mid = await run_in_threadpool(assistant.save, user.sub, ctx["sig"]["as_of"], body.question, {"answer": answer_text, "used_llm": result["used_llm"], "model": result.get("model", "")})
    return {
        "id": mid, "answer": answer_text, "as_of": ctx["sig"]["as_of"], "note": ctx["sig"]["note"], "used_ai": result["used_llm"],
        "sources": [r["symbol"] for r in ctx["sig"]["rows"]],
        "remaining_today": await run_in_threadpool(assistant.remaining_today, user.sub),
        "disclaimer": disclaimer.text(),
    }


@me_router.get("/ask/history")
async def ask_history(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    return {"messages": await run_in_threadpool(assistant.recent, user.sub), "remaining_today": await run_in_threadpool(assistant.remaining_today, user.sub), "daily_limit": assistant.DAILY_QUESTIONS}


# ----------------------------------------------------------------- feedback --


class FeedbackBody(BaseModel):
    target_type: str = Field(max_length=20)
    target_ref: str = Field(default="", max_length=120)
    rating: int = Field(default=0, ge=-1, le=1)
    comment: str = Field(default="", max_length=1000)
    symbol: str = Field(default="", max_length=14)


@me_router.post("/feedback")
async def give_feedback(body: FeedbackBody, user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    def _save() -> Dict[str, Any]:
        # You can only rate your own answers and notifications, never someone else's.
        if body.target_type == "answer" and not assistant.owns(user.sub, body.target_ref):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Answer not found")
        if body.target_type == "notification" and not notifications.owns(user.sub, body.target_ref):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        try:
            return feedback.record(user.sub, body.target_type, body.target_ref, body.rating, body.comment, body.symbol)
        except feedback.FeedbackError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

    return await run_in_threadpool(_save)


@me_router.get("/feedback")
async def my_feedback(user: TokenPayload = Depends(get_current_user)) -> Dict[str, Any]:
    return {"items": await run_in_threadpool(feedback.mine, user.sub)}


# -------------------------------------------------------------------- admin --


@admin_router.get("/summary")
async def feedback_summary(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    return await run_in_threadpool(feedback.summary, days)


@admin_router.get("/export")
async def feedback_export(days: int = Query(365, ge=1, le=365)) -> Response:
    rows = await run_in_threadpool(feedback.rows_for_export, days)
    return Response(to_csv(rows), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="glassbox-feedback.csv"'})
