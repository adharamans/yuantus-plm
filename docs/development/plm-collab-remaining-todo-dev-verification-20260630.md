# PLM x MetaSheet Remaining TODO - Development & Verification Closeout (2026-06-30)

Type: supplemental development and verification record for continuing the
`docs/development/plm-collab-integration-development-plan-and-todo-20260629.md`
remaining TODO after the T1/T2 closeout.

This document records the buildable Yuantus-side items completed after
`docs/development/plm-collab-integration-dev-verification-closeout-20260629.md`.
It does not reclassify owner-, ops-, or product-gated work as complete.

## 1. Baseline

The previous closeout already landed:

- **T1** - MetaSheet2 workbench consumer write-back surface, metasheet2 #3383.
- **T2** - Yuantus cross-line idempotency regression, Yuantus #915.

This follow-up continued the plan through the remaining low-risk, buildable
Yuantus provider/ops slices. The canonical checkouts were not used for the
work; each slice was built in an isolated worktree off `origin/main`.

## 2. Delivered

| Plan item | PR / squash | State | What changed |
|---|---|---|---|
| **T6 fast-follow - If-Match/412** | #917 / `209b2f7b` | MERGED | Added optional `If-Match` optimistic concurrency to governed BOM write-back. The PATCH response now carries `ETag`; stale `If-Match` returns `412` unless the same idempotency key already resolves to the cached successful write. |
| **T7 L3 - date-obsolete impacts export/ad-hoc filter** | #918 / `0f1fb328` | MERGED | Added read-only admin export for date-obsolete impacts (`csv`/`json`) plus `child_obsoleted` filtering. No acknowledge/revert/worker side effect was introduced. |
| **T7 L2 - lifecycle forensic summary** | #919 / `19df3581` | MERGED | Added superuser-only forensic summary aggregating outcomes, reason codes, failed item IDs, and failed actor IDs over the same forensic filter surface. |
| **T7 L2 - lifecycle forensic cross-item list/export** | #920 / `d1b21d54` | MERGED | Added superuser-only cross-item forensic drill-down and CSV/JSON export. Non-success rows remain forensic-only; item-scoped transition-history reads are unchanged. |

## 3. Verification

### #917 - write-back `If-Match`

Local targeted verification:

- `python -m pytest src/yuantus/meta_engine/tests/test_bom_multitable_writeback.py -q`
  - 27 tests passed.
- Pact provider verifier - 3 tests passed.
- Targeted collaboration/projection gate subset - 31 tests passed.
- `git diff --check` passed.

GitHub verification:

- `contracts` passed in 9m51s.
- `regression` passed in 3m14s.
- PR was `MERGEABLE/CLEAN` at merge.

### #918 - date-obsolete impacts export

Local targeted verification:

- `python -m pytest src/yuantus/meta_engine/tests/test_date_obsolete_wiring.py -q`
  - 25 tests passed.
- Route-count / route-pin contracts - 35 tests passed.
- Date-effectivity / worker / CI-list-order subset - 30 tests passed.
- `py_compile` and `git diff --check` passed.

GitHub verification:

- `contracts` passed in 9m47s.
- `regression` passed in 3m25s.
- PR was `MERGEABLE/CLEAN` at merge.

### #919 - lifecycle forensic summary

Local targeted verification:

- `python -m pytest src/yuantus/meta_engine/tests/test_lifecycle_transition_history_router.py -q`
  - 40 tests passed.
- Route-count / route-pin contracts - 35 tests passed.
- `py_compile` and `git diff --check` passed.

GitHub verification:

- `contracts` passed in 10m15s.
- `regression` passed in 3m19s.
- PR was `MERGEABLE/CLEAN` at merge.

### #920 - lifecycle forensic list/export

Local targeted verification:

- `python -m pytest src/yuantus/meta_engine/tests/test_lifecycle_transition_history_router.py -q`
  - 46 tests passed.
- Route-count / route-pin contracts - 35 tests passed.
- `py_compile` and `git diff --check` passed.

GitHub verification:

- `contracts` passed in 10m34s.
- `regression` passed in 3m25s.
- PR was `MERGEABLE/CLEAN` at merge.

## 4. Final route-count state

The route-count pin moved in two explicit steps:

- #918: `728 -> 729` for the date-obsolete export route.
- #919/#920: `729 -> 730 -> 732` for the lifecycle forensic summary and
  list/export routes.

Each bump was verified by the existing route-count / route-pin contract tests.

## 5. Remaining items after this closeout

The remaining items are not hidden development left undone by this session.
They are decision-, owner-, product-, or environment-gated.

| Plan area | Status | Gate |
|---|---|---|
| **Phase 6 SSO/session (T3-T5)** | Deferred by default | Requires owner decision that bridge activation / continuous in-iframe UX is the next product line. Phase 7 write-back no longer forces it. |
| **Phase 7 deeper ECO route** | Deferred | `If-Match` is done; lifecycle-locked parent still returns `409`. A full ECO revision route is a product/governance slice, not a safe default patch. |
| **Date-obsolete revert** | Deferred | Export/ad-hoc filtering is done. Revert would mutate acknowledged/worker-derived state and needs an explicit governance decision. |
| **Commercial hardening (T8)** | Designed, not built | Vendor issuance, admin UX, key rotation, and compatibility gates remain owner-prioritized commercial work. |
| **Infra/ops enablement (T9)** | Blocked by environment | Consumer-side can-i-deploy webhook, owned-HTTPS rerun, and deployment-environment gates need external infra. |
| **metasheet2 edit-UI key-reuse test quality** | Non-blocking P3 follow-up | The implementation is correct, but the current retry-key test stubs `randomUUID()` to a constant. A metasheet2 owner-gated test-only PR should make the stub return different values and prove retry reuses the first key. |

## 6. Outcome

The buildable Yuantus-side remainder from the integration plan is complete:

- Phase 7 write-back got its `If-Match`/`ETag` concurrency guard.
- Date-obsolete impacts gained read-only export and the requested ad-hoc filter.
- Lifecycle all-attempts history gained both aggregate summary and cross-item
  drill-down/export.

No read/write governance invariant was relaxed. The PLM embed iframe remains
read-only; non-success lifecycle rows remain forensic-only; write-back remains
governed by entitlement, permission, lifecycle lock, idempotency, and audit.

## References

- Plan/TODO baseline:
  `docs/development/plm-collab-integration-development-plan-and-todo-20260629.md`
- Prior T1/T2 closeout:
  `docs/development/plm-collab-integration-dev-verification-closeout-20260629.md`
- Phase 7 provider verification:
  `docs/development/plm-collaboration-phase7-writeback-provider-dev-verification-20260629.md`
- Lines 2/3/4 taskbook:
  `docs/development/backlog-lines-2-3-4-scoping-taskbook-20260627.md`
