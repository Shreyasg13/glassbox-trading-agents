"""Weekly discrepancy report job (S3 T13): create the draft for the previous Mon-Sun week.

    python -m app.scripts.weekly_report

Runs from the daily pipeline's "weekly_report" stage (Mondays only). Idempotent: a second run for a week that already
has a draft (or a published report) creates nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import weekly_report

log = logging.getLogger("weekly_report_job")


def main(argv: Optional[List[str]] = None) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    week_start = weekly_report.previous_week_start(today)
    row = weekly_report.create_draft_for_week(week_start)
    if row is None:
        log.info("weekly report for %s already exists; nothing to do", week_start.isoformat())
        return {"ok": True, "week_start": week_start.isoformat(), "created": False}
    log.info("created weekly report draft %s for %s", row["id"], week_start.isoformat())
    return {"ok": True, "week_start": week_start.isoformat(), "created": True, "id": row["id"]}


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.exit(0 if main(sys.argv[1:])["ok"] else 1)
