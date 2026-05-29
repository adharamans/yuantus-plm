# Claude Taskbook: PLM→ERP Publication Contract — R2 Background Worker Daemon (Scope-Lock)

Date: 2026-05-29

Type: **Doc-only taskbook (scope-lock for the worker slice).** It locks the
background worker that auto-drains the R2 publication outbox: the worker form,
the claim/locking model, the reason-based retry/backoff, the per-send
version-drift revalidate, the legal-state entry, batch size, stale-row reclaim,
idempotent re-entry, and the additive schema the implementation needs. It changes
no code. **Merging this taskbook does NOT authorize the worker implementation** —
that requires its own explicit opt-in.

Parents: `..._PLAN_20260527.md` (#663), `..._R1A_TASKBOOK_20260527.md` (#664),
the R1-B API (#665), `..._R2_ADAPTER_OUTBOX_TASKBOOK_20260528.md` (#666),
`..._R2_BUILD_TASKBOOK_20260528.md` (#667), the R2 implementation (#668),
`..._R2_HTTP_ROUTES_TASKBOOK_20260528.md` (#669), and the R2 HTTP routes
implementation (#670, `e6cb4e54`). Baseline `main = e6cb4e54`.

## 0. What this is (and is not)

The manual outbox API (#670) lets an admin advance one row at a time. This slice
adds the **automated** path: a background worker that polls the outbox and
processes due `pending` rows hands-off, with retry/backoff + stale-row reclaim.

It is the **scheduling/claim harness only**. The only adapter in R2 remains the
no-I/O `NullErpPublicationAdapter`, so a worker `send` reaches `sent` via the Null
adapter exactly as the manual route does — the **real ERP connector is a later,
separate slice**, as is **`/publication/export`**. The worker adds **no HTTP
route** (it is a CLI daemon / scheduler task) and does **not** change the
#668/#670 outbox service state machine — only additive claim/backoff columns +
a retry-reschedule helper.

## 1. Grounding (against `main = e6cb4e54`)

- **`JobService` (`services/job_service.py`)** — the proven queue pattern:
  - `poll_next_job` (`:84`): filters `status==PENDING AND scheduled_at<=utcnow()`,
    orders by `(priority, created_at)`, **`with_for_update(skip_locked=True)` on
    PostgreSQL** (plain query SQLite fallback), then claims by setting
    `PROCESSING` + `worker_id` + `started_at` + `attempt_count += 1`.
  - `fail_job(retry=True)` (`:186`): on a retryable failure with
    `attempt_count < max_attempts` → back to `PENDING` with
    `scheduled_at = now + JOB_RETRY_BACKOFF_SECONDS * attempt_count` (linear
    backoff) and cleared `worker_id`/`started_at`; else terminal `FAILED`.
  - `requeue_stale_jobs` (`:308`): `PROCESSING` rows with
    `started_at < now - JOB_STALE_TIMEOUT_SECONDS` → re-`PENDING` (+ backoff) or
    `FAILED` at max attempts.
- **`JobWorker` (`services/job_worker.py:22`)** — the standalone daemon:
  `run_once()` = `requeue_stale_jobs()` then `poll_next_job()` then dispatch to a
  registered handler; `start()`/`_run_loop()` is `while self._running` with a
  `poll_interval`. Launched via the `yuantus worker` CLI (`cli.py:50`) which
  registers a handler per `task_type`.
- **`SchedulerService` (`services/scheduler_service.py:127`)** — `run_once()`
  enqueues due periodic work INTO the job queue and splits `enqueued` vs
  `would_enqueue` (a built-in dry-run bucket).
- **Outbox service (#668/#670)** — `process(row, adapter, *, revalidate=None)`
  guards entry-state (`pending`/`dry_run_ready` only; else `PublicationReplayError`),
  re-validates via **`self._revalidate_allows_send(row, fresh)`** (the #670 gate:
  eligibility **and** version identity; drift → `skipped`/`not_eligible` +
  `revalidated_version_mismatch`, never sends a stale snapshot), folds
  `build`/`validate`/`send` exceptions to `failed`/`adapter_error`, and on a
  non-OK send sets `failed` with `error_kind` (`remote_error`/`adapter_error`).
  `replay` retries `failed` **only** for `remote_error`/`adapter_error`.
- **`ErpPublicationOutbox` model** has `attempt_count`/`max_attempts`/
  `dispatched_at` but **NO** `scheduled_at`/`next_attempt_at`, **no** `worker_id`,
  **no** `claimed_at`, and no `processing` lifecycle state — so a direct-poll
  worker needs additive columns (see §8).

## 2. Worker form (ratify)

**Recommendation: a dedicated `PublicationOutboxWorker` that polls the
`meta_erp_publication_outbox` table directly**, modeled on `JobWorker` +
`JobService.poll_next_job` (`run_once()` = reclaim-stale → claim a batch →
`process` each → reschedule retryable failures), launched via a
`yuantus publication-worker` CLI command, and **also** triggerable as a
`SchedulerService` periodic task `run_once` (with a `would_process` dry-run
bucket mirroring `SchedulerRunResult`).

*Alternative considered and rejected — wrapping each row in a `ConversionJob` and
reusing `JobWorker`:* it double-bookkeeps (ConversionJob status vs outbox state)
and the generic attempt-based `ConversionJob` retry **bypasses the outbox's
locked reason-based retry rule** (only `remote_error`/`adapter_error`). The
outbox was designed (build taskbook §3) to mirror the `JobService` shape and be
polled directly; a dedicated poller keeps one source of truth and honors the
reason-based rule.

## 3. Claim / locking (ratify — no concurrent double-process)

Claim due rows with **`SELECT ... FOR UPDATE SKIP LOCKED`** (PostgreSQL) / plain
ordered query (SQLite fallback), mirroring `poll_next_job`. On claim, set
`worker_id` + `claimed_at` (new columns, §8) so a worker crash mid-process leaves
a **reclaimable** marker.

The claim columns are **orthogonal worker-bookkeeping**, NOT a new lifecycle
state: the row stays `pending` while claimed (the locked §6 state set —
`pending/dry_run_ready/sent/failed/skipped` — is unchanged; reasons/worker-state
are never encoded into it). The poll predicate is
`state == pending AND next_attempt_at <= now AND (claimed_at IS NULL OR claimed_at < stale_cutoff)`.
A never-failed `pending` row has `next_attempt_at = now()` by default (§8), so it
is due immediately; `next_attempt_at` is pushed into the future **only** by a
retry reschedule (§4). (Confirm in impl: a brand-new `pending` row matches this
predicate — do not pin `next_attempt_at` nullable-without-default, which would
strand every never-failed row.)

## 4. Retry / backoff (ratify — reason-based)

Retry **only** `remote_error`/`adapter_error` (the locked #668 §6 rule); never
`not_eligible`/`validation_error`. After `process()` lands a retryable failure
with `attempt_count < max_attempts`, the worker **reschedules**: reset the row to
`pending` and set `next_attempt_at = now + backoff * attempt_count` (linear,
mirroring `fail_job`); at `max_attempts` the row stays `failed` (dead-letter, not
reclaimed). Recommend a small service helper (e.g. `reschedule_retry(row)`) so
the reschedule is unit-testable and the #668/#670 `process()` contract is
unchanged. This is **distinct from `replay()`**: `replay()` retries a failed row
**synchronously** (re-processes inline, no backoff); the worker wants **deferred**
retry — set `next_attempt_at` and let the next poll re-claim the row — so
`reschedule_retry` sets the schedule without re-processing in place. Recommend **dedicated `PUBLICATION_OUTBOX_*` settings**
(`POLL_INTERVAL`, `BATCH_SIZE`, `RETRY_BACKOFF_SECONDS`, `STALE_TIMEOUT_SECONDS`,
`MAX_ATTEMPTS` default) rather than reusing the CAD `JOB_*` knobs, so publication
tuning is independent — to be ratified.

Implementation acceptance item: the retry budget counts **every worker processing
attempt**, including adapter failures that happen before `adapter.send()`. In
the current #668 service, `process()` increments `attempt_count` immediately
before `adapter.send()`, so a send-returned `remote_error` or send-raised
`adapter_error` is already counted. But an `adapter_error` raised by
`adapter.build_payload()` / `adapter.validate_contract()` can reach
`failed/adapter_error` with the same `attempt_count` it had on entry. The worker
`reschedule_retry(row, before_attempt_count=...)` helper MUST detect that case
and increment `attempt_count` once before computing backoff / max-attempt
dead-letter. Otherwise pre-send adapter errors can retry forever with
`attempt_count == 0`.

## 5. Version drift on every send (ratify — reuse #670)

The worker MUST pass a `revalidate` callable to `process()` on every send, so the
auto path honors D-R2-1 exactly as the manual route does: it reuses
`build_publication_readiness` + the #670 **`_revalidate_allows_send`** gate
(eligibility **and** version-identity). A version that drifted between enqueue
and the worker's send → `skipped`/`not_eligible` + `revalidated_version_mismatch`;
the stale snapshot is never sent. If the backing item is gone, the worker treats
the row as un-sendable (skip + record), never crashes the loop.

## 6. Legal-state entry (ratify)

The worker claims **only `pending`** rows that are due (§3 predicate). It does
**not** auto-promote `dry_run_ready` → `sent` (a dry-run is a manual decision);
`sent`/`skipped` are terminal and filtered out; `failed` rows re-enter only via
the §4 reschedule (retryable) which resets them to `pending`. The worker always
calls `process()`, which independently guards entry-state — so an unexpected
state can never be force-sent.

## 7. Batch size / stale reclaim / idempotent re-entry (ratify)

- **Batch**: each `run_once` claims at most `BATCH_SIZE` due rows (bounded work
  per tick); log when the due backlog exceeds the batch (no silent truncation).
- **Stale reclaim**: rows with a `claimed_at` older than `STALE_TIMEOUT_SECONDS`
  (worker crashed mid-process) have their claim cleared and become re-claimable,
  mirroring `requeue_stale_jobs`. Because the row stays `pending` while claimed,
  reclaim = clear `worker_id`/`claimed_at` (no state change). Reclaim is
  **at-least-once**: if a worker is legitimately still processing past
  `STALE_TIMEOUT_SECONDS`, a second worker may reclaim and double-send — harmless
  under the Null adapter (R2 scope), so `STALE_TIMEOUT_SECONDS` MUST exceed the
  max expected processing time; the real-connector slice revisits long-held-lock
  vs an explicit claim-state.
- **Idempotent re-entry**: re-running the worker over an already-`sent` row is a
  no-op (it is filtered out, and `process()` guards `sent` as terminal); a
  duplicate claim is prevented by `FOR UPDATE SKIP LOCKED` + the claim columns.

## 8. Additive schema (the impl will add — pinned, NOT built here)

The worker IMPLEMENTATION adds to `meta_erp_publication_outbox` (additive
columns, **no new table** → migration-table-coverage contract unaffected; the
migration must be SQLite-clean for the live `alembic upgrade head` test):

| column | type | purpose |
|---|---|---|
| `worker_id` | `String`, nullable | claim owner (§3) |
| `claimed_at` | `DateTime(tz)`, nullable | claim time → stale reclaim (§7) |
| `next_attempt_at` | `DateTime(tz)`, **NOT NULL**, `server_default=now()` | backoff schedule (§4); poll gate (§3). Default-now so a never-failed `pending` row is **immediately due** (enqueue need not set it). |

These are orthogonal to the locked state machine (§3). The worker adds **no HTTP
route** → `len(app.routes)` stays **683** (no route-count pin change).

Migration acceptance item: the migration must be SQLite-clean in the repo's live
`alembic upgrade head` smoke. If direct `ADD COLUMN next_attempt_at NOT NULL
DEFAULT now()` is not accepted by SQLite, the implementation must use a safe
sequence (for example: add nullable, backfill existing rows to current
timestamp, then enforce not-null / server default with a batch migration) while
leaving the final model contract as `nullable=False` + due-immediately default.

## 9. Non-Goals

No real ERP connector (Null adapter only; `sent` via the worker is Null, not a
real ERP); no `/publication/export`; no new HTTP route; no change to the
#668/#670 outbox state machine / reason set / idempotency key beyond the additive
claim/backoff columns + the retry-reschedule helper; no scheduler framework
rewrite (reuse `SchedulerService`/`JobWorker` patterns).

## 10. Preconditions to enter the worker IMPLEMENTATION

1. §2 worker form (dedicated outbox poller daemon; scheduler-trigger companion)
   ratified;
2. §3 claim/locking (FOR UPDATE SKIP LOCKED + claim columns, orthogonal to state)
   ratified;
3. §4 reason-based retry/backoff + settings choice ratified;
4. §5 per-send version-drift revalidate (reuse #670 gate) ratified;
5. §6 legal-state entry (pending-only; no dry_run_ready auto-send) ratified;
6. §7 batch / stale reclaim / idempotent re-entry ratified;
7. §8 additive columns ratified.

A **separate explicit opt-in** then authorizes the implementation.

## 11. Reviewer Focus

1. §2 — dedicated outbox poller (not ConversionJob-wrapped)?
2. §3 — claim via FOR UPDATE SKIP LOCKED + claim columns, NOT a new lifecycle
   state?
3. §4 — retry only `remote_error`/`adapter_error`, linear backoff, dead-letter at
   max, and pre-send `adapter_error` still consumes one retry attempt?
4. §5 — every send revalidates via the #670 `_revalidate_allows_send` gate?
5. §6 — `pending`-only claim; `dry_run_ready` not auto-sent?
6. §7 — bounded batch + stale reclaim + idempotent re-entry?
7. §8/§9 — additive columns only, no route change, Null adapter only, connector +
   export stay out?

## 12. Status

Doc-only scope-lock. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it + its DEV/verification record (sorted under
`## Development & Verification`); doc-index / sorting / completeness checks pass;
`git diff --check` clean. Ratifying §2–§8 sets the worker implementation plan;
**a separate explicit opt-in authorizes the implementation.** The real ERP
connector and `/publication/export` remain later, separately-opted slices.
