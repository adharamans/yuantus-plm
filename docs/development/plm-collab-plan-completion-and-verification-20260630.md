# PLM x MetaSheet Plan Completion and Verification (2026-06-30)

Type: final development-plan completion and verification record for the
`plm-collab-integration-development-plan-and-todo-20260629.md` line after the
remaining buildable items were pursued.

This document does not reclassify owner, ops, or product gates as complete. It
records what was actually built, verified, and merged, and names the remaining
items that are no longer unowned development work.

## 1. Baseline

The live baseline for this closeout is Yuantus `origin/main` at `804b6170`
(`fix(plm-collab): harden write etags and csv exports (#922)`) and metasheet2
`origin/main` at `e3c2e21d5` (`test(plm): prove writeback retry key reuse
(#3392)`).

The plan/TODO baseline named:

- T1: MetaSheet2 workbench consumer write-back relay + edit UI.
- T2: Yuantus cross-line idempotency regression test.
- T3-T5: Phase 6 session / bridge activation, gated on an owner decision.
- T6: Phase 7 fast-follows.
- T7: lines 2/3/4 next slices.
- T8: commercialization hardening.
- T9: infra / ops enablement.

## 2. Delivered Buildable Items

| Item | Repo / PR | Status | Verification |
|---|---|---|---|
| T1 consumer write-back surface | metasheet2 #3383 | MERGED `02eb23f6` | Workbench relay + edit UI + caller-owned idempotency key; metasheet2 CI passed. |
| T2 cross-line idempotency regression | Yuantus #915 | MERGED `9125c098` | Same `Idempotency-Key` + same payload + different line returns `409`; contracts passed. |
| T6 If-Match / ETag hardening | Yuantus #917, completed by #922 | MERGED `209b2f7b`, then `804b6170` | `write_etag` added to read projection; write path now row-locks and re-checks `If-Match` inside the lock. Contracts passed. |
| T7 date-obsolete export/filter | Yuantus #918, hardened by #922 | MERGED `0f1fb328`, then `804b6170` | CSV / JSON export and ad-hoc filter landed; CSV formula injection neutralized by shared sanitizer. Contracts passed. |
| T7 lifecycle forensic summary/list/export | Yuantus #919/#920, hardened by #922 | MERGED `19df3581` / `d1b21d54`, then `804b6170` | Summary, cross-item list, and export landed; CSV formula injection neutralized by shared sanitizer. Contracts passed. |
| MetaSheet2 retry-key test quality | metasheet2 #3392 | MERGED `e3c2e21d5` | Strengthens `PlmBomReviewPanel` retry-key test so `randomUUID()` returns distinct values and retry must reuse the first key. Local targeted test: 8 passed; full metasheet2 CI passed. |

## 3. Current Non-Buildable Remainder

The following are not hidden implementation leftovers. They are explicit gates
that need owner, ops, or product input before code should begin.

| Area | Status | Gate |
|---|---|---|
| Phase 6 SSO / identity-session / bridge activation | Deferred by default | Open only if bridge activation or continuous in-iframe UX becomes the next product line. |
| Lifecycle-locked BOM edit via ECO revision route | Deferred product/governance slice | Current Draft/editable BOM write-back is live; locked parent still returns `409`. ECO revision route needs a governance decision. |
| Date-obsolete revert | Deferred governance slice | Export and filtering are live. Revert mutates acknowledged / worker-derived state and needs explicit approval semantics. |
| Commercial hardening | Designed, not built | Vendor-private issuance, key custody, admin UX, multi-kid rotation, and compatibility gates remain a commercial roadmap track. |
| Infra / ops enablement | Environment-blocked | Consumer-side `can-i-deploy` webhook, owned-HTTPS V1.2 rerun, deploy-environment gate, and alerting activation need external infra/secrets. |

## 4. Verification Details

### Yuantus #922

Local verification before merge:

- `pytest` targeted collaboration/write-back/date-obsolete/lifecycle tests:
  111 passed.
- Targeted route / contract subsets:
  26 passed.
- `py_compile` for touched Yuantus modules passed.
- `git diff --check` passed.

GitHub verification:

- `contracts` passed in 9m36s.
- `regression` passed in 3m33s.
- `plugin-tests` passed.
- `playwright-esign` passed.
- Pact provider verifier, broker verify / publish / can-i-deploy, and base-green
  checks passed inside the contracts gate.

### metasheet2 #3392

Local verification before PR:

- `pnpm --filter @metasheet/web exec vitest run tests/PlmBomReviewPanel.spec.ts`
  - 8 tests passed.
- `git diff --check` passed.

GitHub verification:

- `contracts (strict)` passed in 19s.
- `contracts (openapi)` passed in 25s.
- `contracts (dashboard)` passed in 28s.
- `e2e` passed in 1m0s.
- `pr-validate` passed in 5s.
- `test (18.x)` passed in 4m1s.
- `test (20.x)` passed in 9m24s.
- `coverage` passed in 24s.
- DingTalk, K3 WISE, and after-sales integration gates passed.
- Squash-merged as `e3c2e21d5`.

## 5. Outcome

The buildable remainder from the 2026-06-29 PLM x MetaSheet plan is complete to
the honest floor:

- user-reachable governed BOM write-back exists in MetaSheet2 workbench;
- provider idempotency, ETag / If-Match, and CSV export hardening are pinned;
- the retry-key false-green test has a dedicated PR and targeted proof;
- no read-only embed invariant was relaxed;
- no owner/ops/product gate was silently converted into code.

There is no known unowned, buildable development item left in this plan. The
remaining motion is choosing and unblocking one of the gated tracks in section 3.
