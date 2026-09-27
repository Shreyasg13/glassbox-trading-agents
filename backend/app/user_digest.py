"""Per-user digest emails: each opted-in user gets today's stance for THEIR watchlist.

Design rules (same spirit as app/digest.py, which this reuses for SMTP):

  * OPT-IN ONLY. Nothing is sent unless the user turned it on. Their settings live on their own
    `users` row under `digest` (no schema change: the row is a JSON blob).
  * VERIFIED ADDRESSES ONLY. A typed-in address could be anyone's, so it must be confirmed by a
    link sent to it before any digest goes there. A Google-OAuth account's own email counts as
    already verified. Otherwise this would be a way to spam strangers.
  * READ ONLY, CURATED. The content is strategy.stance(book, tickers) -- exactly what the
    dashboard's "Today's stance" shows -- so the email cannot say something the app doesn't.
  * STATELESS LINKS. Confirm/unsubscribe links carry an HMAC of the user id (and the pending
    address, for confirmation), so a changed address invalidates old confirm links and no token
    table is needed.
  * NEVER FATAL. One bad recipient is recorded and skipped; the pipeline stage never raises.
  * IDEMPOTENT. A re-run of the pipeline the same day does not send twice (`last_sent`).
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from . import db, digest, disclaimer
from .auth import SECRET_KEY

log = logging.getLogger("glassbox.user_digest")

FREQUENCIES = ("daily", "weekly")
MAX_PER_RUN = 400  # Gmail SMTP allows ~500/day and the admin digest uses one
CONFIRM_COOLDOWN = timedelta(minutes=10)
PREVIEW_COOLDOWN = timedelta(minutes=2)
_EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[A-Za-z]{2,}$")


# ------------------------------------------------------------------- prefs --


def _prefs(row: Dict[str, Any]) -> Dict[str, Any]:
    p = row.get("digest") or {}
    return {
        "enabled": bool(p.get("enabled")),
        "email": p.get("email"),
        "verified_email": p.get("verified_email"),
        "frequency": p.get("frequency") if p.get("frequency") in FREQUENCIES else "daily",
        "last_sent": p.get("last_sent"),
        "last_error": p.get("last_error"),
        "confirm_sent_at": p.get("confirm_sent_at"),
        "last_preview_at": p.get("last_preview_at"),
    }


def _is_verified(row: Dict[str, Any], email: Optional[str]) -> bool:
    """A Google account's own address is verified by Google; anything else must have been confirmed by link."""
    if not email:
        return False
    if row.get("oauth_provider") and (row.get("email") or "").lower() == email:
        return True
    return (_prefs(row)["verified_email"] or "") == email


def public_view(row: Dict[str, Any]) -> Dict[str, Any]:
    p = _prefs(row)
    email = p["email"] or ((row.get("email") or "").lower() if row.get("oauth_provider") else None)
    return {
        "enabled": p["enabled"],
        "email": email,
        "verified": _is_verified(row, email),
        "frequency": p["frequency"],
        "last_sent": p["last_sent"],
        "last_error": p["last_error"],
        "tickers": row.get("tickers") or [],
    }


def deliverable_address(row: Dict[str, Any]) -> Optional[str]:
    """The address a digest may go to right now, or None (off, no address, or address not verified)."""
    p = _prefs(row)
    if not p["enabled"]:
        return None
    email = p["email"] or ((row.get("email") or "").lower() if row.get("oauth_provider") else None)
    return email if _is_verified(row, email) else None


def normalize_email(raw: Optional[str]) -> Optional[str]:
    if raw is None or not str(raw).strip():
        return None
    e = str(raw).strip().lower()
    if len(e) > 254 or not _EMAIL_RE.match(e):
        raise ValueError("That doesn't look like a valid email address")
    return e


def apply_prefs(row: Dict[str, Any], enabled: bool, email: Optional[str], frequency: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Validates and saves the user's settings. Returns public_view() plus `confirmation_needed`
    (True when the caller should send a confirmation link now)."""
    now = now or datetime.now(timezone.utc)
    if frequency not in FREQUENCIES:
        raise ValueError("frequency must be 'daily' or 'weekly'")
    addr = normalize_email(email)
    if enabled and not addr and not (row.get("oauth_provider") and row.get("email")):
        raise ValueError("Add an email address to receive the digest")
    p = _prefs(row)
    new = dict(p, enabled=bool(enabled), email=addr, frequency=frequency)
    stored = dict(row, digest=new)
    effective = addr or ((row.get("email") or "").lower() if row.get("oauth_provider") else None)
    need = False
    if enabled and effective and not _is_verified(stored, effective):
        last = _parse(p["confirm_sent_at"])
        need = last is None or now - last >= CONFIRM_COOLDOWN or p["email"] != addr
        if need:
            new["confirm_sent_at"] = now.isoformat()
    db.update_user(row["id"], {"digest": new})
    out = public_view(dict(row, digest=new))
    out["confirmation_needed"] = need
    return out


def _parse(ts: Optional[str]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts) if ts else None
    except ValueError:
        return None


# ------------------------------------------------------------------- links --


def make_token(purpose: str, user_id: str, bound: str = "") -> str:
    msg = f"{purpose}|{user_id}|{bound}".encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()[:32]


def check_token(purpose: str, user_id: str, token: str, bound: str = "") -> bool:
    return hmac.compare_digest(make_token(purpose, user_id, bound), token or "")


def _link(kind: str, row: Dict[str, Any], bound: str = "") -> str:
    return f"{digest.LIVE_SITE}/api/digest/{kind}?u={row['id']}&t={make_token(kind, row['id'], bound)}"


def confirm_pending(row: Dict[str, Any], token: str) -> bool:
    """Marks the user's pending address verified when the link's token matches it."""
    p = _prefs(row)
    email = p["email"]
    if not email or not check_token("confirm", row["id"], token, email):
        return False
    db.update_user(row["id"], {"digest": dict(p, verified_email=email)})
    return True


def unsubscribe(row: Dict[str, Any]) -> None:
    db.update_user(row["id"], {"digest": dict(_prefs(row), enabled=False)})


# ----------------------------------------------------------------- content --

_e = _html.escape


def build_user_digest(stance: Dict[str, Any], data_quality: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Pure curation of strategy.stance() -- nothing recomputed."""
    rows = stance.get("rows") or []
    return {
        "as_of": stance.get("as_of"),
        "attention": [r for r in rows if r.get("attention")],
        "watch": [r for r in rows if r.get("watch")],
        "quiet": [r["symbol"] for r in rows if not r.get("attention") and not r.get("watch")],
        "macro_line": stance.get("macro_line"),
        "data_quality": data_quality or {"ok": True, "gaps": []},
        "n": len(rows),
    }


def subject_for(d: Dict[str, Any]) -> str:
    a, w = len(d["attention"]), len(d["watch"])
    if a:
        return f"GlassBox: {a} of your {d['n']} stocks need a look — {d['as_of'] or 'today'}"
    if w:
        return f"GlassBox: nothing to do, {w} on watch — {d['as_of'] or 'today'}"
    return f"GlassBox: all quiet on your {d['n']} stocks — {d['as_of'] or 'today'}"


def _stock_block(r: Dict[str, Any]) -> str:
    eng = (r.get("engine") or {}).get("signal") or "–"
    com = ((r.get("committee") or {}).get("action")) or "–"
    risk = (r.get("risk") or {}).get("level") or "–"
    head = ((r.get("committee") or {}).get("headline")) or ""
    return (
        '<tr><td style="padding:10px 0;border-bottom:1px solid #e5e7eb;font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;">'
        f'<b style="font-size:14px;">{_e(r["symbol"])}</b> <span style="color:#6b7280;">{_e(r.get("name") or "")}</span><br>'
        f'<span style="color:#374151;">engine <b>{_e(eng)}</b> &middot; committee <b>{_e(com)}</b> &middot; risk <b>{_e(risk)}</b></span><br>'
        f'{_e(r.get("summary") or "")}'
        + (f'<br><span style="color:#6b7280;font-size:12px;">Committee: {_e(head)}</span>' if head else "")
        + "</td></tr>"
    )


def _section(title: str, rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return ""
    return (
        f'<h2 style="font:700 15px/1.4 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;margin:20px 0 4px;">{title}</h2>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">'
        + "".join(_stock_block(r) for r in rows)
        + "</table>"
    )


def render_html(d: Dict[str, Any], unsubscribe_url: str) -> str:
    dq = d["data_quality"]
    warn = ""
    if not dq.get("ok"):
        warn = (
            '<div style="background:#b3261e12;border:1px solid #b3261e40;border-radius:6px;padding:10px 14px;margin:12px 0;'
            'font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;"><b style="color:#b3261e;">Data warning.</b> '
            "Some price history is incomplete today, so treat these figures with extra care.</div>"
        )
    macro = f'<p style="font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#374151;margin:12px 0 0;">{_e(d["macro_line"])}</p>' if d.get("macro_line") else ""
    quiet = (
        f'<p style="font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#6b7280;margin:16px 0 0;">Nothing to do: {_e(", ".join(d["quiet"]))}.</p>'
        if d["quiet"] else ""
    )
    nothing = (
        '<p style="font:14px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;margin:16px 0 0;">Nothing needs your attention today.</p>'
        if not d["attention"] and not d["watch"] else ""
    )
    body = warn + macro + nothing + _section("Worth a look", d["attention"]) + _section("On watch (risk is high, no action suggested)", d["watch"]) + quiet
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;">
<tr><td style="background:#0f172a;padding:20px 24px;">
  <div style="font:700 18px/1.3 -apple-system,Segoe UI,Arial,sans-serif;color:#ffffff;">GlassBox &middot; Your daily stance</div>
  <div style="font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#94a3b8;margin-top:2px;">{_e(d["as_of"] or "today")} &middot; {_e(disclaimer.text())}</div>
</td></tr>
<tr><td style="padding:8px 24px 24px;">{body}</td></tr>
<tr><td style="padding:16px 24px;background:#f9fafb;border-top:1px solid #e5e7eb;font:12px/1.6 -apple-system,Segoe UI,Arial,sans-serif;color:#6b7280;">
  <a href="{digest.LIVE_SITE}/dashboard" style="font-weight:600;color:#0f172a;">Open your dashboard →</a><br>
  {_e(disclaimer.text())} You get this because you turned on the daily digest. <a href="{_e(unsubscribe_url)}" style="color:#6b7280;">Unsubscribe</a>.
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def render_confirmation(confirm_url: str) -> str:
    return (
        '<div style="font:14px/1.6 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;max-width:520px;">'
        "<p>Someone (hopefully you) asked GlassBox to send a daily stance digest to this address.</p>"
        f'<p><a href="{_e(confirm_url)}" style="display:inline-block;background:#0f172a;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none;">Confirm this address</a></p>'
        "<p>If it wasn't you, ignore this email and nothing will be sent.</p></div>"
    )


# -------------------------------------------------------------------- send --

Sender = Callable[..., None]


def _headers(unsub_url: str) -> Dict[str, str]:
    return {"List-Unsubscribe": f"<{unsub_url}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def send_confirmation(row: Dict[str, Any], sender: Optional[Sender] = None) -> Dict[str, Any]:
    from . import flags

    if not flags.flag("output.email"):
        return {"ok": False, "detail": "email is switched off right now"}
    cfg = digest.smtp_config()
    p = _prefs(row)
    addr = p["email"]
    if not cfg or not addr:
        return {"ok": False, "detail": "email sending is not configured on this server"}
    try:
        (sender or digest.send_email)("Confirm your GlassBox digest", render_confirmation(_link("confirm", row, addr)), dict(cfg, to=addr))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not send a digest confirmation: %s", type(exc).__name__)
        return {"ok": False, "detail": f"{type(exc).__name__}"}
    return {"ok": True, "detail": f"confirmation sent to {addr}"}


def _stance_for(book, tickers: Optional[List[str]], cache: Dict[Any, Dict[str, Any]]) -> Dict[str, Any]:
    from . import strategy  # local: the strategy stack is heavy

    key = tuple(sorted(tickers)) if tickers else ()
    if key not in cache:
        cache[key] = strategy.stance(book, list(key) or None)
    return cache[key]


def send_to_user(row: Dict[str, Any], book, cfg: Dict[str, Any], cache: Dict[Any, Dict[str, Any]], sender: Optional[Sender] = None, dq: Optional[Dict[str, Any]] = None) -> str:
    """Sends one user's digest; returns the address. Raises on failure (callers record it)."""
    from . import publish

    addr = deliverable_address(row)
    if not addr:
        raise ValueError("no verified address")
    d = build_user_digest(_stance_for(book, row.get("tickers") or None, cache), dq)
    unsub = _link("unsubscribe", row)
    html = render_html(d, unsub)
    content_ref = f"user_digest:{row['id']}:{d.get('as_of', 'unknown')}"
    result = publish.publish_simple(
        channel="user_digest",
        text=html,
        content_ref=content_ref,
        is_html=True,
    )
    # In enforce mode, held emails are not sent
    if not result.allowed:
        raise ValueError("held by publish (enforce mode)")
    (sender or digest.send_email)(subject_for(d), result.text, dict(cfg, to=addr), headers=_headers(unsub))
    return addr


def send_preview(row: Dict[str, Any], book=None, sender: Optional[Sender] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The user asked for one right now (needs a verified address; rate-limited)."""
    from . import flags, publish

    if not flags.flag("output.email"):
        return {"ok": False, "detail": "email is switched off right now"}
    now = now or datetime.now(timezone.utc)
    cfg = digest.smtp_config()
    if not cfg:
        return {"ok": False, "detail": "email sending is not configured on this server"}
    p = _prefs(row)
    last = _parse(p["last_preview_at"])
    if last and now - last < PREVIEW_COOLDOWN:
        return {"ok": False, "detail": "please wait a couple of minutes before sending another preview"}
    if not _is_verified(row, p["email"] or ((row.get("email") or "").lower() if row.get("oauth_provider") else None)):
        return {"ok": False, "detail": "confirm your email address first"}
    forced = dict(row, digest=dict(p, enabled=True))  # a preview works even while the digest itself is off
    try:
        if book is None:
            from . import paper_cycle

            book = paper_cycle.load_book()
        addr = send_to_user(forced, book, cfg, {}, sender)
    except ValueError as exc:
        if "held by publish" in str(exc):
            log.info("User digest preview for %s held by publish (enforce mode)", row.get("id"))
            return {"ok": False, "detail": "held by publish (enforce mode)"}
        log.warning("could not send a digest preview: %s", type(exc).__name__)
        return {"ok": False, "detail": f"could not send: {type(exc).__name__}"}
    except Exception as exc:  # noqa: BLE001
        log.warning("could not send a digest preview: %s", type(exc).__name__)
        return {"ok": False, "detail": f"could not send: {type(exc).__name__}"}
    db.update_user(row["id"], {"digest": dict(p, last_preview_at=now.isoformat())})
    return {"ok": True, "detail": f"sent to {addr}"}


def run(book=None, sender: Optional[Sender] = None, users: Optional[List[Dict[str, Any]]] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The pipeline stage: one digest per opted-in, verified user. Never raises."""
    from . import flags

    if not flags.flag("output.email"):
        return {"ok": True, "sent": 0, "detail": "disabled: the output.email flag is off"}
    cfg = digest.smtp_config()
    if not cfg:
        return {"ok": True, "sent": 0, "detail": "not configured: set DIGEST_SMTP_USER, DIGEST_SMTP_APP_PASSWORD, DIGEST_TO_EMAIL"}
    try:
        users = db.list_users() if users is None else users
        if book is None:
            from . import paper_cycle

            book = paper_cycle.load_book()
        as_of = book.latest_date
        gaps = book.gaps()
        dq = {"ok": not gaps, "gaps": gaps}
    except Exception as exc:  # noqa: BLE001
        log.warning("user digests: could not load data: %s", exc)
        return {"ok": False, "sent": 0, "detail": f"build failed: {type(exc).__name__}: {exc}"}
    is_friday = bool(as_of) and datetime.fromisoformat(as_of).weekday() == 4
    cache: Dict[Any, Dict[str, Any]] = {}
    sent = failed = skipped = 0
    for row in users:
        p = _prefs(row)
        if not deliverable_address(row) or p["last_sent"] == as_of or (p["frequency"] == "weekly" and not is_friday):
            skipped += 1
            continue
        if sent + failed >= MAX_PER_RUN:
            log.warning("user digests: hit the per-run cap of %d; the rest wait for tomorrow", MAX_PER_RUN)
            break
        try:
            addr = send_to_user(row, book, cfg, cache, sender, dq)
            db.update_user(row["id"], {"digest": dict(p, last_sent=as_of, last_error=None)})
            from . import notifications

            notifications.note_email_sent(row["username"], addr, as_of)
            sent += 1
        except Exception as exc:  # noqa: BLE001 -- one bad recipient must not stop the rest
            failed += 1
            log.warning("user digest failed for user %s: %s", row.get("id"), type(exc).__name__)
            db.update_user(row["id"], {"digest": dict(p, last_error=f"{type(exc).__name__} on {as_of}")})
    return {"ok": failed == 0 or sent > 0, "sent": sent, "failed": failed, "skipped": skipped, "detail": f"sent {sent}, failed {failed}, skipped {skipped}"}
