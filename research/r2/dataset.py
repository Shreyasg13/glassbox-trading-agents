"""Build frozen R2 instances from exported runs, entirely offline."""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from . import inject

EXPECTED_CHECK = {
    **{kind: "traceability" for kind in ("E1a", "E1b", "E1c", "E1d", "E3")},
    "E2": None, "E4": "point_in_time", "E5": "staleness", "E6": "unit",
    "E7a": "snapshot_integrity", "E7b": None, "E8a": "narrative", "E8b": "narrative",
    "E8c": None, "E9": "price", "E10": "risk", "C0": None, "C1": None,
}


class Runs(list):
    """List of clean runs with the number excluded during loading."""

    def __init__(self, runs=(), excluded_run_count=0):
        super().__init__(runs)
        self.excluded_run_count = excluded_run_count


class Instances(list):
    """List carrying freeze metadata, including for an empty data set."""

    def __init__(self, seed, excluded_run_count):
        super().__init__()
        self.seed = seed
        self.excluded_run_count = excluded_run_count


def load_runs(directory) -> list[dict]:
    """Read exported JSON runs; exclude and count any with an existing fail."""
    runs = Runs()
    for path in sorted(Path(directory).glob("*.json")):
        run = json.loads(path.read_text(encoding="utf-8"))
        if inject.gate_status(run) == "fail":
            runs.excluded_run_count += 1
        else:
            runs.append(run)
    return runs


def build(runs, seed, allow_small=False) -> list[dict]:
    """Plant the fixed 1,000-instance design; allow_small relaxes diversity only."""
    ordered = sorted(runs, key=lambda run: str(run["run_id"]))
    if len({str(run["run_id"]) for run in ordered}) != len(ordered):
        raise ValueError("Duplicate run_id in source runs")
    rng = random.Random(seed)
    rng.shuffle(ordered)
    instances = Instances(seed, getattr(runs, "excluded_run_count", 0))
    for kind in inject.EXPECTED:
        required = 100 if kind.startswith("C") else 50
        candidates = []
        for index in range(5):
            for source in ordered:
                identity = json.dumps([seed, source["run_id"], kind, index], separators=(",", ":"))
                instance_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                plant_seed = int(instance_id, 16)
                try:
                    inputs, label = inject.plant(source, kind, plant_seed)
                except inject.NotApplicable:
                    continue
                candidates.append({**label, "instance_id": instance_id,
                                   "source_run_id": source["run_id"], "symbol": source["symbol"],
                                   "expected_check": EXPECTED_CHECK[kind], "inputs": inputs})
        if len(candidates) < required:
            raise ValueError(f"{kind}: required {required} instances, only {len(candidates)} possible")
        instances.extend(candidates[:required])
    diversity = diversity_report(instances)
    if not diversity["met"] and not allow_small:
        raise ValueError(f"Diversity requires >=20 runs and >=10 symbols; got "
                         f"{diversity['runs']} runs and {diversity['symbols']} symbols")
    return instances


def diversity_report(instances):
    runs = len({row["source_run_id"] for row in instances})
    symbols = len({row["symbol"] for row in instances})
    return {"runs": runs, "symbols": symbols, "met": runs >= 20 and symbols >= 10}


def write(instances, out_dir, *, gate_commit, seed=None, excluded_run_count=None):
    """Write canonical bytes and a manifest. The manifest hashes data, not itself.

    Pass the gate's frozen git rev-parse HEAD value explicitly; no subprocess runs.
    Explicit metadata supports ordinary lists loaded back from JSONL.
    """
    seed = getattr(instances, "seed", seed) if seed is None else seed
    excluded = (getattr(instances, "excluded_run_count", 0)
                if excluded_run_count is None else excluded_run_count)
    if seed is None:
        raise ValueError("The data-set seed is required")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                   for row in sorted(instances, key=lambda row: row["instance_id"])).encode("utf-8")
    (out / "instances.jsonl").write_bytes(data)
    manifest = {"seed": seed, "gate_commit": gate_commit, "excluded_run_count": excluded,
                "counts_per_type": dict(sorted(Counter(row["error_type"] for row in instances).items())),
                "diversity": diversity_report(instances),
                "sha256": {"instances.jsonl": hashlib.sha256(data).hexdigest()}}
    (out / "MANIFEST.json").write_bytes((json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"))
