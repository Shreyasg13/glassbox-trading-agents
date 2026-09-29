"""SQLAlchemy Core storage for the Agent Factory (Phase 4).

SQLite by default (file at backend/app/glassbox.db, or GLASSBOX_DB_PATH).
Set DATABASE_URL directly (e.g. Neon's "postgresql+psycopg://..." connection
string) to run against Postgres instead -- plain SQLAlchemy Core throughout,
JSON stored as text columns rather than SQLite-specific JSON1 functions, so
no query changes are needed either way. connect_args differs per dialect:
SQLite's check_same_thread=False lets the same connection be reused across
the threadpool FastAPI dispatches sync endpoints on; Postgres needs no such
override and Neon requires TLS, so sslmode=require is added if the caller's
DATABASE_URL didn't already specify one.

pool_pre_ping=True on the Postgres branch is not a defensive guess -- it
fixes a real failure hit during the first live Google OAuth test after
this app's Neon cutover: the backend sat idle for a few minutes, Neon's
pooled (PgBouncer) endpoint silently closed the backend connection on
its side, and the next query through SQLAlchemy's pool (which still
considered that connection valid) failed with
`psycopg.OperationalError: consuming input failed: SSL connection has
been closed unexpectedly` -- a 500 on an otherwise-correct request, not
an OAuth bug. pre_ping issues a cheap liveness check before handing out
a pooled connection and transparently reconnects on failure instead of
surfacing it to the caller -- SQLAlchemy's own documented fix for
exactly this class of problem (docs.sqlalchemy.org/en/20/core/pooling.html
-> "Disconnect Handling - Pessimistic"). pool_recycle=280 additionally
retires any connection older than that outright, comfortably under
Neon's own pooled-connection idle window, as a second line of defense
pre_ping alone doesn't cover (a connection can go stale between pings).
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import Column, Float, Index, Integer, String, Boolean, create_engine, MetaData, Table, select, delete, update, insert, func
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

log = logging.getLogger("glassbox.db")

from .migrated_tables import verification_results_table, claims_table, ledger_calls_table, committee_narratives_table, source_snapshots_table, quarantine_items_table

DB_PATH = Path(os.environ.get("GLASSBOX_DB_PATH", str(Path(__file__).parent / "glassbox.db")))
_env_url = os.environ.get("DATABASE_URL")
DATABASE_URL = _env_url if _env_url else f"sqlite:///{DB_PATH}"


def build_engine(database_url: str):
    """Extracted from module scope so the dialect-branching logic can be
    exercised directly in tests (app/db.py's engine otherwise binds at
    import time -- see test_auth.py's docstring -- so this is the one
    piece of that logic worth making independently testable)."""
    if database_url.startswith("sqlite"):
        return create_engine(database_url, connect_args={"check_same_thread": False})
    url = make_url(database_url)
    if "sslmode" not in url.query:
        url = url.update_query_dict({"sslmode": "require"})
    return create_engine(url, pool_pre_ping=True, pool_recycle=280)


engine = build_engine(DATABASE_URL)
metadata = MetaData()

agents_table = Table(
    "agents",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded AgentConfig
)

orchestrations_table = Table(
    "orchestrations",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded OrchestrationConfig
)

jobs_table = Table(
    "jobs",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded job dict (see jobs.py)
)

llm_calls_table = Table(
    "llm_calls",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded LLMCallLog
)

report_narratives_table = Table(
    "report_narratives",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded DailyReportNarrative
)

audit_log_table = Table(
    "audit_log",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded AuditLogEntry
)

users_table = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded: username, username_lower, password_hash, role, created_at
)

# ---- First-party, cookie-free visit analytics (app/analytics.py) ----
# One row per page view. Real columns (not a JSON blob) so the admin summary can GROUP BY in SQL.
# NO IP address and NO user id is stored: `visitor` is a hash that rotates every day, so it can count
# unique visitors within a day but can never follow a person from one day to the next.
page_views_table = Table(
    "page_views",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("day", String, nullable=False),  # YYYY-MM-DD (UTC)
    Column("ts", String, nullable=False),  # ISO timestamp (UTC)
    Column("path", String, nullable=False),
    Column("visitor", String, nullable=False),
    Column("source", String, nullable=False, default=""),  # normalised referrer or utm_source: linkedin, github, google, direct...
    Column("medium", String, nullable=False, default=""),
    Column("campaign", String, nullable=False, default=""),
    Column("device", String, nullable=False, default=""),  # mobile | desktop | tablet
    Index("ix_page_views_day", "day"),
)

# ---- User inbox, assistant history and feedback (app/notifications.py, app/assistant.py, app/feedback.py) ----
# Real columns (not JSON blobs): the inbox is read per user and the admin summaries GROUP BY.
notifications_table = Table(
    "notifications",
    metadata,
    Column("id", String, primary_key=True),
    Column("user", String, nullable=False),  # lowercased username
    Column("dedupe_key", String, nullable=False),  # (user, dedupe_key) is unique: re-running a job never duplicates a notification
    Column("day", String, nullable=False),  # the trading day it is about (YYYY-MM-DD)
    Column("kind", String, nullable=False),  # signal | alert | report | email
    Column("severity", String, nullable=False, default="info"),  # info | watch | attention
    Column("title", String, nullable=False),
    Column("body", String, nullable=False, default=""),
    Column("link", String, nullable=False, default=""),
    Column("read", Boolean, nullable=False, default=False),
    Column("created_at", String, nullable=False),
    Index("ix_notifications_user", "user", "created_at"),
    Index("uq_notifications_dedupe", "user", "dedupe_key", unique=True),
)

qa_messages_table = Table(
    "qa_messages",
    metadata,
    Column("id", String, primary_key=True),
    Column("user", String, nullable=False),
    Column("day", String, nullable=False),  # UTC day it was asked (for the daily allowance)
    Column("created_at", String, nullable=False),
    Column("as_of", String, nullable=False, default=""),  # the trading day the question was about
    Column("question", String, nullable=False),
    Column("answer", String, nullable=False),
    Column("used_llm", Boolean, nullable=False, default=False),
    Column("model", String, nullable=False, default=""),
    Index("ix_qa_messages_user_day", "user", "day"),
)

feedback_table = Table(
    "feedback",
    metadata,
    Column("id", String, primary_key=True),
    Column("user", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("target_type", String, nullable=False),  # signal | answer | notification | committee | general
    Column("target_ref", String, nullable=False, default=""),  # e.g. "AAPL|2026-09-24", a qa message id, a notification id
    Column("symbol", String, nullable=False, default=""),
    Column("rating", Integer, nullable=False, default=0),  # -1 unhelpful, 0 none (comment only), 1 helpful
    Column("comment", String, nullable=False, default=""),
    Index("ix_feedback_created", "created_at"),
)

# ---- Paper trading (app/paper.py, app/paper_cycle.py) ----
# One row per simulated account: its whole equity curve and trade list live in
# the JSON blob (a few hundred KB at most over a decade), so a daily cycle is
# one read + one write per account instead of thousands of tiny inserts.
paper_accounts_table = Table(
    "paper_accounts",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),  # JSON-encoded account dict (see paper.new_account)
)

# One row per live trading day: the engine's signal for every symbol that day,
# kept as an auditable record of what was actually recommended.
paper_signals_table = Table(
    "paper_signals",
    metadata,
    Column("id", String, primary_key=True),  # "signals:YYYY-MM-DD"
    Column("config", String, nullable=False),
)

# Single row (id "meta"): live_from date, last run, bootstrap info.
paper_meta_table = Table(
    "paper_meta",
    metadata,
    Column("id", String, primary_key=True),
    Column("config", String, nullable=False),
)

# ---- Daily Investment Committee decisions (app/committee_daily.py) ----
# One row per (date, symbol): the decision, the votes, each agent's lean and a
# short summary, and the evidence it was based on -- so the committee's calls can
# be scored against what the market then did.
committee_runs_table = Table(
    "committee_runs",
    metadata,
    Column("id", String, primary_key=True),  # "YYYY-MM-DD:SYMBOL"
    Column("config", String, nullable=False),
)


# ---- Canonical market data (app/price_store.py) ----
# One row per (symbol, date): the RAW OHLCV as fetched. Everything else (RSI, moving averages, signals, risk,
# the matrices in app/panel.py) is derived from these rows, so the database -- not one VM's disk -- is the
# source of truth, updates are delta upserts (only new or corrected bars are written), and the parquet files
# the rest of the app reads are just a rebuildable cache.
price_bars_table = Table(
    "price_bars",
    metadata,
    Column("symbol", String, primary_key=True),
    Column("d", String, primary_key=True),  # 'YYYY-MM-DD', the trading date in New York
    Column("open", Float),
    Column("high", Float),
    Column("low", Float),
    Column("close", Float, nullable=False),
    Column("volume", Float),
    Column("dividends", Float),
    Column("splits", Float),
    Column("updated_at", String),
)

# Small text artifacts that used to live only on the VM disk (trained engine parameters, the free-data cache),
# mirrored here so a rebuilt VM restores itself. Content-hashed: unchanged files are never rewritten.
blobs_table = Table(
    "blobs",
    metadata,
    Column("name", String, primary_key=True),  # relative path, e.g. 'training_results/trained_params.json'
    Column("sha", String),
    Column("content", String, nullable=False),
    Column("updated_at", String),
)

# What the system believed on each trading day, frozen when it ran: per symbol the engine signal, risk, committee
# call and prices, plus market-wide context. Point in time, so a later data correction cannot rewrite history.
snapshots_table = Table(
    "daily_snapshots",
    metadata,
    Column("id", String, primary_key=True),  # the date
    Column("config", String, nullable=False),
)


def init_schema(attempts: int = 5) -> None:
    """Create any missing tables, tolerating a crowded first boot.

    gunicorn starts several workers and each imports this module at the same instant.
    On the first boot after a release adds a table, they can all try to CREATE it at
    once and the losers raise (a duplicate-relation error on Postgres); a brief hiccup
    reaching the database at that moment fails the import the same way, and a worker
    that cannot import kills its container (exit 3, "worker failed to boot"). Trying
    again is safe -- create_all skips tables that now exist -- so retry a few times
    with a short backoff (well inside the container's 40 s health start period) and
    log each retry so the cause is visible instead of a bare crash."""
    for attempt in range(1, attempts + 1):
        try:
            metadata.create_all(engine)
            return
        except SQLAlchemyError as exc:
            if attempt == attempts:
                raise
            delay = 0.5 * attempt
            log.warning("schema init attempt %d/%d failed (%s); retrying in %.1fs", attempt, attempts, type(exc).__name__, delay)
            time.sleep(delay)


init_schema()


def _list(table: Table) -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(select(table)).fetchall()
        return [json.loads(row.config) for row in rows]


def _get(table: Table, item_id: str) -> Optional[Dict[str, Any]]:
    with engine.connect() as conn:
        row = conn.execute(select(table).where(table.c.id == item_id)).fetchone()
        return json.loads(row.config) if row else None


def _create(table: Table, data: Dict[str, Any]) -> Dict[str, Any]:
    new_id = data.get("id") or str(uuid.uuid4())
    data["id"] = new_id
    with engine.begin() as conn:
        conn.execute(insert(table).values(id=new_id, config=json.dumps(data)))
    return data


def _update(table: Table, item_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data["id"] = item_id
    with engine.begin() as conn:
        result = conn.execute(update(table).where(table.c.id == item_id).values(config=json.dumps(data)))
        if result.rowcount == 0:
            return None
    return data


def _delete(table: Table, item_id: str) -> bool:
    with engine.begin() as conn:
        result = conn.execute(delete(table).where(table.c.id == item_id))
        return result.rowcount > 0


def list_agents() -> List[Dict[str, Any]]:
    return _list(agents_table)


def get_agent(agent_id: str) -> Optional[Dict[str, Any]]:
    return _get(agents_table, agent_id)


def create_agent(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(agents_table, data)


def update_agent(agent_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update(agents_table, agent_id, data)


def delete_agent(agent_id: str) -> bool:
    return _delete(agents_table, agent_id)


def list_orchestrations() -> List[Dict[str, Any]]:
    return _list(orchestrations_table)


def get_orchestration(orch_id: str) -> Optional[Dict[str, Any]]:
    return _get(orchestrations_table, orch_id)


def create_orchestration(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(orchestrations_table, data)


def update_orchestration(orch_id: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return _update(orchestrations_table, orch_id, data)


def delete_orchestration(orch_id: str) -> bool:
    return _delete(orchestrations_table, orch_id)


# ---- Jobs (Phase 4/5 background runs) ----

def create_job(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(jobs_table, data)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _get(jobs_table, job_id)


def update_job(job_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merge `patch` over the existing row so partial updates (e.g. just
    `status`) don't clobber fields written earlier (e.g. `created_at`)."""
    existing = get_job(job_id) or {}
    existing.update(patch)
    return _update(jobs_table, job_id, existing)


# ---- LLM call log (Phase 5 cost/latency observability) ----

def create_llm_call(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(llm_calls_table, data)


def update_llm_call(call_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    existing = _get(llm_calls_table, call_id) or {}
    existing.update(patch)
    return _update(llm_calls_table, call_id, existing)


def list_llm_calls() -> List[Dict[str, Any]]:
    return _list(llm_calls_table)


def get_agent_performance() -> List[Dict[str, Any]]:
    """Real per-agent call stats aggregated from llm_calls -- replaces
    an earlier hardcoded 3-agent mock at this same call site
    (data_source.AGENT_PERFORMANCE). Deliberately only exposes call
    volume/reliability/latency, not a trading win-rate: this system
    doesn't link a past signal to its later real-world outcome, so a
    "win_rate" field would either be fabricated or require a genuinely
    separate feature (see docs/PROJECT_STATUS.md's roadmap) -- reporting
    a number we don't actually have would be worse than not having it.

    Public endpoint (routers/data.py, no auth) -- deliberately excludes
    estimated_cost_usd and error detail, which stay admin-only via the
    existing /api/admin/llm-calls, so this stays safe to expose without
    leaking real operational cost/error internals to unauthenticated
    visitors.
    """
    agent_names = {a["id"]: a.get("name", a["id"]) for a in _list(agents_table)}
    calls = _list(llm_calls_table)

    by_agent: Dict[str, List[Dict[str, Any]]] = {}
    for call in calls:
        agent_id = call.get("agent_id")
        if not agent_id:
            continue
        by_agent.setdefault(agent_id, []).append(call)

    results = []
    for agent_id, agent_calls in by_agent.items():
        total = len(agent_calls)
        ok_count = sum(1 for c in agent_calls if c.get("status") == "ok")
        durations = [c["duration_ms"] for c in agent_calls if c.get("duration_ms") is not None]
        started_ats = [c["started_at"] for c in agent_calls if c.get("started_at")]
        results.append(
            {
                "name": agent_names.get(agent_id, agent_id),
                "call_count": total,
                "success_rate": round(100 * ok_count / total, 1) if total else 0.0,
                "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
                "last_active_at": max(started_ats) if started_ats else None,
            }
        )
    results.sort(key=lambda r: r["call_count"], reverse=True)
    return results


# ---- Daily report narratives (Phase 5) ----

def create_report_narrative(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(report_narratives_table, data)


def get_report_narrative(narrative_id: str) -> Optional[Dict[str, Any]]:
    return _get(report_narratives_table, narrative_id)


def list_report_narratives() -> List[Dict[str, Any]]:
    return _list(report_narratives_table)


# ---- Paper trading ----

def list_paper_accounts() -> List[Dict[str, Any]]:
    return _list(paper_accounts_table)


def get_paper_account(account_id: str) -> Optional[Dict[str, Any]]:
    return _get(paper_accounts_table, account_id)


def save_paper_account(account: Dict[str, Any]) -> Dict[str, Any]:
    """Upsert by the account's own stable id (e.g. "profile:demo_growth")."""
    if _update(paper_accounts_table, account["id"], account) is None:
        return _create(paper_accounts_table, account)
    return account


def get_paper_meta() -> Optional[Dict[str, Any]]:
    return _get(paper_meta_table, "meta")


def save_paper_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    if _update(paper_meta_table, "meta", meta) is None:
        return _create(paper_meta_table, meta)
    return meta


def save_paper_signals(date: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc, id=f"signals:{date}", date=date)
    if _update(paper_signals_table, doc["id"], doc) is None:
        return _create(paper_signals_table, doc)
    return doc


def list_paper_signals(limit: int = 30) -> List[Dict[str, Any]]:
    items = _list(paper_signals_table)
    items.sort(key=lambda x: x.get("date", ""), reverse=True)
    return items[:limit]


# ---- Committee runs ----

def save_committee_run(doc: Dict[str, Any]) -> Dict[str, Any]:
    if _update(committee_runs_table, doc["id"], doc) is None:
        return _create(committee_runs_table, doc)
    return doc


# The single-flight lock lives in this table too, as one reserved row. It has to be in the
# database, not a module global: gunicorn runs several workers and the cron job is a separate
# process, so an in-memory flag only ever guards a single one of them.
COMMITTEE_LOCK_ID = "__lock__"


COMMITTEE_ASK_PREFIX = "ask:"  # admin sandbox questions live in this table too, but are not decisions
CHALLENGER_PREFIX = "chal:"  # so do outside challengers' daily calls (the arena): never counted as OUR committee's decisions


def _committee_rows() -> List[Dict[str, Any]]:
    return [
        r for r in _list(committee_runs_table)
        if r.get("id") != COMMITTEE_LOCK_ID and not str(r.get("id", "")).startswith((COMMITTEE_ASK_PREFIX, CHALLENGER_PREFIX))
    ]


def save_challenger_decision(source: str, d: str, symbol: str, action: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One row per (challenger, date, symbol); recording again for the same key replaces it."""
    doc = {"id": f"{CHALLENGER_PREFIX}{source}:{d}:{symbol}", "source": source, "date": d, "symbol": symbol, "action": action, "meta": meta or {}, "created_at": datetime.now(timezone.utc).isoformat()}
    if _update(committee_runs_table, doc["id"], doc) is None:
        return _create(committee_runs_table, doc)
    return doc


def list_challenger_decisions(source: Optional[str] = None) -> List[Dict[str, Any]]:
    rows = [r for r in _list(committee_runs_table) if str(r.get("id", "")).startswith(CHALLENGER_PREFIX)]
    return [r for r in rows if source is None or r.get("source") == source]


def save_committee_ask(doc: Dict[str, Any]) -> Dict[str, Any]:
    if _update(committee_runs_table, doc["id"], doc) is None:
        return _create(committee_runs_table, doc)
    return doc


def get_committee_ask(ask_id: str) -> Optional[Dict[str, Any]]:
    return _get(committee_runs_table, ask_id) if ask_id.startswith(COMMITTEE_ASK_PREFIX) else None


def list_committee_asks(limit: int = 15) -> List[Dict[str, Any]]:
    items = [r for r in _list(committee_runs_table) if str(r.get("id", "")).startswith(COMMITTEE_ASK_PREFIX)]
    items.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return items[:limit]


def acquire_committee_lock(owner: str, ttl_s: float, now: Optional[float] = None) -> bool:
    """Take the committee lock; False if another live run holds it.

    The INSERT is the atomic step (primary key), so two callers can never both win. A lock
    older than ttl_s is treated as abandoned (the holder crashed or was killed mid-run) and
    taken over with a compare-and-swap on the exact stored row, so two callers noticing the
    same stale lock cannot both take it."""
    now = time.time() if now is None else now
    blob = json.dumps({"id": COMMITTEE_LOCK_ID, "owner": owner, "acquired_at": now})
    for _ in range(2):
        try:
            with engine.begin() as conn:
                conn.execute(insert(committee_runs_table).values(id=COMMITTEE_LOCK_ID, config=blob))
            return True
        except IntegrityError:
            pass
        with engine.begin() as conn:
            row = conn.execute(select(committee_runs_table).where(committee_runs_table.c.id == COMMITTEE_LOCK_ID)).fetchone()
            if row is None:  # released between our insert and this read: try the insert again
                continue
            if now - float(json.loads(row.config).get("acquired_at", 0)) < ttl_s:
                return False
            taken = conn.execute(
                update(committee_runs_table)
                .where(committee_runs_table.c.id == COMMITTEE_LOCK_ID, committee_runs_table.c.config == row.config)
                .values(config=blob)
            )
            return taken.rowcount == 1
    return False


def release_committee_lock(owner: str) -> None:
    """Drop the lock, but only if we still hold it (a stale one may have been taken over)."""
    with engine.begin() as conn:
        row = conn.execute(select(committee_runs_table).where(committee_runs_table.c.id == COMMITTEE_LOCK_ID)).fetchone()
        if row is not None and json.loads(row.config).get("owner") == owner:
            conn.execute(delete(committee_runs_table).where(committee_runs_table.c.id == COMMITTEE_LOCK_ID, committee_runs_table.c.config == row.config))


def committee_lock_active(ttl_s: float, now: Optional[float] = None) -> bool:
    now = time.time() if now is None else now
    held = _get(committee_runs_table, COMMITTEE_LOCK_ID)
    return held is not None and now - float(held.get("acquired_at", 0)) < ttl_s


def list_committee_runs_for_date(d: str) -> List[Dict[str, Any]]:
    return sorted((r for r in _committee_rows() if r.get("date") == d), key=lambda r: r.get("symbol", ""))


def list_committee_runs(limit: int = 60) -> List[Dict[str, Any]]:
    items = _committee_rows()
    items.sort(key=lambda r: (r.get("date", ""), r.get("symbol", "")), reverse=True)
    return items[:limit]


def list_all_committee_runs() -> List[Dict[str, Any]]:
    return _committee_rows()


def get_committee_run(run_id: str) -> Optional[Dict[str, Any]]:
    """Get a single committee run by its ID (e.g. '2024-04-23:AAPL')."""
    with engine.connect() as conn:
        row = conn.execute(select(committee_runs_table).where(committee_runs_table.c.id == run_id)).fetchone()
    return json.loads(row.config) if row else None


# ---- Market data, artifacts and snapshots ----

def _upsert_stmt(table: Table, rows: List[Dict[str, Any]]):
    if engine.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    return dialect_insert(table).values(rows)


_BAR_COLS = ("open", "high", "low", "close", "volume", "dividends", "splits", "updated_at")


def upsert_price_bars(rows: List[Dict[str, Any]]) -> int:
    """Insert new bars and overwrite corrected ones, in one transaction. Idempotent: the same rows twice change nothing."""
    if not rows:
        return 0
    with engine.begin() as conn:
        for i in range(0, len(rows), 400):
            stmt = _upsert_stmt(price_bars_table, rows[i: i + 400])
            conn.execute(stmt.on_conflict_do_update(index_elements=["symbol", "d"], set_={c: getattr(stmt.excluded, c) for c in _BAR_COLS}))
    return len(rows)


def price_bars_summary() -> Dict[str, Dict[str, Any]]:
    """symbol -> {first, last, count}: what is stored, without reading a single bar."""
    t = price_bars_table
    with engine.connect() as conn:
        rows = conn.execute(select(t.c.symbol, func.min(t.c.d), func.max(t.c.d), func.count()).group_by(t.c.symbol)).fetchall()
    return {r[0]: {"first": r[1], "last": r[2], "count": int(r[3])} for r in rows}


def load_price_bars(symbol: Optional[str] = None, since: Optional[str] = None) -> List[Dict[str, Any]]:
    t = price_bars_table
    q = select(t).order_by(t.c.symbol, t.c.d)
    if symbol:
        q = q.where(t.c.symbol == symbol)
    if since:
        q = q.where(t.c.d >= since)
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(q).fetchall()]


def put_blob(name: str, content: str, sha: str, updated_at: str) -> None:
    row = {"name": name, "sha": sha, "content": content, "updated_at": updated_at}
    with engine.begin() as conn:
        if conn.execute(update(blobs_table).where(blobs_table.c.name == name).values(**{k: v for k, v in row.items() if k != "name"})).rowcount == 0:
            conn.execute(insert(blobs_table).values(**row))


def get_blob(name: str) -> Optional[Dict[str, Any]]:
    with engine.connect() as conn:
        r = conn.execute(select(blobs_table).where(blobs_table.c.name == name)).fetchone()
        return dict(r._mapping) if r else None


def list_blob_meta() -> Dict[str, str]:
    with engine.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(select(blobs_table.c.name, blobs_table.c.sha)).fetchall()}


def save_snapshot(d: str, doc: Dict[str, Any]) -> Dict[str, Any]:
    doc = dict(doc, id=d, date=d)
    if _update(snapshots_table, d, doc) is None:
        return _create(snapshots_table, doc)
    return doc


def list_snapshots(limit: int = 400) -> List[Dict[str, Any]]:
    items = _list(snapshots_table)
    items.sort(key=lambda x: x.get("date", ""))
    return items[-limit:]


def get_snapshot(d: str) -> Optional[Dict[str, Any]]:
    return _get(snapshots_table, d)


# ---- Pagination + audit log (Phase 6) ----
#
# Tables here store one JSON blob per row rather than typed columns (the
# pattern already used throughout this file), so "order by timestamp,
# then page" is done in Python rather than pushed to SQL. At this
# deployment's scale (a single dev SQLite file) that's simpler and more
# obviously correct than a partial ORDER BY over a JSON column.

def _list_paginated_sorted(table: Table, limit: int, offset: int, sort_key: str) -> Tuple[List[Dict[str, Any]], int]:
    items = _list(table)
    items.sort(key=lambda x: x.get(sort_key, ""), reverse=True)
    total = len(items)
    return items[offset : offset + limit], total


def log_audit(
    actor: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    entry = {
        "id": str(uuid.uuid4()),
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "detail": detail or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return _create(audit_log_table, entry)


def list_audit_log(limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    return _list_paginated_sorted(audit_log_table, limit, offset, "created_at")


def list_llm_calls_page(limit: int = 50, offset: int = 0) -> Tuple[List[Dict[str, Any]], int]:
    return _list_paginated_sorted(llm_calls_table, limit, offset, "started_at")


# ---- Users (real signup/login accounts, distinct from the hardcoded dev
# accounts in app/auth.py's _DEV_USERS -- see that module for how the two
# are reconciled at authentication time) ----

def list_users() -> List[Dict[str, Any]]:
    return _list(users_table)


def create_user(data: Dict[str, Any]) -> Dict[str, Any]:
    return _create(users_table, data)


def update_user(user_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Merges `patch` into the existing row (same read-modify-write
    pattern as update_llm_call) -- _update() itself replaces the whole
    JSON blob, so the full existing row must be read first or a partial
    patch would silently delete every field it didn't mention."""
    existing = _get(users_table, user_id)
    if existing is None:
        return None
    existing.update(patch)
    return _update(users_table, user_id, existing)


def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    """Case-insensitive lookup -- every row's config carries a
    pre-lowercased `username_lower` field precisely so this can be a
    linear scan without needing a second indexed column at the SQL layer
    (consistent with this file's existing JSON-blob-per-row pattern)."""
    target = username.strip().lower()
    for row in _list(users_table):
        if row.get("username_lower") == target:
            return row
    return None


def get_user_by_oauth(provider: str, subject: str) -> Optional[Dict[str, Any]]:
    """Identity for OAuth accounts is keyed on (provider, subject) --
    the provider's own stable user id -- not on email/username, since
    those can change; see auth.oauth_login for how a username is picked
    on first login."""
    for row in _list(users_table):
        if row.get("oauth_provider") == provider and row.get("oauth_subject") == subject:
            return row
    return None
