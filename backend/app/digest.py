"""The daily admin digest: one HTML email summarising what the committee did today, whether the
pipeline ran cleanly, what data sources fed it, and how the paper strategies are doing.

    python -m app.scripts.send_daily_digest          # what the pipeline's own "digest_email" stage runs

Design:
  * READ ONLY, CURATED. This never computes anything new -- build_digest() curates
    strategy.overview() (the same data the admin Strategy page renders), free_data.read_status()
    and strategy.agent_leaderboard() into one page, so the email can never say something the UI
    does not already back up.
  * NEVER FATAL. Sending is a nicety: a bad SMTP config, a missing recipient or a network hiccup
    is reported in the result, never raised, so it sits safely as a non-fatal pipeline stage
    (see app/pipeline.py -- only "paper_cycle" can fail the whole run).
  * NO CREDENTIALS ASSUMED. Without DIGEST_SMTP_USER / DIGEST_SMTP_APP_PASSWORD / DIGEST_TO_EMAIL
    set, run() renders nothing and just says "not configured", exactly like the SEC/insiders
    sources when SEC_USER_AGENT is unset -- never invent or guess a credential.
"""
from __future__ import annotations

import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from . import free_data, strategy, disclaimer

log = logging.getLogger("glassbox.digest")

SMTP_HOST_DEFAULT = "smtp.gmail.com"
SMTP_PORT_DEFAULT = 587
LIVE_SITE = os.environ.get("PUBLIC_SITE_URL", "https://glassbox-portfolio-review.duckdns.org")


def smtp_config() -> Optional[Dict[str, Any]]:
    """None when the three required values are not all set -- never guess a credential."""
    user, pw, to = os.environ.get("DIGEST_SMTP_USER"), os.environ.get("DIGEST_SMTP_APP_PASSWORD"), os.environ.get("DIGEST_TO_EMAIL")
    if not (user and pw and to):
        return None
    return {"host": os.environ.get("DIGEST_SMTP_HOST", SMTP_HOST_DEFAULT), "port": int(os.environ.get("DIGEST_SMTP_PORT", SMTP_PORT_DEFAULT)), "user": user, "password": pw, "to": to}


# ---------------------------------------------------------------------- content --


def build_digest(overview: Dict[str, Any], free_status: Dict[str, Any], leaderboard: Dict[str, Any], runs_today: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure data assembly: no network, no rendering, no new computation -- see the module docstring
    for why this only curates what strategy.overview() already computed."""
    pipe = overview.get("pipeline") or {}
    stages = pipe.get("stages") or {}
    decisions = [
        {
            "symbol": r["symbol"],
            "engine_signal": r.get("engine_signal"),
            "decision": r.get("decision"),
            "action": r.get("action"),
            "consensus": (r.get("ceo") or {}).get("label"),
            "answered": r.get("answered"),
            "total": r.get("total"),
            "gated": bool(r.get("gate")),
        }
        for r in sorted(runs_today, key=lambda r: r["symbol"])
    ]
    capital = [
        {"id": a["id"], "name": a["name"], "total_return": a["total_return"], "live_return": a["live_return"], "sharpe": a["sharpe"], "max_drawdown": a["max_drawdown"], "live_days": a["live_days"]}
        for a in overview.get("capital", [])
    ]
    return {
        "date": overview.get("data_date"),
        "review_date": overview.get("latest_review_date"),
        "pipeline": {
            "status": pipe.get("status"),
            "message": pipe.get("message"),
            "stages": {name: bool(s.get("ok")) for name, s in stages.items()},
            "sync_coverage": (pipe.get("sync") or {}).get("coverage"),
        },
        "decisions": decisions,
        "data_quality": overview.get("data_quality") or {"ok": True, "gaps": []},
        "free_data": {name: {"ok": bool(s.get("ok")), "detail": s.get("detail")} for name, s in free_status.items()},
        "capital": capital,
        "ranked_agents": [a for a in leaderboard.get("agents", []) if a.get("ranked")],
        "leaderboard_note": leaderboard.get("note"),
    }


def gather(book=None) -> Dict[str, Any]:  # pragma: no cover -- thin IO wiring; build_digest() carries the real logic
    """Everything build_digest() needs, freshly read. `book`/`runner` are injectable for tests."""
    from . import db, paper_cycle  # local: avoid importing the whole paper stack at module load

    book = book or paper_cycle.load_book()
    ov = strategy.overview(book)
    runs = db.list_all_committee_runs()
    todays = [r for r in runs if r["date"] == ov.get("latest_review_date")]
    board = strategy.agent_leaderboard(book, runs)
    return build_digest(ov, free_data.read_status(), board, todays)


# --------------------------------------------------------------------- rendering --

_PCT = lambda v: "n/a" if v is None else f"{v:+.1%}"  # noqa: E731
_TONE_OK, _TONE_WARN, _TONE_BAD, _TONE_MUTE = "#1a7f5a", "#a87400", "#b3261e", "#6b7280"


def _pill(label: str, ok: Optional[bool]) -> str:
    color = _TONE_MUTE if ok is None else (_TONE_OK if ok else _TONE_BAD)
    return f'<span style="display:inline-block;padding:2px 8px;border-radius:10px;background:{color}1a;color:{color};font:600 11px/1.6 -apple-system,Segoe UI,Arial,sans-serif;">{label}</span>'


def _row(cells: List[str], header: bool = False) -> str:
    tag = "th" if header else "td"
    style = "padding:6px 10px;border-bottom:1px solid #e5e7eb;font:13px/1.4 -apple-system,Segoe UI,Arial,sans-serif;text-align:left;" + ("font-weight:600;color:#374151;" if header else "color:#111827;")
    return "<tr>" + "".join(f'<{tag} style="{style}">{c}</{tag}>' for c in cells) + "</tr>"


def _table(headers: List[str], rows: List[List[str]]) -> str:
    body = _row(headers, header=True) + "".join(_row(r) for r in rows)
    return f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;margin:8px 0 16px;">{body}</table>'


def _section(title: str, inner: str) -> str:
    return f'<h2 style="font:700 15px/1.4 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;margin:24px 0 4px;">{title}</h2>{inner}'


def render_html(d: Dict[str, Any]) -> str:
    pipe = d["pipeline"]
    pipe_rows = [[name, _pill("ok" if ok else "failed", ok)] for name, ok in pipe["stages"].items()]
    pipeline_html = _section(
        "Daily pipeline",
        f'<p style="font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#374151;margin:4px 0;">'
        f'Target <b>{d["date"] or "n/a"}</b> &middot; {_pill(pipe["status"] or "unknown", pipe["status"] == "ok")}'
        + (f' &middot; sync coverage {pipe["sync_coverage"]:.0%}' if pipe["sync_coverage"] is not None else "")
        + (f' &middot; {pipe["message"]}' if pipe.get("message") else "")
        + "</p>" + (_table(["stage", "result"], pipe_rows) if pipe_rows else ""),
    )

    dq = d["data_quality"]
    quality_html = ""
    if not dq.get("ok"):
        gaps = "; ".join(f"{g['from']} → {g['to']} ({g['days']}d)" for g in dq.get("gaps", [])[:3])
        quality_html = (
            f'<div style="background:{_TONE_BAD}12;border:1px solid {_TONE_BAD}40;border-radius:6px;padding:10px 14px;margin:10px 0;'
            f'font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#111827;"><b style="color:{_TONE_BAD};">Data quality warning.</b> {gaps}</div>'
        )

    dec_rows = [
        [r["symbol"], r["engine_signal"] or "–", r["decision"] or "–", r["consensus"] or "–", f'{r["answered"]}/{r["total"]}' + (" ⚠ gated" if r["gated"] else "")]
        for r in d["decisions"]
    ]
    decisions_html = _section(
        "Today's committee calls",
        _table(["symbol", "engine", "decision", "consensus", "answered"], dec_rows) if dec_rows else '<p style="font:13px;color:#6b7280;">No symbols reviewed today.</p>',
    )

    fd_rows = [[name, _pill("live" if s["ok"] else "off", s["ok"]), s["detail"] or ""] for name, s in sorted(d["free_data"].items())]
    signals_html = _section("Signals captured today", _table(["source", "status", "detail"], fd_rows))

    cap_rows = [[c["name"], _PCT(c["total_return"]), _PCT(c["live_return"]), f'{c["sharpe"]:.2f}' if c["sharpe"] is not None else "n/a", _PCT(c["max_drawdown"]), str(c["live_days"])] for c in d["capital"]]
    capital_html = _section("Portfolio snapshot", _table(["account", "total return", "live return", "sharpe", "max drawdown", "live days"], cap_rows))

    if d["ranked_agents"]:
        board_rows = [[a["agent"], f'{a["hit_rate"]:.0%}' if a["hit_rate"] is not None else "n/a", _PCT(a["mean_edge"]), str(a["directional_calls"])] for a in d["ranked_agents"]]
        board_html = _table(["agent", "hit rate", "mean edge", "scored calls"], board_rows)
    else:
        board_html = f'<p style="font:13px;color:#6b7280;">{d.get("leaderboard_note") or "No agent has enough scored calls yet."}</p>'
    board_html = _section("Agent leaderboard", board_html)

    body = pipeline_html + quality_html + decisions_html + signals_html + capital_html + board_html
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:24px 0;">
<tr><td align="center">
<table role="presentation" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%;background:#ffffff;border-radius:8px;overflow:hidden;">
<tr><td style="background:#0f172a;padding:20px 24px;">
  <div style="font:700 18px/1.3 -apple-system,Segoe UI,Arial,sans-serif;color:#ffffff;">GlassBox &middot; Daily Committee Digest</div>
  <div style="font:13px/1.5 -apple-system,Segoe UI,Arial,sans-serif;color:#94a3b8;margin-top:2px;">{d["date"] or "n/a"} &middot; {html.escape(disclaimer.text())}</div>
</td></tr>
<tr><td style="padding:8px 24px 24px;">{body}</td></tr>
<tr><td style="padding:16px 24px;background:#f9fafb;border-top:1px solid #e5e7eb;">
  <a href="{LIVE_SITE}/admin/strategy" style="font:600 13px -apple-system,Segoe UI,Arial,sans-serif;color:#0f172a;">Open the Strategy page →</a>
</td></tr>
</table>
</td></tr>
</table>
</body></html>"""


# ------------------------------------------------------------------------- send --


def send_email(subject: str, html: str, cfg: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, cfg["user"], cfg["to"]
    for k, v in (headers or {}).items():
        msg[k] = v
    msg.attach(MIMEText("This report needs an HTML-capable mail client.", "plain"))
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=30) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())


def run(now=None, book=None, runner=None) -> Dict[str, Any]:
    """The pipeline stage entry point. Never raises: a bad SMTP config or a rendering surprise is
    reported, not fatal (see the module docstring)."""
    from . import flags, publish

    if not flags.flag("output.email"):
        return {"ok": True, "sent": False, "detail": "disabled: the output.email flag is off"}
    try:
        d = gather(book=book)
    except Exception as exc:  # noqa: BLE001 -- a report failing to build must not fail the pipeline
        log.warning("could not build the daily digest: %s", exc)
        return {"ok": False, "detail": f"build failed: {type(exc).__name__}: {exc}"}
    cfg = smtp_config()
    if not cfg:
        # Not configured is not a failure -- same convention as SEC/insiders without SEC_USER_AGENT:
        # an optional, not-yet-turned-on extra must never mark the whole pipeline day "partial".
        return {"ok": True, "sent": False, "detail": "not configured: set DIGEST_SMTP_USER, DIGEST_SMTP_APP_PASSWORD, DIGEST_TO_EMAIL"}
    try:
        html = render_html(d)
        content_ref = f"admin_digest:{d.get('date', 'unknown')}"
        # Publish through the single exit (admin digest is HTML)
        result = publish.publish_simple(
            channel="admin_digest",
            text=html,
            content_ref=content_ref,
            is_html=True,
        )
        # In enforce mode, held emails are not sent
        if not result.allowed:
            log.info("Admin digest for %s held by publish (enforce mode)", d.get('date', 'unknown'))
            return {"ok": True, "sent": False, "detail": "held by publish (enforce mode)"}
        (runner or send_email)(f"GlassBox digest — {d['date'] or 'today'}", result.text, cfg)
    except Exception as exc:  # noqa: BLE001 -- sending is a nicety; never let it fail the pipeline
        log.warning("could not send the daily digest: %s", exc)
        return {"ok": False, "sent": False, "detail": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "sent": True, "detail": f"sent to {cfg['to']}"}
