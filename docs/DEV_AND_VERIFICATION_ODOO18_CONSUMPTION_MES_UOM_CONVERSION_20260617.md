# Dev & Verification: MES uom **conversion** (Consumption R2.4)

Date: 2026-06-17
Status: **IMPLEMENTED** — pending gate review + merge
Taskbook: `DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_UOM_CONVERSION_TASKBOOK_20260617.md`.
Follows R2.1 (#779) which *rejected* a declared-uom mismatch.

## 1. Summary

A declared `event.uom` that differs from `plan.uom` is now **converted** (within a dimension)
instead of rejected. Same dimension (e.g. `g` → `kg`) → convert the quantity into the plan's
unit and store that; genuinely **unconvertible** (different dimension or unknown unit) → still
`422` (`consumption_mes_uom_unconvertible`). Variance stays unit-consistent.

## 2. Design (as built, per taskbook)

- **`services/consumption_uom_conversion.py`** (new): versioned (`CONVERSION_TABLE_VERSION`)
  in-code table — dimensions mass (base G) / length (base MM) / volume (base ML) / count
  (base EA), each unit → factor-to-base. `convert_quantity(qty, from, to) -> (converted,
  factor)`; identity is a no-op; cross-dimension / unknown unit → `UnconvertibleUnitsError`;
  result rounded to **6 dp** (so `1000 * 0.001` can't make equivalents compare unequal).
- **Route** (`web/parallel_tasks_consumption_router.py`): the R2.1 uom block now, on a declared
  mismatch, tries `convert_quantity(event.actual_quantity, event.uom, plan.uom)`; on success it
  `dataclasses.replace`s the mapped `inputs` with the **converted** quantity + a conversion
  **audit envelope** in `properties._ingestion` (`original_uom`, `original_quantity`,
  `converted_to_uom`, `conversion_factor`, `conversion_table_version`); on
  `UnconvertibleUnitsError` → `422`. Omitted / identical uom → pass through.
- **Idempotency interaction (load-bearing)**: conversion happens **before** `ingest`, so the
  stored quantity and the R2 conflict-compare both see the converted value; the idempotency
  **key** never included qty/uom, so a same-`mes_event_id` delivery in an *equivalent* unit
  (e.g. `1 KG` then `1000 G`, plan `KG`) converts to the same quantity → **DUPLICATE** (not a
  conflict); a converted-to-different-quantity → **CONFLICT**.

## 3. Verification (71 pass)

- pure (`test_consumption_mes_uom_conversion.py`): identity no-op; `g↔kg`, `kg→lb`, `m→mm`,
  `in→ft`, `dozen→ea`, `l→ml`; factor returned; 6-dp rounding (`oz→g`); unconvertible
  (mass↔length, volume↔count, unknown unit) raises.
- route (`_runtime.py`): cross-dimension `kg` vs `EA` plan → `422 unconvertible` (nothing
  written); `1000 g` vs `KG` plan → `200 CREATED`, stored `1.0`, envelope records the original;
  equivalent-unit replay (`1 KG` then `1000 G`) → `DUPLICATE`, one row; equivalent-key
  different-converted-qty → `409`; matching/omitted uom unchanged.
- R2.1's old `consumption_mes_uom_mismatch` test updated to the new convert/unconvertible
  behavior. No route added (713), no migration/pin/key change; manual `/actuals`, R2.2 auth,
  R2.3 source_type unchanged. New test dual-registered (ci.yml + conftest).

## 4. Boundary

Conversion of declared units within a dimension only. No tenant-configurable table, no reversal
semantics, no new route/worker — separate, later, opted slices.
