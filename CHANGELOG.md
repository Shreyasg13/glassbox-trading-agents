# Changelog

All notable changes to GlassBox. Newest first. Each release lists what was **added**, **changed**, **fixed**, what is **built
but switched off**, and **known limitations**. Every S3 task PR adds its lines under "Unreleased"; a deploy turns "Unreleased"
into a dated release with a git tag.

## [Unreleased]

### Changed
- Removed the "For Advisors" section and its nav link. It pitched paid advisor features (white-label, multi-client,
  compliance export) and showed two testimonials attributed to named professionals that were not real customers.

### Fixed
- The A6 gate crashed on monthly data: BLS snapshots describe a month (`2026-08`), which the staleness check could not read,
  so no gate results were stored for any run. A month now counts as its last day. Found by the first production check.
- The gate can no longer be stopped by one odd value: a check that meets data it cannot handle records a failed check with
  the reason (that number is then not counted as verified) and the rest of the run is still recorded.
- Re-running the claims step for a saved decision no longer duplicates its claims: the claims table is append-only, so the
  first set stored for a run is kept and reused. Narrative rows for a run are replaced instead of failing on a duplicate.

## [s3-part1] — 2026-09-26 — S3 "Trustworthy MVP", part 1 (T0–T4, T9, T10, T11)

The goal of S3: nothing reaches a user, is scored as a track record, or appears in marketing unless it passed a verification gate
and a compliance filter and was written to an append-only ledger. This part builds the foundations: a point-in-time data store,
structured claims, the verification gate and the ledger. **Nothing blocks or changes what users see yet**; the single publish
exit (T5) will switch the gate on. Committee decision logic is unchanged (tests prove the saved decisions are identical).

### What users will notice
- The website no longer claims an "A6 Auditor badge" or a live "Discrepancy Rate dashboard"; both now say "coming" until they
  exist (P1, #20).
- The research disclaimer reads the same everywhere: "Simulated research, not investment advice." The committee report and the
  admin digest header now use this wording (they had slightly different sentences) (T9, #23).
- The pricing section is now a single free "research preview" card: no paid plans, payments or trials (the Standard $20 and Advisor $150 cards were marketing only) (#29).
- Nothing else changes for users in this release.

### Added
- **Feature flags and kill switches** (T1, #21). Admins can turn off, without a deploy: every email, report pages, speech
  (off by default), the Ask assistant, "run my report", and the whole daily job. Changes are audit-logged. Admin → Flags tab.
- **`require_role(...)`** for routes, plus a test that every route declares its role; 26 older public routes are on an
  allowlist that may only shrink (T1).
- **Alembic migrations** for all S3 tables (T1). They run once at container start; `python -m app.migrate --down base` rolls
  them back. Older tables are untouched.
- **Baseline discrepancy audit** of the 77 stored committee runs, with no new model calls: the honest "before" number
  (`docs/s3/baseline.md`) (T2, #22).
- **One disclaimer file**, `backend/config/disclaimer.md`, marked PENDING LEGAL REVIEW, used by the user digest, admin digest,
  weekly research digest, committee report, inbox answers and the assistant's fallback; `GET /api/public/disclaimer` (T9, #23).
- **Point-in-time snapshot store** (`source_snapshots`) (T10, #24). Every SEC facts / filings / insider, Treasury, BLS and daily
  price fetch is recorded with when it was fetched, what date it describes and a sha256 hash. The committee's extra context now
  reads the newest snapshot fetched no later than the run started, so a run can never see data fetched after it began.
  Admin → Snapshots tab.
- **Append-only, hash-chained call ledger** (`ledger_calls`) (T11, #25). Database triggers reject every UPDATE and DELETE; each
  row's hash covers the previous row's hash, so editing any row is detected at the exact row. Safe under concurrent writers.
  Admin → Ledger tab with "Verify chain". Not yet written to (T5/T12 will).
- **Structured claims** (`claims`) (T3, #26). After each committee decision, every number (price, fundamentals, macro, risk)
  is stored as a claim pointing at the exact value in its source snapshot; derived numbers (margins, growth, P/E, yield-curve
  spread, inflation) record their formula from a fixed table and a pointer for every input.
- **Placeholder-only narrative** (`committee_narratives`) (T3). One pinned-model call writes prose in which every number is a
  `{{claim:id}}` placeholder; any stray digit or unknown id is rejected, retried once, then held for review.
- **A6 verification gate** (`verification_results`) (T4, #27). After each run it checks every claim: matches its source,
  data fetched before the run, data not stale (warning only), price equals the price book, risk equals an independent
  recomputation, narrative uses only valid placeholders. Summary badge "N/N numbers verified against source" counts a number
  only if every check on it passed. Admin API `GET /api/admin/verification?run_id=` and `/verification/summary?date=`.

### Changed
- `committee_daily.run_daily`: after the committee finishes (and releases its lock) it attaches claims and runs the gate for
  each saved decision. Both steps are wrapped so they can never break a run or delay the committee's time budget.
- `free_data`: every refresh also writes a snapshot; a failed snapshot write is logged and never breaks the refresh.
- The daily price sync records a `prices` snapshot per symbol when bars change. `update_symbol()` keeps its signature.

### Security
- Claim formulas are checked by a small allow-listed evaluator (`+ - * /`, negation, `abs`, the constant 1). There is **no
  `eval`**: an early version evaluated formula text stored in the database, which T6's admin editing would have turned into
  code execution. Inputs must come from the source data, so a claim cannot "prove itself" with a typed-in number.
- The ledger is protected twice: the repository has only `append`/`read`/`head`/`verify`, and database triggers refuse edits.

### Fixed (found in review before release)
- `/api/admin/verification/summary` imported a table that does not exist and would have returned 500 on every call.
- A test left an admin login override on the shared app, making every later auth test run as admin.
- The macro context could silently drop a jobs-data series when only some series had snapshots.
- Timestamps are compared as instants, not strings (`isoformat()` drops zero microseconds).

### Built but switched off
- `pipeline.claims` flag (**default off**): the narrative LLM call. Needs a paid model key (decision D6). Claims and the gate
  run regardless; they make no model calls.
- `output.speech` stays off by default.

### Known limitations
- If a source's value goes A → B → A, the second A is not stored again (same hash), so the store returns B.
- Deleting the newest ledger rows is not detectable by a hash chain alone; proposal: publish the ledger head hash in the weekly
  report (T13).
- Staleness windows (prices 1 trading day, fundamentals 120 days, macro 35 days, insider trades 14 days) are proposals pending
  the owner's confirmation (decision D9).
- The disclaimer wording is a placeholder pending legal review (decision D7).

### Database migrations
`0001_feature_flags` → `0002_source_snapshots` → `0003_ledger_calls` → `0004_claims` → `0005_verification_results`, each with a
downgrade. Back up `/data/glassbox.db` before deploying.

### Tests
Backend 751 → 906 on the T0–T4 chain (+12 on the T9 branch); frontend type check clean.

### Coming next (S3 part 2)
T5 single publish exit (turns the gate on), T12 forward-only scoring, T6 quarantine and admin review, T8 compliance filter,
T7 "Show my work", T13 weekly discrepancy report, T14 gate-health dashboard, T15 ablation harness.

## 2026-09-25 and earlier
Changes before this file existed are in the git history and in `docs/PROJECT_STATUS.md`.
