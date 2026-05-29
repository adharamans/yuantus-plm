# DEV & Verification: PLM→ERP Publication Contract — R2 HTTP Routes Taskbook

Date: 2026-05-28

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_HTTP_ROUTES_TASKBOOK_20260528.md`
— the scope-lock for the **thin** HTTP surface over the merged R2 outbox service
(#668). Doc-only: no code; merging it does **not** authorize the routes
implementation. Baseline `main = 392019ad` (after the R2 implementation #668).

## 1. What changed

- New routes-slice scope-lock taskbook (5 admin-gated manual routes; manual
  process-one; error→HTTP map; concurrency; route-count delta; test acceptance
  items; non-goals).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = 392019ad`)

The taskbook's error→HTTP mapping and acceptance items are grounded on the
**current** (post-#668, incl. the merge-time review fixes) service:

- `process()` entry-state guard raises `PublicationReplayError` for any state
  outside `{pending, dry_run_ready}` → routes map to **409** (the two #668 guards
  continue at the route level).
- `build_payload`/`validate_contract`/`send` exceptions are folded to
  `failed`/`adapter_error` and **returned, not raised** (`_fail_adapter_error`) →
  routes return the failed row (200), never 500.
- `PublicationConflictError` (enqueue changed-content vs `sent`) → **409**;
  `enqueue` versionless → returns `None` → route 200 with `persisted:false`.
- `revalidate` for `process`/`replay` reuses the extracted
  `build_publication_readiness` (#668) — R1-B's exact logic, not a copy (#667 §8).
- Auth mirrors R1-B (`require_admin_permission`); route-count pins live at
  `test_phase4_search_closeout_contracts.py:155` (`== 678`) and the `tier_b_3`
  cross-reference (`:240`).

## 3. Locked decisions (summary)

5 routes (`enqueue` / `dry-run` / `process` / `replay` / `status`); `process` is
manual process-one (no worker daemon); Null adapter only (no real connector);
admin auth = R1-B; revalidate reuses `build_publication_readiness`; error map
(Conflict/Replay → 409, not-found → 404, param → 422, versionless → 200,
adapter-exception → 200); concurrent enqueue → `IntegrityError` → rollback →
re-find → reuse-row; **route-count 678 → 683 with a full-tree residual scan
first**; both #668 guards retested at the route level. Non-goals: no worker
daemon, no real connector, no `/publication/export`.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only scope-lock. Ratifying §2–§9 of the taskbook sets the routes
implementation plan; the routes implementation (then the background worker, the
real ERP connector, and `/publication/export`) each need their own explicit
opt-in.
