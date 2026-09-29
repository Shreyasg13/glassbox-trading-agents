"""Tests for scoring API routes (S3 T12c).

Tests:
- GET /api/me/ledger-track-record (user): returns scored calls + per-horizon metrics
- GET /api/public/track-record/summary (public): per-horizon metrics only, no call_ids/symbols
- Regression: GET /api/me/track-record still works
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import db, scoring
from app.main import app
from app.migrated_tables import call_outcomes_table, ledger_calls_table, migrated_metadata


def _hdr(sub: str, role: str) -> dict[str, str]:
    from app.auth import create_access_token, TokenPayload
    return {"Authorization": "Bearer " + create_access_token(TokenPayload(sub=sub, role=role))}


def _seed_ledger_and_outcomes(real_db):
    """Seed ledger_calls and call_outcomes with test data."""
    # Create migrated tables in the test database
    migrated_metadata.create_all(real_db.engine)

    # Ledger calls - committee decisions
    calls_data = [
        {
            "seq": 1,
            "call_id": "2026-09-28:AAPL",
            "ticker": "AAPL",
            "call_type": "committee_decision",
            "payload_json": json.dumps({"decision": "BUY", "confidence": 80}),
            "input_snapshot_ids": "[]",
            "committee_config_id": None,
            "recorded_at": "2026-09-28T20:00:00+00:00",
            "prev_hash": "0" * 64,
            "hash": "a" * 64,
        },
        {
            "seq": 2,
            "call_id": "2026-09-28:MSFT",
            "ticker": "MSFT",
            "call_type": "committee_decision",
            "payload_json": json.dumps({"decision": "SELL", "confidence": 70}),
            "input_snapshot_ids": "[]",
            "committee_config_id": None,
            "recorded_at": "2026-09-28T20:00:00+00:00",
            "prev_hash": "a" * 64,
            "hash": "b" * 64,
        },
        {
            "seq": 3,
            "call_id": "2026-09-29:AAPL",
            "ticker": "AAPL",
            "call_type": "committee_decision",
            "payload_json": json.dumps({"decision": "HOLD", "confidence": 50}),
            "input_snapshot_ids": "[]",
            "committee_config_id": None,
            "recorded_at": "2026-09-29T20:00:00+00:00",
            "prev_hash": "b" * 64,
            "hash": "c" * 64,
        },
        # Pre-ledger call (before cutoff) - should not be scored
        {
            "seq": 4,
            "call_id": "2026-09-25:GOOG",
            "ticker": "GOOG",
            "call_type": "committee_decision",
            "payload_json": json.dumps({"decision": "BUY", "confidence": 90}),
            "input_snapshot_ids": "[]",
            "committee_config_id": None,
            "recorded_at": "2026-09-25T20:00:00+00:00",
            "prev_hash": "c" * 64,
            "hash": "d" * 64,
        },
    ]

    # Outcomes for the first three calls (horizons 1, 5, 20)
    # Using simple prices: entry=100, exit=101 for 1-day; entry=100, exit=105 for 5-day; etc.
    outcomes_data = []
    for call_id, decision in [
        ("2026-09-28:AAPL", "BUY"),
        ("2026-09-28:MSFT", "SELL"),
        ("2026-09-29:AAPL", "HOLD"),
    ]:
        for horizon in (1, 5, 20):
            # Simple outcome: forward_return=0.01, benchmark=0.005, excess=0.005
            # score = sign(decision) * excess
            sign = 1 if decision == "BUY" else (-1 if decision == "SELL" else 0)
            excess = 0.005
            forward_return = 0.01
            benchmark_return = 0.005
            score = sign * excess

            outcome_json = json.dumps({
                "entry_date": "2026-09-29",
                "exit_date": "2026-09-30",
                "entry_price": 100.0,
                "exit_price": 101.0,
                "forward_return": round(forward_return, 6),
                "benchmark_return": round(benchmark_return, 6),
                "excess_return": round(excess, 6),
            })

            outcomes_data.append({
                "call_id": call_id,
                "horizon": horizon,
                "evaluated_at": "2026-10-15T12:00:00+00:00",
                "outcome_json": outcome_json,
                "score": round(score, 6),
            })

    with real_db.engine.connect() as conn:
        conn.execute(ledger_calls_table.insert(), calls_data)
        conn.execute(call_outcomes_table.insert(), outcomes_data)
        conn.commit()


class TestLedgerTrackRecordRoute:
    """Tests for GET /api/me/ledger-track-record (user role)."""

    def test_401_without_login(self, real_db, monkeypatch):
        # No need to monkeypatch - the route handler does the work inline
        with TestClient(app) as client:
            r = client.get("/api/me/ledger-track-record")
            assert r.status_code == 401

    def test_200_for_normal_user(self, real_db, monkeypatch):
        _seed_ledger_and_outcomes(real_db)
        with TestClient(app) as client:
            r = client.get("/api/me/ledger-track-record", headers=_hdr("viewer", "viewer"))
            assert r.status_code == 200
            body = r.json()
            # Check structure
            assert "scored_calls" in body
            assert "metrics" in body
            assert "ledger_calls" in body
            assert "pre_ledger_note" in body
            # Should have 3 scored calls (one per outcome row for 3 calls * 3 horizons = 9 rows)
            assert len(body["scored_calls"]) == 9
            # Check a scored call has all required fields
            sc = body["scored_calls"][0]
            assert set(sc.keys()) == {
                "call_id", "symbol", "decision", "confidence", "recorded_at",
                "recorded_label", "horizon", "entry_date", "exit_date",
                "forward_return", "benchmark_return", "excess_return", "right"
            }
            # Check recorded_label
            assert sc["recorded_label"] == "recorded before outcome"
            # Check metrics for horizons 1, 5, 20
            assert set(body["metrics"].keys()) == {"1", "5", "20"}
            for h in ("1", "5", "20"):
                m = body["metrics"][h]
                assert m["count"] == 3  # 3 calls per horizon
                # With n=3 < min_n=10, metrics are None with reason "too few calls"
                assert m["hit_rate"] is None
                assert m["mean_excess_return"] is None
                assert m["rank_ic"] is None
                assert m["reason"] == "too few calls"
            # ledger_calls count should include the pre-ledger call (4 total)
            assert body["ledger_calls"] == 4
            # pre_ledger_note present
            assert "pre-ledger" in body["pre_ledger_note"].lower()

    def test_metrics_match_scoring_metrics_on_same_rows(self, real_db, monkeypatch):
        _seed_ledger_and_outcomes(real_db)
        with TestClient(app) as client:
            r = client.get("/api/me/ledger-track-record", headers=_hdr("viewer", "viewer"))
            assert r.status_code == 200
            body = r.json()

            # Manually compute metrics using scoring.metrics on the same data
            scored_calls = body["scored_calls"]
            by_horizon = {}
            for sc in scored_calls:
                by_horizon.setdefault(sc["horizon"], []).append(sc)

            for h in (1, 5, 20):
                horizon_calls = by_horizon[h]
                # Convert to format expected by scoring.metrics
                metric_input = []
                for sc in horizon_calls:
                    metric_input.append({
                        "decision": sc["decision"],
                        "confidence": sc["confidence"],
                        "score": sc["excess_return"] if sc["decision"] == "BUY" else (-sc["excess_return"] if sc["decision"] == "SELL" else 0),
                        "excess_return": sc["excess_return"],
                        "forward_return": sc["forward_return"],
                    })
                expected = scoring.metrics(metric_input)
                actual = body["metrics"][str(h)]
                assert actual["count"] == expected["count"]
                # With n=3 < min_n=10, all metrics are None
                assert actual["hit_rate"] == expected["hit_rate"]
                assert actual["mean_excess_return"] == expected["mean_excess_return"]
                assert actual["rank_ic"] == expected["rank_ic"]
                assert actual["rank_ic_t"] == expected["rank_ic_t"]
                assert actual["brier"] == expected["brier"]
                assert actual["calibration"] == expected["calibration"]
                assert actual["reason"] == expected["reason"]


class TestPublicTrackRecordSummaryRoute:
    """Tests for GET /api/public/track-record/summary (public role)."""

    def test_200_without_login(self, real_db, monkeypatch):
        _seed_ledger_and_outcomes(real_db)
        with TestClient(app) as client:
            r = client.get("/api/public/track-record/summary")
            assert r.status_code == 200
            body = r.json()
            # Should have horizons 1, 5, 20
            assert set(body.keys()) == {"1", "5", "20"}
            for h in ("1", "5", "20"):
                m = body[h]
                assert set(m.keys()) == {"count", "hit_rate", "mean_excess_return", "rank_ic"}
                assert m["count"] == 3

    def test_no_call_id_or_symbol_in_response(self, real_db, monkeypatch):
        _seed_ledger_and_outcomes(real_db)
        with TestClient(app) as client:
            r = client.get("/api/public/track-record/summary")
            assert r.status_code == 200
            # Assert on JSON text to catch any accidental inclusion
            text = r.text
            assert "call_id" not in text
            assert "symbol" not in text
            assert "AAPL" not in text
            assert "MSFT" not in text
            assert "GOOG" not in text


class TestTrackRecordRegression:
    """Regression test: GET /api/me/track-record still returns the same keys."""

    def test_track_record_keys_unchanged(self, real_db, monkeypatch):
        # The existing track_record endpoint uses strategy.track_record()
        # which reads from paper trading accounts. Just verify it returns
        # the expected top-level keys without erroring.
        from app import paper_cycle, strategy, paper
        from tests.test_paper_cycle import make_book, day, N_SHORT

        # Bootstrap paper trading so track_record has data
        paper_cycle.run_cycle(bootstrap=True, start=day(0), book=make_book(N_SHORT))
        monkeypatch.setattr(strategy, "cached_book", lambda: make_book(N_SHORT))

        with TestClient(app) as client:
            r = client.get("/api/me/track-record", headers=_hdr("viewer", "viewer"))
            assert r.status_code == 200
            body = r.json()
            # Expected keys from TrackRecord type
            assert set(body.keys()) == {"initialised", "live_from", "strategies", "curves", "tax_assumptions"}
            assert body["initialised"] is True
            assert isinstance(body["strategies"], list)
            assert isinstance(body["curves"], dict)
            # Tax assumptions from paper module
            assert body["tax_assumptions"] == {"short_term": paper.TAX_ST, "long_term": paper.TAX_LT}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])