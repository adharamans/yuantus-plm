# Claude Taskbook: PLM→ERP Publication Contract — R2 Adapter / Outbox (Scope-Lock)

Date: 2026-05-28

Type: **Doc-only taskbook (design / scope-lock).** It locks the R2 adapter +
outbox boundaries, the dry-run/replay semantics, the idempotency key, the
outbox state machine (separate from error classification), and the publication
snapshot. It changes no code. **Merging this taskbook does NOT authorize the R2
implementation** — that requires its own explicit opt-in.

Parents: `DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_PLAN_20260527.md` (#663),
`..._R1A_TASKBOOK_20260527.md` (#664), and the merged R1-B
`publication-readiness` API (#665, `649576b6`). Baseline `main = 649576b6`.

## 1. Scope

R2 adds the **outbound publication seam**: an `adapter` interface (to an external
ERP) plus an `outbox` (durable enqueue + dry-run/replay), consuming the R1-B
publication-readiness verdict. It is **not** an ERP; it binds to **no** real ERP
runtime and **no** Odoo. R2 defines the adapter **interface** + the outbox; the
**real ERP connector** is a later, separate taskbook.

## 2. Boundaries (locked)

1. **Adapter interface only** — a narrow interface an external-ERP adapter
   implements (`build_payload`, `validate_contract`, `send`/`dry_run`). No real
   connector in R2; no Odoo runtime dependency; no GPL/AGPL reuse; no external
   write from R2 itself.
2. **Outbox is durable** — enqueue → process; survives restarts; supports
   dry-run and replay. Model it on the existing `document_sync` precedent
   (`SyncJob` + `SyncRecord`), not a new ad-hoc table shape, where reasonable.
3. **Dry-run / replay** are first-class (see §3, §4).
4. **Idempotency key** is version-scoped (see §5).
5. **State machine ≠ error classification** (see §6).
6. **Outbox stores a publication snapshot** at enqueue (see §7).
7. **Gated on R1-B eligibility** — only `eligible` items are publishable; an
   ineligible item is `skipped` with its blocking reasons (see §7/§8).

## 3. Dry-run — NO external side effects (locked)

Dry-run MAY: build the payload, run the adapter `validate_contract`, write the
**local** outbox row + audit, and surface the result. Dry-run MUST NOT: call any
real ERP endpoint or produce any external side effect. A dry-run leaves the
outbox row in `dry_run_ready` (§6), never `sent`.

## 4. Replay semantics (locked) + the eligibility-revalidation decision

Replay re-processes an existing outbox row (e.g. after a remote failure). Because
the row carries a **snapshot** (§7), replay does not silently re-derive
publication semantics.

- **Decision to ratify (D-R2-1)**: on replay, does R2 **re-validate eligibility
  against current PLM state**, or **replay the snapshot as-enqueued**?
  - *Replay-snapshot* (deterministic, but may publish stale eligibility);
  - *Re-validate* (always current, but the verdict can change between enqueue and
    replay → may flip to `skipped`).
  The R2 build taskbook must pick one explicitly; recommend **re-validate for
  `sent` transitions, snapshot for dry-run/audit** unless the team decides
  otherwise — but this is the call to make, not assume.

## 5. Idempotency key (locked — version-scoped)

The idempotency key MUST include the version dimension, not just `item_id`:

```
idempotency_key = (item_id, version_id /* or generation */, target_system, publication_kind)
```

`item_id` alone would let different versions of the same item overwrite each
other in the outbox / at the target. `target_system` distinguishes ERP targets;
`publication_kind` distinguishes payload kinds (e.g. readiness vs a future
package export).

## 6. State machine ≠ error classification (locked)

Follow the existing `document_sync` precedent, which **already separates** these:
`SyncJobState` (pending/running/completed/failed/cancelled) vs
`SyncRecordOutcome` (synced/skipped/conflict/error) (`document_sync/models.py:44,58`).

- **States** (the row's lifecycle): `pending`, `dry_run_ready`, `sent`,
  `failed`, `skipped`.
- **Reasons** (a separate field, why a row is in a non-happy state):
  `not_eligible`, `adapter_error`, `remote_error`, `validation_error`.

Do **not** encode reasons into state names. Keeping them orthogonal keeps
retry/skip logic and stats clean (retry on `remote_error`/`adapter_error`; never
retry `not_eligible`/`validation_error`).

## 7. Publication snapshot (locked)

On enqueue, the outbox row stores a **snapshot** so replay/audit does not drift:

- the R1-B `eligible` verdict + `blocking_reasons` at enqueue time;
- `ruleset_id` + `limits` used;
- the `item{}` / `version{}` identity (incl. `version_id`/`generation`) and a
  `file_refs[]` **summary** (ids/roles — not the file bytes);
- the readiness `summary` (ok/error_count) at enqueue.

An **ineligible** item enqueued (or re-checked) resolves to state `skipped`,
reason `not_eligible`, carrying its `blocking_reasons` — it is never `sent`.

## 8. Link to R1-B

R2 consumes the R1-B publication-readiness verdict (it does **not** re-implement
eligibility; it reads `eligible` + `blocking_reasons`). The snapshot (§7) is that
verdict captured at enqueue; D-R2-1 (§4) decides whether `sent` re-checks it.

## 9. Non-Goals

No real ERP connector (interface only; its own later taskbook); no Odoo runtime
dependency; no GPL/AGPL reuse; no external write from R2; no purchase/sale
transaction; R2 does not re-derive readiness/eligibility. **`/publication/export`
is explicitly OUT of R2** — a separate future slice (annotated only).

## 10. Preconditions to enter the R2 IMPLEMENTATION taskbook

1. §2 adapter-interface shape ratified (methods, no real connector);
2. §4 D-R2-1 replay eligibility-revalidation decision made;
3. §5 idempotency key ratified (version-scoped);
4. §6 state/reason split ratified (modeled on `document_sync`);
5. §7 snapshot fields ratified;
6. the persistence choice ratified (reuse/extend `document_sync` `SyncJob`/`SyncRecord`
   vs a dedicated `plm_erp_outbox` table) — a grounded decision, not assumed.

## 11. Reviewer Focus

1. Confirm §1/§2: R2 is adapter-interface + outbox only; no real ERP/Odoo/GPL,
   no external write.
2. Confirm §3 dry-run has no external side effects.
3. Ratify §4 D-R2-1 (replay re-validate vs snapshot).
4. Confirm §5 idempotency key is version-scoped.
5. Confirm §6 state ≠ reason (aligned to `document_sync` SyncJobState vs
   SyncRecordOutcome).
6. Confirm §7 snapshot prevents replay drift; ineligible → `skipped/not_eligible`.
7. Confirm §9: `/publication/export` excluded; real connector deferred.

## 12. Status

Doc-only scope-lock. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / sorting checks pass;
`git diff --check` clean. Ratifying §3–§7 + D-R2-1 sets the R2 build plan; **a
separate explicit opt-in authorizes the implementation.** The real ERP connector
and `/publication/export` remain later, separately-opted slices.
