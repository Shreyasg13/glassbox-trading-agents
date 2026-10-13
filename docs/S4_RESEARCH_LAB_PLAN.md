# S4: Research Lab and calibration

Written 2026-10-13. Follows S3 (trust layer; see [s3/GLASSBOX_S3_BUILD_PLAN.md](s3/GLASSBOX_S3_BUILD_PLAN.md)).
Claims, tests and sample targets are fixed in [s3/PREREGISTRATION.md](s3/PREREGISTRATION.md); this plan builds the tools that
measure them and must never loosen them. Dates are planned, not promised; slips are recorded in the change log, never silently moved.

## 1. Goal

One admin page, the **Research Lab** (`/admin/lab`), where the owner sees:

- which analyst model is best, measured on its own forward P(beat SPY) calls, with sample size and confidence intervals;
- how the market is structured (correlations, clusters, lead-lag) and how that changes;
- every strategy's live result against the placebo, after cost and tax, with deflated Sharpe;
- what trading costs do to a strategy (a cost-impact surface, the one 3D view);
- automatic calibration of each model, accepted only when it wins on calls it never saw;
- a challenger loop where new strategy variants are proposed, run forward in shadow, and promoted only through the gate.

## 2. Rules (from `research.py` and the pre-registration)

1. **Forward-only evidence.** Backtests appear only on panels labelled "historical, in-sample". They are never pooled with ledger data.
2. **Machines propose, people promote.** No code path changes an agent, prompt, weight or live strategy. Promotion is an admin click
   after the gate passes.
3. **Every variant is a trial.** Each challenger counts toward the deflated Sharpe trial count. New challengers are capped at
   **3 per month**, so the field cannot be searched until something wins by luck.
4. **Calibration must win out of sample.** A calibration map is fitted only on calls older than the evaluation window and is switched on
   only if it beats the raw probabilities there.
5. **Show n everywhere.** Any number with n < 10 is greyed and flagged; any verdict before its pre-registered minimum reads "insufficient".
6. **Cost is part of the result.** Every strategy figure is net of commission and, for taxable accounts, tax.

## 3. Milestones

Each milestone is one branch `s4/<id>-<slug>` and one PR. Sizes as in S3: S under half a day, M about a day, L two or more days.

| ID | Milestone | Size | Depends on | Planned | Done when |
|---|---|---|---|---|---|
| L0 | Deflated Sharpe on daily excess over the placebo, not raw returns; pre-registration amendment | S | — | 2026-10-14 | Evidence table shows DSR of excess; test proves a long-only no-skill strategy in a rising market fails |
| L1 | Lab page shell (`/admin/lab`, admin role, tabs) + **Strategies** tab: live equity vs placebo and SPY, excess with CI, DSR, cost and tax drag, gate status | M | L0 | 2026-10-23 | Tab renders real `forward_study` output; route has a role test |
| L2 | **Models** tab: per analyst model, Brier of its own `p_beat_spy`, base-rate Brier, calibration curve, rank IC, latency, cost per call | M | first 5-day P(beat SPY) outcomes (about 2026-10-20) | 2026-10-28 | Ranking shows CIs and reads "insufficient" below 30 scored calls per model |
| L3 | **Market** tab: correlation heatmap, clusters over time, lead-lag results already computed by `associations.py` | M | — | 2026-10-30 | No new statistics; existing outputs only, with dates |
| L4 | **Cost-impact surface** (3D): rule-engine excess return over cost (bps) × rebalance band, on history | M | L1 | 2026-11-06 | Labelled "historical, in-sample"; flat heatmap fallback on phones; data cached, not computed per request |
| L5 | **Auto-calibration**: per model isotonic map, walk-forward (fit on calls older than 20 trading days, score the next 5), raw vs calibrated Brier | L | L2, about 200 scored calls per model | build 2026-11-13, first out-of-sample result 2026-11-24 | A map is switched on per model only if calibrated Brier < raw on the held-out window; before/after shown in Models tab |
| L6 | **Challenger loop**: variants as versioned config with parent → child lineage, created as shadow paper accounts (max 3 a month), lineage view, promote button behind the gate | L | L1, L0 | 2026-12-04 | Test proves a challenger cannot reach any user channel; promotion requires gate "pass" and an admin action, logged in the audit log |
| L7 | **Study refresh**: regenerate the forward study page from `forward_study` at each pre-registered checkpoint | S | each checkpoint | 2026-12-31, 2027-03-31 | Every checkpoint published, including null results |

## 4. S3 work that S4 relies on (still open)

| Task | Why it matters here | Planned |
|---|---|---|
| T6 quarantine review UI | `publish.enforce` holds failing outputs; someone must be able to release or reject them. Needed before enforce goes on | 2026-10-17 |
| `publish.enforce` on (owner decision) | Starts claim P5's 20-day clock | 2026-10-19 |
| T13 weekly discrepancy report | S3 exit needs 4 in a row, so the earliest S3 exit is about 4 weeks after the first | first 2026-10-23, fourth 2026-11-13 |
| T7 "Show my work" | User-facing evidence view | 2026-10-21 |
| T15 ablation harness | Blocked on decision D6 (paid model key). Without it, committee-size results are not possible | after D6 |

## 5. Claim checkpoints (from the pre-registration; trading days, US market holidays excluded)

| Claim | Minimum data | Earliest date |
|---|---|---|
| P5 evidence gate keeps unsupported numbers out | 20 trading days with enforce on | 2026-11-16 (if enforce starts 2026-10-19) |
| P4 committee beats placebo on paper | 60 live days (16 on 2026-10-12) | about 2026-12-15 |
| P1-P3 P(beat SPY) informative, ranks, calibrated | 400 calls over 60+ entry dates | about 2027-01-15 |
| Fixed checkpoints | — | 2026-12-31, 2027-03-31 |

## 6. Not in S4

- Fine-tuning the LLMs. The committee runs on hosted, mostly free-tier models, and a few hundred calls cannot support it. L5 calibrates
  their outputs instead.
- Self-modifying strategies or prompts (rule 2).
- New data providers, a news feed, or paid data (Phase 9 in PROJECT_STATUS stays blocked on its own decision).
- The durable-agent runtime: no runs yet; future work.

## 7. Risks

1. **Correlated strategies.** All candidates hold the same 15 stocks, so their Sharpes barely differ and the DSR luck bar is low. L0 and
   the excess-over-placebo framing address this; it remains a limitation to state.
2. **Model drift.** Failover can swap the model behind an analyst. L2 ranks by the model that actually answered (recorded since T3).
3. **Free-tier quota.** Longer analyst answers (P(beat SPY)) add tokens. Watch the Observability page after 2026-10-13.
4. **Too many charts, too little n.** Rule 5 is enforced in the UI, not left to the reader.

## 8. Change log

| Date | Milestone | Summary | PR |
|---|---|---|---|
| 2026-10-13 | — | Plan written | `docs/roadmap-s4` |
