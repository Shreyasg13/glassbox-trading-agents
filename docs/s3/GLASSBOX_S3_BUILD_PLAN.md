# GlassBox — S3 "Trustworthy MVP" Build Plan

> **For Claude Code.** Read this whole file before writing any code. Work through the tasks in order. After each task, tick its checkbox in the **Status board** and add one line to the **Change log** at the bottom.

---

## 0. How to use this document

1. **Explore first (task T0).** The file names below come from the architecture diagram. They may not match the repo exactly. Map every name to its real path before you edit anything, and record the mapping in section 3.
2. **One task = one branch = one PR-sized commit series.** Branch naming: `s3/T<id>-<slug>`.
3. **Every task has acceptance criteria.** A task is done only when all criteria pass and the tests listed in it exist and are green.
4. **Stay in scope.** Section 2 lists what must not be built. If a task seems to need something out of scope, stop and ask.
5. **Stop and ask** before:
   - any destructive DB migration (dropping or renaming a column or table)
   - changing auth or OAuth flows
   - deleting existing data or files
   - any change to wording users see that relates to legal disclaimers (Phase 2 needs human legal review)
6. **Visibility is a first-class requirement.** Every feature is tagged **[USER]**, **[ADMIN]**, **[PUBLIC]** or **[SYSTEM]**. Section 5 is the source of truth for who can see what. Enforce it on the server, never only in the UI.

---

## 1. Goal

Move GlassBox from a *feature-complete prototype* to a *trustworthy MVP*. The rule the whole plan enforces:

> **Nothing reaches a user, gets scored as a track record, or appears in marketing unless it has passed the A6 verification gate, passed the A7 compliance filter, and been written to the append-only call ledger.**

### Target pipeline

```
Market + public data (existing sync)
        │
        ▼
Point-in-time store            ← NEW: as-of timestamps on every row
        │
        ▼
Investment committee           ← CHANGED: emits structured claims, not free prose
        │
        ▼
A6 verification gate  ──fail──► Quarantine → admin review
        │ pass
        ▼
A7 compliance filter  ──fail──► Quarantine → admin review
        │ pass
        ▼
Call ledger                    ← NEW: append-only, hash-chained
        │
        ├──► Reports / Email digests / Speech (existing outputs, now gated)
        └──► Scoring loop (paper vs quant baselines, forward-only)
                    │
                    ▼
             Discrepancy rate (published weekly, public page)
```

---

## 2. Scope guardrails

**In scope:** the tasks in section 6 only.

**Out of scope — do not build or extend:**
- New features for speech generation (`tts.py`). Keep it working, put it behind the gate and a feature flag, and add nothing else.
- New challenger-arena features (`arena.py`). Only change it to read from the ledger.
- New data providers, new LLM providers, or new UI pages beyond those listed in section 5.
- Any backtest that asks an LLM to make calls on historical dates. **This is forbidden** because of look-ahead bias: LLMs memorize market history, so such backtests look better than reality. Performance evidence must come only from forward-recorded ledger calls.

**Constraints:**
- The A6 gate and the A7 filter must be **deterministic Python with no LLM calls.**
- Do not add new paid infrastructure. The budget ceiling is $500/month.
- Keep the existing stack: FastAPI backend, Next.js frontend, the existing `db.py` storage layer, and the existing `auth.py` plus Google OAuth.

---

## 3. Repo map (fill in during T0)

| Diagram name | Role | Actual path (fill in) |
|---|---|---|
| `main.py` | FastAPI gateway | `backend/app/main.py` (routers are registered at the bottom; Caddy only forwards `/api/*`, `/auth/*`, `/health`, `/ws/*` to it) |
| HTTP routers | Route modules | `backend/app/routers/*.py`: admin, auth, oauth, data, me, reports, committee, strategy, paper, inbox, analytics, user_digest, tts, insights, monte_carlo, ws, jobs_ws |
| `pipeline.py` | Daily pipeline | `backend/app/pipeline.py` (stages built in `default_stages`; entry point `python -m app.scripts.run_daily_pipeline`) |
| `data_source.py` | Market data sync | `backend/app/data_source.py` reads price files and reports. The price SYNC itself is `backend/app/scripts/update_daily_data.py` (`update_symbol`) plus `backend/app/price_store.py`, called through `pipeline.default_sync()` |
| `free_data.py` | Free public data | `backend/app/free_data.py` (`refresh_all`, `refresh_sec`, `refresh_macro`, `refresh_insiders`; cache under `TRADING_STORAGE_PATH/free_data`) |
| `committee_graph.py` | Investment committee (LangGraph?) | `backend/app/committee_graph.py` (LangGraph; `run_committee_graph`, `FORMAT_INSTRUCTIONS`). It is driven by `backend/app/committee_daily.py` (`run_daily`, `select_candidates`, `build_context`, `ceo_brief`, `_run_doc`) |
| `research.py` (research) | Research and filings | NO SEPARATE MODULE EXISTS. SEC filings are `free_data.py`; cross-stock associations are `backend/app/associations.py` |
| `llm_router.py` | LLM failover | `backend/app/llm_router.py` (`complete_routed`) plus `llm_chat.py` and `llm_call_logging.py` |
| `factory.py` | Model providers | `backend/app/providers/factory.py` plus `providers/{gemini,claude,openai_compat,ollama,vllm}.py` |
| `risk.py` | Risk checks | `backend/app/risk.py` (`risk_at`); today it is a deterministic risk SIGNAL computed from prices and also fed INTO the committee |
| `digest.py` | Reports and digests | `backend/app/digest.py` (admin digest email) and `backend/app/user_digest.py` (per-user emails); report narratives are written by `committee_daily._write_report`, `paper_cycle._write_reports` and `research.py` |
| `arena.py` | Challenger arena | `backend/app/arena.py` |
| `portfolio_view.py` | Portfolio views | `backend/app/portfolio_view.py` and `backend/app/portfolio_analytics.py` |
| `research.py` (evidence) | Evidence gate — **the name clashes with the research module; confirm which is which** | `backend/app/research.py` IS the evidence gate and weekly research loop (the two names in the diagram are one module here) |
| `paper_cycle.py` | Paper accounts | `backend/app/paper_cycle.py` (`run_cycle`) on top of the engine in `backend/app/paper.py` |
| `strategy.py` | Quant baselines | `backend/app/strategy.py` (read models). The BASELINES themselves are the `ctl_*` accounts built in `paper_cycle._control_accounts` (SPY, equal-weight, placebo, cash, trend, vol-target, engine) |
| `tts.py` | Speech generation | `backend/app/tts.py` and `backend/app/routers/tts.py` |
| `auth.py` | Authentication | `backend/app/auth.py` (`get_current_user`, `require_admin`) |
| `oauth_google.py` | Google OAuth | `backend/app/oauth_google.py` and `backend/app/routers/oauth.py` |
| `db.py` | Application storage | `backend/app/db.py` (SQLAlchemy Core tables, JSON-blob pattern for most, real columns for newer tables) |
| `page.tsx` | Next.js user app | `frontend/app/(app)/dashboard/page.tsx` plus the other pages under `frontend/app/(app)/` (ask, notifications, reports, track-record, agents, onboarding) |
| `ResearchPanel.tsx` | Admin console | `frontend/components/admin/strategy/ResearchPanel.tsx`; the admin console tabs are in `frontend/app/(app)/admin/strategy/page.tsx` |
| `api.ts` | Frontend API client | `frontend/lib/api.ts` (`apiFetch`, `ApiError`); shared types in `frontend/lib/types.ts` |

Also record in T0:
- DB engine and migration tool: SQLAlchemy Core. SQLite file `/data/glassbox.db` in production (Docker volume); Postgres if `DATABASE_URL` is set. There is NO migration tool: tables are created by `metadata.create_all` in `db.init_schema()` at import time, which can only ADD tables. Renames, drops and column changes have no path today (see EXECUTION_PLAN.md decision D2)
- How the "admin" role is determined today: a `role` claim inside the signed JWT. `auth.require_admin` returns 403 unless it equals "admin". Only the bootstrap admin (password hash in the `GLASSBOX_ADMIN_PASSWORD_HASH` env var) gets it; self-serve signups and Google logins are always "viewer". There is no `require_role(...)` yet
- Test framework and command (backend / frontend): backend `cd backend && python -m pytest -q` (pytest, 751 tests, plus `pip_audit`); frontend has NO test framework, CI only runs `npx tsc --noEmit`. CI = `.github/workflows/ci.yml` (jobs `backend-tests`, `frontend-typecheck`)
- How the daily pipeline is triggered (cron, scheduler…): the VM's crontab, weekdays at 20:35 and 23:45 UTC, runs `docker compose exec -T backend python -m app.scripts.run_daily_pipeline` (the second run adds `--no-wait` and skips stages already done). There is no in-app scheduler
- The committee's current output format (paste one real example into `docs/s3/committee_output_example.json`): done, see that file (a real 2026-09-25 run; long text trimmed). Shape: a decision object with votes, a deterministic `ceo` block and `headline`, per-agent rows, and a `context` string that carries the numbers. There are no structured numeric claims yet

---

## 4. Data model (new tables / collections)

Adapt names to the existing `db.py` conventions. Use migrations, never manual DDL.

| Entity | Key fields | Visibility |
|---|---|---|
| `snapshots` | `id`, `source`, `ticker`, `as_of` (UTC), `fetched_at`, `payload_json`, `payload_hash` | SYSTEM, ADMIN (inspector) |
| `claims` | `id`, `run_id`, `ticker`, `metric`, `value`, `unit`, `period`, `source_snapshot_id`, `source_path` (JSON pointer), `text_span` | USER (evidence view), ADMIN |
| `verification_results` | `id`, `run_id`, `claim_id` (nullable), `check_type`, `status` (`pass`/`fail`/`warn`), `expected`, `observed`, `reason`, `created_at` | ADMIN; the summary badge is USER |
| `compliance_events` | `id`, `run_id`, `rule_id`, `matched_text`, `action` (`blocked`/`rewritten`/`flagged`), `created_at` | ADMIN |
| `quarantine_items` | `id`, `run_id`, `stage` (`A6`/`A7`), `status` (`pending`/`approved`/`rejected`), `reviewer_id`, `review_note`, `reviewed_at` | ADMIN |
| `ledger_calls` | `seq` (monotonic), `call_id`, `ticker`, `call_type`, `payload_json`, `input_snapshot_ids`, `committee_config_id`, `recorded_at`, `prev_hash`, `hash` | USER (own track record view), PUBLIC (aggregate), ADMIN (full) |
| `call_outcomes` | `call_id`, `evaluated_at`, `horizon`, `outcome_json`, `score` | USER (aggregate), ADMIN |
| `discrepancy_reports` | `id`, `period_start`, `period_end`, `outputs_total`, `outputs_failed_A6`, `outputs_failed_A7`, `rate`, `top_issues_json`, `published` (bool) | PUBLIC once published, ADMIN always |
| `committee_configs` | `id`, `n_members`, `member_roles_json`, `model_map_json`, `active` | ADMIN |
| `ablation_runs` | `id`, `config_id`, `call_id`, … | ADMIN |
| `feature_flags` | `key`, `enabled`, `updated_by`, `updated_at` | ADMIN |

**The ledger is append-only.** Never update or delete rows in `ledger_calls`. Enforce this in code: the repository class exposes only `append()` and `read()` methods. If the DB supports it, also add a trigger or rule that rejects UPDATE and DELETE.

**Hash chain:** `hash = sha256(prev_hash + canonical_json(call_row_without_hash))`. The first row uses `prev_hash = "GENESIS"`.

---

## 5. Visibility matrix (source of truth)

Roles: **PUBLIC** (not logged in), **USER** (logged in), **ADMIN** (admin role).

### 5.1 User-facing (`page.tsx` and child routes)

| Surface | PUBLIC | USER | ADMIN | Notes |
|---|:-:|:-:|:-:|---|
| Report view: verified reports only | – | ✓ | ✓ | A report still in quarantine returns **404 to users**, not an error message |
| Verified badge: "N/N numbers verified against source · as of <time>" | – | ✓ | ✓ | Shows pass counts only; never exposes internal reasons |
| "Show my work" panel: each claim with source, field and as-of time | – | ✓ | ✓ | Reads from `claims` and `snapshots`; show only the value and path, not the full raw payload |
| Research-only disclaimer on every report and digest | ✓ | ✓ | ✓ | Text comes from a config file; wording needs human legal approval |
| Track record: forward calls with recorded time and outcome | aggregate only | ✓ | ✓ | Every row shows `recorded_at`; label it "recorded before outcome" |
| Transparency page: weekly discrepancy rate history | ✓ | ✓ | ✓ | Only `published=true` reports |
| Email digests | – | ✓ (subscribed) | ✓ | Built only from ledger calls that passed both gates |
| Speech | – | ✓ if flag on | ✓ | Uses gated text only; flag defaults to **off** |

### 5.2 Admin-facing (`ResearchPanel.tsx`, new tabs)

| Tab | What it shows / does |
|---|---|
| **Quarantine queue** | Pending items with the failed checks and a side-by-side of expected vs observed. Actions: **Approve** (re-runs A6 and A7; if the item still fails, a written override reason is required), **Reject**, **Edit and resubmit** (the edit goes back through both gates) |
| **Gate health** | Pass rate by day, top failing check types, top failing metrics, and a list of recent failures |
| **Compliance log** | Rule hits, matched text and the action taken |
| **Ledger explorer** | Search by ticker, date or config. A **Verify chain** button recomputes all hashes and reports the first broken `seq`, if any |
| **Snapshot inspector** | View the raw payload for any `snapshot_id` referenced by a claim |
| **Discrepancy reports** | The auto-generated weekly draft. Admin edits `top_issues` and presses **Publish**; publishing makes it PUBLIC and cannot be undone |
| **Ablation** | Results by committee size (n = 1, 3, 5, 7): calls, hit rate versus baselines, confidence intervals. Read-only |
| **Feature flags & kill switches** | Per output channel (reports, email, speech) and for the pipeline. Every change is logged with who made it and when |

### 5.3 Enforcement rules
- Every new API route declares its role with a single dependency (for example `require_role("admin")`). This is the only permitted way to check roles.
- Admin endpoints live under `/admin/*`. Public endpoints live under `/public/*`.
- Add a test that lists every route in the app and fails if any route has no declared role.
- User endpoints must never return rows with `quarantine_items.status != 'approved'` unless those rows also passed the gate.

### 5.4 API surface (add to `api.ts` as typed clients)

```
PUBLIC  GET  /public/transparency/discrepancy          -> published reports
PUBLIC  GET  /public/track-record/summary              -> aggregate hit rate, n calls, since date
USER    GET  /reports/{id}                             -> verified report or 404
USER    GET  /reports/{id}/evidence                    -> claims + source paths + as_of
USER    GET  /track-record                             -> ledger calls + outcomes (paginated)
ADMIN   GET  /admin/quarantine?status=pending
ADMIN   POST /admin/quarantine/{id}/approve            {override_reason?}
ADMIN   POST /admin/quarantine/{id}/reject             {note}
ADMIN   POST /admin/quarantine/{id}/resubmit           {edited_payload}
ADMIN   GET  /admin/gate/metrics?from&to
ADMIN   GET  /admin/compliance/events?from&to
ADMIN   GET  /admin/ledger?ticker&from&to&config
ADMIN   POST /admin/ledger/verify                      -> {ok, first_broken_seq?}
ADMIN   GET  /admin/snapshots/{id}
ADMIN   GET  /admin/discrepancy/drafts
ADMIN   POST /admin/discrepancy/{id}/publish
ADMIN   GET  /admin/ablation/results
ADMIN   GET  /admin/flags      POST /admin/flags/{key}
```

---

## 6. Tasks

### Phase 0 — Freeze and baseline

#### T0 · Repo discovery (no code changes)
- Fill in section 3 completely.
- Write `docs/s3/current_flow.md` tracing one ticker from data sync to user output, citing file and function names.
- List every place user-facing text leaves the system: report API, digest email, speech, any others you find.
- **Acceptance:** section 3 has no blanks, and every output channel is listed. Include a list of anything that surprised you.

#### T1 · Feature flags and output kill switches · [ADMIN] [SYSTEM]
- Add a `feature_flags` table and a helper `flag(key) -> bool`.
- Put `output.speech` behind a flag (default **off**), plus `output.email`, `output.reports` and `pipeline.daily` (default on).
- Build the admin Flags tab.
- **Acceptance:** turning off `output.email` stops the digest from sending. Tests cover each flag. Every flag change is written to the audit log.

#### T2 · Baseline discrepancy script · [ADMIN]
- Create `scripts/baseline_discrepancy.py`. It runs the **current** pipeline on 100 tickers (the list goes in `docs/s3/baseline_tickers.txt`), pulls every number out of the output, compares each against the raw source data, and writes a CSV plus a summary.
- **Acceptance:** the script runs end to end and produces the baseline rate in `docs/s3/baseline.md`. This number is the "before" for everything that follows.

### Phase 1 — A6 verification gate (the moat)

#### T3 · Structured claim output from the committee · [SYSTEM]
- Change the committee's final node so it emits JSON matching `schemas/committee_output.schema.json`:
  ```json
  {
    "run_id": "uuid",
    "ticker": "AAPL",
    "narrative": "text with {{claim:c1}} placeholders",
    "claims": [
      {"id": "c1", "metric": "debt_to_equity", "value": 1.87, "unit": "ratio",
       "period": "FY2025", "source_snapshot_id": "snap_…", "source_path": "/balanceSheet/0/debtToEquity"}
    ],
    "call": {"type": "rating|watch|none", "direction": "…", "horizon_days": 30, "confidence": 0.0}
  }
  ```
- The narrative may contain numbers **only** through `{{claim:id}}` placeholders. The renderer fills them in from verified claims.
- Validate the output against the schema. If the model output is invalid, retry once through `llm_router.py`, then send the run to quarantine.
- **Acceptance:** 20 consecutive runs produce valid schema output; a test covers the invalid-output path.

#### T4 · A6 verification gate · [SYSTEM]
- Create a new module `verification/gate.py`: a pure function `verify(output, snapshot_store) -> GateResult`, with no network or LLM calls. It runs these checks:
  1. **Source match.** Re-read `source_path` from the referenced snapshot and compare it to the claimed value. Tolerance: a relative difference of 0.5%, or rounding at the displayed precision. Understand units, so a percentage and a ratio are not falsely compared.
  2. **Orphan numbers.** After rendering, no digit sequence may appear in the text unless it came from a claim. Whitelist years and dates that appear in the claim's `period`.
  3. **Staleness.** The snapshot's `as_of` must be within the freshness window for its metric type. Configure the windows in `verification/config.yaml`.
  4. **Snapshot integrity.** `payload_hash` must match the stored payload.
  5. **Risk checks.** Run `risk.py` here, *after* the committee, as an independent check. Remove the call from inside the committee, or leave it there as advisory only.
- Write every check result to `verification_results`.
- **Acceptance:** at least 30 unit tests, including crafted wrong values, unit mismatches, orphan numbers, stale snapshots and tampered payloads. Gate latency is under 200 ms per report.

#### T5 · Route every output through the gate · [SYSTEM]
- Build a single function `publish(run)`: gate → (T8 compliance) → ledger → fan out to the channels.
- Replace every direct output path listed in T0 with `publish()`. Nothing may call `digest.py`, the report writer or `tts.py` directly with committee output.
- Add a test that fails if any module other than `publish` imports the output writers. Use an import-graph check or a grep in CI.
- **Acceptance:** a report that fails the gate never appears on any channel. An end-to-end test proves this for each channel.

#### T6 · Quarantine and admin review UI · [ADMIN]
- Build the quarantine table, the endpoints from 5.4 and the Quarantine tab from 5.2.
- Approving always re-runs the gates. Overriding a failed check requires a written reason and the reviewer's ID, both stored.
- **Acceptance:** the full cycle works — fail, quarantine, edit, resubmit, pass, publish. Non-admins get a 403 on every `/admin/*` route (test this).

#### T7 · User evidence view ("Show my work") · [USER]
- Add a verified badge and an expandable evidence panel to the report view in `page.tsx`.
- Clicking a number highlights its claim and shows the source, field path and as-of time.
- **Acceptance:** every number on a report can be clicked through to its source. Snapshot of the UI test passes. Nothing internal (failure reasons, raw payloads, quarantine details) leaks to the user view.

### Phase 2 — A7 compliance filter

#### T8 · Compliance filter · [SYSTEM]
- Create `compliance/filter.py` with rules in `compliance/rules.yaml`, which should include:
  - banned advisory phrases ("you should buy", "guaranteed", "can't lose", "risk-free", and similar)
  - no personalized instructions tied to the user's own holdings
  - performance mentions must reference ledger data
  - the required disclaimer must be present
- Actions: `block` (sends to quarantine), `rewrite` (only for safe, deterministic substitutions), or `flag`.
- Log every hit to `compliance_events`.
- **Acceptance:** at least 25 rule tests. The filter runs after A6 inside `publish()`.

#### T9 · Disclaimer config and compliance log UI · [USER] [ADMIN]
- Load the disclaimer text from `config/disclaimer.md`, marked **PENDING LEGAL REVIEW**. Do not invent final legal wording.
- Build the Compliance log admin tab.
- **Acceptance:** the disclaimer shows on every report, digest and speech script.

### Phase 3 — Point-in-time data and call ledger

#### T10 · Point-in-time snapshot store · [SYSTEM] [ADMIN]
- Update `data_source.py` and `free_data.py` so every fetch writes a `snapshots` row with `as_of`, `fetched_at` and `payload_hash`.
- The committee must read data only through `snapshot_store.get(ticker, source, as_of<=run_time)`.
- Build the Snapshot inspector admin tab.
- **Acceptance:** a test proves the committee cannot see any snapshot with `fetched_at` later than the run time.

#### T11 · Append-only hash-chained ledger · [SYSTEM] [ADMIN]
- Build the `ledger_calls` repository with `append()` and `read()` only, and the hash chain from section 4.
- Build `POST /admin/ledger/verify` and the Ledger explorer tab.
- **Acceptance:** tampering with any row in a test DB is detected at the correct `seq`. Concurrent appends keep `seq` monotonic (test with parallel writers).

#### T12 · Forward-only scoring · [SYSTEM] [USER] [PUBLIC]
- Change `paper_cycle.py`, `arena.py` and the evidence gate so they score **only** `ledger_calls`, and only where `recorded_at` is earlier than the start of the outcome window.
- Compare against `strategy.py` baselines using the same calls and the same time horizons.
- Build `/track-record` (USER) and `/public/track-record/summary` (PUBLIC).
- Stop showing any performance numbers from before the ledger existed; label that period "pre-ledger, not scored".
- **Acceptance:** a test shows a call recorded after its outcome window opens is excluded from scoring. Track-record rows display `recorded_at`.

### Phase 4 — Transparency

#### T13 · Weekly discrepancy report · [ADMIN] [PUBLIC]
- A scheduled job (reuse the existing scheduler) creates a draft every Monday from `verification_results` and `compliance_events`.
- Admin reviews and publishes the draft (5.2). Build the public transparency page.
- **Acceptance:** the draft is created automatically; only published reports are public; published reports are read-only.

#### T14 · Gate health dashboard · [ADMIN]
- Build the Gate health tab from 5.2.
- **Acceptance:** it shows the daily pass rate, the top five failing checks and the top five failing metrics over any date range.

### Phase 5 — Committee size ablation

#### T15 · Ablation harness · [ADMIN]
- Create `committee_configs` for n = 1, 3, 5 and 7, all using the same member roles (a subset for smaller n) and the same models.
- The daily pipeline runs every config on the same tickers. Each config's calls go into the ledger tagged with `committee_config_id`, but **only the active config's output goes to users.** The others are shadow runs.
- Watch LLM cost: put a daily token budget in config. If it's exceeded, reduce the ablation ticker sample, never the production run.
- Build the Ablation tab: calls per config, hit rate versus baseline, 95% bootstrap confidence intervals, and a warning when n is below 200 scored calls.
- **Acceptance:** shadow calls never reach any user channel (test this). The results table renders from real ledger data.

---

## 7. Status board

- [x] T0 Repo discovery
- [x] T1 Feature flags and kill switches
- [x] T2 Baseline discrepancy script
- [x] T3 Structured claim output
- [x] T4 A6 verification gate
- [ ] T5 Route all outputs through `publish()`
- [ ] T6 Quarantine and admin review
- [ ] T7 User evidence view
- [x] T8 A7 compliance filter (filter built; wired into outputs by T5)
- [x] T9 Disclaimer config (the compliance log tab moved to T8: it needs `compliance_events`; the Compliance tab now exists)
- [x] T10 Point-in-time snapshot store
- [x] T11 Hash-chained ledger
- [ ] T12 Forward-only scoring
- [ ] T13 Weekly discrepancy report
- [ ] T14 Gate health dashboard
- [ ] T15 Ablation harness

**Dependencies:** T0 → everything. T3 → T4 → T5. T5 → T6, T7, T8. T10 → T4 (the full source-match check needs snapshots; until T10 lands, T4 can use a stub store). T11 → T12 → T15. T13 needs T4 and T8.

**Suggested order:** T0, T1, T2, T10, T3, T4, T5, T6, T8, T9, T7, T11, T12, T13, T14, T15.

---

## 8. S3 exit criteria (the whole plan is done when all hold)

| Metric | Target |
|---|---|
| User-visible outputs that went through `publish()` | 100% (enforced by a CI test) |
| A6 pass rate on production runs | ≥98% (stretch goal 99.9%) |
| Outputs that passed A7 | 100% |
| Performance claims backed by the ledger | 100% |
| Ledger chain verification | passes |
| Routes without a declared role | 0 |
| Weekly discrepancy reports published | 4 in a row |
| Out-of-scope features added | 0 |

---

## 9. Definition of done (every task)

- The code follows existing repo conventions (lint and format pass).
- Tests are added and the full suite is green.
- Every new route has a declared role, and a test checks unauthorized access.
- Nothing in user-facing responses exposes internal failure reasons, raw payloads or admin data.
- Migrations can be rolled back.
- Section 7 is ticked and the change log is updated.

---

## 10. Change log

| Date | Task | Summary | PR/commit |
|---|---|---|---|
| 2026-09-26 | T0 | Repo map filled, current flow traced, output channels listed, surprises recorded, real committee output saved, execution plan written | PR `s3/T0-discovery` |
| 2026-09-26 | T1 | `feature_flags` table (Alembic 0001), `flags.flag()`, kill switches on email, reports, speech (default off), assistant, user reports and the daily job; admin Flags tab and API; `require_role`; every-route-declares-a-role test with a shrinking allowlist of 26 legacy public routes; Alembic adopted for S3 tables only | PR `s3/T1-flags` |
| 2026-09-26 | T2 | `app/scripts/baseline_discrepancy.py` audits the 77 stored committee runs with no new model calls: context fidelity 1.1% differ (0.2% beyond rounding), headline 4.0% (all 1 point), AI-prose unsupported numbers 2.3% overall but 0.2% since the first day. Report `docs/s3/baseline.md`, per-number data `docs/s3/baseline.csv`. 22 tests incl. a check against the real context builder | PR `s3/T2-baseline` |
| 2026-09-26 | T9 | `backend/config/disclaimer.md` (PENDING LEGAL REVIEW) is the one source for the disclaimer in 6 output sites; `GET /api/public/disclaimer`; compliance log tab moved to T8. Built by the twin worker, 2 review rounds | PR #23 `s3/T9-disclaimer` |
| 2026-09-26 | T10 | `source_snapshots` (Alembic 0002): every free-data and price fetch recorded with as_of, fetched_at, sha256; committee context reads snapshots fetched <= run time (cache fallback per source/series); admin Snapshots tab. 3 review rounds | PR #24 `s3/T10-snapshots` |
| 2026-09-26 | T11 | `ledger_calls` (Alembic 0003): append-only (triggers on SQLite and Postgres), sha256 hash chain, `BEGIN IMMEDIATE`/table lock for concurrent appends; admin Ledger tab + verify. Accepted first time; reviewer verified 180 concurrent appends and tamper detection at the exact row | PR #25 `s3/T11-ledger` |
| 2026-09-26 | T3 | `claims` + `committee_narratives` (Alembic 0004): deterministic claims with exact JSON pointers, derived values from a fixed FORMULAS table checked by an `ast` allow-list (no eval); placeholder-only narrative behind `pipeline.claims` (default off); decisions proven identical. 3 rounds incl. a security fix | PR #26 `s3/T3-claims` |
| 2026-09-26 | T4 | `verification_results` (Alembic 0005): pure A6 checks (traceability, point-in-time, staleness, price, risk, narrative) + runner wired after the claims step; badge counts fully verified claims; 40 gate tests. Finished by the reviewer after the worker hit the free-model daily limit | PR #27 `s3/T4-a6-gate` |
| 2026-09-26 | T8 | `compliance_events` (Alembic 0006); pure `compliance.check()` + never-raising `record()`; rules as data in `backend/config/compliance_rules.json` (JSON, not the planned rules.yaml: PyYAML is not installed): promotional phrases, direct advice and personalized holding instructions (real tickers only) block, but are flagged when quoted or reported; "act now" and performance mentions without `{{ledger:...}}` flag; a missing disclaimer is appended (the only rewrite, HTML-aware); matching sees through HTML; admin Compliance tab + `/api/admin/compliance/events` and `/rules`; 113 rule tests (133 in `test_compliance.py`). One review round (false positives on real text, HTML, regex performance). Filter built; wired into outputs by T5 | branch `s3/T8-compliance` |
