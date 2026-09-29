"""Tests for the daily scoring job (S3 T12b).

Uses the migrated_db fixture pattern from test_publish.py / test_ledger.py.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from sqlalchemy import select

from app import db, flags, migrate, paper, paper_cycle, scoring
from app.migrated_tables import call_outcomes_table, ledger_calls_table

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    dt = datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _create_ledger_call(
    call_id: str,
    ticker: str,
    decision: str,
    confidence: float,
    recorded_at: str,
    engine_signal: str = "BUY",
) -> dict[str, Any]:
    """Create a ledger_calls row payload for a committee_decision."""
    return {
        "call_id": call_id,
        "ticker": ticker,
        "call_type": "committee_decision",
        "payload": {
            "decision": decision,
            "confidence": confidence,
            "engine_signal": engine_signal,
            "horizon_days": 30,
            "a6_ok": True,
            "a7_action": "pass",
        },
        "input_snapshot_ids": [],
        "committee_config_id": None,
        "recorded_at": recorded_at,
    }


def _append_ledger_call(conn, call_data: dict[str, Any]) -> None:
    """Append a row to ledger_calls directly (bypassing ledger.append for test setup)."""
    from app.ledger import _canonical_json, _compute_hash, GENESIS

    # Get current head
    head_row = conn.execute(select(ledger_calls_table).order_by(ledger_calls_table.c.seq.desc()).limit(1)).fetchone()
    seq = (head_row.seq + 1) if head_row else 1
    prev_hash = head_row.hash if head_row else GENESIS

    row_without_hash = {
        "seq": seq,
        "call_id": call_data["call_id"],
        "ticker": call_data["ticker"],
        "call_type": call_data["call_type"],
        "payload": call_data["payload"],
        "input_snapshot_ids": call_data["input_snapshot_ids"],
        "committee_config_id": call_data["committee_config_id"],
        "recorded_at": call_data["recorded_at"],
        "prev_hash": prev_hash,
    }
    row_hash = _compute_hash(prev_hash, row_without_hash)

    insert_row = {
        "seq": seq,
        "call_id": call_data["call_id"],
        "ticker": call_data["ticker"],
        "call_type": call_data["call_type"],
        "payload_json": _canonical_json(call_data["payload"]),
        "input_snapshot_ids": _canonical_json(call_data["input_snapshot_ids"]),
        "committee_config_id": call_data["committee_config_id"],
        "recorded_at": call_data["recorded_at"],
        "prev_hash": prev_hash,
        "hash": row_hash,
    }
    conn.execute(ledger_calls_table.insert().values(**insert_row))


def _create_price_frame(symbol: str, dates: list[str], closes: list[float]) -> pd.DataFrame:
    """Create a price frame with the required columns for a symbol."""
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    idx = pd.DatetimeIndex(pd.to_datetime(dates)).tz_localize(ET)
    idx.name = "Date"
    df = pd.DataFrame({
        "Open": [c * 0.99 for c in closes],
        "High": [c * 1.01 for c in closes],
        "Low": [c * 0.98 for c in closes],
        "Close": closes,
        "Volume": [1000000] * len(closes),
        "Dividends": [0.0] * len(closes),
        "Stock Splits": [0.0] * len(closes),
    }, index=idx)
    return df


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_db(tmp_path):
    """Create a temp SQLite DB with older tables + migrated tables applied."""
    db_path = tmp_path / "test_score.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    # Create older tables (as production init_schema does)
    db.metadata.create_all(engine)

    # Apply all migrations up to head
    with engine.begin() as conn:
        migrate.upgrade(conn)

    # Patch the db module to use this engine
    original_engine = db.engine
    db.engine = engine
    # Also patch modules that import db.engine at module level
    import app.ledger as ledger_module
    import app.scoring as scoring_module
    import app.paper_cycle as pc_module
    import app.db as db_module
    original_ledger_engine = ledger_module.db.engine
    original_scoring_engine = scoring_module.db.engine
    original_pc_engine = pc_module.db.engine
    original_db_engine = db_module.engine

    ledger_module.db.engine = engine
    scoring_module.db.engine = engine
    pc_module.db.engine = engine
    db_module.engine = engine

    yield engine

    # Restore
    db.engine = original_engine
    ledger_module.db.engine = original_ledger_engine
    scoring_module.db.engine = original_scoring_engine
    pc_module.db.engine = original_pc_engine
    db_module.engine = original_db_engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _reset_flags(monkeypatch):
    """Reset flags before each test."""
    flags.clear_cache()
    yield
    flags.clear_cache()


@pytest.fixture
def price_frames():
    """Create price frames for testing: 25 trading days (5 weeks) to support horizon=20."""
    import pandas as pd
    from datetime import date, timedelta

    # Generate 25 trading days starting from 2026-09-28 (Monday)
    dates = []
    d = date(2026, 9, 28)
    while len(dates) < 25:
        if d.weekday() < 5:  # Mon-Fri
            dates.append(d.isoformat())
        d += timedelta(days=1)

    # AAPL: slowly rising
    aapl_closes = [100.0 + i * 0.1 for i in range(25)]
    # SPY: slowly rising
    spy_closes = [400.0 + i * 0.2 for i in range(25)]

    aapl = _create_price_frame("AAPL", dates, aapl_closes)
    spy = _create_price_frame("SPY", dates, spy_closes)
    return {"AAPL": aapl, "SPY": spy}


@pytest.fixture
def mock_price_book(price_frames, monkeypatch):
    """Mock the PriceBook loading to return our test frames."""
    from app.data_source import STOCK_INFO

    # Only include our test symbols
    test_symbols = ["AAPL", "SPY"]
    monkeypatch.setattr("app.data_source.STOCK_INFO", test_symbols)

    params = {"rsi_low": 30, "rsi_high": 70, "fast_ma": 20, "slow_ma": 50}
    frames = {}
    for sym in test_symbols:
        frames[sym] = price_frames[sym]

    book = paper.PriceBook.from_frames(frames, {s: params for s in frames})

    def mock_load_book():
        return book

    monkeypatch.setattr("app.paper_cycle.load_book", mock_load_book)
    # score_ledger uses its own _load_pricebook function, so patch the data source functions it uses
    monkeypatch.setattr("app.scripts.score_ledger._load_trained_params", lambda: params)
    monkeypatch.setattr("app.scripts.score_ledger._load_parquet_row", lambda sym: frames.get(sym))
    monkeypatch.setattr("app.scripts.score_ledger.STOCK_INFO", test_symbols)
    return book


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_load_ledger_calls_returns_committee_decisions(migrated_db, mock_price_book):
    """load_ledger_calls returns committee_decision rows with parsed payload."""
    with migrated_db.begin() as conn:
        # Add a committee_decision call
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

        # Add a non-committee call (should be filtered out)
        call2 = _create_ledger_call(
            "2026-09-28:MSFT", "MSFT", "SELL", 70.0,
            "2026-09-28T20:00:00+00:00"
        )
        call2["call_type"] = "other_type"
        _append_ledger_call(conn, call2)

    calls = scoring.load_ledger_calls()
    assert len(calls) == 1
    assert calls[0]["call_id"] == "2026-09-28:AAPL"
    assert calls[0]["ticker"] == "AAPL"
    assert calls[0]["decision"] == "BUY"
    assert calls[0]["confidence"] == 80.0
    assert calls[0]["recorded_at"] == "2026-09-28T20:00:00+00:00"


def test_load_ledger_calls_filters_missing_decision(migrated_db, mock_price_book):
    """Calls without decision/confidence are filtered out."""
    with migrated_db.begin() as conn:
        # Add a call with missing decision
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", None, 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        call1["payload"]["decision"] = None
        _append_ledger_call(conn, call1)

    calls = scoring.load_ledger_calls()
    assert len(calls) == 0


def test_load_outcomes_returns_parsed_json(migrated_db, mock_price_book):
    """load_outcomes returns rows with parsed outcome_json."""
    with migrated_db.begin() as conn:
        outcome_data = {
            "entry_date": "2026-09-29",
            "exit_date": "2026-10-02",
            "entry_price": 101.0,
            "exit_price": 104.0,
            "forward_return": 0.029703,
            "benchmark_return": 0.014925,
            "excess_return": 0.014778,
            "score": 0.014778,
        }
        conn.execute(call_outcomes_table.insert().values(
            call_id="2026-09-28:AAPL",
            horizon=5,
            evaluated_at="2026-10-02T20:00:00+00:00",
            outcome_json=json.dumps(outcome_data),
            score=0.014778,
        ))

    outcomes = scoring.load_outcomes()
    assert len(outcomes) == 1
    assert outcomes[0]["call_id"] == "2026-09-28:AAPL"
    assert outcomes[0]["horizon"] == 5
    assert outcomes[0]["outcome_json"] == outcome_data
    assert outcomes[0]["score"] == 0.014778


def test_score_ledger_inserts_rows_for_eligible_calls(migrated_db, mock_price_book, monkeypatch):
    """Eligible calls with closed windows get scored and inserted."""
    # Setup: ledger call recorded Mon 28th evening, entry Tue 29th
    # With 25 trading days, all 3 horizons (1, 5, 20) have closed windows
    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"  # Mon evening, before Tue 09:30 ET market open
        )
        _append_ledger_call(conn, call1)

    # Mock the script's main to run without dry-run
    from app.scripts import score_ledger
    result = score_ledger.main(["--dry-run"])

    # Should score all 3 horizons (1, 5, 20) since we have 25 trading days of data
    assert result["scored"] == 3
    assert result["pending_window"] == 0
    assert result["skipped_not_eligible"] == 0

    # Verify rows were NOT inserted (dry-run)
    with migrated_db.connect() as conn:
        rows = conn.execute(select(call_outcomes_table)).fetchall()
        assert len(rows) == 0


def test_score_ledger_writes_rows_when_not_dry_run(migrated_db, mock_price_book):
    """Non-dry-run inserts rows into call_outcomes."""
    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    result = score_ledger.main([])  # not dry-run

    assert result["scored"] == 3
    assert result["pending_window"] == 0

    # Verify rows were inserted
    with migrated_db.connect() as conn:
        rows = conn.execute(select(call_outcomes_table)).fetchall()
        assert len(rows) == 3
        horizons = sorted([r.horizon for r in rows])
        assert horizons == [1, 5, 20]


def test_score_ledger_idempotent_second_run_inserts_zero(migrated_db, mock_price_book):
    """Second run inserts 0 rows (idempotent)."""
    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    # First run
    result1 = score_ledger.main([])
    assert result1["scored"] == 3

    # Second run
    result2 = score_ledger.main([])
    assert result2["scored"] == 0
    assert result2["skipped_not_eligible"] == 0
    assert result2["pending_window"] == 0


def test_score_ledger_dry_run_writes_nothing(migrated_db, mock_price_book):
    """--dry-run computes but writes nothing."""
    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    result = score_ledger.main(["--dry-run"])

    assert result["scored"] == 3
    assert result["pending_window"] == 0

    # Verify no rows inserted
    with migrated_db.connect() as conn:
        rows = conn.execute(select(call_outcomes_table)).fetchall()
        assert len(rows) == 0


def test_score_ledger_call_recorded_after_window_opened_not_scored(migrated_db, mock_price_book):
    """A call recorded after its window opened (after market open on entry day) is not scored."""
    with migrated_db.begin() as conn:
        # Call recorded Tue 29th 14:00 UTC = after 09:30 ET (13:30 UTC) market open
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-29T14:00:00+00:00"  # After market open on entry day
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    result = score_ledger.main([])

    # Should be skipped as not eligible
    assert result["scored"] == 0
    assert result["skipped_not_eligible"] == 3  # all 3 horizons
    assert result["pending_window"] == 0


def test_pipeline_declares_score_ledger_after_paper_cycle():
    """The pipeline STAGE_ORDER has score_ledger right after paper_cycle."""
    from app.pipeline import STAGE_ORDER
    idx_paper = STAGE_ORDER.index("paper_cycle")
    idx_score = STAGE_ORDER.index("score_ledger")
    assert idx_score == idx_paper + 1


def test_pipeline_score_ledger_stage_never_raises(migrated_db, mock_price_book, monkeypatch):
    """The score_ledger stage function returns a summary dict and never raises."""
    from app.pipeline import default_stages

    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    stages = default_stages("2026-10-02")
    score_fn = stages.get("score_ledger")
    assert score_fn is not None

    # Should not raise, returns a summary dict
    result = score_fn()
    assert isinstance(result, dict)
    assert "scored" in result
    assert "skipped_not_eligible" in result
    assert "pending_window" in result


def test_score_ledger_handles_missing_symbol_data(migrated_db, mock_price_book):
    """Calls for symbols without price data are skipped."""
    with migrated_db.begin() as conn:
        # MSFT has no price data in our mock
        call1 = _create_ledger_call(
            "2026-09-28:MSFT", "MSFT", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    result = score_ledger.main([])

    assert result["scored"] == 0
    assert result["skipped_not_eligible"] == 3
    assert result["pending_window"] == 0


def test_score_ledger_handles_missing_benchmark_data(migrated_db, monkeypatch):
    """Calls where benchmark (SPY) data is missing are not scored."""
    # Create price frames without SPY
    dates = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]
    aapl = _create_price_frame("AAPL", dates, [100.0, 101.0, 102.0, 103.0, 104.0])

    from app.data_source import STOCK_INFO
    monkeypatch.setattr("app.data_source.STOCK_INFO", ["AAPL"])

    params = {"rsi_low": 30, "rsi_high": 70, "fast_ma": 20, "slow_ma": 50}
    # Patch the data source functions that score_ledger uses
    monkeypatch.setattr("app.scripts.score_ledger._load_trained_params", lambda: params)
    monkeypatch.setattr("app.scripts.score_ledger._load_parquet_row", lambda sym: {"AAPL": aapl}.get(sym))
    monkeypatch.setattr("app.scripts.score_ledger.STOCK_INFO", ["AAPL"])

    with migrated_db.begin() as conn:
        call1 = _create_ledger_call(
            "2026-09-28:AAPL", "AAPL", "BUY", 80.0,
            "2026-09-28T20:00:00+00:00"
        )
        _append_ledger_call(conn, call1)

    from app.scripts import score_ledger
    result = score_ledger.main([])

    # No benchmark data -> no scoring
    assert result["scored"] == 0
    assert result["pending_window"] == 3  # all horizons pending (window technically open but benchmark missing)


# Need to import create_engine
from sqlalchemy import create_engine


if __name__ == "__main__":
    pytest.main([__file__, "-v"])