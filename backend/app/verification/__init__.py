"""Verification package (S3 T4)."""
from __future__ import annotations

from .gate import Result, check_traceability, check_point_in_time, check_staleness, check_price, check_risk, check_narrative, check_snapshot_integrity, check_unit, verify_run, summarize
from .runner import run_gate

__all__ = [
    "Result",
    "check_traceability",
    "check_point_in_time",
    "check_staleness",
    "check_price",
    "check_risk",
    "check_narrative",
    "check_snapshot_integrity",
    "check_unit",
    "verify_run",
    "summarize",
    "run_gate",
]
