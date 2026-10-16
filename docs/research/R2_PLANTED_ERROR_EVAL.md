# R2 design: does the verification gate catch planted wrong numbers?

**Status:** design (G2, durable-agent-runtime #32). The data set is built and frozen in G3 (#33); nothing here has been run yet. GlassBox is feature-frozen: R2 adds an offline evaluation, not product code.

GlassBox is a non-commercial research preview. Nothing here is investment advice.

## 1. Question

Every number GlassBox shows is a claim that points at a stored source value (`backend/app/claims.py`), and the gate (`backend/app/verification/gate.py`, `verify_run`) checks each claim before a run is shown. **When a wrong number is planted in an otherwise real run, how often does the gate catch it, how often does it raise a false alarm on a clean run, and what does that cost, compared with an LLM reviewer given the same evidence?**

## 2. Unit and inputs

- **Unit:** one *instance* = the `verify_run` inputs of one real, archived committee run (`claims`, `snapshots_by_claim_id`, `run_time`, `run_date`, `prices`, `recomputed_risk`, `narrative_row`) with **at most one** planted error.
- **Source runs:** clean runs from the production database, taken before the freeze date, where the unmodified gate returns no `fail`. Runs that already fail are excluded and counted.
- **No product code changes.** The harness copies the inputs, mutates the copy and calls `verify_run` unchanged.

## 3. Error types and injection rules

Each type is a pure function `(inputs, rng) -> (mutated_inputs, label)`. It changes one claim (or the narrative) chosen uniformly among eligible claims, with the seed fixed in G3.

| Id | Error | Injection | Gate check expected to catch it | Expected status |
|---|---|---|---|---|
| E1a | Small value drift | `value *= 1 + d`, d uniform in ±[0.1%, 1%] | traceability (rel. tol 1e-9) | fail |
| E1b | Large value error | `value *= 1 + d`, d in ±[10%, 50%] | traceability | fail |
| E1c | Scale slip | `value *= 100` or `/= 100`, unit unchanged (pct vs ratio confusion) | traceability | fail |
| E1d | Sign flip | `value = -value` (nonzero values only) | traceability | fail |
| E2 | Wrong field | `source_path` and `value` moved together to a sibling field in the same payload (e.g. prior period) | none: the claim is self-consistent | **pass (known blind spot)** |
| E3 | Wrong formula | derived claim's formula text replaced by another metric's formula from `FORMULAS` | traceability (formula must match table) | fail |
| E4 | Look-ahead | snapshot `fetched_at`/`as_of` moved after `run_time` | point_in_time | fail |
| E5 | Stale source | snapshot `as_of` moved beyond the source's window in `verification/config.py` | staleness | warn |
| E6 | Unit swap | claim `unit` changed (pct <-> ratio, USD -> pct) | unit | fail |
| E7a | Payload tampered | one numeric leaf of the snapshot payload changed, hash kept | snapshot_integrity | fail |
| E7b | Payload and hash rewritten | same as E7a, `payload_hash` recomputed | traceability only if the claim's own value changed; else none | **pass (documented limit)** |
| E8a | Raw number in narrative | one `{{claim:id}}` replaced by its literal value | narrative | fail |
| E8b | Dangling reference | one `{{claim:id}}` replaced by an unknown id | narrative | fail |
| E8c | Wrong claim referenced | one `{{claim:id}}` swapped for another valid claim id of a different metric | none: syntax is valid | **pass (known blind spot)** |
| E9 | Wrong price | pricebook claim `value *= 1 + d`, d in ±[1%, 10%] | price | fail |
| E10 | Wrong risk number | one risk claim changed vs `recomputed_risk` | risk | fail |

E2, E7b and E8c are included on purpose: they measure what the gate **cannot** catch, which is the case for an LLM or human reviewer.

**Controls (false alarms):**
- C0: unmodified instance.
- C1: benign change: claim value perturbed by relative 1e-12 (below tolerance), JSON key order shuffled, whitespace changed in the narrative.

## 4. Labels

Each instance stores: `instance_id`, `source_run_id`, `error_type` (or `C0`/`C1`), `target_claim_id`, `field`, `original`, `planted`, `expected_check`, `expected_status` (from the table), and the seed. The labels are written by the injector before any detector runs.

## 5. Detectors

1. **Gate:** `verify_run` + `summarize`, deterministic. An instance is **flagged** if any result has status `fail` (primary) or `fail`/`warn` (secondary).
2. **LLM reviewer (comparison arm):** one call per instance with the same evidence (claims, the resolved source values, the narrative), asked to list every claim whose number does not match its source or whose narrative use is wrong. Output parsed as JSON. Model and prompt fixed in G3; no sampling parameters sent. Spend needs owner approval (estimate: about 600 instances x ~3k tokens ≈ $2 on Haiku 4.5).
3. **Gate + LLM:** flagged if either flags. This is what the product could ship.

## 6. Metrics

- **Catch rate** per error type and detector = flagged / planted, with Wilson 95% intervals. Attribution: for the gate, also report whether the flag came from the expected check.
- **False-alarm rate** on C0 and C1 (each separately).
- **Cost** per instance (USD, tokens; gate = $0) and **latency** (wall time).
- **Pooled catch rate** over "catchable by design" types (all except E2, E7b, E8c), and separately over the blind-spot types.

## 7. Size

- 50 instances per error type (16 types = 800) plus 100 C0 and 100 C1 = **1,000 instances**.
- With 50 per type and 0 misses, the 95% upper bound on the miss rate is about 6% (rule of three); with 100 controls and 0 false alarms, about 3%.
- Instances are drawn from at least 20 distinct source runs and 10 symbols; a run contributes at most 5 instances per type.

## 8. Decisions fixed before any detector runs

- The gate is the version at the commit G3 freezes; it is not changed during R2. A bug found by R2 is reported, fixed after, and re-run as a separate result.
- The pass/fail rule, metrics and sizes above. Changes go in a change log here with date and reason.

## 9. Outputs

- `research/r2/` (G3): injector, frozen data set with a SHA-256 manifest, one command that runs all detectors and writes `results.json` + a table.
- Report (January 2027): catch and false-alarm tables, cost, and the blind spots with examples.

## 10. Change log

| Date | Change | Reason |
|---|---|---|
| 2026-10-14 | Drafted | G2 |
| 2026-10-16 | C0: one per distinct run (n = min(100, eligible runs)); bounds on independent inputs | reason: deterministic gate makes repeated untouched copies one observation (G3b review) |
| 2026-10-14 | Injector `research/r2/inject.py` added; `backend/tests/test_r2_inject.py` plants every type (12 seeds each) into a synthetic clean run and confirms the gate status in §3 for all 18 rows | G3 part 1. Left for G3: export clean runs from production, plant the §7 sizes, write the SHA-256 manifest |
