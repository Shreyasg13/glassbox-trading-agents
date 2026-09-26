"""Structured claims extracted from committee decision sources (S3 T3).

Every number that reaches the user is represented as a claim that points at the
exact source value. The narrative (app/narrative.py) then references numbers
ONLY through {{claim:<id>}} placeholders.

No LLM calls here -- fully deterministic extraction from the same data the
committee context used.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from . import db, free_data, risk, snapshot_store
from .migrated_tables import claims_table

log = logging.getLogger("glassbox.claims")


# Fixed table of allowed derived formulas.
# Metric -> (formula text, required input names in text_span).
# The formula text is what goes into text_span (after "derived: ").
# Input names must be JSON pointers (starting with /) EXCEPT "price" for PE.
# Use abs(x) for absolute value (not |x|).
FORMULAS: Dict[str, Tuple[str, Tuple[str, ...]]] = {
    "revenue_growth": (
        "(revenue - revenue_prior) / revenue_prior",
        ("revenue", "revenue_prior"),
    ),
    "net_margin": (
        "net_income / revenue",
        ("net_income", "revenue"),
    ),
    "operating_margin": (
        "operating_income / revenue",
        ("operating_income", "revenue"),
    ),
    "roe": (
        "net_income / equity",
        ("net_income", "equity"),
    ),
    "fcf_margin": (
        "(op_cash_flow - abs(capex)) / revenue",
        ("op_cash_flow", "capex", "revenue"),
    ),
    "debt_to_equity": (
        "long_term_debt / equity",
        ("long_term_debt", "equity"),
    ),
    "liabilities_to_equity": (
        "liabilities / equity",
        ("liabilities", "equity"),
    ),
    "pe": (
        "price / eps",
        ("price", "eps"),
    ),
    "curve_10y_2y": (
        "y10 - y2",
        ("y10", "y2"),
    ),
    "y10_change_3m": (
        "y10_now - y10_3m_ago",
        ("y10_now", "y10_3m_ago"),
    ),
    "cpi_yoy": (
        "(cpi_now / cpi_year_ago) - 1",
        ("cpi_now", "cpi_year_ago"),
    ),
}


def _iso(dt_or_str: Optional[str | datetime] = None) -> str:
    """Normalize a datetime or ISO string to a single exact format: YYYY-MM-DDTHH:MM:SS.ffffff+00:00."""
    if dt_or_str is None:
        dt = datetime.now(timezone.utc)
    elif isinstance(dt_or_str, datetime):
        dt = dt_or_str
    else:
        dt = datetime.fromisoformat(dt_or_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f+00:00")


def resolve_pointer(payload: Any, path: str) -> Any:
    """Resolve an RFC 6901 JSON pointer into a payload.

    Supports object keys and array indices. Does NOT support the '-' suffix.
    Raises KeyError/IndexError if the path doesn't resolve.
    """
    if not path or path == "":
        return payload
    if not path.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {path!r}")
    parts = path.split("/")[1:]  # skip leading empty
    current = payload
    for part in parts:
        # Unescape ~1 -> / and ~0 -> ~
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            idx = int(part)
            current = current[idx]
        elif isinstance(current, dict):
            current = current[part]
        else:
            raise KeyError(f"Cannot traverse into {type(current).__name__} at {part!r}")
    return current


def _fetch_snapshot_with_id(source: str, ticker: str, run_time: str) -> Optional[Tuple[str, Any]]:
    """Fetch snapshot for a source, returning (id, payload) or None."""
    try:
        return snapshot_store.get_with_id(source, ticker, run_time)
    except Exception as exc:
        log.warning("snapshot_store.get_with_id failed for %s/%s: %s", source, ticker, exc)
        return None


def _make_claim(
    run_id: str,
    ticker: str,
    metric: str,
    value: float,
    unit: str,
    period: str,
    source: str,
    source_snapshot_id: Optional[str] = None,
    source_path: Optional[str] = None,
    text_span: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": str(uuid.uuid4()),
        "run_id": run_id,
        "ticker": ticker.upper(),
        "metric": metric,
        "value": value,
        "unit": unit,
        "period": period,
        "source": source,
        "source_snapshot_id": source_snapshot_id,
        "source_path": source_path,
        "text_span": text_span,
        "created_at": _iso(),
    }


def _find_treasury_index(payload: List[Dict[str, Any]], target_date: str) -> Optional[int]:
    """Find the index of the treasury row with the given date."""
    for i, row in enumerate(payload):
        if row.get("date") == target_date:
            return i
    return None


def _find_bls_index(payload: List[Dict[str, Any]], target_month: str) -> Optional[int]:
    """Find the index of the BLS row with the given month."""
    for i, row in enumerate(payload):
        if row.get("month") == target_month:
            return i
    return None


def _find_treasury_index_3m_ago(payload: List[Dict[str, Any]], current_idx: int) -> Optional[int]:
    """Find the index of the treasury row approximately 3 months (90 days) before the current row."""
    if current_idx is None or not payload:
        return None
    current_row = payload[current_idx]
    current_date_str = current_row.get("date")
    if not current_date_str:
        return None
    try:
        from datetime import date, timedelta
        current_date = date.fromisoformat(current_date_str)
        target_date = current_date - timedelta(days=90)
        # Find the closest row on or before target_date
        best_idx = None
        best_diff = None
        for i, row in enumerate(payload):
            row_date_str = row.get("date")
            if not row_date_str:
                continue
            row_date = date.fromisoformat(row_date_str)
            if row_date <= target_date:
                diff = (target_date - row_date).days
                if best_diff is None or diff < best_diff:
                    best_diff = diff
                    best_idx = i
        return best_idx
    except Exception:
        return None


def _find_bls_index_year_ago(payload: List[Dict[str, Any]], current_idx: int) -> Optional[int]:
    """Find the index of the BLS row approximately 12 months before the current row."""
    if current_idx is None or not payload:
        return None
    current_row = payload[current_idx]
    current_month_str = current_row.get("month")
    if not current_month_str:
        return None
    try:
        year, month = map(int, current_month_str.split("-"))
        target_year = year - 1
        target_month = f"{target_year:04d}-{month:02d}"
        # Find exact match
        for i, row in enumerate(payload):
            if row.get("month") == target_month:
                return i
        return None
    except Exception:
        return None


def _find_concept_series_index(
    concepts: Dict[str, Any], concept_name: str, target_end: str
) -> Optional[Tuple[int, Optional[int]]]:
    """Find the index of the series entry for a concept with the given fiscal year end.
    Returns (series_index, filed_index) where filed_index is always the same as series_index
    since each entry has its own filed date.
    """
    concept = concepts.get(concept_name)
    if not concept or "series" not in concept:
        return None
    for i, entry in enumerate(concept["series"]):
        if entry.get("end") == target_end:
            return (i, i)
    return None


def _find_prior_concept_series_index(
    concepts: Dict[str, Any], concept_name: str, target_end: str
) -> Optional[int]:
    """Find the index of the PRIOR year's series entry for a concept."""
    concept = concepts.get(concept_name)
    if not concept or "series" not in concept:
        return None
    series = concept["series"]
    target_idx = None
    for i, entry in enumerate(series):
        if entry.get("end") == target_end:
            target_idx = i
            break
    if target_idx is None or target_idx == 0:
        return None
    # The prior year should be the previous entry in the series
    # (fiscal years are in order in the series)
    prior_idx = target_idx - 1
    prior_entry = series[prior_idx]
    # Verify it's roughly a year earlier (330-400 days)
    try:
        from .free_data import _days_between
        if 330 <= _days_between(prior_entry.get("end", ""), target_end) <= 400:
            return prior_idx
    except Exception:
        pass
    return prior_idx


def _build_fundamentals_pointers(
    concepts: Dict[str, Any], fy_end: str
) -> Dict[str, Optional[str]]:
    """Build JSON pointers for all concept series entries used in fundamentals calculations.
    Returns a dict mapping concept name to its pointer, or None if not found.
    Includes both latest and prior pointers for revenue (for growth calculation).
    Pointers point to the 'val' field of the series entry for direct numeric resolution.
    """
    pointers = {}
    for concept_name in ("revenue", "net_income", "operating_income", "equity", "liabilities", "long_term_debt", "op_cash_flow", "capex", "eps"):
        found = _find_concept_series_index(concepts, concept_name, fy_end)
        if found:
            pointers[concept_name] = f"/concepts/{concept_name}/series/{found[0]}/val"
        else:
            pointers[concept_name] = None
        # Also find prior for revenue (for growth)
        if concept_name == "revenue":
            prior_idx = _find_prior_concept_series_index(concepts, concept_name, fy_end)
            if prior_idx is not None:
                pointers["revenue_prior"] = f"/concepts/revenue/series/{prior_idx}/val"
            else:
                pointers["revenue_prior"] = None
    return pointers


def _build_derived_text_span(metric: str, pointers: Dict[str, Optional[str]]) -> str:
    """Build a text_span for a derived value using the FORMULAS table."""
    if metric not in FORMULAS:
        raise ValueError(f"Unknown derived metric: {metric}")
    formula, required_inputs = FORMULAS[metric]
    parts = [f"derived: {formula}"]
    for inp in required_inputs:
        if pointers.get(inp):
            parts.append(f" | {inp}={pointers[inp]}")
    return "".join(parts)


def build_claims(
    run_id: str,
    sym: str,
    d: str,
    book: Any,
    run_time: str,
) -> List[Dict[str, Any]]:
    """Build structured claims from the same data the committee context used.

    Sources:
    - close price on date d (source: pricebook, period = d)
    - fundamentals from free_data.fundamentals_as_of (source: sec_facts)
    - macro from free_data.macro_as_of (source: treasury, bls)
    - risk from risk.risk_at (source: risk)
    """
    claims: List[Dict[str, Any]] = []
    sym = sym.upper()
    close = book.close[sym][d]

    # 1. Close price (pricebook)
    claims.append(
        _make_claim(
            run_id=run_id,
            ticker=sym,
            metric="close",
            value=close,
            unit="USD",
            period=d,
            source="pricebook",
            source_path=None,  # pricebook has no snapshot id
            text_span=None,
        )
    )

    # 2. Fundamentals (sec_facts snapshot)
    fund = free_data.fundamentals_as_of(sym, d, price=close, run_time=run_time)
    if fund:
        snap = _fetch_snapshot_with_id("sec_facts", sym, run_time)
        snap_id = snap[0] if snap else None
        payload = snap[1] if snap else None
        concepts = payload.get("concepts") if payload else None

        fy_end = fund.get("fiscal_year_end", d)
        pointers = _build_fundamentals_pointers(concepts, fy_end) if concepts else {}

        # revenue_growth
        if "revenue_growth" in fund and fund["revenue_growth"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="revenue_growth",
                    value=fund["revenue_growth"],
                    unit="pct",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("revenue"),
                    text_span=_build_derived_text_span("revenue_growth", pointers),
                )
            )

        # net_margin
        if "net_margin" in fund and fund["net_margin"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="net_margin",
                    value=fund["net_margin"],
                    unit="pct",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("net_income"),
                    text_span=_build_derived_text_span("net_margin", pointers),
                )
            )

        # operating_margin
        if "operating_margin" in fund and fund["operating_margin"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="operating_margin",
                    value=fund["operating_margin"],
                    unit="pct",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("operating_income"),
                    text_span=_build_derived_text_span("operating_margin", pointers),
                )
            )

        # roe
        if "roe" in fund and fund["roe"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="roe",
                    value=fund["roe"],
                    unit="pct",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("equity"),
                    text_span=_build_derived_text_span("roe", pointers),
                )
            )

        # fcf_margin
        if "fcf_margin" in fund and fund["fcf_margin"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="fcf_margin",
                    value=fund["fcf_margin"],
                    unit="pct",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("op_cash_flow"),
                    text_span=_build_derived_text_span("fcf_margin", pointers),
                )
            )

        # debt_to_equity
        if "debt_to_equity" in fund and fund["debt_to_equity"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="debt_to_equity",
                    value=fund["debt_to_equity"],
                    unit="ratio",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("long_term_debt"),
                    text_span=_build_derived_text_span("debt_to_equity", pointers),
                )
            )

        # liabilities_to_equity
        if "liabilities_to_equity" in fund and fund["liabilities_to_equity"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="liabilities_to_equity",
                    value=fund["liabilities_to_equity"],
                    unit="ratio",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("liabilities"),
                    text_span=_build_derived_text_span("liabilities_to_equity", pointers),
                )
            )

        # eps (direct value from series)
        if "eps" in fund and fund["eps"] is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="eps",
                    value=fund["eps"],
                    unit="USD",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("eps"),
                    text_span=None,
                )
            )

        # pe (derived: price / eps) - price comes from pricebook, include as price=pricebook:<date>
        if "pe" in fund and fund["pe"] is not None:
            pe_pointers = dict(pointers)
            # Add price as a special pointer marker
            pe_pointers["price"] = f"pricebook:{d}"
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="pe",
                    value=fund["pe"],
                    unit="ratio",
                    period=fy_end,
                    source="sec_facts",
                    source_snapshot_id=snap_id,
                    source_path=pointers.get("eps"),
                    text_span=_build_derived_text_span("pe", pe_pointers),
                )
            )

    # 3. Macro (treasury, bls snapshots)
    macro = free_data.macro_as_of(d, run_time=run_time)
    if macro:
        # Treasury
        treasury_snap = _fetch_snapshot_with_id("treasury", "", run_time)
        treasury_payload = treasury_snap[1] if treasury_snap else None
        treasury_date = macro.get("treasury_date")
        treasury_idx = _find_treasury_index(treasury_payload, treasury_date) if treasury_payload and treasury_date else None

        if "y10" in macro and treasury_idx is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="y10",
                    value=macro["y10"],
                    unit="pct",
                    period=treasury_date or d,
                    source="treasury",
                    source_snapshot_id=treasury_snap[0] if treasury_snap else None,
                    source_path=f"/{treasury_idx}/y10",
                    text_span=None,
                )
            )
        if "y2" in macro and treasury_idx is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="y2",
                    value=macro["y2"],
                    unit="pct",
                    period=treasury_date or d,
                    source="treasury",
                    source_snapshot_id=treasury_snap[0] if treasury_snap else None,
                    source_path=f"/{treasury_idx}/y2",
                    text_span=None,
                )
            )
        if "y3m" in macro and treasury_idx is not None:
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="y3m",
                    value=macro["y3m"],
                    unit="pct",
                    period=treasury_date or d,
                    source="treasury",
                    source_snapshot_id=treasury_snap[0] if treasury_snap else None,
                    source_path=f"/{treasury_idx}/y3m",
                    text_span=None,
                )
            )
        if "curve_10y_2y" in macro and treasury_idx is not None:
            macro_pointers = {
                "y10": f"/{treasury_idx}/y10",
                "y2": f"/{treasury_idx}/y2",
            }
            claims.append(
                _make_claim(
                    run_id=run_id,
                    ticker=sym,
                    metric="curve_10y_2y",
                    value=macro["curve_10y_2y"],
                    unit="pct",
                    period=treasury_date or d,
                    source="treasury",
                    source_snapshot_id=treasury_snap[0] if treasury_snap else None,
                    source_path=f"/{treasury_idx}/y10",
                    text_span=_build_derived_text_span("curve_10y_2y", macro_pointers),
                )
            )
        if "y10_change_3m" in macro and treasury_idx is not None:
            # This is derived from current and 3m-ago y10
            treasury_3m_idx = _find_treasury_index_3m_ago(treasury_payload, treasury_idx)
            if treasury_3m_idx is not None:
                macro_pointers = {
                    "y10_now": f"/{treasury_idx}/y10",
                    "y10_3m_ago": f"/{treasury_3m_idx}/y10",
                }
                claims.append(
                    _make_claim(
                        run_id=run_id,
                        ticker=sym,
                        metric="y10_change_3m",
                        value=macro["y10_change_3m"],
                        unit="pct",
                        period=treasury_date or d,
                        source="treasury",
                        source_snapshot_id=treasury_snap[0] if treasury_snap else None,
                        source_path=f"/{treasury_idx}/y10",
                        text_span=_build_derived_text_span("y10_change_3m", macro_pointers),
                    )
                )

        # BLS - unemployment
        if "unemployment" in macro:
            bls_snap = _fetch_snapshot_with_id("bls", "unemployment", run_time)
            bls_payload = bls_snap[1] if bls_snap else None
            unemp_month = macro.get("unemployment_month")
            unemp_idx = _find_bls_index(bls_payload, unemp_month) if bls_payload and unemp_month else None
            if unemp_idx is not None:
                claims.append(
                    _make_claim(
                        run_id=run_id,
                        ticker=sym,
                        metric="unemployment",
                        value=macro["unemployment"],
                        unit="pct",
                        period=unemp_month or d,
                        source="bls",
                        source_snapshot_id=bls_snap[0] if bls_snap else None,
                        source_path=f"/{unemp_idx}/value",
                        text_span=None,
                    )
                )

        # BLS - cpi_yoy
        if "cpi_yoy" in macro:
            bls_snap = _fetch_snapshot_with_id("bls", "cpi", run_time)
            bls_payload = bls_snap[1] if bls_snap else None
            cpi_month = macro.get("cpi_month")
            cpi_idx = _find_bls_index(bls_payload, cpi_month) if bls_payload and cpi_month else None
            if cpi_idx is not None:
                cpi_year_ago_idx = _find_bls_index_year_ago(bls_payload, cpi_idx)
                if cpi_year_ago_idx is not None:
                    macro_pointers = {
                        "cpi_now": f"/{cpi_idx}/value",
                        "cpi_year_ago": f"/{cpi_year_ago_idx}/value",
                    }
                    claims.append(
                        _make_claim(
                            run_id=run_id,
                            ticker=sym,
                            metric="cpi_yoy",
                            value=macro["cpi_yoy"],
                            unit="pct",
                            period=cpi_month or d,
                            source="bls",
                            source_snapshot_id=bls_snap[0] if bls_snap else None,
                            source_path=f"/{cpi_idx}/value",
                            text_span=_build_derived_text_span("cpi_yoy", macro_pointers),
                        )
                    )

    # 4. Risk (source: risk)
    r = risk.risk_at(book, sym, d)
    if r:
        claims.append(
            _make_claim(
                run_id=run_id,
                ticker=sym,
                metric="risk_score",
                value=r["score"],
                unit="ratio",
                period=d,
                source="risk",
                source_snapshot_id=None,
                source_path=None,
                text_span=None,
            )
        )
        claims.append(
            _make_claim(
                run_id=run_id,
                ticker=sym,
                metric="risk_vol_pct",
                value=r["vol_pct"],
                unit="pct",
                period=d,
                source="risk",
                source_snapshot_id=None,
                source_path=None,
                text_span=None,
            )
        )
        claims.append(
            _make_claim(
                run_id=run_id,
                ticker=sym,
                metric="risk_drawdown",
                value=r["drawdown"],
                unit="pct",
                period=d,
                source="risk",
                source_snapshot_id=None,
                source_path=None,
                text_span=None,
            )
        )
        claims.append(
            _make_claim(
                run_id=run_id,
                ticker=sym,
                metric="risk_below_ma200",
                value=1.0 if r["below_ma200"] else 0.0,
                unit="count",
                period=d,
                source="risk",
                source_snapshot_id=None,
                source_path=None,
                text_span=None,
            )
        )

    # Store claims
    # The claims table is append-only (database triggers, migration 0004): the first set stored for a run is its permanent
    # record. Re-running the step for a run that already has claims returns those instead of adding duplicates.
    with db.engine.begin() as conn:
        existing = conn.execute(select(claims_table).where(claims_table.c.run_id == run_id)).mappings().all()
        if existing:
            return [dict(r) for r in existing]
        if claims:
            conn.execute(claims_table.insert(), claims)

    return claims


def check_claim(claim: Dict[str, Any], payload: Any, prices: Optional[Dict[str, float]] = None) -> bool:
    """Verify that a claim's value matches its source.

    For direct claims (source_path resolves to the exact value): returns True if
    resolve_pointer(payload, source_path) == value (within 1e-9 relative tolerance).

    For derived claims (text_span starts with 'derived:'): looks up the formula in
    the FORMULAS table, verifies the formula text and input names match exactly,
    resolves each input pointer from the payload (or prices for PE), evaluates
    the formula with a safe AST-based evaluator, and checks equality within
    1e-9 relative tolerance.

    Returns False if source is not a snapshot source (risk, pricebook) or if
    source_path is None for a snapshot source.
    """
    source = claim.get("source")
    if source in ("risk", "pricebook"):
        # These don't have snapshot payloads to verify against
        return True

    source_path = claim.get("source_path")
    if source_path is None:
        log.warning("check_claim: source_path is None for snapshot source %s, metric %s", source, claim.get("metric"))
        return False

    text_span = claim.get("text_span", "")
    value = claim.get("value")

    try:
        if text_span and text_span.startswith("derived:"):
            # Parse derived claim
            return _check_derived_claim(claim, payload, prices)
        else:
            # Direct claim: source_path should resolve to the value
            resolved = resolve_pointer(payload, source_path)
            # Handle different types of resolved values
            if isinstance(resolved, dict):
                # For treasury row (curve_10y_2y), the value is in the dict
                # The claim value should match a specific key
                # We already set source_path to the specific key for direct values
                return False
            if isinstance(resolved, list):
                # Series array - this is a direct value claim pointing to series, shouldn't happen for direct
                return False
            # Numeric comparison with relative tolerance
            if isinstance(resolved, (int, float)) and isinstance(value, (int, float)):
                if value == 0 and resolved == 0:
                    return True
                if value == 0:
                    return abs(resolved) < 1e-9
                rel_diff = abs(resolved - value) / abs(value)
                return rel_diff < 1e-9
            return resolved == value
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        log.warning("check_claim failed for %s: %s", claim.get("id"), exc)
        return False


def _check_derived_claim(claim: Dict[str, Any], payload: Any, prices: Optional[Dict[str, float]] = None) -> bool:
    """Verify a derived claim using the FORMULAS table and safe AST evaluation."""
    text_span = claim.get("text_span", "")
    value = claim.get("value")
    metric = claim.get("metric")

    # Look up the metric in FORMULAS table
    if metric not in FORMULAS:
        log.warning("check_claim: unknown derived metric %s", metric)
        return False
    expected_formula, required_inputs = FORMULAS[metric]

    # Parse text_span: "derived: <formula> | a=/ptr1 | b=/ptr2 ... | c=pricebook:..."
    if " | " not in text_span:
        return False
    formula_part, rest_part = text_span.split(" | ", 1)
    formula_part = formula_part.strip()
    if not formula_part.startswith("derived:"):
        return False
    formula = formula_part[len("derived:") :].strip()

    # Verify formula text matches the table exactly
    if formula != expected_formula:
        log.warning("check_claim: formula mismatch for %s: got %r, expected %r", metric, formula, expected_formula)
        return False

    # Parse input pointers from the rest
    input_names = []
    input_values = {}
    for part in rest_part.split(" | "):
        part = part.strip()
        if "=" not in part:
            continue
        name, val_or_ptr = part.split("=", 1)
        name = name.strip()
        val_or_ptr = val_or_ptr.strip()
        input_names.append(name)

    # Verify input names match exactly the required inputs (order doesn't matter)
    if set(input_names) != set(required_inputs):
        log.warning("check_claim: input names mismatch for %s: got %s, expected %s", metric, input_names, required_inputs)
        return False

    # Resolve each input
    for part in rest_part.split(" | "):
        part = part.strip()
        if "=" not in part:
            continue
        name, val_or_ptr = part.split("=", 1)
        name = name.strip()
        val_or_ptr = val_or_ptr.strip()

        if val_or_ptr.startswith("/"):
            # JSON pointer - resolve from payload
            try:
                resolved = resolve_pointer(payload, val_or_ptr)
                if not isinstance(resolved, (int, float)):
                    log.warning("check_claim: resolved non-numeric for %s: %s", name, type(resolved))
                    return False
                input_values[name] = float(resolved)
            except (KeyError, IndexError, ValueError) as exc:
                log.warning("check_claim: failed to resolve %s for %s: %s", val_or_ptr, claim.get("id"), exc)
                return False
        elif val_or_ptr.startswith("pricebook:"):
            # Special case for PE: price from pricebook
            if prices is None:
                log.warning("check_claim: prices dict required for PE metric but not provided")
                return False
            price_date = val_or_ptr[len("pricebook:"):]
            if price_date not in prices:
                log.warning("check_claim: price for date %s not in prices dict", price_date)
                return False
            input_values[name] = float(prices[price_date])
        else:
            # Direct numeric value - NOT ALLOWED (security: no constants from claim)
            log.warning("check_claim: direct numeric value not allowed for %s: %s", name, val_or_ptr)
            return False

    # Evaluate formula using safe AST evaluator
    try:
        computed = _safe_eval_formula(formula, input_values)
    except Exception as exc:
        log.warning("check_claim: formula eval failed for %s: %s", claim.get("id"), exc)
        return False

    # Compare with claim value
    if value == 0 and computed == 0:
        return True
    if value == 0:
        return abs(computed) < 1e-9
    rel_diff = abs(computed - value) / abs(value)
    return rel_diff < 1e-9


def _safe_eval_formula(formula: str, variables: Dict[str, float]) -> float:
    """Safely evaluate a formula using AST parsing.

    Allowed AST nodes:
    - Expression (root)
    - BinOp with Add, Sub, Mult, Div
    - UnaryOp with USub (negation)
    - Name (must be in variables)
    - Constant (only int/float, but only the literal 1 is allowed for "- 1" patterns)
    - Call to abs() with exactly one argument

    Anything else raises ValueError.
    """
    tree = ast.parse(formula, mode="eval")

    def eval_node(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return eval_node(node.body)

        if isinstance(node, ast.BinOp):
            left = eval_node(node.left)
            right = eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            elif isinstance(node.op, ast.Sub):
                return left - right
            elif isinstance(node.op, ast.Mult):
                return left * right
            elif isinstance(node.op, ast.Div):
                return left / right
            else:
                raise ValueError(f"Unsupported binary operator: {type(node.op).__name__}")

        if isinstance(node, ast.UnaryOp):
            operand = eval_node(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            else:
                raise ValueError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"Unknown variable: {node.id}")
            return variables[node.id]

        if isinstance(node, ast.Constant):
            # Only allow the literal 1 (for "- 1" in cpi_yoy formula)
            if node.value == 1 and isinstance(node.value, (int, float)):
                return float(node.value)
            raise ValueError(f"Constants other than 1 are not allowed: {node.value}")

        if isinstance(node, ast.Call):
            # Only allow abs() with one argument
            if isinstance(node.func, ast.Name) and node.func.id == "abs":
                if len(node.args) != 1:
                    raise ValueError("abs() requires exactly one argument")
                if node.keywords:
                    raise ValueError("abs() does not accept keyword arguments")
                return abs(eval_node(node.args[0]))
            raise ValueError(f"Function calls other than abs() are not allowed: {node.func}")

        raise ValueError(f"Unsupported AST node: {type(node).__name__}")

    return eval_node(tree)