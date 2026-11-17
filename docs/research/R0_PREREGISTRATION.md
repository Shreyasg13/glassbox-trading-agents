# R0 pre-registration: does the LLM committee beat a placebo on live data?

**Registered:** drafted 2026-10-07; the window was restarted on 2026-10-13, before any data in it existed (section 9). The registration's timestamp is this file's first commit on `main` and, once uploaded, its Zenodo DOI. Any later change goes in section 9 with date and reason; nothing is changed silently.

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

- **Start:** **2026-10-14** (see section 9: the window first opened 2026-10-08 and was restarted after a prompt change). All earlier live days, since the committee began (~2026-09-21), are **excluded** and reported separately.
- **Primary look:** after **60 live trading days** in the window (the existing GlassBox evidence gate; about 2027-01-08, counting US market holidays).
- **Final look:** after **120 live trading days** (about 2027-04-07).
- A trading day counts only if the paper cycle processed it for all three accounts. Days missing because of a data or cycle outage are not filled in; the window extends by the number of missing days, and the count is reported.

## 5. Metric and analysis

- **Unit:** the trading day. Metric: the daily simple-return difference between two accounts' equity curves (`committee − placebo` for H1, `committee − engine` for H2).
- **Estimate:** the mean daily difference, also reported annualised (× 252) and as a cumulative difference.
- **Interval and test (primary):** a confidence interval from a **moving-block bootstrap** (block length 10 trading days, 10,000 resamples, seed 20261007), because daily returns are autocorrelated. Reject at the corrected level below.
- **Multiplicity:** H1 is claim P4 in `docs/s3/PREREGISTRATION.md`, whose five claims share α = 0.01. Two looks, so each look tests H1 at **α = 0.005** (a 99.5% interval). H2 is reported at the same level, and labelled secondary.
- **P4 also requires** (`docs/s3/PREREGISTRATION.md`, 2026-10-13 amendments): GlassBox's evidence gate (`app/research.py`) returns the verdict "edge", and the deflated Sharpe of daily excess over the placebo is above 0.95. All three are reported at each look.

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
| 2026-10-07 | Drafted (PR #51) | — |
| 2026-10-13 | Window restarted at 2026-10-14; looks moved to about 2027-01-08 and 2027-04-07; α 0.025 → 0.005 per look; gate verdict and deflated Sharpe added | PR #54, deployed 2026-10-13, changed the committee prompt (P(beat SPY) on every call), which section 6 says ends the window. α and the extra tests align R0 with claim P4 of `docs/s3/PREREGISTRATION.md`. All stricter; no data in the new window existed. Days 2026-10-08 to 2026-10-13 are reported separately. |
