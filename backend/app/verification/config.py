"""Verification configuration (S3 T4).

Staleness windows for each source type. Marked as pending owner confirmation
(decision D9) so the values can be adjusted without code changes once the
product owner reviews them.

Trading days: skip Saturday/Sunday (no holiday calendar).

Kept as Python (not the plan's config.yaml): the values are typed, reviewed in code and need no loader (S3 T4b).
"""
from __future__ import annotations

from datetime import timedelta

# Staleness windows: maximum age of data (as_of vs run_date) before a WARN.
# Source -> maximum age. Beyond this window, the check returns 'warn' (not 'fail').
# These are the plan's starting values; marked PENDING OWNER CONFIRMATION (decision D9).
DEFAULT_WINDOWS: dict[str, timedelta] = {
    "prices": timedelta(days=1),      # 1 trading day
    "sec_facts": timedelta(days=120), # ~4 months
    "sec_filings": timedelta(days=120),
    "sec_insiders": timedelta(days=14),
    "treasury": timedelta(days=35),
    "bls": timedelta(days=35),
}

# Sources that use trading-day calendar (skip weekends)
TRADING_DAY_SOURCES = {"prices"}

# Expected unit of every metric claims.build_claims can emit (S3 T4b). A claim whose unit differs FAILS the unit check, so a
# percentage and a ratio are never compared as if they were the same kind of number. A metric missing here WARNS.
# Grouped as build_claims emits them: close (pricebook), fundamentals (sec_facts: direct eps and the derived FORMULAS
# metrics), macro (treasury, bls) and risk. tests/test_gate.py checks this covers FORMULAS and a real build.
EXPECTED_UNITS: dict[str, str] = {
    # pricebook
    "close": "USD",
    # sec_facts
    "revenue_growth": "pct",
    "net_margin": "pct",
    "operating_margin": "pct",
    "roe": "pct",
    "fcf_margin": "pct",
    "debt_to_equity": "ratio",
    "liabilities_to_equity": "ratio",
    "eps": "USD",
    "pe": "ratio",
    # treasury
    "y10": "pct",
    "y2": "pct",
    "y3m": "pct",
    "curve_10y_2y": "pct",
    "y10_change_3m": "pct",
    # bls
    "unemployment": "pct",
    "cpi_yoy": "pct",
    # risk
    "risk_score": "ratio",
    "risk_vol_pct": "pct",
    "risk_drawdown": "pct",
    "risk_below_ma200": "count",
}

# Tolerance for numbers as RENDERED to a user (plan T4: 0.5% relative, or rounding at the displayed precision). Not used
# yet: claim values are copied from the source, so the gate's traceability check compares them exactly (stricter than
# the plan allows). T7 will use this when it checks the numbers shown in the evidence view against their claims.
RENDER_TOLERANCE_REL = 0.005
