# Dev & Verification - Phase-7 BOM write-back capability manifest

Date: 2026-06-29

## Context

Phase-7 governed BOM multi-table write-back is now implemented on the provider side:
`PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` is protected by the
distinct `bom_multitable_writeback` entitlement, whose production app name is
`plm.bom_multitable_writeback`.

The integration capability manifest still only advertised the read projection
feature, `bom_multitable`. That made the write endpoint callable when a tenant
held the write SKU, but invisible to a consumer that relies on
`GET /api/v1/integrations/capabilities` to decide which PLM affordances to show.

## Change

This slice adds `bom_multitable_writeback` to the advisory integration manifest as
an independent feature:

- `api_version: "v1"`
- `scenarios: ["bom_review"]`
- `actions: ["line_patch"]`
- `action_status: "governed"`

The existing read feature remains unchanged: `bom_multitable` continues to
advertise `scenarios: ["bom_review"]` and no actions. The manifest is still
advisory only; the write endpoint remains the actual authorization boundary via
`EntitlementService.is_entitled("bom_multitable_writeback")` plus PLM permission
and lifecycle guards.

## Verification

Focused local verification:

- `test_integration_capabilities.py` proves:
  - unlicensed tenants see the write-back feature as supported but not entitled;
  - `plm.bom_multitable` entitles only the read feature, not write-back;
  - `plm.bom_multitable_writeback` entitles only the write-back feature, not read;
  - the write-back descriptor emits `actions: ["line_patch"]` and
    `action_status: "governed"`;
  - descriptor keys stay within `FEATURE_APP_NAMES`, so `is_entitled` cannot see
    an unknown feature key.

CI wiring is already in place for this surface: `integration_capabilities_service.py`
and `test_integration_capabilities.py` are in the entitlement `detect_changes`
case, and `test_integration_capabilities.py` is in the contracts pytest list.

## Files

- `src/yuantus/meta_engine/services/integration_capabilities_service.py`
- `src/yuantus/meta_engine/tests/test_integration_capabilities.py`
- `docs/DEV_AND_VERIFICATION_PHASE7_BOM_WRITEBACK_CAPABILITY_MANIFEST_20260629.md`
- `docs/DELIVERY_DOC_INDEX.md`
