"""The user's in-app inbox: daily stance, alerts, paper-trading reports and emailed digests, in one place.

Design rules (same spirit as app/digest.py):
  * READ ONLY, CURATED. Content comes from strategy.stance() (what the dashboard shows) and the reports the
    paper cycle already wrote, so the inbox can never say something the app doesn't.
  * IDEMPOTENT. (user, dedupe_key) is unique, so re-running the daily job never duplicates anything.
  * BOUNDED. At most MAX_ALERTS_PER_USER_DAY alerts per user per day, so a broad sell-off can't bury the inbox.
  * NEVER FATAL. One bad user is skipped and recorded; the pipeline stage never raises.
  * PRIVATE. Every read and write is scoped to the caller's own username.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import case, delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from . import db

log = logging.getLogger("glassbox.notifications")

KINDS = ("signal", "alert", "report", "email")
SEVERITIES = ("info", "watch", "attention")
RETENTION_DAYS = 90
MAX_ALERTS_PER_USER_DAY = 5
_T = db.notifications_table


def _u(user: str) -> str:
    return user.strip().lower()


# ---------------------------------------------------------------- storage --


def add(user: str, dedupe_key: str, day: str, kind: str, title: str, body: str = "", link: str = "", severity: str = "info", now: Optional[datetime] = None) -> bool:
    """Stores one notification. Returns False when it already existed (same user + dedupe_key)."""
    if kind not in KINDS or severity not in SEVERITIES:
        raise ValueError("unknown kind or severity")
    now = now or datetime.now(timezone.utc)
    row = {
        "id": str(uuid.uuid4()), "user": _u(user), "dedupe_key": dedupe_key[:200], "day": day, "kind": kind, "severity": severity,
        "title": title[:200], "body": body[:2000], "link": link[:200], "read": False, "created_at": now.isoformat(timespec="seconds"),
    }
    try:
        with db.engine.begin() as conn:
            conn.execute(insert(_T).values(**row))
    except IntegrityError:
        return False
    return True


def _public(r) -> Dict[str, Any]:
    return {k: r._mapping[k] for k in ("id", "day", "kind", "severity", "title", "body", "link", "read", "created_at")}


def list_for(user: str, *, kind: Optional[str] = None, unread_only: bool = False, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    q = select(_T).where(_T.c.user == _u(user))
    if kind in KINDS:
        q = q.where(_T.c.kind == kind)
    if unread_only:
        q = q.where(_T.c.read == False)  # noqa: E712 -- SQLAlchemy needs the comparison operator
    # Newest trading day first; within a day: the stance, then its alerts, then the report, then the email. (created_at
    # alone would interleave them arbitrarily, since a whole job writes within the same second or two.)
    rank = case((_T.c.kind == "signal", 0), (_T.c.kind == "alert", 1), (_T.c.kind == "report", 2), else_=3)
    q = q.order_by(_T.c.day.desc(), rank, _T.c.created_at.desc(), _T.c.id).limit(min(max(limit, 1), 200)).offset(max(offset, 0))
    with db.engine.connect() as conn:
        items = [_public(r) for r in conn.execute(q)]
    return {"items": items, "unread": unread_count(user)}


def unread_count(user: str) -> int:
    with db.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(_T).where(_T.c.user == _u(user), _T.c.read == False)).scalar() or 0)  # noqa: E712


def mark_read(user: str, ids: Optional[List[str]] = None) -> int:
    """Marks the caller's own notifications read (all of them when `ids` is None). Other users' ids are ignored."""
    q = update(_T).where(_T.c.user == _u(user), _T.c.read == False).values(read=True)  # noqa: E712
    if ids is not None:
        q = q.where(_T.c.id.in_(ids[:200]))
    with db.engine.begin() as conn:
        return conn.execute(q).rowcount


def prune(now: Optional[datetime] = None) -> int:
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds")
    with db.engine.begin() as conn:
        return conn.execute(delete(_T).where(_T.c.created_at < cutoff)).rowcount


def owns(user: str, notification_id: str) -> bool:
    with db.engine.connect() as conn:
        return conn.execute(select(_T.c.id).where(_T.c.id == notification_id, _T.c.user == _u(user))).first() is not None


# ---------------------------------------------------------------- content --


def daily_items(stance: Dict[str, Any], label: str = "your stocks") -> List[Dict[str, Any]]:
    """Pure: the day's signal summary plus one alert per stock that needs attention (bounded)."""
    as_of = stance.get("as_of")
    if not as_of:
        return []
    rows = stance.get("rows") or []
    att, watch = [r for r in rows if r.get("attention")], [r for r in rows if r.get("watch")]
    quiet = len(rows) - len(att) - len(watch)
    lines = [f"{r['symbol']}: {r.get('summary', '')}" for r in att[:8]] + [f"{r['symbol']}: risk is HIGH, no action suggested" for r in watch[:5]]
    if not lines:
        lines.append(f"Nothing needs your attention on {label} today.")
    if stance.get("macro_line"):
        lines.append(stance["macro_line"])
    items = [
        {
            "dedupe_key": f"signal:{as_of}", "day": as_of, "kind": "signal",
            "severity": "attention" if att else "watch" if watch else "info",
            "title": f"Your daily stance for {as_of}: " + (f"{len(att)} need a look, " if att else "") + (f"{len(watch)} on watch, " if watch else "") + f"{quiet} quiet",
            "body": "\n".join(lines) or f"Nothing needs your attention on {label} today.",
            "link": f"/ask?date={as_of}",
        }
    ]
    for r in att[:MAX_ALERTS_PER_USER_DAY]:
        items.append(
            {
                "dedupe_key": f"alert:{r['symbol']}:{as_of}", "day": as_of, "kind": "alert", "severity": "attention",
                "title": f"{r['symbol']}: {r.get('summary', 'needs a look')}"[:200],
                "body": "; ".join(r.get("reasons") or []),
                "link": f"/ask?date={as_of}&symbol={r['symbol']}",
            }
        )
    return items


def report_items(user: str, narratives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure: a notification for each paper-trading report the system wrote for this user's profile."""
    base = _u(user)
    out = []
    for n in narratives:
        if n.get("provider") != "system" or n.get("profile") != f"profile:{base}":
            continue
        stamp = str(n.get("date") or "")
        day = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}" if len(stamp) == 8 else stamp
        out.append({"dedupe_key": f"report:{n['id']}", "day": day, "kind": "report", "severity": "info", "title": f"Your paper-trading report for {day} is ready", "body": (n.get("title") or ""), "link": f"/reports/{n['id']}"})
    return out


def mask_email(addr: str) -> str:
    name, _, dom = addr.partition("@")
    return f"{name[:1]}***@{dom}" if dom else "your address"


def note_email_sent(user: str, addr: str, as_of: str) -> None:
    """Called after a digest email goes out, so 'what did you email me?' has an answer in the inbox."""
    try:
        add(user, f"email:{as_of}", as_of, "email", f"Daily digest emailed to {mask_email(addr)}", f"The email carries the same stance as your inbox entry for {as_of}.", f"/ask?date={as_of}")
    except Exception as exc:  # noqa: BLE001 -- an inbox hiccup must never fail a digest run
        log.warning("could not record the emailed-digest notification: %s", type(exc).__name__)


# -------------------------------------------------------------- the daily job --


def generate_daily(book=None, users: Optional[List[Dict[str, Any]]] = None, narratives: Optional[List[Dict[str, Any]]] = None, stance_fn: Optional[Callable] = None) -> Dict[str, Any]:
    """The pipeline stage. One stance computation per distinct watchlist; one bad user never stops the rest."""
    try:
        if book is None:
            from . import paper_cycle

            book = paper_cycle.load_book()
        users = db.list_users() if users is None else users
        narratives = db.list_report_narratives() if narratives is None else narratives
        if stance_fn is None:
            from . import strategy

            stance_fn = strategy.stance
    except Exception as exc:  # noqa: BLE001
        log.warning("notifications: could not load data: %s", exc)
        return {"ok": False, "created": 0, "detail": f"build failed: {type(exc).__name__}: {exc}"}

    from . import publish

    cache: Dict[Any, Dict[str, Any]] = {}
    created = failed = 0
    for row in users:
        if row.get("role") == "admin" or not row.get("username"):
            continue
        try:
            tickers = row.get("tickers") or None
            key = tuple(sorted(tickers)) if tickers else ()
            if key not in cache:
                cache[key] = stance_fn(book, list(key) or None)
            for it in daily_items(cache[key], "your watchlist" if tickers else "the tracked stocks") + report_items(row["username"], narratives):
                # Publish the notification content through the single exit
                content = f"{it['title']}\n\n{it['body']}"
                content_ref = f"inbox:{row['username']}:{it['dedupe_key']}"
                result = publish.publish_simple(
                    channel="inbox",
                    text=content,
                    content_ref=content_ref,
                    is_html=False,
                )
                # In enforce mode, store the holding text; in shadow mode, store the (possibly rewritten) text
                notification_text = result.text if result.allowed else "This update is being reviewed."
                created += add(row["username"], it["dedupe_key"], it["day"], it["kind"], it["title"], notification_text, it["link"], it["severity"])
        except Exception as exc:  # noqa: BLE001 -- one bad user must not stop the rest
            failed += 1
            log.warning("notifications failed for a user: %s", type(exc).__name__)
    prune()
    return {"ok": failed == 0 or created > 0, "created": created, "failed": failed, "detail": f"created {created}, failed {failed}"}
