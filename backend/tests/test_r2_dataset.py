"""Synthetic-only coverage of the frozen R2 builder and gate detector."""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from tests.test_r2_inject import clean_run

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from research.r2 import dataset, inject, run_gate  # noqa: E402


def source_runs(n=20):
    return [dict(clean_run(), run_id=f"r{i:02}", symbol=f"SYM{i % 10}") for i in range(n)]


@pytest.fixture(scope="module")
def instances():
    return dataset.build(source_runs(), 42)


def test_deterministic_bytes_manifest_and_cap(tmp_path, instances):
    other = dataset.build(list(reversed(source_runs())), 42)
    dataset.write(instances, tmp_path / "a", gate_commit="frozen")
    dataset.write(other, tmp_path / "b", gate_commit="frozen")
    for name in ("instances.jsonl", "MANIFEST.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
    counts = Counter(row["error_type"] for row in instances)
    assert counts == {kind: 100 if kind.startswith("C") else 50 for kind in inject.EXPECTED}
    assert max(Counter((row["source_run_id"], row["error_type"]) for row in instances).values()) == 5
    assert len({row["instance_id"] for row in instances}) == 1000
    assert dataset.diversity_report(instances) == {"runs": 20, "symbols": 10, "met": True}
    assert dataset.build(source_runs(), 43) != instances
    manifest = json.loads((tmp_path / "a" / "MANIFEST.json").read_text())
    assert manifest["seed"] == 42 and manifest["gate_commit"] == "frozen"
    assert manifest["counts_per_type"] == counts
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((tmp_path / "a" / name).read_bytes()).hexdigest() == digest
    rows = [json.loads(line) for line in (tmp_path / "a" / "instances.jsonl").read_text().splitlines()]
    assert [row["instance_id"] for row in rows] == sorted(row["instance_id"] for row in rows)


def test_type_shortage_names_type_and_possible_count():
    runs = source_runs()
    for row in runs[9:]:
        row["narrative_row"] = None
    with pytest.raises(ValueError, match="E8a: required 50 instances, only 45 possible"):
        dataset.build(runs, 1)


def test_diversity_enforced_and_small_override():
    runs = source_runs()
    for row in runs:
        row["symbol"] = "ONE"
    with pytest.raises(ValueError, match="20 runs and 1 symbols"):
        dataset.build(runs, 1)
    assert not dataset.diversity_report(dataset.build(runs, 1, allow_small=True))["met"]
    with pytest.raises(ValueError, match="only 5 possible"):
        dataset.build(source_runs(1), 1, allow_small=True)


def test_load_excludes_failing_but_retains_warn_and_records_count(tmp_path):
    runs = source_runs()
    stale, _ = inject.plant(runs[0], "E5", 2)
    runs[0] = stale
    bad, _ = inject.plant(runs[1], "E1a", 2)
    for i, row in enumerate(runs + [dict(bad, run_id="bad")]):
        (tmp_path / f"{i:02}.json").write_text(json.dumps(row), encoding="utf-8")
    loaded = dataset.load_runs(tmp_path)
    assert len(loaded) == 20 and loaded.excluded_run_count == 1
    assert any(inject.gate_status(row) == "warn" for row in loaded)
    assert all(inject.gate_status(row) != "fail" for row in loaded)
    dataset.write(dataset.build(loaded, 7), tmp_path / "out", gate_commit="frozen")
    manifest = json.loads((tmp_path / "out" / "MANIFEST.json").read_text())
    assert manifest["excluded_run_count"] == 1


def test_every_planted_instance_runs_real_gate_and_preserves_labels(instances):
    results = run_gate.run(instances)
    assert len(results) == 1000
    for instance, result in zip(instances, results):
        assert result["status"] == result["expected_status"] == inject.EXPECTED[result["error_type"]]
        assert result["matches_expected"]
        assert result["detected"] == (result["status"] != "pass")
        assert result["primary_detected"] == (result["status"] == "fail")
        assert result["instance_id"] == instance["instance_id"]
        if result["expected_check"]:
            assert any(check["check_type"] == result["expected_check"] and check["status"] != "pass"
                       for check in result["checks"])


def test_summarize_hand_computed_rates_and_bounds():
    def rows(kind, n, caught):
        return [{"error_type": kind, "detected": i < caught} for i in range(n)]
    summary = run_gate.summarize(rows("E1a", 50, 50) + rows("E2", 50, 10)
                                 + rows("C0", 100, 0) + rows("C1", 100, 2))
    # 50/50 caught, 0 misses: rule-of-three = 3/50 = .06.
    assert summary["E1a"]["catch_rate"] == 1
    assert summary["E1a"]["miss_rate_upper_95"] == pytest.approx(.06)
    # 10/50 caught = .2; 2/100 false alarms = .02; 3/100 = .03 upper bound.
    assert summary["E2"]["catch_rate"] == pytest.approx(.2)
    assert summary["E2"]["miss_rate_upper_95"] is None
    assert summary["C0"]["false_alarm_rate"] == 0
    assert summary["C0"]["false_alarm_rate_upper_95"] == pytest.approx(.03)
    assert summary["C1"]["false_alarm_rate"] == pytest.approx(.02)
    assert summary["C1"]["false_alarm_rate_upper_95"] is None
    assert run_gate.summarize([]) == {}


def test_cli_writes_json_and_markdown(tmp_path, instances, monkeypatch):
    dataset.write(instances[:2], tmp_path, gate_commit="frozen", seed=42)
    monkeypatch.setattr(sys, "argv", ["run_gate", str(tmp_path / "instances.jsonl"), str(tmp_path / "out")])
    run_gate.main()
    report = json.loads((tmp_path / "out" / "results.json").read_text())
    assert len(report["results"]) == 2
    assert report["summary"]["E1a"]["catch_rate"] == 1
    assert "| E1a | 2 | 2 | 100.00% |" in (tmp_path / "out" / "results.md").read_text(encoding="utf-8")
