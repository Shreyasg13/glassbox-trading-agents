# R0 pre-registration: does the LLM committee beat a placebo on live data?

**Registered:** 2026-10-07, before any data in the analysis window exists. The registration's timestamp is this file's first git commit and, once uploaded, its Zenodo DOI. Any later change goes in section 9 with date and reason; nothing is changed silently.

GlassBox is a non-commercial research preview. Nothing here is investment advice.

## 1. Question

On out-of-sample (live) trading days, does acting on the 10-agent LLM committee's daily reviews produce a higher daily return than a **placebo** that trades on scrambled signals, under identical rules and costs?

## 2. What is compared (all are existing paper accounts in `backend/app/paper_cycle.py`)

| Account | Strategy | Role |
|---|---|---|
| `ctl_committee` | `committee_tilt` | **Treatment.** Identical to `ctl_engine`, except on a symbol the committee reviewed in the last 5 trading days, where the committee's call sets the tilt. |
| `ctl_placebo` | `random_tilt` | **Primary comparator.** Each symbol trades on another symbol's real signal history: same signal frequency and turnover, no information about the stock itself. |
| `ctl_engine` | `engine_tilt` | **Key secondary comparator.** Same as the treatment without the committee, so the difference isolates what the committee adds. |

Shared rules: the same 15 symbols at equal strategic weight, moderate profile, a decision at day D's close filled at D+1's close (no look-ahead), 5 bps costs, long-only, no leverage, pre-tax.

## 3. Hypotheses (two-sided)

- **H1 (primary):** the mean daily return of `ctl_committee` minus `ctl_placebo` differs from zero.
- **H2 (key secondary):** the mean daily return of `ctl_committee` minus `ctl_engine` differs from zero.

A null result is reported the same way as an effect.

## 4. Data window

- **Start:** the first trading day after registration, **2026-10-08**. The live days already observed (since the committee began, ~2026-09-21) are **excluded**, because they were seen before registration.
- **Primary look:** after **60 live trading days** in the window (the existing GlassBox evidence gate; about 2027-01-04, counting US market holidays).
- **Final look:** after **120 live trading days** (about 2027-04-01).
- A trading day counts only if the paper cycle processed it for all three accounts. Days missing because of a data or cycle outage are not filled in; the window extends by the number of missing days, and the count is reported.

## 5. Metric and analysis

- **Unit:** the trading day. Metric: the daily simple-return difference between two accounts' equity curves (`committee − placebo` for H1, `committee − engine` for H2).
- **Estimate:** the mean daily difference, also reported annualised (× 252) and as a cumulative difference.
- **Interval and test (primary):** a 95% confidence interval from a **moving-block bootstrap** (block length 10 trading days, 10,000 resamples, seed 20261007), because daily returns are autocorrelated. Reject at the corrected level below.
- **Multiplicity:** two looks, so each look tests H1 at α = 0.025 (Bonferroni across looks), i.e. a 97.5% interval. H2 is reported at the same level, and labelled secondary.
- **Also reported, for continuity:** GlassBox's existing evidence-gate verdict (`app/research.py`: iid bootstrap of daily excess returns, multiplicity-corrected), with its result shown side by side and differences explained.

## 6. Stopping rule

- **No early stopping for success.** Analyses happen only at the two looks.
- **Committee outages:** on days the committee did not run (e.g. provider rate limits), `committee_tilt` falls back to the engine, and those days stay in the analysis (intention to treat). The number of such days, and the share of symbol-days with a fresh committee call, are reported.
- **Stopping for harm or failure:** only if the paper cycle can no longer run, or the committee is disabled for more than 20 consecutive trading days. Then the last complete window is analysed and the study is reported as stopped, with the reason.
- **No changes inside the window** to agents, prompts, weights, the risk gate or the strategies under test. GlassBox's research loop already has no code path that changes them automatically. Any manual change ends the window early, and that is reported.

## 7. Threats and limits (stated up front)

- **15 symbols, chosen with hindsight;** equal-weight hold has been the strongest yardstick in-sample. The study claims nothing about other universes.
- **Short horizon:** 60–120 days of returns are noisy. A non-significant result is likely and is reported as inconclusive, not as "no effect", unless the interval is narrow enough to say so.
- **Model and provider drift:** the committee's LLM providers can change; the committee's logs record providers per run, and they are reported.
- **Paper trading, not live execution:** fills at the close with fixed costs.

## 8. Commands and data

All inputs are the stored daily equity curves of the three accounts (`/api/admin/paper/*`). The analysis script is committed before the primary look and run unchanged at both looks; its output, the commit and the command are published with the result.

## 9. Change log

| Date | Change | Reason |
|---|---|---|
| 2026-10-07 | Registered | — |
