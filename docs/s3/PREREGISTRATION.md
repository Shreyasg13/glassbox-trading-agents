# Pre-registration: what GlassBox will claim, and what data each claim needs

Written 2026-10-12, before any `p_beat_spy` outcome exists. The git commit date of this file is the
registration date. Change it only by a new dated section below; never edit a target after outcomes are visible.

## Why this exists

The first forward-only study (2026-09-29 to 2026-10-12, 150 ledger calls) found 149 HOLD calls and 1 BUY.
HOLD scores `sign(HOLD) * excess = 0`, so hit rate, Brier and calibration could not measure skill. This file
fixes the rules **before** more data arrives, so a later result cannot be a claim chosen after seeing it.

## Data rules (all claims)

- **Forward-only.** Only `ledger_calls` rows recorded before 09:30 ET on the entry day (`scoring.eligible`).
  No backtest, replayed or LLM-on-past-date calls are ever pooled with these.
- **Benchmark.** SPY, total price return, same entry/exit dates (`scoring.outcome`). SPY's own calls are excluded
  from every probability metric (its excess is 0 by construction).
- **Independence.** Calls made on the same day are correlated, and 5-day windows overlap. Every test below
  uses a **block bootstrap by entry date** (10,000 resamples, seed 20261012). Plain per-call t-stats are
  reported only as descriptive figures.
- **Checkpoints.** Results are evaluated only at these points, not continuously: when each target below is first
  met, and at 2026-12-31 and 2027-03-31 regardless. Every checkpoint is reported, including null results.
- **Multiple comparisons.** Five primary claims are listed below. Each is tested at α = 0.05 / 5 = 0.01.

## Primary claims

| # | Claim | Metric | Pass rule | Minimum data before testing |
|---|---|---|---|---|
| P1 | The committee's P(beat SPY) is informative | Brier score of `p_beat_spy` (horizon 5) vs the base-rate forecast | Bootstrap 99% CI of (base-rate Brier − committee Brier) is above 0 | 400 calls with `p_beat_spy`, across 60+ distinct entry dates |
| P2 | P(beat SPY) ranks outcomes | Spearman ρ of `p_beat_spy` vs 5-day excess return | Bootstrap 99% CI of ρ is above 0 | Same as P1 |
| P3 | P(beat SPY) is calibrated | Calibration bins of width 20 | Every bin with n ≥ 30 has its observed rate inside its bootstrap 99% CI of mean p | At least 3 bins with n ≥ 30 |
| P4 | The committee beats the placebo on paper | `research.gate`, `ctl_committee` vs `ctl_placebo` | Gate verdict "pass" (as coded) | 60 live days (the gate's own floor) |
| P5 | The evidence gate (A6) keeps unsupported numbers out of published output | Share of published runs with a traceability fail, enforce-on period | Rate in published runs is below the rate in all generated runs, 99% CI | 20 trading days with `publish.enforce` on |

## Secondary (descriptive, no pass/fail)

- **Directional hit rate.** BUY is right if 5-day excess > 0, SELL if < 0. HOLD is right if |5-day excess| ≤ 1.0%.
  The band is half the 5-day excess standard deviation seen in the first study (2.33%), fixed here before any
  hit rate was computed with it. Reported with n, and only once there are 30+ calls of a decision type.
- Horizon 1 and horizon 20 versions of P1–P3.
- Each analyst's `p_beat_spy` Brier score, to see which analysts carry the signal.
- Unsupported-number rate by day (`baseline_discrepancy`), with 09-18 reported separately.

## What will not be claimed

- Anything from backtests as evidence of forward skill. Backtests are shown separately and labelled in-sample.
- Results across all horizons or analysts picked after the fact as "the one that worked".
- Superiority over published systems. These results only describe GlassBox's own forward sample.

## Out of scope

- The durable-agent runtime: no runs yet. Future work.

## Amendments

**2026-10-12 (same day, before any `p_beat_spy` outcome exists).** Two tests added; nothing loosened.
- P1 also requires the **date-blocked shuffle test** (`scoring.shuffle_test`, 10,000 shuffles of `p_beat_spy`
  among calls with the same entry date) to give p < 0.01.
- P4 also requires a **deflated Sharpe ratio** (`research.deflated_sharpe`, Bailey & Lopez de Prado 2014) above
  0.95 for `ctl_committee`, with every evidence-table candidate counted as a trial.
- All numbers come from one read-only command: `python -m app.scripts.forward_study`.
