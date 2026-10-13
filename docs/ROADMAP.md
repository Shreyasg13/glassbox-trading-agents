# GlassBox roadmap

One page for "where are we". Detail lives in the linked plans; this page links them in order and keeps every past phase.
Last updated 2026-10-13. Dates ahead are planned; when one slips, the old date stays struck through next to the new one.

## History (done)

| When | Phase | What | Detail |
|---|---|---|---|
| to 2026-09-11 | 0-6 | Contract, FastAPI gateway, frontend shell, live signals, Agent Factory, provider layer, hardening | [PROJECT_STATUS.md](PROJECT_STATUS.md) |
| to 2026-09-11 | 7-14 | GCP deploy, marketing site, real data and daily refresh, committee personas, onboarding guide, report charts and more | [PROJECT_STATUS.md](PROJECT_STATUS.md) |
| 2026-09-19 | — | Paper accounts go live: the out-of-sample clock starts | [ARENA.md](ARENA.md) |
| 2026-09-26 to 09-29 | S3 | Trust layer: flags and roles, baseline audit, point-in-time snapshots, structured claims, A6 gate, A7 compliance, `publish()`, hash-chained ledger, forward-only scoring, gate health | [s3/GLASSBOX_S3_BUILD_PLAN.md](s3/GLASSBOX_S3_BUILD_PLAN.md) |
| 2026-10-13 | Research | Literature review: 14 benchmarks compared; open gaps and funding options | [research/reports/](research/reports/LLM%20trading%20benchmark%20gaps%20and%20funding.md) |
| 2026-10-12 | S3 | First forward-only study on production data; three scoring fixes; P(beat SPY) on every call; pre-registration; shuffle test and deflated Sharpe. Deployed 2026-10-13 (6651a1a) | [s3/PREREGISTRATION.md](s3/PREREGISTRATION.md) |

## Now (week of 2026-10-13)

- First committee run that records P(beat SPY): 2026-10-13 evening.
- L0: deflated Sharpe on excess over the placebo (2026-10-14).
- T6 quarantine review UI, so `publish.enforce` can be switched on (2026-10-17).

## Next

```
Oct 2026   14 L0 DSR fix · 17 T6 quarantine UI · 19 enforce on (owner) · 21 T7 Show my work
           23 L1 Lab + Strategies tab · 23 T13 first weekly report · 28 L2 Models tab · 30 L3 Market tab
Nov 2026   02 apply: Anthropic External Researcher Access (after attorney check, owner) · 20 L8 glassbox-eval package
           06 L4 cost-impact surface (3D) · 13 L5 auto-calibration built · 13 fourth weekly report (S3 exit possible)
           16 P5 checkpoint (gate) · 24 L5 first out-of-sample calibration result
Dec 2026   04 L6 challenger loop · 18 L9 multi-model leaderboard · ~15 P4 checkpoint (committee vs placebo, 60 live days)
           31 fixed checkpoint: study refresh (L7)
Jan 2027   13 literature re-check (quarterly) · ~15 P1-P3 checkpoint (400 P(beat SPY) calls, 60+ dates) · 29 L10 paper draft
Mar 2027   31 fixed checkpoint: study refresh (L7)
```

Plans: [S4_RESEARCH_LAB_PLAN.md](S4_RESEARCH_LAB_PLAN.md) (L0-L7) · open S3 tasks T6, T7, T13, T15 in
[s3/GLASSBOX_S3_BUILD_PLAN.md](s3/GLASSBOX_S3_BUILD_PLAN.md).

## Decisions waiting on the owner

| Decision | Needed by | Blocks |
|---|---|---|
| Switch on `publish.enforce` | 2026-10-19 | P5 clock |
| D6: paid model key for the ablation (budget ceiling $500/month in the S3 plan) | any time | T15 |
| D3: keep the assistant and user reports on, gate them, or switch to facts-only | before enforce | outputs outside the gate |
| Funding route: credit programs vs cash grants vs a company (immigration attorney first) | before 2026-11-02 | API credits for L9 |
| D7: disclaimer wording (counsel) | before any public claim | public track record wording |

## What can be claimed, by date

| From | Claim |
|---|---|
| now | The method: forward-only, tamper-evident, auto-scored, pre-registered pipeline; traceability rates with n |
| 2026-11-16 at the earliest | Whether the enforced gate keeps unsupported numbers out of published output (P5) |
| about 2026-12-15 | Whether the committee beats the placebo on paper after cost (P4) |
| about 2027-01-15 | Whether the committee's P(beat SPY) is informative, ranks stocks, and is calibrated (P1-P3) |

A checkpoint that fails is published as a failure. No claim is made before its row.

## Change log

| Date | Change |
|---|---|
| 2026-10-13 | Roadmap created; S4 planned; S3 status board brought up to date (T5, T12 done) |
| 2026-10-13 | Literature review added; L8-L10 (package, leaderboard, paper), quarterly literature re-check and funding decision added |
