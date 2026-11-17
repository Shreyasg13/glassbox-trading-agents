"""R2 injector (research/r2/inject.py): every planted error type gets the gate status the design predicts."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "r2"))
import inject  # noqa: E402

from app import snapshot_store  # noqa: E402

FACTS = {"concepts": {"net_income": {"series": [{"val": 10.0}, {"val": 8.0}]},
                      "revenue": {"series": [{"val": 100.0}, {"val": 90.0}]},
                      "eps": {"series": [{"val": 2.5}, {"val": 2.0}]}}}
NM_SPAN = "derived: net_income / revenue | net_income=/concepts/net_income/series/0/val | revenue=/concepts/revenue/series/0/val"


def clean_run():
    meta = {"fetched_at": "2026-09-18T10:00:00+00:00", "as_of": "2026-09-01", "payload_hash": snapshot_store._payload_hash(FACTS)}
    claims = [
        {"id": "nm", "run_id": "r", "metric": "net_margin", "source": "sec_facts", "value": 0.1, "unit": "pct",
         "source_path": "/concepts/net_income/series/0/val", "text_span": NM_SPAN},
        {"id": "eps", "run_id": "r", "metric": "eps", "source": "sec_facts", "value": 2.5, "unit": "USD",
         "source_path": "/concepts/eps/series/0/val", "text_span": "eps"},
        {"id": "px", "run_id": "r", "metric": "close", "source": "pricebook", "value": 150.0, "unit": "USD", "period": "2026-09-18"},
        {"id": "rs", "run_id": "r", "metric": "risk_score", "source": "risk", "value": 0.4, "unit": "ratio"},
    ]
    return {"claims": claims, "snapshots_by_claim_id": {"nm": (meta, FACTS), "eps": (meta, FACTS)},
            "run_time": "2026-09-18T12:00:00+00:00", "run_date": "2026-09-18", "prices": {"2026-09-18": 150.0},
            "recomputed_risk": {"score": 0.4}, "windows": None,
            "narrative_row": {"status": "ok", "narrative": "Margin was {{claim:nm}}. EPS was {{claim:eps}}. Close was {{claim:px}}."}}


def test_the_clean_run_passes():
    assert inject.gate_status(clean_run()) == "pass"


@pytest.mark.parametrize("error_type", sorted(inject.EXPECTED))
@pytest.mark.parametrize("seed", range(12))
def test_planted_error_gets_the_expected_status(error_type, seed):
    base = clean_run()
    planted, label = inject.plant(base, error_type, seed)
    assert inject.gate_status(planted) == label["expected_status"] == inject.EXPECTED[error_type]
    assert inject.gate_status(base) == "pass"  # the source run is never mutated


def test_plant_is_deterministic_per_seed():
    a, la = inject.plant(clean_run(), "E1b", 7)
    b, lb = inject.plant(clean_run(), "E1b", 7)
    assert la == lb and a == b


def test_a_run_without_a_narrative_is_not_applicable_for_narrative_errors():
    run = clean_run()
    run["narrative_row"] = None
    with pytest.raises(inject.NotApplicable):
        inject.plant(run, "E8a", 0)


def test_e5_targets_only_fresh_claims():
    run = clean_run()
    meta, payload = run["snapshots_by_claim_id"]["nm"]
    run["snapshots_by_claim_id"]["nm"] = (dict(meta, as_of="2020-01-01"), payload)
    for seed in range(100):
        _, label = inject.plant(run, "E5", seed)
        assert label["target_claim_id"] == "eps"
    assert inject.gate.check_staleness(run["claims"][1], meta, run["run_date"]).status == "pass"


def test_e5_without_fresh_claims_cannot_plant():
    run = clean_run()
    run["snapshots_by_claim_id"]["nm"][0]["as_of"] = "2020-01-01"
    with pytest.raises(inject.NotApplicable):
        inject.plant(run, "E5", 0)
