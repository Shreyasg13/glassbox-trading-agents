"""Run the frozen gate on R2 JSONL instances, without network or LLM calls."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from app.verification import gate

# reviewer: G3c


def run(instances) -> list[dict]:
    results = []
    for instance in instances:
        checks = gate.verify_run(instance["inputs"])
        statuses = {check.status for check in checks}
        status = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
        results.append({key: value for key, value in instance.items() if key != "inputs"} | {
            "status": status, "detected": status != "pass", "primary_detected": status == "fail",
            "matches_expected": status == instance["expected_status"],
            "checks": [asdict(check) for check in checks],
        })
    return results


def summarize(results):
    """Secondary catch rates (fail/warn), control false alarms, rule-of-three bounds."""
    grouped = defaultdict(list)
    for row in results:
        grouped[row["error_type"]].append(row)
    summary = {}
    for kind, rows in sorted(grouped.items()):
        n = len(rows)
        caught = sum(row["detected"] for row in rows)
        control = kind in ("C0", "C1")
        summary[kind] = {"count": n, "detected": caught,
                         "false_alarm_rate" if control else "catch_rate": caught / n,
                         "miss_rate_upper_95": min(1.0, 3 / n) if not control and caught == n else None,
                         "false_alarm_rate_upper_95": min(1.0, 3 / n) if control and caught == 0 else None}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instances", type=Path)
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    instances = [json.loads(line) for line in args.instances.read_text(encoding="utf-8").splitlines() if line.strip()]
    results = run(instances)
    summary = summarize(results)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "results.json").write_text(
        json.dumps({"results": results, "summary": summary}, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    lines = ["| Type | N | Detected | Catch / false-alarm rate | 95% rule-of-three upper bound |",
             "|---|---:|---:|---:|---:|"]
    for kind, row in summary.items():
        rate = row.get("catch_rate", row.get("false_alarm_rate"))
        bound = row["miss_rate_upper_95"] if kind.startswith("E") else row["false_alarm_rate_upper_95"]
        rendered = "—" if bound is None else f"{bound:.2%}"
        lines.append(f"| {kind} | {row['count']} | {row['detected']} | {rate:.2%} | {rendered} |")
    (args.out_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
