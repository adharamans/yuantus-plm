# DEV & Verification: PLM→ERP Publication Contract — R2 Worker Daemon Taskbook

Date: 2026-05-29

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_WORKER_DAEMON_TASKBOOK_20260529.md`
— the scope-lock for the background worker that auto-drains the R2 publication
outbox. Doc-only: no code; merging it does **not** authorize the worker
implementation. Baseline `main = e6cb4e54` (after the R2 HTTP routes impl #670).

## 1. What changed

- New worker-slice scope-lock taskbook (worker form; claim/locking; reason-based
  retry/backoff; per-send version-drift revalidate; legal-state entry; batch /
  stale reclaim / idempotent re-entry; additive schema; non-goals).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = e6cb4e54`)

The worker harness is modeled on proven repo patterns, not invented:

- **Claim/poll/retry/stale**: `JobService.poll_next_job` (`with_for_update(
  skip_locked=True)` PG + SQLite fallback), `fail_job` (linear backoff via
  `JOB_RETRY_BACKOFF_SECONDS * attempt_count`), `requeue_stale_jobs`
  (`JOB_STALE_TIMEOUT_SECONDS`); the standalone-daemon loop `JobWorker`
  (`run_once` = reclaim-stale + poll + dispatch) launched from `cli.py` `worker`.
- **Scheduler-trigger companion**: `SchedulerService.run_once` enqueues due work
  with an `enqueued` vs `would_enqueue` dry-run split.
- **Per-send safety reused from #670**: `process()` calls
  `_revalidate_allows_send(row, fresh)` (eligibility **and** version-identity;
  drift → `skipped`/`not_eligible` + `revalidated_version_mismatch`); reason-based
  retry (`remote_error`/`adapter_error` only).
- **Outbox model gap**: has `attempt_count`/`max_attempts`/`dispatched_at` but no
  `worker_id`/`claimed_at`/`next_attempt_at` — so the impl adds those additive
  columns (no new table; SQLite-clean migration).

## 3. Locked decisions (summary)

Dedicated `PublicationOutboxWorker` polling the outbox directly (ConversionJob-wrap
rejected — double-bookkeeping + bypasses the reason-based retry rule); claim via
`FOR UPDATE SKIP LOCKED` + `worker_id`/`claimed_at` (orthogonal to the locked
state machine, NOT a new state); retry only `remote_error`/`adapter_error` with
linear `next_attempt_at` backoff + dead-letter at max; every send revalidates via
the #670 `_revalidate_allows_send` gate; claim `pending`-only (no `dry_run_ready`
auto-send); bounded batch + stale reclaim + idempotent re-entry; additive
`worker_id`/`claimed_at`/`next_attempt_at` columns; **no HTTP route → route count
stays 683**. Non-goals: no real connector, no `/publication/export`.

Implementation guard added during review: retry accounting must count pre-send
`adapter_error` attempts too. Because #668 `process()` increments
`attempt_count` only immediately before `adapter.send()`, the worker's
`reschedule_retry` helper must increment once when a build/validate exception
left the count unchanged, so such failures cannot retry forever at attempt `0`.
The migration guard was also tightened: final `next_attempt_at` is not-null with
a due-immediately default, but the Alembic path must remain SQLite-clean even if
that requires add/backfill/batch-alter sequencing.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only scope-lock. Ratifying §2–§8 of the taskbook sets the worker
implementation plan; the worker implementation (then the real ERP connector and
`/publication/export`) each need their own explicit opt-in.
