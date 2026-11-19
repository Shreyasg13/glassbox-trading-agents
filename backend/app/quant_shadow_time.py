"""Offline helpers for evaluating timestamped shadow predictions."""

from bisect import bisect_left
from dataclasses import dataclass
from datetime import date, datetime, timezone


@dataclass(frozen=True)
class CompletedSession:
    date: date
    close: datetime


def seven_session_target(
    sessions: tuple[CompletedSession, ...],
    decision_date: date,
    cutoff: datetime,
) -> date | None:
    """Select the seventh calendar session only once its close is observable."""
    if cutoff.utcoffset() is None:
        raise ValueError("cutoff must be timezone-aware")
    dates = [session.date for session in sessions]
    if any(session.close.utcoffset() is None for session in sessions):
        raise ValueError("session closes must be timezone-aware")
    closes = [session.close.astimezone(timezone.utc) for session in sessions]
    if any(a >= b for a, b in zip(dates, dates[1:])) or any(
        a >= b for a, b in zip(closes, closes[1:])
    ):
        raise ValueError("session calendar must be strictly ordered")
    index = bisect_left(dates, decision_date)
    if index == len(dates) or dates[index] != decision_date:
        raise ValueError("decision date is absent from the session calendar")
    target = index + 7
    if target >= len(sessions):
        return None
    if closes[target] > cutoff.astimezone(timezone.utc):
        return None
    return dates[target]
