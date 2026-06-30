# Phase 7 BOM Write-Back Audit Readout - Development & Verification (2026-06-30)

## Scope

Add one Yuantus-local, read-only PLM ops surface for the Phase 7 governed BOM
multi-table write-back domain audit:

- `GET /api/v1/bom/multitable/writeback-audit`
- superuser-only (`require_superuser`)
- tenant-scoped to the caller identity
- filters: `part_id`, `bom_line_id`, `user_id`, `created_after`, `created_before`
- pagination: `limit`, `offset`
- `Cache-Control: no-store`

This does not add any write semantics, does not alter the consumer pact, does
not relax the embed read-only invariant, and does not expose the audit rows to
ordinary item readers. The endpoint reads the existing
`meta_bom_writeback_audit` rows that Phase 7 writes atomically with the BOM
line mutation.

## Implementation

- `bom_multitable_router.py`
  - Adds the audit readout route under the existing BOM multi-table router.
  - Serializes the existing domain audit fields:
    `id`, `idempotency_key`, `tenant_id`, `org_id`, `user_id`, `part_id`,
    `bom_line_id`, `before`, `after`, `status`, `created_at`.
  - Defaults to the authenticated superuser's tenant, so a tenant superuser
    cannot query another tenant's write-back audit rows through this surface.

- `test_bom_multitable_writeback.py`
  - Covers the superuser gate, tenant scoping, newest-first ordering,
    filters, empty-result 200, invalid datetime 400, and route presence.

- Route-count lockstep
  - App route count moves `732 -> 733`.
  - Updated the authoritative route-count pin and the three companion
    route-count/string pins.
  - Updated BOM router local route-count pins from `3 -> 4`.

## Verification

Local verification:

- `python -m pytest` targeted subset — **98 passed, 1 warning**:
  - `src/yuantus/meta_engine/tests/test_bom_multitable_writeback.py`
  - `src/yuantus/meta_engine/tests/test_bom_multitable_embed_token.py`
  - `src/yuantus/meta_engine/tests/test_bom_multitable_projection.py`
  - `src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py`
  - `src/yuantus/meta_engine/tests/test_metrics_router_route_count_delta.py`
  - `src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py`
  - `src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py`
  - `src/yuantus/meta_engine/tests/test_ci_contracts_ci_yml_test_list_order.py`
  - `src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py`
  - `src/yuantus/meta_engine/tests/test_ci_change_scope_contracts.py`

The warning is the existing bootstrap deprecation warning for legacy relationship
models, unrelated to this slice.

## Boundaries

- No export route in this slice.
- No admin UI in this slice.
- No mutation / replay / reconcile action in this slice.
- No change to Phase 7 write-back guard order.
- No change to metasheet2 consumer behavior.
