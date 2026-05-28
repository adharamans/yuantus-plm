# DEV & Verification: PLM→ERP Publication Contract — R2 Adapter/Outbox Taskbook

Date: 2026-05-28

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_ADAPTER_OUTBOX_TASKBOOK_20260528.md`
— the scope-lock for the G2 R2 adapter + outbox seam. Doc-only: no code; it locks
boundaries the R2 build taskbook/implementation will follow. Baseline `main =
649576b6` (after R1-B #665).

## 1. What changed

- New scope-lock taskbook (boundaries; dry-run/replay; idempotency key; state vs
  reason split; publication snapshot; non-goals).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries.

## 2. Grounding (against `main 649576b6`)

- **State ≠ reason split** is grounded on the existing `document_sync` precedent,
  which already separates `SyncJobState` (pending/running/completed/failed/cancelled)
  from `SyncRecordOutcome` (synced/skipped/conflict/error) — `document_sync/models.py:44,58`.
  The taskbook adopts the same orthogonality (states vs reasons) and proposes
  modelling the outbox on `SyncJob`/`SyncRecord` (a grounded persistence decision,
  not assumed).
- **Eligibility consumption** is grounded on the merged R1-B
  `publication-readiness` API (#665): R2 reads `eligible` + `blocking_reasons`,
  does not re-derive them.

## 3. Locked boundaries (summary)

Adapter interface only (no real connector / Odoo runtime / GPL-AGPL / external
write); durable outbox; dry-run = no external side effects; **replay
eligibility-revalidation is an explicit decision (D-R2-1)**; idempotency key is
version-scoped (`item_id + version_id/generation + target_system +
publication_kind`); state machine (`pending/dry_run_ready/sent/failed/skipped`)
separate from reason (`not_eligible/adapter_error/remote_error/validation_error`);
outbox stores a publication snapshot at enqueue; ineligible →
`skipped/not_eligible`. `/publication/export` and the real ERP connector are
explicitly OUT (later slices).

## 4. Verification (this doc-only PR)

- doc-contract pytests (delivery-doc-index references + sorting, DEV/verification
  index completeness + sorting, doc-index sorting) — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only scope-lock. Ratifying §3–§7 + D-R2-1 of the taskbook sets the R2 build
plan; the R2 implementation (and, later, the real ERP connector and
`/publication/export`) each need their own explicit opt-in.
