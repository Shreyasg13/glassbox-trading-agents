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
    for earlier, later in zip(sessions, sessions[1:]):
        if earlier.date >= later.date or earlier.close >= later.close:
            raise ValueError("session calendar must be strictly ordered")
    if any(session.close.utcoffset() is None for session in sessions):
        raise ValueError("session closes must be timezone-aware")
    index = bisect_left(dates, decision_date)
    if index == len(dates) or dates[index] != decision_date:
        raise ValueError("decision date is absent from the session calendar")
    target = index + 7
    if target >= len(sessions):
        return None
    if sessions[target].close.astimezone(timezone.utc) > cutoff.astimezone(timezone.utc):
        return None
    return dates[target]
