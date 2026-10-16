"""R2 G3: export the verify_run inputs of every committee run that has claims (read-only).

Runs inside the production backend container: `python /tmp/export_runs.py /tmp/r2_runs.jsonl`.
The loading mirrors app.verification.runner.run_gate line for line, but stores nothing; run_time is the committee run's
created_at (the same source publish.py uses). Each line also carries the gate results production stored, so the
offline re-run can be checked against them.
"""
from __future__ import annotations

import json
import sys

from sqlalchemy import select

from app import db, paper_cycle, risk
from app.migrated_tables import claims_table, committee_narratives_table, source_snapshots_table, verification_results_table

CLAIM_COLS = ("id", "run_id", "ticker", "metric", "value", "unit", "period", "source", "source_snapshot_id", "source_path",
              "text_span", "created_at")
NARRATIVE_COLS = ("run_id", "narrative", "status", "attempts", "provider_requested", "model_requested", "provider_answered",
                  "model_answered", "error", "created_at")


def _s(v):
    return v.isoformat() if hasattr(v, "isoformat") else v


def export(out_path: str) -> None:
    book = paper_cycle.load_book()
    with db.engine.connect() as conn:
        run_ids = [r[0] for r in conn.execute(select(claims_table.c.run_id).distinct())]
    n = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for run_id in sorted(run_ids):
            run_doc = db.get_committee_run(run_id) or {}
            run_time = run_doc.get("created_at")
            try:
                d, sym = run_id.split(":", 1)
            except ValueError:
                continue
            with db.engine.connect() as conn:
                claim_rows = conn.execute(select(claims_table).where(claims_table.c.run_id == run_id)
                                          .order_by(claims_table.c.created_at)).fetchall()
                claims_list = [{k: _s(getattr(r, k)) for k in CLAIM_COLS} for r in claim_rows]
                snaps = {}
                for c in claims_list:
                    if not c["source_snapshot_id"]:
                        continue
                    row = conn.execute(select(source_snapshots_table)
                                       .where(source_snapshots_table.c.id == c["source_snapshot_id"])).fetchone()
                    if row:
                        meta = {"id": row.id, "source": row.source, "ticker": row.ticker, "as_of": _s(row.as_of),
                                "fetched_at": _s(row.fetched_at), "payload_hash": row.payload_hash}
                        snaps[c["id"]] = (meta, json.loads(row.payload_json))
                nar = conn.execute(select(committee_narratives_table)
                                   .where(committee_narratives_table.c.run_id == run_id)).fetchone()
                stored = conn.execute(select(verification_results_table.c.claim_id, verification_results_table.c.check_type,
                                             verification_results_table.c.status)
                                      .where(verification_results_table.c.run_id == run_id)).fetchall()
            prices = {ds: book.close[sym][ds] for ds in book._sorted_dates.get(sym, []) if ds in book.close.get(sym, {})}
            inputs = {
                "claims": claims_list,
                "snapshots_by_claim_id": snaps,
                "run_time": _s(run_time),
                "run_date": d,
                "prices": prices,
                "recomputed_risk": risk.risk_at(book, sym, d),
                "narrative_row": {k: _s(getattr(nar, k)) for k in NARRATIVE_COLS} if nar else None,
                "windows": None,
            }
            out.write(json.dumps({"run_id": run_id, "symbol": sym, "inputs": inputs,
                                  "stored_results": [list(r) for r in stored]}) + "\n")
            n += 1
    print(f"exported {n} runs to {out_path}")


if __name__ == "__main__":
    export(sys.argv[1])
