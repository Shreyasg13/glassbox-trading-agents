"""Export archived gate inputs without network calls or database writes.

Run with the backend on PYTHONPATH. The gate's run_time is the run's stored `run_time` if present, else its
`created_at` (what production's publish path passes to the gate; production rows store no run_time). created_at is
written after the daily batch starts, so point-in-time checks can be slightly more lenient than in the daily job; the
report counts which source each run used. Runs with neither are skipped.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import FunctionType, SimpleNamespace

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import make_url


def readonly_engine(db_url):
    url = make_url(db_url)
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            raise ValueError("An existing SQLite file is required")
        database = url.database if url.database.startswith("file:") else "file:" + Path(url.database).resolve().as_posix()
        url = url.set(database=database).update_query_dict({"mode": "ro", "uri": "true"})
    elif url.get_backend_name() != "postgresql":
        raise ValueError("Only SQLite and PostgreSQL are supported")
    engine = create_engine(url)

    @event.listens_for(engine, "begin")
    def read_only(conn):
        conn.exec_driver_sql("PRAGMA query_only=ON" if engine.dialect.name == "sqlite"
                             else "SET TRANSACTION READ ONLY")

    return engine


def rebuild_inputs(conn, run_id, run_time, book):
    """Reuse the production assembler, stopping at its pure gate boundary.

    run_gate has no separate input builder. A private globals mapping binds
    only its DB and gate dependencies; production module globals stay intact.
    The capture exception exits before the runner's result persistence code.
    """
    from app.verification import runner

    class Captured(Exception):
        def __init__(self, inputs):
            self.inputs = inputs

    def capture(inputs):
        raise Captured(inputs)

    @contextmanager
    def connect():
        yield conn

    namespace = dict(runner.run_gate.__globals__,
                     db=SimpleNamespace(engine=SimpleNamespace(connect=connect)),
                     gate=SimpleNamespace(verify_run=capture))
    assemble = FunctionType(runner.run_gate.__code__, namespace,
                            runner.run_gate.__name__, runner.run_gate.__defaults__)
    try:
        asyncio.run(assemble(run_id, run_time, book))
    except Captured as captured:
        inputs = captured.inputs
    else:
        raise ValueError("missing claims")
    for claim in inputs["claims"]:
        if claim.get("source_snapshot_id") and claim["id"] not in inputs["snapshots_by_claim_id"]:
            raise ValueError("missing snapshot")
    if not inputs["prices"].get(inputs["run_date"]):
        raise ValueError("missing price on run date")
    if any(c["source"] == "risk" for c in inputs["claims"]) and inputs["recomputed_risk"] is None:
        raise ValueError("missing risk history")
    return inputs


def export_runs(db_url, cutoff, out_dir):
    # app.db initializes its legacy schema on first import. Keep that import
    # on a disposable in-memory database, never the source or default DB.
    previous_url = os.environ.get("DATABASE_URL")
    if "app.db" not in sys.modules:
        os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    try:
        from app import db, paper
        from .inject import gate_status
    finally:
        if previous_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_url

    cutoff = date.fromisoformat(cutoff)
    out = Path(out_dir)
    # A used directory would keep runs this export skipped or excluded (load_runs reads every file).
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} is not empty; export into a fresh directory")
    out.mkdir(parents=True, exist_ok=True)
    engine = readonly_engine(db_url)
    report = {"exported": 0, "skipped": 0, "reasons": {}, "run_time_source": {"run_time": 0, "created_at": 0}}
    try:
        with engine.connect() as conn:
            book = paper.PriceBook()
            for row in conn.execute(select(db.price_bars_table).order_by(db.price_bars_table.c.d)):
                book.close.setdefault(row.symbol, {})[row.d] = row.close
            book._sorted_dates = {sym: sorted(prices) for sym, prices in book.close.items()}
            for row in conn.execute(select(db.committee_runs_table).order_by(db.committee_runs_table.c.id)):
                if row.id == db.COMMITTEE_LOCK_ID or row.id.startswith((db.COMMITTEE_ASK_PREFIX, db.CHALLENGER_PREFIX)):
                    continue
                try:
                    run_date, symbol = row.id.split(":", 1)
                    if date.fromisoformat(run_date) >= cutoff:
                        continue
                    doc = json.loads(row.config)
                    source = "run_time" if doc.get("run_time") else "created_at" if doc.get("created_at") else None
                    if source is None:
                        raise ValueError("missing run_time and created_at")
                    datetime.fromisoformat(doc[source].replace("Z", "+00:00"))
                    inputs = rebuild_inputs(conn, row.id, doc[source], book)
                    if gate_status(inputs) == "fail":
                        raise ValueError("unmodified gate failed")
                    # Colons are illegal in Windows filenames; run_id remains intact in JSON.
                    filename = row.id.replace(":", "_")
                    if Path(filename).name != filename or "/" in filename or "\\" in filename:
                        raise ValueError("unsafe run_id")
                    (out / f"{filename}.json").write_text(
                        json.dumps(dict(inputs, run_id=row.id, symbol=symbol), sort_keys=True, indent=2) + "\n",
                        encoding="utf-8")
                    report["exported"] += 1
                    report["run_time_source"][source] += 1
                except (ValueError, KeyError, TypeError) as exc:
                    report["skipped"] += 1
                    report["reasons"][row.id] = str(exc)
    finally:
        engine.dispose()
    (out / "export_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_url")
    parser.add_argument("cutoff")
    parser.add_argument("out_dir")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    export_runs(args.db_url, args.cutoff, args.out_dir)


if __name__ == "__main__":
    main()
