"""Feature flags and output kill switches: turn a whole output channel (or the daily job) off without a deploy.

The point (S3 task T1): if something is wrong with what the system is saying, an admin can stop it reaching people in seconds.

DESIGN
  * Every flag is declared once in FLAGS with its default and a plain-English description. Only declared flags can be set,
    so a typo can never silently create a switch that does nothing.
  * A flag with no stored row uses its DEFAULT. So a fresh database, a missing table or a database hiccup all behave exactly like
    "nobody has touched anything": flag() NEVER raises, because a broken switch must not take an output channel down.
  * Reads are cached for a few seconds per process (gunicorn runs several workers); a change is therefore live everywhere within
    CACHE_TTL_S seconds. Writes clear the local cache immediately.
  * Every change is written to the audit log with who made it and when.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.exc import SQLAlchemyError

from . import db
from .migrated_tables import feature_flags_table as _T

log = logging.getLogger("glassbox.flags")

CACHE_TTL_S = 5.0

# key -> (default enabled, description shown to the admin)
FLAGS: Dict[str, Tuple[bool, str]] = {
    "pipeline.daily": (True, "The daily job (price sync, committee, paper cycle, inbox, emails). Off: the scheduled run exits without doing anything."),
    "pipeline.claims": (False, "Attach structured claims and an LLM narrative to each committee decision. Off: claims (no LLM) are still stored; narrative step is skipped. Requires a paid key for the narrative model."),
    "output.reports": (True, "Report pages for the public and users. Off: report links return 404 (admins can still open them)."),
    "output.email": (True, "Every email GlassBox sends: the admin digest, user digests, previews and address confirmations. Off: nothing is emailed."),
    "output.speech": (False, "Voice narration (text-to-speech). Off by default; the site falls back to the browser's own voice."),
    "output.assistant": (True, "The portfolio assistant's answers (Ask). Off: the assistant is unavailable; signals for a date still work."),
    "output.user_reports": (True, "Reports a user generates for themselves (Run my report). Off: unavailable."),
    "publish.enforce": (False, "Hold outputs that fail verification or compliance. Off = shadow mode: checks are recorded, nothing is held."),
}

_cache: Dict[str, Tuple[float, bool]] = {}


class UnknownFlag(KeyError):
    pass


def _check(key: str) -> None:
    if key not in FLAGS:
        raise UnknownFlag(key)


def _read(key: str) -> bool:
    """The stored value, or the default if there is none or the database can't be read. Uncached."""
    default = FLAGS[key][0]
    try:
        with db.engine.connect() as conn:
            row = conn.execute(select(_T.c.enabled).where(_T.c.key == key)).first()
        return default if row is None else bool(row.enabled)
    except SQLAlchemyError as exc:  # table not migrated yet, locked database, ...
        log.debug("feature flag %s: using the default (%s)", key, type(exc).__name__)
        return default


def flag(key: str, *, now: Optional[float] = None) -> bool:
    """Is this flag on? Never raises: an unknown key (treated as off) or a database problem falls back safely."""
    if key not in FLAGS:
        log.warning("unknown feature flag %r requested; treating it as off", key)
        return False
    t = time.monotonic() if now is None else now
    hit = _cache.get(key)
    if hit and 0 <= t - hit[0] < CACHE_TTL_S:
        return hit[1]
    value = _read(key)
    _cache[key] = (t, value)
    return value


def clear_cache() -> None:
    _cache.clear()


def all_flags() -> List[Dict[str, Any]]:
    stored: Dict[str, Any] = {}
    try:
        with db.engine.connect() as conn:
            stored = {r.key: r for r in conn.execute(select(_T))}
    except SQLAlchemyError:
        pass
    out = []
    for key, (default, description) in FLAGS.items():
        row = stored.get(key)
        out.append(
            {
                "key": key,
                "enabled": default if row is None else bool(row.enabled),
                "default": default,
                "description": description,
                "updated_by": row.updated_by if row else None,
                "updated_at": row.updated_at if row else None,
            }
        )
    return out


def set_flag(key: str, enabled: bool, actor: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Changes a flag and records who did it. Raises UnknownFlag for an undeclared key and SQLAlchemyError if the table is missing."""
    _check(key)
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    previous = _read(key)  # uncached, so the audit entry records the true previous value
    values = {"key": key, "enabled": bool(enabled), "updated_by": actor, "updated_at": stamp}
    with db.engine.begin() as conn:
        insert_fn = postgresql.insert if db.engine.dialect.name == "postgresql" else sqlite.insert
        stmt = insert_fn(_T).values(**values)
        conn.execute(stmt.on_conflict_do_update(index_elements=["key"], set_={k: v for k, v in values.items() if k != "key"}))
    clear_cache()
    db.log_audit(actor, "flag.set", "feature_flag", key, {"enabled": bool(enabled), "previous": previous})
    log.warning("feature flag %s set to %s by %s", key, enabled, actor)
    return next(f for f in all_flags() if f["key"] == key)
