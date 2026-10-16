"""R2 pipeline (research/r2/r2.py): build -> gate -> review -> report on synthetic clean runs, no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "research" / "r2"))
import r2  # noqa: E402

from tests.test_r2_inject import clean_run  # noqa: E402


def runs(n=25):
    out = []
    for i in range(n):
        inp = clean_run()
        sym = f"S{i % 10}"
        out.append({"run_id": f"2026-09-18:{sym}-{i}", "symbol": sym, "inputs": inp, "stored_results": []})
    bad = clean_run()
    bad["claims"][1]["value"] = 99.0  # eps no longer matches its source: must be excluded, not planted into
    out.append({"run_id": "2026-09-18:BAD", "symbol": "BAD", "inputs": bad, "stored_results": []})
    return out


class FakeClient:
    """Flags the claim whose value differs from its source; otherwise answers 'no problems'. One bad JSON answer."""

    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        ev = json.loads(kw["messages"][0]["content"])
        probs = [{"claim_id": c["id"], "reason": "mismatch"} for c in ev["claims"]
                 if isinstance(c.get("source_value"), (int, float)) and c["metric"] == "eps" and c["value"] != c["source_value"]]
        text = "not json" if self.calls == 1 else json.dumps({"problems": probs})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], model=r2.REVIEW_MODEL, stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=50))


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.setattr(r2, "HERE", tmp_path)
    monkeypatch.setattr(r2, "DATA", tmp_path / "data")
    src = tmp_path / "export.jsonl"
    r2.write_jsonl(src, runs())
    return src


def test_build_gate_review_report(workdir):
    m = r2.build(workdir)
    assert m["runs_exported"] == 26 and m["runs_clean"] == 25 and m["runs_excluded_gate_fail"] == ["2026-09-18:BAD"]
    assert m["per_type"]["C0"] == 100 and m["per_type"]["C1"] == 100
    assert m["distinct_symbols"] == 10
    insts = r2.read_jsonl(r2.DATA / "instances.jsonl")
    per_run_type = {}
    for i in insts:
        per_run_type[(i["source_run_id"], i["error_type"])] = per_run_type.get((i["source_run_id"], i["error_type"]), 0) + 1
    assert max(per_run_type.values()) <= r2.PER_RUN_CAP
    assert "2026-09-18:BAD" not in {i["source_run_id"] for i in insts}

    gate = {r["instance_id"]: r for r in r2.run_gate()}
    for i in insts:  # the synthetic run is clean, so the gate gives exactly the design status (as in test_r2_inject)
        assert r2._status(gate[i["instance_id"]]) == i["expected_status"], i["instance_id"]

    fake = FakeClient()
    r2.run_review(dry_run=False, limit=40, client=fake)
    r2.run_review(dry_run=False, limit=40, client=fake)  # resumable: the second pass makes no calls
    assert fake.calls == 40

    out = r2.report()
    assert out["pooled"]["gate_fail"]["false_alarm_C0"]["k"] == 0
    assert out["pooled"]["gate_fail"]["blind_spots"]["k"] == 0
    assert out["by_type"]["E1b"]["gate_fail"]["rate"] == 1.0
    assert out["by_type"]["E1b"]["gate_flag_from_expected_check"]["rate"] == 1.0
    assert out["cost"]["llm"]["calls"] == 40 and out["cost"]["llm"]["parse_errors"] == 1
    assert out["cost"]["llm"]["usd"] == pytest.approx(40 * (1000 * 1.0 + 50 * 5.0) / 1e6)
    assert "## By error type" in (r2.HERE / "RESULTS.md").read_text(encoding="utf-8")


def test_a_changed_data_set_is_refused(workdir):
    r2.build(workdir)
    p = r2.DATA / "instances.jsonl"
    p.write_text(p.read_text(encoding="utf-8").replace('"seed": 0', '"seed": 1', 1), encoding="utf-8")
    with pytest.raises(SystemExit):
        r2.run_gate()


def test_wilson_and_parse():
    p, lo, hi = r2.wilson(0, 50)
    assert p == 0 and lo == 0 and 0.06 < hi < 0.08  # design §7: ~6% upper bound with 0 misses in 50
    assert r2.parse_review('{"problems": []}') == ([], None)
    assert r2.parse_review("```json\n{\"problems\": [{\"claim_id\": \"x\"}]}\n```")[0] == [{"claim_id": "x"}]
    assert r2.parse_review("no")[0] is None
