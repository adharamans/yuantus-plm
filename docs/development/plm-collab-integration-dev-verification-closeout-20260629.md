# PLM x MetaSheet Integration — Development & Verification Closeout (2026-06-29)

Type: dev & verification record for the 2026-06-29 integration plan/TODO execution.
This document closes the buildable items in
`docs/development/plm-collab-integration-development-plan-and-todo-20260629.md` and
records the remaining items as explicit owner/infra/future gates, not hidden work.

## 1. Scope

The plan identified two immediately buildable items:

- **T1** — complete the MetaSheet2 workbench consumer surface for governed BOM
  write-back.
- **T2** — pin the Yuantus cross-line idempotency regression test.

The rest of the plan was already gated by owner decisions, infra prerequisites, or
future product priority (Phase 6, bridge activation, commercial hardening, Phase 7
fast-follows, and line-next slices). Those gates remain real and are listed in
section 5.

## 2. Delivered

| Item | Repo / PR | State | What changed |
|---|---|---|---|
| Plan/TODO baseline | Yuantus #913 | **MERGED** `61d95f22` | Reviewable development plan + TODO landed and indexed. It established T1/T2 as the immediate buildable items and kept Phase 6/ops/commercial tracks behind their gates. |
| **T1 consumer write-back surface** | metasheet2 #3383 | **MERGED** `02eb23f6` | Workbench relay route + edit UI shipped. `PLMAdapter.updateBomMultitableLine()` now accepts a caller-owned `idempotencyKey`; the workbench generates one key per logical submit and reuses it on retry; only the workbench surface is editable. The embedded read-only iframe remains read-only. |
| **T2 idempotency regression** | Yuantus #915 | **MERGED** `9125c098` | Adds the missing regression: same `Idempotency-Key` + same payload + different `bom_line_id` returns `409`, never the first line's cached result. |

## 3. T1 implementation details

### metasheet2 backend relay

- Adds a `PATCH /api/plm-workbench/data-sources/:id/bom-multitable/:partId/lines/:bomLineId`
  relay under the workbench API.
- Requires the caller-provided `Idempotency-Key`; missing keys return `400`.
- Gates on `bom_multitable_writeback` capability/entitlement.
- Whitelists the editable BOM cells: `quantity`, `uom`, `find_num`, and `refdes`.
- Preserves provider-visible failures as actionable statuses (`400`, `403`, `404`,
  `409`, `422`) instead of flattening them into an opaque client error.

### metasheet2 adapter + UI

- `PLMAdapter.updateBomMultitableLine()` accepts `options.idempotencyKey`.
  The legacy random UUID fallback remains for direct callers, but the workbench
  passes its own key so a user retry is deduped by the provider.
- `PlmBomReviewTable.vue` gains an editable mode for the four write-back cells.
- `PlmBomReviewPanel.vue` owns one retry key per logical line submit, reuses it on
  failure/retry, updates the displayed row on success, and surfaces provider errors
  as user-visible edit states.
- The embedded PLM review iframe is unchanged and remains read-only by design.

## 4. Verification

### Local verification before PRs

metasheet2 #3383:

- `pnpm --filter @metasheet/core-backend type-check`
- `pnpm --filter @metasheet/web type-check`
- `pnpm --filter @metasheet/core-backend exec vitest run tests/unit/plm-adapter-bom-multitable.test.ts tests/unit/plm-workbench-bom-multitable-routes.test.ts`
  — 32 tests passed.
- `pnpm --filter @metasheet/web exec vitest run tests/plm-bom-review-service.spec.ts tests/PlmBomReviewPanel.spec.ts`
  — 18 tests passed.
- `pnpm --filter @metasheet/web lint`
- `git diff --check`

Yuantus #915:

- `python -m pytest src/yuantus/meta_engine/tests/test_bom_multitable_writeback.py -q`
  — 24 tests passed.
- `git diff --check`

Closeout doc:

- Delivery-doc index contracts passed locally.
- `git diff --check`

### GitHub verification

metasheet2 #3383:

- `test (18.x)` passed.
- `test (20.x)` passed.
- `coverage` passed.
- `yuantus-pact-consumer` passed.
- `contracts (strict)`, `contracts (openapi)`, and `contracts (dashboard)` passed.
- E2E, migration replay, SQL Server, K3, DingTalk, telemetry, and after-sales
  gates passed.

Yuantus #915:

- `contracts` passed in 9m03s.
- `detect_changes` passed; non-impacted jobs correctly skipped.

## 5. Remaining items after this closeout

These are not omissions from this build. They are the gates named by the plan and
remain parked until their prerequisite is explicitly cleared.

| Plan item | Status after this closeout | Gate |
|---|---|---|
| T3 Phase 6 SSO fork decisions | Deferred by default | Owner decision: bridge activation / continuous in-iframe UX must become a concrete next line before the session layer opens. |
| T4 Phase 6 session build | Not started | Gated on T3 = yes. |
| T5 `metasheet_bridge` activation | Not started | Gated on T4. Current bridge route remains a flag-gated health stub. |
| T6 Phase 7 fast-follows | Deferred v1 improvements | `If-Match`/412, deeper locked-parent ECO route, and related write-hardening remain future opt-in slices. |
| T7 lines 2/3/4 next slices | Owner opt-in | First cuts are live; export/aggregate/search/revert/ops views remain product-prioritized follow-ups. |
| T8 commercialization hardening | Designed, not built | Vendor issuance, admin UX, key rotation, and compatibility gates remain a larger commercial track. |
| T9 infra / ops enablement | Blocked by environment | Consumer-side `can-i-deploy` webhook, owned-HTTPS rerun, and deploy-environment gates need external infra. |

## 6. Honest boundary

The buildable code in the 2026-06-29 plan is complete and merged: T1 on metasheet2
and T2 on Yuantus. Verification covers type-checks, unit/component route tests,
lint, contract gates, and the Yuantus provider test suite. This closeout does **not**
claim a fresh deployed operator run of the new workbench edit UI; that belongs with
the next staging/ops evidence pass if the owner wants runtime proof beyond CI and
local targeted tests.

## References

- Plan/TODO baseline: `docs/development/plm-collab-integration-development-plan-and-todo-20260629.md`
- Phase 7 provider write-back verification:
  `docs/development/plm-collaboration-phase7-writeback-provider-dev-verification-20260629.md`
- Phase 6 deferred-by-default cleanup:
  `docs/development/plm-collab-session-dev-verification-closeout-20260627.md`
- Phase 7 day-2 design:
  `docs/development/plm-collaboration-phase7-writeback-day2-design-resolution-20260629.md`
