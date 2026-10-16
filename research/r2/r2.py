"""R2 G3/G4: build the frozen planted-error data set, run the detectors, write the results (design §4-§9).

    python research/r2/r2.py build  <runs.jsonl>     # clean-run filter, plant §7 sizes, write instances + manifest
    python research/r2/r2.py gate                    # detector 1: verify_run, $0
    python research/r2/r2.py review [--dry-run]      # detector 2: LLM reviewer (needs ANTHROPIC_API_KEY)
    python research/r2/r2.py report                  # results.json + RESULTS.md (gate, LLM, gate+LLM)

Data (production runs, planted copies, raw detector output) stays in research/r2/data/ (git-ignored); the manifest pins it
by SHA-256. Run from the repo root with the backend venv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1] / "backend"), str(HERE)]

import inject  # noqa: E402
from app.verification import gate  # noqa: E402

DATA = HERE / "data"
PER_TYPE, PER_CONTROL, PER_RUN_CAP = 50, 100, 5
BLIND_SPOTS = ("E2", "E7b", "E8c")
# design §3: the check expected to catch each type (attribution)
EXPECTED_CHECK = {"E1a": "traceability", "E1b": "traceability", "E1c": "traceability", "E1d": "traceability",
                  "E3": "traceability", "E4": "point_in_time", "E5": "staleness", "E6": "unit",
                  "E7a": "snapshot_integrity", "E8a": "narrative", "E8b": "narrative", "E9": "price", "E10": "risk"}
REVIEW_MODEL = "claude-haiku-4-5"  # fixed by design §5
PRICE_IN, PRICE_OUT = 1.00, 5.00  # USD per 1M tokens, Haiku 4.5


def sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def read_jsonl(p: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(p: Path, rows: List[Dict[str, Any]]) -> None:
    p.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows), encoding="utf-8")


def load_runs() -> Dict[str, Dict[str, Any]]:
    return {r["run_id"]: r for r in read_jsonl(DATA / "runs.jsonl")}


def materialize(run: Dict[str, Any], inst: Dict[str, Any]) -> Dict[str, Any]:
    return inject.plant(run["inputs"], inst["error_type"], inst["seed"])[0]


# ---------------------------------------------------------------- build

def build(runs_path: Path) -> Dict[str, Any]:
    DATA.mkdir(exist_ok=True)
    rows = read_jsonl(runs_path)
    clean, excluded = [], []
    for r in rows:
        (excluded if inject.gate_status(r["inputs"]) == "fail" else clean).append(r)
    write_jsonl(DATA / "runs.jsonl", clean)

    instances: List[Dict[str, Any]] = []
    shortfall = {}
    for et in inject.EXPECTED:
        target = PER_CONTROL if et in ("C0", "C1") else PER_TYPE
        order = sorted(clean, key=lambda r: r["run_id"])
        random.Random(f"order:{et}").shuffle(order)
        seen, got = set(), []
        for rnd in range(PER_RUN_CAP):
            for i, r in enumerate(order):
                if len(got) == target:
                    break
                seed = i * PER_RUN_CAP + rnd
                try:
                    planted, label = inject.plant(r["inputs"], et, seed)
                except inject.NotApplicable:
                    continue
                h = sha(planted)
                if not et.startswith("C") and h in seen:  # same planted error twice is one observation, not two
                    continue
                seen.add(h)
                got.append({"instance_id": f"{et}-{len(got):03d}", "source_run_id": r["run_id"], "symbol": r["symbol"],
                            **label, "expected_check": EXPECTED_CHECK.get(et), "sha256": h})
        if len(got) < target:
            shortfall[et] = target - len(got)
        instances.extend(got)
    write_jsonl(DATA / "instances.jsonl", instances)

    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True).stdout.strip()
    manifest = {
        "gate_commit": commit,
        "runs_exported": len(rows), "runs_clean": len(clean),
        "runs_excluded_gate_fail": sorted(r["run_id"] for r in excluded),
        "distinct_source_runs": len({i["source_run_id"] for i in instances}),
        "distinct_symbols": len({i["symbol"] for i in instances}),
        "instances": len(instances),
        "per_type": {et: sum(i["error_type"] == et for i in instances) for et in inject.EXPECTED},
        "shortfall": shortfall,
        "sha256": {"runs.jsonl": file_sha(DATA / "runs.jsonl"), "instances.jsonl": file_sha(DATA / "instances.jsonl"),
                   "export": file_sha(runs_path)},
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def file_sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def check_manifest() -> None:
    m = json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))
    for name in ("runs.jsonl", "instances.jsonl"):
        if file_sha(DATA / name) != m["sha256"][name]:
            raise SystemExit(f"{name} does not match manifest.json: the frozen data set changed")


# ---------------------------------------------------------------- detectors

def run_gate() -> List[Dict[str, Any]]:
    check_manifest()
    runs, out = load_runs(), []
    for inst in read_jsonl(DATA / "instances.jsonl"):
        planted = materialize(runs[inst["source_run_id"]], inst)
        assert sha(planted) == inst["sha256"], inst["instance_id"]  # planting is reproducible
        t0 = time.perf_counter()
        results = gate.verify_run(planted)
        ms = (time.perf_counter() - t0) * 1000
        bad = [(r.check_type, r.status, r.claim_id) for r in results if r.status != "pass"]
        out.append({"instance_id": inst["instance_id"], "ms": ms,
                    "fail": any(s == "fail" for _, s, _ in bad), "warn": any(s == "warn" for _, s, _ in bad),
                    "checks": sorted({c for c, _, _ in bad}), "non_pass": bad})
    write_jsonl(DATA / "gate.jsonl", out)
    return out


REVIEW_SYSTEM = (
    "You audit numbers in an automated equity research report before it is published. You get every numeric claim with "
    "the stored source value it cites, the source's timestamps, and the report text, where {{claim:ID}} marks a place "
    "the claim's value is shown. List every claim that is wrong: the number does not match its source, the unit or "
    "formula is wrong, the source was fetched after the run or is stale, or the text shows a raw number or references "
    "the wrong or a missing claim. Report only real problems. Answer with JSON only: "
    '{"problems": [{"claim_id": "...", "reason": "..."}]} and an empty list if everything is correct.'
)


def _at(payload: Any, pointer: Optional[str]) -> Any:
    if not pointer:
        return None
    try:
        for p in pointer.lstrip("/").split("/"):
            payload = payload[int(p)] if isinstance(payload, list) else payload[p]
        return payload
    except (KeyError, IndexError, ValueError, TypeError):
        return "<missing>"


def evidence(inp: Dict[str, Any]) -> Dict[str, Any]:
    """What the reviewer sees: the same facts the gate reads, resolved to values (design §5)."""
    snaps, claims = inp["snapshots_by_claim_id"], []
    for c in inp["claims"]:
        e = {k: c.get(k) for k in ("id", "metric", "value", "unit", "period", "source", "source_path", "text_span")}
        if c["id"] in snaps:
            meta, payload = snaps[c["id"]]
            e["source_value"] = _at(payload, c.get("source_path"))
            e["snapshot"] = {k: meta.get(k) for k in ("as_of", "fetched_at")}
            e["integrity"] = "ok" if snapshot_hash(payload) == meta.get("payload_hash") else "payload hash mismatch"
        elif c.get("source") == "pricebook":
            e["source_value"] = (inp.get("prices") or {}).get(c.get("period"))
        elif c.get("source") == "risk":
            e["source_value"] = inp.get("recomputed_risk")
        claims.append(e)
    row = inp.get("narrative_row") or {}
    return {"run_time": inp["run_time"], "run_date": inp["run_date"], "claims": claims, "text": row.get("narrative")}


def snapshot_hash(payload: Any) -> str:
    from app import snapshot_store
    return snapshot_store._payload_hash(payload)


def run_review(dry_run: bool, limit: Optional[int] = None, client: Any = None) -> List[Dict[str, Any]]:
    check_manifest()
    runs = load_runs()
    insts = read_jsonl(DATA / "instances.jsonl")[:limit]
    path = DATA / "review.jsonl"
    done = {r["instance_id"]: r for r in read_jsonl(path)} if path.exists() else {}
    prompts = [(i, json.dumps(evidence(materialize(runs[i["source_run_id"]], i)), default=str)) for i in insts]
    if dry_run:
        chars = sum(len(REVIEW_SYSTEM) + len(p) for _, p in prompts)
        est_in = chars / 3.5  # rough chars-per-token for JSON
        cost = est_in * PRICE_IN / 1e6 + len(prompts) * 150 * PRICE_OUT / 1e6
        print(f"{len(prompts)} calls, ~{est_in / 1e6:.2f}M input tokens, ~${cost:.2f} on {REVIEW_MODEL}")
        return []
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    with path.open("a", encoding="utf-8") as f:
        for inst, prompt in prompts:
            if inst["instance_id"] in done:  # resumable: never pay twice for one instance
                continue
            t0 = time.perf_counter()
            msg = client.messages.create(model=REVIEW_MODEL, max_tokens=2000, system=REVIEW_SYSTEM,
                                         messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in msg.content if b.type == "text")
            problems, parse_error = parse_review(text)
            row = {"instance_id": inst["instance_id"], "ms": (time.perf_counter() - t0) * 1000, "model": msg.model,
                   "stop_reason": msg.stop_reason, "input_tokens": msg.usage.input_tokens,
                   "output_tokens": msg.usage.output_tokens, "problems": problems, "parse_error": parse_error, "raw": text}
            f.write(json.dumps(row) + "\n")
            f.flush()
            done[inst["instance_id"]] = row
    return [done[i["instance_id"]] for i in insts if i["instance_id"] in done]


def parse_review(text: str):
    """(problems, parse_error). An unparseable answer counts as 'flagged' (fail closed), and is reported."""
    s, e = text.find("{"), text.rfind("}")
    try:
        obj = json.loads(text[s:e + 1])
        probs = obj["problems"]
        assert isinstance(probs, list)
        return [p for p in probs if isinstance(p, dict)], None
    except (ValueError, KeyError, AssertionError, TypeError) as exc:
        return None, f"{type(exc).__name__}"


# ---------------------------------------------------------------- report

def wilson(k: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, c - h), min(1.0, c + h)


def rate(k: int, n: int) -> Dict[str, Any]:
    p, lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": p, "ci95": [lo, hi]}


def report() -> Dict[str, Any]:
    insts = {i["instance_id"]: i for i in read_jsonl(DATA / "instances.jsonl")}
    g = {r["instance_id"]: r for r in read_jsonl(DATA / "gate.jsonl")}
    rv_path = DATA / "review.jsonl"
    rv = {r["instance_id"]: r for r in read_jsonl(rv_path)} if rv_path.exists() else {}

    def llm_flag(iid):  # parse failure fails closed
        r = rv.get(iid)
        return None if r is None else (r["problems"] is None or len(r["problems"]) > 0)

    detectors = {
        "gate_fail": lambda i: g[i]["fail"],
        "gate_fail_or_warn": lambda i: g[i]["fail"] or g[i]["warn"],
        "llm": llm_flag,
        "gate_or_llm": lambda i: None if llm_flag(i) is None else (g[i]["fail"] or llm_flag(i)),
    }
    by_type: Dict[str, Dict[str, Any]] = {}
    for et in inject.EXPECTED:
        ids = [i for i, x in insts.items() if x["error_type"] == et]
        row = {"n": len(ids), "expected_gate_status": inject.EXPECTED[et]}
        for name, fn in detectors.items():
            vals = [fn(i) for i in ids]
            vals = [v for v in vals if v is not None]
            row[name] = rate(sum(vals), len(vals))
        if et in EXPECTED_CHECK:
            row["gate_flag_from_expected_check"] = rate(sum(EXPECTED_CHECK[et] in g[i]["checks"] for i in ids if g[i]["fail"]),
                                                        sum(g[i]["fail"] for i in ids))
            if rv:
                hit = [i for i in ids if llm_flag(i) and rv[i]["problems"]]
                row["llm_named_target_claim"] = rate(sum(any(p.get("claim_id") == insts[i]["target_claim_id"]
                                                             for p in rv[i]["problems"]) for i in hit), len(hit))
        row["gate_matches_design"] = rate(sum(_status(g[i]) == inject.EXPECTED[et] for i in ids), len(ids))
        by_type[et] = row

    def pooled(types, name):
        ids = [i for i, x in insts.items() if x["error_type"] in types]
        vals = [v for v in (detectors[name](i) for i in ids) if v is not None]
        return rate(sum(vals), len(vals))

    catchable = [t for t in inject.EXPECTED if t not in BLIND_SPOTS and not t.startswith("C")]
    out = {
        "manifest": json.loads((HERE / "manifest.json").read_text(encoding="utf-8")),
        "by_type": by_type,
        "pooled": {name: {"catchable": pooled(catchable, name), "blind_spots": pooled(BLIND_SPOTS, name),
                          "false_alarm_C0": pooled(["C0"], name), "false_alarm_C1": pooled(["C1"], name)}
                   for name in detectors},
        "cost": {
            "gate": {"usd": 0.0, "ms_mean": sum(r["ms"] for r in g.values()) / max(1, len(g))},
            "llm": None if not rv else {
                "calls": len(rv), "model": REVIEW_MODEL,
                "input_tokens": sum(r["input_tokens"] for r in rv.values()),
                "output_tokens": sum(r["output_tokens"] for r in rv.values()),
                "usd": sum(r["input_tokens"] * PRICE_IN + r["output_tokens"] * PRICE_OUT for r in rv.values()) / 1e6,
                "ms_mean": sum(r["ms"] for r in rv.values()) / len(rv),
                "parse_errors": sum(r["parse_error"] is not None for r in rv.values()),
            },
        },
    }
    (HERE / "results.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    (HERE / "RESULTS.md").write_text(markdown(out), encoding="utf-8")
    return out


def _status(r):
    return "fail" if r["fail"] else "warn" if r["warn"] else "pass"


def _fmt(x):
    if not x or x["rate"] is None:
        return "–"
    return f"{x['k']}/{x['n']} = {x['rate']:.0%} [{x['ci95'][0]:.0%}, {x['ci95'][1]:.0%}]"


def markdown(o: Dict[str, Any]) -> str:
    m, llm = o["manifest"], o["cost"]["llm"]
    lines = [
        "# R2 results: does the verification gate catch planted wrong numbers?", "",
        "Generated by `research/r2/r2.py report`; design in `docs/research/R2_PLANTED_ERROR_EVAL.md`. "
        "Flag rates with Wilson 95% intervals. GlassBox is a non-commercial research preview; nothing here is investment advice.", "",
        f"Data set: {m['instances']} instances from {m['distinct_source_runs']} clean production runs "
        f"({m['distinct_symbols']} symbols); {len(m['runs_excluded_gate_fail'])} of {m['runs_exported']} exported runs "
        f"excluded because the unmodified gate already failed them. Gate commit `{m['gate_commit'][:7]}`. "
        f"Shortfall vs design §7: {m['shortfall'] or 'none'}.", "",
        "## Pooled", "",
        "| Detector | Catch (catchable types) | Catch (blind spots E2, E7b, E8c) | False alarm C0 | False alarm C1 |",
        "|---|---|---|---|---|",
    ]
    for name, p in o["pooled"].items():
        lines.append(f"| {name} | {_fmt(p['catchable'])} | {_fmt(p['blind_spots'])} | {_fmt(p['false_alarm_C0'])} | {_fmt(p['false_alarm_C1'])} |")
    lines += ["", "## By error type", "",
              "| Type | n | Design | Gate status = design | Gate (fail) | Gate (fail/warn) | From expected check | LLM | Gate + LLM |",
              "|---|---|---|---|---|---|---|---|---|"]
    for et, r in o["by_type"].items():
        lines.append(f"| {et} | {r['n']} | {r['expected_gate_status']} | {_fmt(r['gate_matches_design'])} | {_fmt(r['gate_fail'])} | "
                     f"{_fmt(r['gate_fail_or_warn'])} | {_fmt(r.get('gate_flag_from_expected_check'))} | {_fmt(r['llm'])} | {_fmt(r['gate_or_llm'])} |")
    lines += ["", "## Cost", "", f"- Gate: $0, mean {o['cost']['gate']['ms_mean']:.1f} ms per instance."]
    lines.append(f"- LLM reviewer ({llm['model']}): {llm['calls']} calls, ${llm['usd']:.2f}, mean {llm['ms_mean'] / 1000:.1f} s, "
                 f"{llm['parse_errors']} unparseable answers (counted as flagged)." if llm else "- LLM reviewer: not run.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("runs")
    sub.add_parser("gate")
    rv = sub.add_parser("review")
    rv.add_argument("--dry-run", action="store_true")
    rv.add_argument("--limit", type=int)
    sub.add_parser("report")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        print(json.dumps({k: v for k, v in build(Path(a.runs)).items() if k != "runs_excluded_gate_fail"}, indent=2))
    elif a.cmd == "gate":
        print(f"gate: {len(run_gate())} instances")
    elif a.cmd == "review":
        run_review(a.dry_run, a.limit)
    else:
        print(json.dumps(report()["pooled"], indent=2))


if __name__ == "__main__":
    main()
