"""Runs the daily Investment Committee review (see app/committee_daily.py).

    python -m app.scripts.run_committee_daily                 # what cron runs
    python -m app.scripts.run_committee_daily --dry-run       # show today's picks, call no model
    python -m app.scripts.run_committee_daily --symbols AAPL,NVDA --force

Cron (host crontab). 22:15 UTC on weekdays: the US market closes at 20:00 UTC (21:00 in
winter) and the data sync runs at 22:00, so by 22:15 today's prices and signals are final. It
must run BEFORE the paper-trading cycle (22:45), whose "Committee on all symbols" account
trades on these decisions:

    15 22 * * 1-5 cd /home/shrey/glassbox && /usr/bin/docker compose exec -T backend python -m app.scripts.run_committee_daily >> /home/shrey/glassbox/logs/committee_daily.log 2>&1

Idempotent (one decision per date+symbol), so a holiday or a second run is a no-op. After a run that
reviewed something, the Daily Studio (app/studio.py) writes the day's share drafts for Admin -> Studio.
Exit code: 0 = ran or nothing to do, 1 = could not run, or every model call failed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from .. import committee_daily, studio
from ..logging_config import quiet_http_clients

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
quiet_http_clients()  # httpx INFO lines carry the Gemini ?key= URL
log = logging.getLogger("run_committee_daily")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Run the daily Investment Committee review.")
    p.add_argument("--dry-run", action="store_true", help="show which symbols would be reviewed; call no model")
    p.add_argument("--force", action="store_true", help="re-review symbols already reviewed today")
    p.add_argument("--symbols", help="comma-separated tickers to review instead of the automatic picks")
    args = p.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    try:
        result = asyncio.run(committee_daily.run_daily(symbols=symbols, dry_run=args.dry_run, force=args.force))
    except committee_daily.CommitteeError as exc:
        log.error("%s", exc)
        return 1
    print(json.dumps(result, indent=2))
    if result.get("ran") and not result.get("answered_total"):
        log.error("every committee run failed (no agent answered) -- check Admin -> Observability")
        return 1
    if result.get("ran") and not args.dry_run:
        # The Daily Studio drafts today's share posts (and Friday's recap) from the casebook just written.
        # Drafts only: nothing is posted. A studio failure never fails the committee run.
        try:
            print(json.dumps({"studio": asyncio.run(studio.run())}, indent=2))
        except Exception:  # noqa: BLE001
            log.exception("daily studio failed; the committee run itself succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
