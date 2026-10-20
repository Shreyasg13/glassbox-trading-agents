# R2 design: how well does the A6 verification gate catch wrong numbers?

**Status:** design v0.1, 2026-10-07 (durable-agent-runtime issue #32, G2). The data set is built and frozen in G3. This file freezes, with a git tag, before G3 runs anything on production data; later changes go in section 10.

GlassBox is a non-commercial research preview. Nothing here is investment advice.

## 1. Question

GlassBox's A6 gate (`backend/app/verification/gate.py`) checks every structured claim in a committee output against point-in-time source snapshots before `publish()` lets it out. On realistic outputs:

1. **Catch rate:** what share of planted numeric errors does it flag, by error type?
2. **False alarms:** how often does it flag clean production outputs?
3. **Blind spots:** which kinds of wrong numbers can it not see, by design?
4. **Cost:** latency per output, and the share of outputs that enforce mode would hold.

The gate is built to catch errors inside structured claims, so a near-100% catch rate there is expected and amounts to a correctness check. The research value is in questions 2–4: false alarms on real data, and an honest map of what a structured-claims gate misses.

## 2. What the gate checks (as of `origin/main`, 2026-10-07)

| Check | Catches |
|---|---|
| `traceability` | Claim value ≠ value at `source_path` in its snapshot, or a derived claim whose formula or inputs don't reproduce it (1e-9 relative tolerance) |
| `price` | A price-book claim ≠ that date's close |
| `risk` | A risk claim ≠ the independently recomputed risk |
| `point_in_time` | Snapshot fetched after the run time (look-ahead) |
| `staleness` | Data older than its source's window (`verification/config.py`) |
| `snapshot_integrity` | Stored payload no longer hashes to its stored hash |
| `unit` | A metric's unit ≠ `EXPECTED_UNITS` |
| `narrative` | The narrative contains stray digits, or placeholders that point to another run's claims |

## 3. Error types and injection rules

Each item gets **at most one** planted error, except the multi-fault subset (section 4). Mutations are deterministic (seed `20261107` + item id), applied to **copies** of exported records, never to the production database.

### Class A: inside the gate's design (expected to be caught)

| Code | Error | Injection rule | Expected check |
|---|---|---|---|
| A1 | Value off by a small relative amount | value × (1 + δ), δ drawn from {0.1%, 0.5%, 1%, 5%} with a random sign | traceability / price / risk |
| A2 | Large error | value × (1 + δ), δ ∈ {10%, 50%}; sign flip | same |
| A3 | Unit slip | pct ↔ ratio (× or ÷ 100), USD in thousands vs units | traceability + unit |
| A5 | Derived claim with swapped inputs | swap two inputs of a FORMULAS entry; recompute value from the swapped inputs | traceability |
| A6 | Stale data | the snapshot's `as_of` moved back past the source window + 1 day | staleness |
| A7 | Look-ahead | `fetched_at` moved after `run_time` | point_in_time |
| A8 | Tampered snapshot | one payload value edited, hash left as stored | snapshot_integrity |
| A9 | Narrative stray number | a literal number inserted into the narrative text | narrative |
| A10 | Narrative cross-run placeholder | `{{claim:id}}` replaced by a claim id from another run | narrative |

### Class B: at the boundary (catch not guaranteed; measured)

| Code | Error | Rule |
|---|---|---|
| B1 | Right number, wrong label | `source_path` of another metric in the same snapshot, with that metric's value: internally consistent but describing the wrong metric. Expected to **pass**; that is the finding |
| B2 | Sub-tolerance drift | δ = 1e-10 relative. Should pass; a fail is a false alarm |
| B3 | Display rounding | the rendered value differs from the claim only by rounding at its displayed precision. `RENDER_TOLERANCE_REL = 0.005` exists in config but is **not used by any check yet** (reserved for T7, the evidence view), so render-time errors are unchecked |
| B4 | Edge dates | monthly BLS dates, Mondays after holidays, weekends (the hotfix in PR #32 crashed on monthly dates). Clean items that should pass |

### Class C: outside the gate's design (expected to be missed; measured to size the gap)

| Code | Error | Rule |
|---|---|---|
| C1 | Number in a committee agent's free-text rationale | edit a number inside an agent's `rationale` (prompted "use ONLY the numbers above", but not gated) |
| C2 | Upstream data error | the source snapshot and claim are both wrong but consistent (the value changed in both before hashing) |
| C3 | Wrong direction in words | the narrative says "rose" where the claim shows a fall, numbers untouched |

## 4. Data set (built in G3)

- **Source:** stored committee runs (claims, narratives, snapshots, verification results) exported from production. One item = one committee run for one symbol.
- **Clean set:** 300 items sampled uniformly over dates and symbols from runs with no recorded gate failure. They measure false alarms and serve as the base for injections.
- **Injected set:** 50 items per error code in classes A and B (A1–A3, A5–A10, B1–B4), plus 30 per code in class C, each item drawn from the clean set without replacement within a code.
- **Multi-fault subset:** 50 items with two errors of different codes, to test that one failure doesn't mask another.
- **Natural failures:** every production run the gate failed on is kept as its own set and adjudicated (real upstream issue vs false alarm) **blind to the gate's verdict reason**, by a reviewer who sees only the item and its sources.
- **Freeze:** the exported records, the injection script, its seed and the resulting items are hashed (sha256 manifest); the hash is published with the results.

## 5. Labels

Every item carries: `item_id`, `run_id`, `symbol`, `date`, `class` (clean / A / B / C / multi / natural), `error_code`, `target` (claim id, narrative or rationale), `mutation` (before → after), `expected` (catch / pass / out-of-scope), and for natural failures the adjudicated label plus the adjudicator's note.

## 6. Metrics

- **Catch rate** per error code = items with ≥1 `fail` result ÷ items, with **Wilson 95% intervals**. Reported separately for each class, never pooled across classes.
- **Localization:** share of caught A items where the failing result's `claim_id` is the mutated claim.
- **False-alarm rate** = clean items with ≥1 `fail` ÷ clean items (Wilson CI), and per check. `warn` results are reported separately; they are not counted as catches or false alarms.
- **Masking:** in the multi-fault subset, the share where both errors are flagged.
- **Cost:** gate wall time per item (median, p95) on the production VM; and the share of the clean set that enforce mode would hold (= the false-alarm rate in practice).
- **Gap size:** catch rate for class C, the honest measure of what a structured-claims gate does not cover.

## 7. Comparators (secondary, budget permitting)

- **Trivial baseline:** "flag any output whose narrative contains a digit". Its catch and false-alarm rates show what the structure adds.
- **LLM-as-verifier** (optional, needs spend approval): the same items given to one model with the sources and asked to flag wrong numbers. Same metrics, plus cost per item.

## 8. Analysis and reporting

One script regenerates every table from the frozen items and the gate's results. Every number is reported with its interval, the command and the commit. Class A results are labelled as a correctness check, not as evidence that the product is accurate.

## 9. Threats

- **Injections are synthetic.** Real LLM errors may cluster differently. Mitigation: class C uses realistic edits, and natural failures are adjudicated blind.
- **Single production universe** (15 symbols, about two weeks of runs at freeze time): results describe this system, not structured-claims gates in general.
- **The adjudicator knows the system:** blind to the gate's reason, and a 20% sample is double-checked by a second reviewer (owner or another model, recorded).

## 10. Change log

| Date | Change | Reason |
|---|---|---|
| 2026-10-07 | v0.1 design | — |
