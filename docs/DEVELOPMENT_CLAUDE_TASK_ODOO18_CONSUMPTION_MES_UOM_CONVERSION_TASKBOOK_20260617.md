# Claude Taskbook: MES uom **conversion** (Consumption R2.4)

Date: 2026-06-17
Status: **DECISION — doc-only, pending gate review + a separate build opt-in**
Roadmap: `DEVELOPMENT_ROADMAP_AND_TODO_20260617.md` §2 R2.4. Follows R2.1 uom reconciliation (#779).

## 0. Why / scope

R2.1 **rejects** (`422`) a declared `event.uom` that differs from `plan.uom`. R2.4 instead
**converts** `event.actual_quantity` from `event.uom` to `plan.uom` when the two are the same
**dimension** (e.g. `g` → `kg`), and keeps the `422` only when they are genuinely
**unconvertible** (different dimension or an unknown unit). Variance still sums quantities in the
plan's unit, so after conversion the numbers are unit-consistent.

Narrow: route-level only; no new route/migration/pin; no change to the idempotency key, manual
`/actuals`, or `variance` semantics. `return`/reversal stays out (R2.3 carve-out).

## 1. Baseline (file:line)

- R2.1 reconciliation in `web/parallel_tasks_consumption_router.py:399-415`: `if event.uom is
  not None and event.uom != plan.uom (normalized) -> 422 consumption_mes_uom_mismatch`.
- `ConsumptionRecord` has **no uom column** (`models/parallel_tasks.py:156-166`) — the stored
  `actual_quantity` is implicitly in the plan's unit. So conversion = convert the number, store
  it in `plan.uom`; the original is kept only in `properties` for audit.
- `plan.uom` stored upper-cased, default `"EA"` (`parallel_tasks_service.py:2693`). No validated
  unit vocabulary today.
- No existing unit-conversion utility in the repo (grep clean).
- The R2 conflict compare (`parallel_tasks_service.py:2973-2977`) compares
  `existing.actual_quantity` vs `inputs.actual_quantity` — so the **converted** value must be in
  `inputs` *before* `ingest_mes_consumption`.

## 2. Locked decisions

- **D1 — Versioned in-code conversion table.** A new module
  `services/consumption_uom_conversion.py` with a `CONVERSION_TABLE_VERSION` constant and a
  dimension → {unit → factor-to-base} map. v1 dimensions + base + factors:
  - **mass** (base `G`): `G`=1, `KG`=1000, `MG`=0.001, `T`=1_000_000, `LB`=453.59237, `OZ`=28.349523125
  - **length** (base `MM`): `MM`=1, `CM`=10, `M`=1000, `KM`=1_000_000, `IN`=25.4, `FT`=304.8
  - **volume** (base `ML`): `ML`=1, `L`=1000, `M3`=1_000_000
  - **count** (base `EA`): `EA`=1, `PCS`=1, `PC`=1, `DOZEN`=12
  Units normalized `strip().upper()`. Two units are convertible iff they share a dimension.

- **D2 — Convert, else 422.** When `event.uom` is declared (non-None) and `!= plan.uom`
  (normalized): same dimension → `converted = qty * factor(event.uom) / factor(plan.uom)`;
  different dimension **or** either unit unknown → **`422 consumption_mes_uom_unconvertible`**
  (distinct from R2.1's `…_mismatch`), no write. `event.uom is None` or `== plan.uom` → no
  conversion (pass through). The plan-not-found case still 404s in `ingest`.

- **D3 — Convert before map→ingest (load-bearing).** The route converts and builds a NEW event
  (or passes the converted quantity into the mapper) **before** `ingest_mes_consumption`, so the
  stored quantity and the conflict-compare both see the converted value. The idempotency **key**
  is unchanged (it never included uom/quantity), so a replay is still idempotent; a same-`mes_event_id`
  delivery in a *different but equivalent* unit (e.g. `1 KG` then `1000 G`, plan `KG`) converts to
  the **same** quantity → `DUPLICATE` (correct); one converting to a different quantity → `CONFLICT`.

- **D4 — Rounding.** Round the converted quantity to **6 decimals** (`round(x, 6)`) so float
  noise (`1000 * 0.001`) can't make equivalent values compare unequal in the conflict check.
  Lock 6 dp; banker's rounding (Python `round`) is fine at this precision.

- **D5 — Audit envelope.** Record the conversion in `properties._ingestion`:
  `original_uom`, `original_quantity`, `converted_to_uom` (= plan.uom), `conversion_factor`,
  `conversion_table_version`. The record column stays the converted quantity in `plan.uom`.

- **D6 — Boundary.** Route-level only. No new route/migration/pin; idempotency key, manual
  `/actuals`, `variance`, and R2.3 source_type all unchanged. Conversion table is in-code (a
  tenant-configurable table is a later follow-up).

## 3. Open questions to ratify (my recommendation in **bold**)

- **OQ1 dimensions**: **mass + length + volume + count (D1 set)** vs a smaller/larger set.
- **OQ2 unconvertible code**: **new `422 consumption_mes_uom_unconvertible`** vs reuse `…_mismatch`.
- **OQ3 rounding**: **6 dp, banker's** vs Decimal / different precision.
- **OQ4 unknown unit on EITHER side** (e.g. plan in a unit not in the table): **422 unconvertible**
  (fail-closed) vs pass-through-as-equal. Recommend fail-closed.

## 4. Verification plan (`test_consumption_mes_uom_conversion.py` + runtime)

- pure conversion: `g↔kg`, `mm↔m`, `lb→kg`, `dozen→ea`, identity, unknown-unit, cross-dimension.
- route: `1000 g` against a `KG` plan → `200 CREATED`, stored `actual_quantity == 1.0`, envelope
  records original; `1 m` against a `KG` plan → `422 unconvertible`; same `mes_event_id` as `1 KG`
  then `1000 G` → `DUPLICATE` (equivalent); → different qty → `CONFLICT`.
- R2.1/R2.2/R2.3 unchanged; route count 713; no migration.

## 5. Boundary

Conversion of declared units within a dimension only. No tenant-configurable table, no reversal
semantics, no new route/worker. Those are separate, later, opted slices.
