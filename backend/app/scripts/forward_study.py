"""Forward-only study: every pre-registered number in one read-only run (docs/s3/PREREGISTRATION.md).

    python -m app.scripts.forward_study            # JSON to stdout
    python -m app.scripts.forward_study --perm 2000

Reads ledger_calls, call_outcomes and paper_accounts. Writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .. import db, research, scoring


def scored_rows() -> list[dict[str, Any]]:
    """One row per (ledger call, scored horizon), the same rows the track-record routes use."""
    return scoring.scored_calls()


def study(n_perm: int = 10000) -> dict[str, Any]:
    rows = scored_rows()
    by_h = {h: [r for r in rows if r["horizon"] == h] for h in (1, 5, 20)}
    return {
        "calls": len(scoring.load_ledger_calls()),
        "horizons": {str(h): {**scoring.metrics(rs), "shuffle_test": scoring.shuffle_test(rs, n_perm)} for h, rs in by_h.items()},
        "evidence": research.evidence_table(db.list_paper_accounts()),
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--perm", type=int, default=10000, help="shuffles per horizon (default 10000)")
    args = p.parse_args(argv)
    json.dump(study(args.perm), sys.stdout, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
