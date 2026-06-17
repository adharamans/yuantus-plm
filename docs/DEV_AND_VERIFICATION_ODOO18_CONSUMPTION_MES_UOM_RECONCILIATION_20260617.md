# Dev & Verification: Consumption MES ingestion — **uom reconciliation (R2.1)**

Date: 2026-06-17
Status: **IMPLEMENTED** — pending gate review + merge
Follows R2 (`…CONSUMPTION_MES_INGESTION_RUNTIME_R2…`, #778). Closes the top R2-deferred
follow-up (taskbook §8; R1 mapper note "reconciliation is a documented follow-up").

## 1. Summary

The MES ingestion route accepted `event.uom`, echoed it for observability, and **never
reconciled it against `plan.uom`**. Since `variance()` sums quantities regardless of unit, an
MES event declaring a different unit (e.g. `kg` against an `EA` plan) would be **silently
mis-counted** — the last "silent wrong number" vector in the MES line (after double-count and
silent-overwrite, both already closed). R2.1 reconciles the declared unit at ingestion and
**rejects a mismatch** rather than converting or swallowing it.

## 2. Decision (as built)

- **Reconcile only a DECLARED uom.** When `event.uom` is omitted (the R1 DTO normalizes
  blank → `None`), the event implicitly uses the plan's unit and is never rejected (lenient).
- **Normalized compare.** `event.uom.strip().upper()` vs `(plan.uom or "EA").strip().upper()`
  (plan uom is stored upper-cased; the compare is case-insensitive, so `ea` == `EA`).
- **Reject on mismatch → `422`** `consumption_mes_uom_mismatch`, body
  `context={plan_id, plan_uom, event_uom}`, **no write**. No unit conversion (would need a
  conversion table — out of scope); no warn-and-record (that re-introduces the silent
  mis-count). Same "surface, never silently mis-number" stance as the idempotency conflict.
- **404 precedence.** If the plan does not exist, the uom check is skipped and
  `ingest_mes_consumption` returns the `404` (no false `422`).
- **No extra round-trip.** The plan is loaded once for the check and reused via the session
  identity map by the subsequent `add_actual` lookup. The check runs only when a uom is
  declared.

## 3. Scope

Route-level reconciliation only. **No** new route (count stays 713), **no** migration, **no**
pin/owner-contract change, **no** change to the pure mapper (it still only echoes uom), **no**
change to manual `/actuals` or `variance` semantics. Unit *conversion* and `source_type`
widening remain separate, later, explicitly-opted slices.

## 4. Verification (`test_consumption_mes_ingestion_runtime.py`, 23 pass)

- declared mismatch (`kg` vs `EA` plan) → `422 consumption_mes_uom_mismatch`, body asserts
  `plan_uom`/`event_uom`, **zero rows written**.
- matching unit case-insensitive (`ea` vs `EA`) → `200 CREATED`.
- omitted uom → `200` (lenient; implicitly the plan unit).
- declared uom on a missing plan → `404` (not a false `422`).
- all prior R2 behavior in this file unchanged (idempotency CREATED/DUPLICATE/CONFLICT,
  variance-counts-once, SAVEPOINT outer-tx safety, 503/500 error mapping).

Guarded by their own contracts (not this runtime file): **route count stays 713** — no route is
added — pinned in `test_metrics_router_route_count_delta.py`; the consumption-router **owner
contract** (`_CONSUMPTION_ROUTE_KEYS`) is unchanged.

## 5. Files changed

- `web/parallel_tasks_consumption_router.py` — uom reconciliation check (422) + `ConsumptionPlan` import.
- `services/consumption_mes_contract.py` — mapper comment updated (reconciliation now at the route).
- `tests/test_consumption_mes_ingestion_runtime.py` — 4 uom cases.
- `docs/DELIVERY_DOC_INDEX.md` — this doc.
