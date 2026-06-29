# Dev & Verification — Phase 7 governed BOM multi-table write-back (provider endpoint)

> Date 2026-06-29 · branch `codex/plm-phase7-writeback-provider-governed-20260629` (impl by the codex session) + CI wiring / this doc added on top. Implements the RATIFIED design #901 (`plm-collaboration-phase7-writeback-day2-design-resolution-20260629.md`).

## What
`PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` → `200 {ok, bom_line_id}` — the synchronous, lifecycle-guarded, idempotent, audited write-back of a single governed BOM multi-table line's editable cells (quantity/uom/find_num/refdes). The Draft/editable-state fast path (NOT auto-ECO; revising a Released/locked BOM stays the deferred ECO route). Adds **1 route → app route-count 727 → 728**.

## Implementation (codex, design #901)
- **Guard order (router, exact #901 §1):** 401 unauthenticated → 403 NOT entitled on the **distinct WRITE key** `plm.bom_multitable_writeback` (first — no existence-leak on a write surface) → 403 NOT `check_permission("Part BOM", AMLAction.update)` → 400 malformed/empty whitelist OR missing `Idempotency-Key` (fail-closed, before any lookup) → 404 part-missing / line∉part → 409 parent lifecycle-locked (`is_item_locked`, the `add_bom_child` precedent) → apply.
- **Idempotency + audit (one row, `meta_bom_writeback_audit`):** the MES-inbox savepoint pattern — `begin_nested` insert+flush; `IntegrityError` → fetch existing → **same intent → cached `{ok,bom_line_id}` (no re-apply); different payload → 409**. The audit `before/after` (touched cells) is committed **atomically** with the property mutation (audit-insert failure rolls back the mutation; `before` snapshotted pre-reassignment). Dual migration: `migrations/bom_writeback_audit_001` + `migrations_tenant/t2_bom_writeback_audit`.
- **Write entitlement:** `FEATURE_APP_NAMES["bom_multitable_writeback"]` + `WRITE_FEATURE_KEY` (distinct from the read projection's `bom_multitable`).
- **Last-write-wins v1** (`If-Match`/412 deferred), bounded by the single-use guard + lifecycle lock.

## Verification
- Local (`YUANTUS_PYTEST_DB=1`): **23 passed** = the §7 acceptance suite `test_bom_multitable_writeback.py` (401; read-key-on-write 403; no-existence-leak; permission 403; missing-Idempotency-Key 400; empty-whitelist 400; part-missing 404; line∉part 404; **lifecycle-locked 409 with a real `LifecycleState(version_lock=True)`**; **Draft parent 200**; **replay same-key→cached, no re-apply**; **same-key different-payload→409**; audit before/after captured; **audit-insert-failure rolls back the mutation**; service-level + router-3-routes) + the route-count pin at **728**. All four design-lock pins (phase4/metrics/breakage/tier_b_3) bumped 727→728 + the per-router `bom_multitable_router.routes == 3` assertion.
- **Anti-false-green CI wiring (this change):** `test_bom_multitable_writeback.py` added to the `ci.yml` contracts list (sorted; list-order pin green); the write-back service/model/test paths added to the `detect_changes` entitlement case → `run_contracts=true` (empirically verified). Without it codex's 23-test suite would never run in CI.
- **Pact:** the provider-side **checked-in** `contracts/pacts/metasheet2-yuantus-plm.json` carries a governed PATCH interaction (fresh W1/W3 fixtures) for the local provider-verifier; the **live broker** consumer pact is untouched (the #3337 depublish stands — no broker re-break). Re-adding the interaction to the broker is the metasheet2 #3332 consumer follow-up (providerState text → `plm.bom_multitable_writeback` + send `Idempotency-Key`), **after** this provider lands.

## Files
- Impl (codex): `meta_bom_writeback_audit` model + dual migration + bootstrap; `bom_multitable_writeback_service.py`; `bom_multitable_router.py` PATCH route; `entitlement_service.py` reg; route-count pins ×4 + per-router assertion; `_seed_pact_fixtures` write-license + provider-side pact.
- Verification layer (this change): `.github/workflows/ci.yml` (contracts list + detect_changes case); `docs/DELIVERY_DOC_INDEX.md` (this doc).
