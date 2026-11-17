"""R2 planted-error injector (G3, design: docs/research/R2_PLANTED_ERROR_EVAL.md).

Takes the verify_run inputs of one clean run, plants at most one error in a deep copy and labels it. The gate itself is
called unchanged (app.verification.gate.verify_run). Offline only: no product code imports this.
"""
from __future__ import annotations

import copy
import random
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

from app import snapshot_store
from app.claims import FORMULAS
from app.verification import config as vconfig
from app.verification import gate

PLACEHOLDER = re.compile(r"\{\{claim:([^}]+)\}\}")
SNAPSHOT_SOURCES_EXCLUDED = ("risk", "pricebook")

# error type -> gate status it should produce ("fail", "warn" or "pass" for a known blind spot). Mirrors design §3.
EXPECTED: Dict[str, str] = {
    "E1a": "fail", "E1b": "fail", "E1c": "fail", "E1d": "fail", "E2": "pass", "E3": "fail", "E4": "fail",
    "E5": "warn", "E6": "fail", "E7a": "fail", "E7b": "pass", "E8a": "fail", "E8b": "fail", "E8c": "pass",
    "E9": "fail", "E10": "fail", "C0": "pass", "C1": "pass",
}


class NotApplicable(Exception):
    """The run has no claim this error type can be planted in."""


def _num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _snap_claims(inp: Dict[str, Any]) -> List[Dict[str, Any]]:
    snaps = inp["snapshots_by_claim_id"]
    return [c for c in inp["claims"] if c.get("source") not in SNAPSHOT_SOURCES_EXCLUDED and c["id"] in snaps]


def _derived(c: Dict[str, Any]) -> bool:
    return (c.get("text_span") or "").startswith("derived:")


def _pick(rng: random.Random, items: List[Any]) -> Any:
    if not items:
        raise NotApplicable
    return items[rng.randrange(len(items))]


def _leaves(obj: Any, path: str = "") -> List[Tuple[str, Any]]:
    """(JSON pointer, value) of every numeric leaf."""
    if isinstance(obj, dict):
        return [x for k, v in obj.items() for x in _leaves(v, f"{path}/{k}")]
    if isinstance(obj, list):
        return [x for i, v in enumerate(obj) for x in _leaves(v, f"{path}/{i}")]
    return [(path, obj)] if _num(obj) else []


def _set(obj: Any, pointer: str, value: Any) -> None:
    parts = pointer.lstrip("/").split("/")
    for p in parts[:-1]:
        obj = obj[int(p)] if isinstance(obj, list) else obj[p]
    last = parts[-1]
    if isinstance(obj, list):
        obj[int(last)] = value
    else:
        obj[last] = value


def _pointers_read(c: Dict[str, Any]) -> set:
    """Payload pointers a claim reads: source_path, plus every /pointer input of a derived span."""
    read = {c.get("source_path")}
    read.update(m.group(1) for m in re.finditer(r"=(/[^ |]+)", c.get("text_span") or ""))
    return {p for p in read if p}


def _own_snapshot(inp: Dict[str, Any], cid: str) -> Tuple[Dict[str, Any], Any]:
    """Unshare the target claim's snapshot so a mutation touches only that claim."""
    meta, payload = inp["snapshots_by_claim_id"][cid]
    meta, payload = copy.deepcopy(meta), copy.deepcopy(payload)
    inp["snapshots_by_claim_id"][cid] = (meta, payload)
    return meta, payload


def _scale(rng: random.Random, lo: float, hi: float) -> float:
    return 1 + rng.choice((-1, 1)) * rng.uniform(lo, hi)


def _value_error(kind: str) -> Callable:
    def inject(inp, rng):
        c = _pick(rng, [c for c in _snap_claims(inp) if _num(c.get("value")) and c["value"] != 0])
        old = c["value"]
        if kind == "E1a":
            c["value"] = old * _scale(rng, 0.001, 0.01)
        elif kind == "E1b":
            c["value"] = old * _scale(rng, 0.10, 0.50)
        elif kind == "E1c":
            c["value"] = old * 100 if rng.random() < 0.5 else old / 100
        else:
            c["value"] = -old
        return c["id"], "value", old, c["value"]
    return inject


def _wrong_field(inp, rng):
    cands = []
    for c in _snap_claims(inp):
        if _derived(c) or not c.get("source_path"):
            continue
        payload = inp["snapshots_by_claim_id"][c["id"]][1]
        sib = [(p, v) for p, v in _leaves(payload) if p != c["source_path"] and v != c.get("value")]
        if sib:
            cands.append((c, sib))
    c, sib = _pick(rng, cands)
    p, v = _pick(rng, sib)
    old = (c["source_path"], c["value"])
    c["source_path"], c["value"] = p, v
    return c["id"], "source_path+value", old, (p, v)


def _wrong_formula(inp, rng):
    c = _pick(rng, [c for c in _snap_claims(inp) if _derived(c) and c.get("metric") in FORMULAS])
    mine = FORMULAS[c["metric"]][0]
    other = _pick(rng, sorted({f for f, _ in FORMULAS.values() if f != mine}))
    old = c["text_span"]
    c["text_span"] = old.replace(mine, other, 1)
    return c["id"], "text_span", old, c["text_span"]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _look_ahead(inp, rng):
    c = _pick(rng, _snap_claims(inp))
    meta, _ = _own_snapshot(inp, c["id"])
    old = meta.get("fetched_at")
    run = datetime.fromisoformat(inp["run_time"].replace("Z", "+00:00"))
    meta["fetched_at"] = _iso(run + timedelta(hours=rng.randint(1, 72)))
    return c["id"], "fetched_at", old, meta["fetched_at"]


def _stale(inp, rng):
    windows = inp.get("windows") or vconfig.DEFAULT_WINDOWS
    c = _pick(rng, [c for c in _snap_claims(inp)
                    if gate.check_staleness(c, inp["snapshots_by_claim_id"][c["id"]][0],
                                            inp["run_date"], windows).status == "pass"])
    meta, _ = _own_snapshot(inp, c["id"])
    old = meta.get("as_of")
    # Calendar days beyond the window; for trading-day sources 3x covers weekends.
    excess = windows[c["source"]] * 3 + timedelta(days=rng.randint(1, 30))
    meta["as_of"] = (date.fromisoformat(inp["run_date"]) - excess).isoformat()
    return c["id"], "as_of", old, meta["as_of"]


def _unit_swap(inp, rng):
    c = _pick(rng, [c for c in inp["claims"] if c.get("metric") in vconfig.EXPECTED_UNITS])
    want = vconfig.EXPECTED_UNITS[c["metric"]]
    old = c.get("unit")
    c["unit"] = _pick(rng, [u for u in ("pct", "ratio", "USD", "count") if u != want])
    return c["id"], "unit", old, c["unit"]


def _tamper(rehash: bool) -> Callable:
    def inject(inp, rng):
        cands = []
        for c in _snap_claims(inp):
            payload = inp["snapshots_by_claim_id"][c["id"]][1]
            # Only leaves no claim on this snapshot reads, so traceability stays green and the integrity check is isolated.
            read = set().union(*(_pointers_read(o) for o in inp["claims"]
                                 if inp["snapshots_by_claim_id"].get(o["id"], (None, None))[1] is payload))
            free = [(p, v) for p, v in _leaves(payload) if p not in read]
            if free:
                cands.append((c, free))
        c, free = _pick(rng, cands)
        meta, payload = _own_snapshot(inp, c["id"])
        p, v = _pick(rng, free)
        _set(payload, p, v * _scale(rng, 0.01, 0.5) if v else 1.0)
        if rehash:
            meta["payload_hash"] = snapshot_store._payload_hash(payload)
        return c["id"], f"payload{p}", v, None
    return inject


def _narrative(kind: str) -> Callable:
    def inject(inp, rng):
        row = inp.get("narrative_row")
        if not row or row.get("status") != "ok":
            raise NotApplicable
        text = row["narrative"]
        ids = PLACEHOLDER.findall(text)
        by_id = {c["id"]: c for c in inp["claims"]}
        cid = _pick(rng, [i for i in ids if i in by_id])
        if kind == "E8a":
            new = str(by_id[cid].get("value"))
        elif kind == "E8b":
            new = "{{claim:missing-" + format(rng.getrandbits(32), "x") + "}}"
        else:
            other = _pick(rng, [c["id"] for c in inp["claims"] if c["id"] != cid and c.get("metric") != by_id[cid].get("metric")])
            new = "{{claim:" + other + "}}"
        row["narrative"] = text.replace("{{claim:" + cid + "}}", new, 1)
        return cid, "narrative", text, row["narrative"]
    return inject


def _source_value(source: str, kind: str) -> Callable:
    def inject(inp, rng):
        c = _pick(rng, [c for c in inp["claims"] if c.get("source") == source and _num(c.get("value"))])
        old = c["value"]
        if c.get("metric") == "risk_below_ma200":
            c["value"] = 0.0 if old else 1.0
        elif old == 0:
            c["value"] = 1.0
        else:
            c["value"] = old * _scale(rng, 0.01, 0.10)
        return c["id"], "value", old, c["value"]
    return inject


def _benign(inp, rng):
    c = _pick(rng, [c for c in _snap_claims(inp) if _num(c.get("value")) and c["value"] != 0])
    old = c["value"]
    c["value"] = old * (1 + 1e-12)
    _, payload = _own_snapshot(inp, c["id"])
    if isinstance(payload, dict):  # key order: integrity hashes canonical JSON, so this must not matter
        inp["snapshots_by_claim_id"][c["id"]] = (inp["snapshots_by_claim_id"][c["id"]][0], dict(reversed(list(payload.items()))))
    row = inp.get("narrative_row")
    if row and row.get("narrative"):
        row["narrative"] = "  " + row["narrative"].replace(". ", ".  ")
    return c["id"], "value+order+whitespace", old, c["value"]


INJECTORS: Dict[str, Optional[Callable]] = {
    "E1a": _value_error("E1a"), "E1b": _value_error("E1b"), "E1c": _value_error("E1c"), "E1d": _value_error("E1d"),
    "E2": _wrong_field, "E3": _wrong_formula, "E4": _look_ahead, "E5": _stale, "E6": _unit_swap,
    "E7a": _tamper(False), "E7b": _tamper(True), "E8a": _narrative("E8a"), "E8b": _narrative("E8b"),
    "E8c": _narrative("E8c"), "E9": _source_value("pricebook", "E9"), "E10": _source_value("risk", "E10"),
    "C0": None, "C1": _benign,
}
assert INJECTORS.keys() == EXPECTED.keys()


def plant(inputs: Dict[str, Any], error_type: str, seed: int) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return (mutated copy, label). Raises NotApplicable if the run has nothing to plant this type in."""
    rng = random.Random(f"{error_type}:{seed}")
    inp = copy.deepcopy(inputs)
    fn = INJECTORS[error_type]
    target, field, original, planted = fn(inp, rng) if fn else (None, None, None, None)
    label = {"error_type": error_type, "seed": seed, "target_claim_id": target, "field": field,
             "original": repr(original), "planted": repr(planted), "expected_status": EXPECTED[error_type]}
    return inp, label


def gate_status(inputs: Dict[str, Any]) -> str:
    """Worst status the gate returns for the run: fail > warn > pass."""
    statuses = {r.status for r in gate.verify_run(inputs)}
    return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
