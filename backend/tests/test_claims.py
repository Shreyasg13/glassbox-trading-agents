"""Tests for structured claims (S3 T3)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app import claims, db, free_data, paper, paper_cycle, risk, snapshot_store
from app.migrated_tables import claims_table
from app.migrate import upgrade


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Use a temporary SQLite database for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("GLASSBOX_DB_PATH", str(db_path))
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    import importlib
    importlib.reload(db)
    importlib.reload(free_data)
    importlib.reload(snapshot_store)
    importlib.reload(claims)
    importlib.reload(risk)
    importlib.reload(paper)
    importlib.reload(paper_cycle)
    upgrade(db.engine.connect())
    yield
    db.engine.dispose()


def make_book():
    """Create a simple PriceBook for testing with enough history for risk."""
    N = 600  # Need at least 252 for risk, plus dates through 2025
    DATES = pd.bdate_range("2023-01-02", periods=N)
    PARAMS = {"rsi_low": 30, "rsi_high": 70, "fast_ma": 20, "slow_ma": 50}
    LAST = DATES[N - 1].strftime("%Y-%m-%d")

    def frame(close, buy=(), sell=()):
        rsi, fast, slow = [], [], []
        for i in range(N):
            if i in buy:
                rsi.append(20.0), fast.append(101.0), slow.append(100.0)
            elif i in sell:
                rsi.append(80.0), fast.append(99.0), slow.append(100.0)
            else:
                rsi.append(50.0), fast.append(100.0), slow.append(100.0)
        closes = close if isinstance(close, list) else [float(close)] * N
        return pd.DataFrame({"Close": closes, "RSI": rsi, "MA_20": fast, "MA_50": slow}, index=DATES)

    frames = {
        "AAPL": frame([100.0 + 0.05 * k for k in range(N)], buy={N - 1}),
        "NVDA": frame([200.0 - 0.03 * k for k in range(N)], sell={N - 1}),
    }
    return paper.PriceBook.from_frames(frames, {s: PARAMS for s in frames}), LAST


def test_resolve_pointer_basic():
    payload = {"a": {"b": [1, 2, {"c": 3}]}}
    assert claims.resolve_pointer(payload, "/a/b/0") == 1
    assert claims.resolve_pointer(payload, "/a/b/1") == 2
    assert claims.resolve_pointer(payload, "/a/b/2/c") == 3


def test_resolve_pointer_escapes():
    payload = {"a~b": {"c/d": 42}}
    assert claims.resolve_pointer(payload, "/a~0b/c~1d") == 42


def test_build_claims_creates_close_claim():
    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = datetime.now(timezone.utc).isoformat()
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    # Should have at least the close claim
    close_claims = [c for c in claim_list if c["metric"] == "close"]
    assert len(close_claims) == 1
    c = close_claims[0]
    assert c["run_id"] == run_id
    assert c["ticker"] == "AAPL"
    assert c["metric"] == "close"
    assert c["unit"] == "USD"
    assert c["period"] == d
    assert c["source"] == "pricebook"
    assert c["source_snapshot_id"] is None
    assert c["source_path"] is None
    assert c["value"] == book.close["AAPL"][d]


def test_build_claims_creates_risk_claims():
    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = datetime.now(timezone.utc).isoformat()
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    risk_claims = [c for c in claim_list if c["source"] == "risk"]
    assert len(risk_claims) == 4  # risk_score, risk_vol_pct, risk_drawdown, risk_below_ma200
    metrics = {c["metric"] for c in risk_claims}
    assert metrics == {"risk_score", "risk_vol_pct", "risk_drawdown", "risk_below_ma200"}
    for c in risk_claims:
        assert c["source"] == "risk"
        assert c["source_snapshot_id"] is None
        assert c["source_path"] is None


def test_claims_from_snapshot_resolve(tmp_path, monkeypatch):
    """Every claim from a snapshot source resolves: resolve_pointer(payload, source_path) == value."""
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    monkeypatch.setenv("SEC_USER_AGENT", "GlassBox test ops@example.com")
    import importlib
    importlib.reload(free_data)
    importlib.reload(snapshot_store)

    # Create a fundamentals snapshot with known data
    # The as_of date (d) will be around 2025-04 (from make_book), so we need a fiscal year filed BEFORE that
    fund_cache = {
        "cik": 320193,
        "fetched_at": "2026-09-15T10:00:00+00:00",
        "concepts": {
            "revenue": {
                "tag": "Revenues",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 100.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 110.0, "filed": "2025-02-01"},
                ],
            },
            "net_income": {
                "tag": "NetIncomeLoss",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 20.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 25.0, "filed": "2025-02-01"},
                ],
            },
            "equity": {
                "tag": "StockholdersEquity",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 50.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 55.0, "filed": "2025-02-01"},
                ],
            },
            "operating_income": {
                "tag": "OperatingIncomeLoss",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 22.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 28.0, "filed": "2025-02-01"},
                ],
            },
            "op_cash_flow": {
                "tag": "NetCashProvidedByUsedInOperatingActivities",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 30.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 35.0, "filed": "2025-02-01"},
                ],
            },
            "capex": {
                "tag": "PaymentsToAcquirePropertyPlantAndEquipment",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 5.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 6.0, "filed": "2025-02-01"},
                ],
            },
            "long_term_debt": {
                "tag": "LongTermDebtNoncurrent",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 10.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 12.0, "filed": "2025-02-01"},
                ],
            },
            "liabilities": {
                "tag": "Liabilities",
                "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 40.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 45.0, "filed": "2025-02-01"},
                ],
            },
            "eps": {
                "tag": "EarningsPerShareDiluted",
                "unit": "USD/shares",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 2.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 2.5, "filed": "2025-02-01"},
                ],
            },
        },
    }
    snapshot_store.put("sec_facts", "AAPL", "2024-12-31", fund_cache, fetched_at="2026-09-15T10:00:00+00:00")

    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = "2026-09-15T12:00:00+00:00"
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    # Check sec_facts claims resolve
    sec_claims = [c for c in claim_list if c["source"] == "sec_facts"]
    assert len(sec_claims) > 0, f"No sec_facts claims created. d={d}, claims={claim_list}"

    # Fetch the snapshot payload to verify resolution
    snap = snapshot_store.get_with_id("sec_facts", "AAPL", run_time)
    assert snap is not None
    snap_id, payload = snap

    # PE claim needs prices dict for the pricebook price
    close_price = book.close["AAPL"][d]
    prices = {d: close_price}

    for c in sec_claims:
        assert c["source_snapshot_id"] == snap_id
        # Every snapshot-source claim MUST have a source_path
        assert c["source_path"] is not None, f"source_path is None for snapshot-source claim {c['metric']}"
        # And check_claim must return True (PE needs prices dict)
        ok = claims.check_claim(c, payload, prices=prices if c["metric"] == "pe" else None)
        assert ok, f"check_claim failed for {c['metric']} (id={c['id']}, path={c['source_path']}, text_span={c['text_span']})"


def test_claims_use_snapshot_before_run_time(tmp_path, monkeypatch):
    """Claims are built from the snapshot fetched <= run_time, not a later one."""
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    monkeypatch.setenv("SEC_USER_AGENT", "GlassBox test ops@example.com")
    import importlib
    importlib.reload(free_data)
    importlib.reload(snapshot_store)

    # Snapshot at t1 (earlier, revenue=100) - fiscal year 2024 filed 2025-02-01
    snap_v1 = {
        "cik": 1,
        "fetched_at": "2026-09-15T10:00:00+00:00",
        "concepts": {
            "revenue": {"tag": "Revenues", "unit": "USD", "series": [{"end": "2024-12-31", "start": "2024-01-01", "val": 100.0, "filed": "2025-02-01"}]},
            "net_income": {"tag": "NetIncomeLoss", "unit": "USD", "series": [{"end": "2024-12-31", "start": "2024-01-01", "val": 20.0, "filed": "2025-02-01"}]},
            "equity": {"tag": "StockholdersEquity", "unit": "USD", "series": [{"end": "2024-12-31", "val": 50.0, "filed": "2025-02-01"}]},
        },
    }
    snapshot_store.put("sec_facts", "AAPL", "2024-12-31", snap_v1, fetched_at="2026-09-15T10:00:00+00:00")

    # Snapshot at t3 (later, revenue=200) - same fiscal year but different values
    snap_v2 = {
        "cik": 1,
        "fetched_at": "2026-09-15T14:00:00+00:00",
        "concepts": {
            "revenue": {"tag": "Revenues", "unit": "USD", "series": [{"end": "2024-12-31", "start": "2024-01-01", "val": 200.0, "filed": "2025-02-01"}]},
            "net_income": {"tag": "NetIncomeLoss", "unit": "USD", "series": [{"end": "2024-12-31", "start": "2024-01-01", "val": 40.0, "filed": "2025-02-01"}]},
            "equity": {"tag": "StockholdersEquity", "unit": "USD", "series": [{"end": "2024-12-31", "val": 60.0, "filed": "2025-02-01"}]},
        },
    }
    snapshot_store.put("sec_facts", "AAPL", "2024-12-31", snap_v2, fetched_at="2026-09-15T14:00:00+00:00")

    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = "2026-09-15T12:00:00+00:00"  # Between t1 and t3
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    # Should use the earlier snapshot (v1, revenue=100, net_margin=0.2)
    margin_claims = [c for c in claim_list if c["metric"] == "net_margin"]
    assert len(margin_claims) == 1, f"No net_margin claim. d={d}, claims={claim_list}"
    # net_margin = net_income/revenue = 20/100 = 0.2 from v1
    assert margin_claims[0]["value"] == pytest.approx(0.2)
    # The key is the claim's source_snapshot_id should be the v1 snapshot
    snap = snapshot_store.get_with_id("sec_facts", "AAPL", run_time)
    assert snap is not None
    assert margin_claims[0]["source_snapshot_id"] == snap[0]


def test_claims_stored_in_db():
    """Claims are persisted to the database."""
    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = datetime.now(timezone.utc).isoformat()
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    with db.engine.connect() as conn:
        rows = conn.execute(select(claims_table).where(claims_table.c.run_id == run_id)).fetchall()

    assert len(rows) == len(claim_list)
    for row, claim in zip(rows, claim_list):
        assert row.id == claim["id"]
        assert row.metric == claim["metric"]
        assert row.value == claim["value"]
        assert row.unit == claim["unit"]


def test_macro_claims_from_snapshot(tmp_path, monkeypatch):
    """Macro claims (treasury, bls) are created from snapshots and verify with check_claim."""
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    import importlib
    importlib.reload(free_data)
    importlib.reload(snapshot_store)

    book, d = make_book()
    # d is around 2025-04, so use treasury dates before that
    treasury_snap = [{"date": "2025-03-10", "y10": 4.0, "y2": 3.5, "y3m": 4.5}]
    snapshot_store.put("treasury", "", "2025-03-10", treasury_snap, fetched_at="2026-09-11T10:00:00+00:00")

    # BLS snapshots - unemployment month before d
    bls_unemp = [{"month": "2025-02", "value": 4.5}]
    snapshot_store.put("bls", "unemployment", "2025-02", bls_unemp, fetched_at="2026-09-11T10:00:00+00:00")

    # CPI needs 13 months for YoY, all before d (include 2023-12 for year-ago comparison)
    cpi_rows = [{"month": f"2023-{m:02d}", "value": 3.0 + m * 0.01} for m in range(12, 13)] + \
               [{"month": f"2024-{m:02d}", "value": 3.0 + m * 0.01} for m in range(1, 13)]
    snapshot_store.put("bls", "cpi", "2024-12", cpi_rows, fetched_at="2026-09-11T10:00:00+00:00")

    run_id = "2024-04-23:AAPL"
    run_time = "2026-09-11T12:00:00+00:00"
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    macro_claims = [c for c in claim_list if c["source"] in ("treasury", "bls")]
    assert len(macro_claims) > 0, f"No macro claims. d={d}, claims={claim_list}"

    # Check treasury claims
    treasury_claims = [c for c in macro_claims if c["source"] == "treasury"]
    assert any(c["metric"] == "y10" for c in treasury_claims)
    assert any(c["metric"] == "curve_10y_2y" for c in treasury_claims)

    # Check BLS claims
    bls_claims = [c for c in macro_claims if c["source"] == "bls"]
    assert any(c["metric"] == "unemployment" for c in bls_claims)
    assert any(c["metric"] == "cpi_yoy" for c in bls_claims)

    # Verify all macro claims with check_claim
    treasury_snap_id, treasury_payload = snapshot_store.get_with_id("treasury", "", run_time)
    bls_unemp_id, bls_unemp_payload = snapshot_store.get_with_id("bls", "unemployment", run_time)
    bls_cpi_id, bls_cpi_payload = snapshot_store.get_with_id("bls", "cpi", run_time)

    for c in macro_claims:
        # Every snapshot-source claim MUST have a source_path
        assert c["source_path"] is not None, f"source_path is None for snapshot-source claim {c['metric']}"
        if c["source"] == "treasury":
            ok = claims.check_claim(c, treasury_payload)
            assert ok, f"check_claim failed for treasury {c['metric']} (path={c['source_path']}, text_span={c['text_span']})"
        elif c["source"] == "bls" and c["metric"] == "unemployment":
            ok = claims.check_claim(c, bls_unemp_payload)
            assert ok, f"check_claim failed for bls unemployment (path={c['source_path']})"
        elif c["source"] == "bls" and c["metric"] == "cpi_yoy":
            ok = claims.check_claim(c, bls_cpi_payload)
            assert ok, f"check_claim failed for bls cpi_yoy (path={c['source_path']}, text_span={c['text_span']})"


def test_migration_upgrade_downgrade(tmp_path):
    """Migration 0004 upgrade then downgrade works on a temp SQLite DB."""
    from sqlalchemy import create_engine, inspect, text
    from app.migrate import upgrade, downgrade

    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False})
    db.metadata.create_all(eng)

    # Upgrade
    with eng.begin() as conn:
        upgrade(conn)
    assert "claims" in inspect(eng).get_table_names()
    assert "committee_narratives" in inspect(eng).get_table_names()

    # Check claims columns
    cols = {c["name"] for c in inspect(eng).get_columns("claims")}
    expected = {"id", "run_id", "ticker", "metric", "value", "unit", "period", "source", "source_snapshot_id", "source_path", "text_span", "created_at"}
    assert cols == expected

    # Check indexes
    indexes = {idx["name"] for idx in inspect(eng).get_indexes("claims")}
    assert "ix_claims_run_id" in indexes

    # Check committee_narratives columns
    cols = {c["name"] for c in inspect(eng).get_columns("committee_narratives")}
    expected = {"run_id", "narrative", "status", "attempts", "provider_requested", "model_requested", "provider_answered", "model_answered", "error", "created_at"}
    assert cols == expected

    # Check triggers exist (append-only)
    with eng.connect() as conn:
        triggers = conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='claims'")).fetchall()
    trigger_names = {row[0] for row in triggers}
    assert "claims_no_update" in trigger_names
    assert "claims_no_delete" in trigger_names

    # Downgrade
    with eng.begin() as conn:
        downgrade("base", conn)
    assert "claims" not in inspect(eng).get_table_names()
    assert "committee_narratives" not in inspect(eng).get_table_names()

    # Older tables still exist (only db.metadata tables, since downgrade to "base" rolls back all migrations)
    remaining = set(inspect(eng).get_table_names())
    # Tables created by db.metadata.create_all() - these are the "older tables" not managed by migrations
    expected_older = {"users", "committee_runs", "paper_accounts", "price_bars", "agents", "audit_log", "blobs", "daily_snapshots", "feedback", "jobs", "llm_calls", "notifications", "orchestrations", "page_views", "paper_meta", "paper_signals", "qa_messages", "report_narratives"}
    assert expected_older.issubset(remaining), f"Missing older tables: {expected_older - remaining}"
    # Migration-managed tables should be gone
    assert "feature_flags" not in remaining
    assert "source_snapshots" not in remaining
    assert "ledger_calls" not in remaining
    assert "claims" not in remaining
    assert "committee_narratives" not in remaining


def test_check_claim_rejects_self_referential_formula():
    """(a) value=X, text_span='derived: c | c=X' -> False (no constants from claim)."""
    claim = {
        "id": "test-claim",
        "metric": "revenue_growth",
        "value": 0.1,
        "source": "sec_facts",
        "source_path": "/concepts/revenue/series/0/val",
        "text_span": "derived: c | c=0.1",
    }
    payload = {"concepts": {"revenue": {"series": [{"val": 100.0}]}}}
    # Should fail because formula doesn't match FORMULAS table and uses direct constant
    assert claims.check_claim(claim, payload) is False


def test_check_claim_rejects_malicious_formula():
    """(b) text_span containing __import__('os') -> False and no import happens."""
    claim = {
        "id": "test-claim",
        "metric": "revenue_growth",
        "value": 0.1,
        "source": "sec_facts",
        "source_path": "/concepts/revenue/series/0/val",
        "text_span": "derived: __import__('os') | x=/concepts/revenue/series/0/val",
    }
    payload = {"concepts": {"revenue": {"series": [{"val": 100.0}]}}}
    # Should fail - formula doesn't match, and no import should happen
    assert claims.check_claim(claim, payload) is False


def test_check_claim_rejects_formula_mismatch():
    """(c) formula text differing from the table -> False."""
    claim = {
        "id": "test-claim",
        "metric": "revenue_growth",
        "value": 0.1,
        "source": "sec_facts",
        "source_path": "/concepts/revenue/series/0/val",
        "text_span": "derived: (revenue - revenue_prior) / revenue_prior + 0.0 | revenue=/concepts/revenue/series/1/val | revenue_prior=/concepts/revenue/series/0/val",
    }
    payload = {
        "concepts": {
            "revenue": {"series": [{"val": 100.0}, {"val": 110.0}]}
        }
    }
    # Formula has extra "+ 0.0" - doesn't match table exactly
    assert claims.check_claim(claim, payload) is False


def test_check_claim_all_existing_derived_pass(tmp_path, monkeypatch):
    """(d) Every existing derived claim still passes check_claim."""
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    monkeypatch.setenv("SEC_USER_AGENT", "GlassBox test ops@example.com")
    import importlib
    importlib.reload(free_data)
    importlib.reload(snapshot_store)

    # Create a comprehensive fundamentals snapshot
    fund_cache = {
        "cik": 320193,
        "fetched_at": "2026-09-15T10:00:00+00:00",
        "concepts": {
            "revenue": {
                "tag": "Revenues", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 100.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 110.0, "filed": "2025-02-01"},
                ],
            },
            "net_income": {
                "tag": "NetIncomeLoss", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 20.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 25.0, "filed": "2025-02-01"},
                ],
            },
            "equity": {
                "tag": "StockholdersEquity", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 50.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 55.0, "filed": "2025-02-01"},
                ],
            },
            "operating_income": {
                "tag": "OperatingIncomeLoss", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 22.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 28.0, "filed": "2025-02-01"},
                ],
            },
            "op_cash_flow": {
                "tag": "NetCashProvidedByUsedInOperatingActivities", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 30.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 35.0, "filed": "2025-02-01"},
                ],
            },
            "capex": {
                "tag": "PaymentsToAcquirePropertyPlantAndEquipment", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 5.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 6.0, "filed": "2025-02-01"},
                ],
            },
            "long_term_debt": {
                "tag": "LongTermDebtNoncurrent", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 10.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 12.0, "filed": "2025-02-01"},
                ],
            },
            "liabilities": {
                "tag": "Liabilities", "unit": "USD",
                "series": [
                    {"end": "2023-12-31", "val": 40.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "val": 45.0, "filed": "2025-02-01"},
                ],
            },
            "eps": {
                "tag": "EarningsPerShareDiluted", "unit": "USD/shares",
                "series": [
                    {"end": "2023-12-31", "start": "2023-01-01", "val": 2.0, "filed": "2024-02-01"},
                    {"end": "2024-12-31", "start": "2024-01-01", "val": 2.5, "filed": "2025-02-01"},
                ],
            },
        },
    }
    snapshot_store.put("sec_facts", "AAPL", "2024-12-31", fund_cache, fetched_at="2026-09-15T10:00:00+00:00")

    # Treasury snapshot for macro derived claims
    treasury_snap = [{"date": "2025-03-10", "y10": 4.5, "y2": 3.8, "y3m": 5.0}]
    snapshot_store.put("treasury", "", "2025-03-10", treasury_snap, fetched_at="2026-09-11T10:00:00+00:00")

    # CPI snapshot for cpi_yoy (need 13 months)
    cpi_rows = [{"month": f"2023-{m:02d}", "value": 300.0 + m} for m in range(1, 13)] + \
               [{"month": f"2024-{m:02d}", "value": 310.0 + m} for m in range(1, 13)]
    snapshot_store.put("bls", "cpi", "2024-12", cpi_rows, fetched_at="2026-09-11T10:00:00+00:00")

    # Also need treasury data for 3m ago for y10_change_3m
    treasury_3m_ago = [{"date": "2024-12-10", "y10": 4.2, "y2": 3.5, "y3m": 4.7}]
    snapshot_store.put("treasury", "", "2024-12-10", treasury_3m_ago, fetched_at="2026-09-11T09:00:00+00:00")

    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = "2026-09-15T12:00:00+00:00"
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    # Get payloads
    sec_snap = snapshot_store.get_with_id("sec_facts", "AAPL", run_time)
    treasury_snap_id, treasury_payload = snapshot_store.get_with_id("treasury", "", run_time)
    bls_cpi_id, bls_cpi_payload = snapshot_store.get_with_id("bls", "cpi", run_time)

    sec_payload = sec_snap[1] if sec_snap else None
    close_price = book.close["AAPL"][d]
    prices = {d: close_price}

    # Verify all derived claims pass
    derived_claims = [c for c in claim_list if (c.get("text_span") or "").startswith("derived:")]
    assert len(derived_claims) > 0, "No derived claims created"

    for c in derived_claims:
        if c["source"] == "sec_facts":
            ok = claims.check_claim(c, sec_payload, prices=prices if c["metric"] == "pe" else None)
        elif c["source"] == "treasury":
            ok = claims.check_claim(c, treasury_payload)
        elif c["source"] == "bls":
            ok = claims.check_claim(c, bls_cpi_payload)
        else:
            continue
        assert ok, f"check_claim failed for {c['metric']} (text_span={c['text_span']})"


def test_check_claim_pe_with_and_without_prices(tmp_path, monkeypatch):
    """(e) PE passes with prices={date: close} and fails without."""
    monkeypatch.setenv("FREE_DATA_DIR", str(tmp_path / "free_data"))
    monkeypatch.setenv("SEC_USER_AGENT", "GlassBox test ops@example.com")
    import importlib
    importlib.reload(free_data)
    importlib.reload(snapshot_store)

    fund_cache = {
        "cik": 320193,
        "fetched_at": "2026-09-15T10:00:00+00:00",
        "concepts": {
            "eps": {
                "tag": "EarningsPerShareDiluted", "unit": "USD/shares",
                "series": [{"end": "2024-12-31", "start": "2024-01-01", "val": 2.5, "filed": "2025-02-01"}],
            },
        },
    }
    snapshot_store.put("sec_facts", "AAPL", "2024-12-31", fund_cache, fetched_at="2026-09-15T10:00:00+00:00")

    book, d = make_book()
    run_id = "2024-04-23:AAPL"
    run_time = "2026-09-15T12:00:00+00:00"
    claim_list = claims.build_claims(run_id, "AAPL", d, book, run_time)

    pe_claims = [c for c in claim_list if c["metric"] == "pe"]
    assert len(pe_claims) == 1
    pe_claim = pe_claims[0]

    snap = snapshot_store.get_with_id("sec_facts", "AAPL", run_time)
    payload = snap[1] if snap else None

    close_price = book.close["AAPL"][d]
    prices = {d: close_price}

    # Should pass with prices
    ok_with = claims.check_claim(pe_claim, payload, prices=prices)
    assert ok_with, "PE check_claim should pass with prices dict"

    # Should fail without prices
    ok_without = claims.check_claim(pe_claim, payload, prices=None)
    assert not ok_without, "PE check_claim should fail without prices dict"


# Import select for the test
from sqlalchemy import select


def test_rerunning_claims_for_a_run_reuses_them_instead_of_duplicating():
    """Found in production 2026-09-26: the claims table is append-only, so re-running the step for a saved decision must reuse its claims, not add duplicates."""
    from sqlalchemy import func, select

    book, d = make_book()
    run_id = f"{d}:AAPL"
    run_time = datetime.now(timezone.utc).isoformat()
    first = claims.build_claims(run_id, "AAPL", d, book, run_time)
    claims.build_claims(run_id, "AAPL", d, book, run_time)
    with db.engine.connect() as c:
        n = c.execute(select(func.count()).select_from(claims_table).where(claims_table.c.run_id == run_id)).scalar()
    assert first and n == len(first)
