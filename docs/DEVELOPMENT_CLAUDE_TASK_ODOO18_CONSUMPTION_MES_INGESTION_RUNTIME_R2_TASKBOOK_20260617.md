# Claude Taskbook: Odoo18 Consumption ↔ MES Ingestion **Runtime R2** (route + idempotency enforcement)

Date: 2026-06-17
Status: **DECISION — doc-only, pending gate review + a separate build opt-in**
Builds on: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_INGESTION_CONTRACT_20260515.md`
(R1 contract, merged #567) and its module
`src/yuantus/meta_engine/services/consumption_mes_contract.py`.

## 0. Why this exists

R1 delivered a **contract-only** boundary: a typed `MesConsumptionEvent`, a pure mapper into
the exact kwargs `ConsumptionPlanService.add_actual` accepts, and a deterministic
`derive_consumption_idempotency_key`. R1 deliberately stopped short of two things, stated in
its own module docstring (`consumption_mes_contract.py:9-11`):

> *does NOT expose any route or wire any runtime ingestion; … does NOT enforce idempotency
> (the key is derived and recorded only)*.

R2 closes exactly those two gaps — **a route + DB-enforced idempotency** — and nothing else.
This taskbook locks the load-bearing decisions before code, so they can be gate-reviewed (the
ECM-P1D pattern), because two of them (the enforcement mechanism and the `add_actual` change)
intentionally trip R1's drift guardrail and must be done as a deliberate, reviewed change —
not a quiet loosening.

## 1. Scope decision

| Item | R2 | Rationale |
|---|---|---|
| Ingestion route (typed MES event → `add_actual`) | **IN** | The missing runtime seam; R1's mapper already produces the kwargs. |
| Idempotency **enforcement** (replay never double-counts `variance`) | **IN** | The core reliability property; R1 records the key but enforces nothing. |
| `idempotency_key` promoted to a real column + UNIQUE | **IN** | The only race-safe enforcement; R1 left the key inside `properties` JSON. |
| uom reconciliation vs `plan.uom` | **OUT** | R1 documented follow-up (`consumption_mes_contract.py:180-182`). |
| `source_type` widening beyond `{mes, workorder}` | **OUT** | R1 documented follow-up (`:34-38`). |
| An MES **outbox/worker** | **OUT** | This is *inbound* synchronous ingest, not an outbound at-least-once queue. |
| A dedicated MES service credential / entitlement gate | **OUT** | v1 mirrors the existing route's auth; credential is a follow-up. |

## 2. Current baseline (read before implementing — file:line)

- **Model** `ConsumptionRecord` (`models/parallel_tasks.py:156-165`), table
  `meta_consumption_records`: `id, plan_id, source_type, source_id, actual_quantity,
  recorded_at, properties(JSON/JSONB)`. **No unique constraint beyond the PK; no idempotency
  column.** The very same file already uses the target idiom: `BreakageIncident.incident_code`
  (`:172`) and `.eco_id` (`:194`) are both `nullable=True, unique=True, index=True`.
- **Write path** `ConsumptionPlanService.add_actual(*, plan_id, actual_quantity,
  source_type="workorder", source_id=None, recorded_at=None, properties=None)`
  (`services/parallel_tasks_service.py:2901-2925`): validates the plan exists, inserts one row,
  `flush()`. **No dedupe.**
- **Double-count risk** `variance()` (`:2927-2948`) sums *every* record for a plan
  (`actual_total = sum(record.actual_quantity)`), so a duplicated insert double-counts. This is
  the exact failure R2's idempotency must prevent.
- **Existing route** `POST /consumption/plans/{plan_id}/actuals` → `add_consumption_actual`
  (`web/parallel_tasks_consumption_router.py:311-351`): auth `Depends(get_current_user)`,
  body `ConsumptionActualRequest`, errors via `_raise_api_error`, `db.commit()` in the route.
  This is the **generic manual** route; it is NOT the MES contract and must not change behavior.
- **R1 contract** `consumption_mes_contract.py`: `MesConsumptionEvent` (frozen, `extra=forbid`),
  `map_mes_event_to_consumption_record_inputs` (records key in `properties._ingestion`),
  `derive_consumption_idempotency_key` = `sha256(plan_id \x1f source_type \x1f mes_event_id)`.
- **Drift guardrail** `tests/test_consumption_mes_ingestion_contract.py` "fails loudly if
  `ConsumptionRecord` columns or the `add_actual` signature change out of sync" — R2 trips it
  on purpose (see D2/D7); update it deliberately, never loosen it.
- **Single Alembic head** today = `ecm_pub_outbox_001`. Route-count pin = **712**
  (`test_metrics_router_route_count_delta.py:46`). No consumption-router-specific route-count
  contract; no separate tenant-baseline creates `meta_consumption_records` (model is the single
  create source; `Base.metadata.create_all` in tests picks up new columns automatically).

## 3. Locked decisions

- **D1 — Enforcement = DB column + UNIQUE (race-safe), not a pre-check SELECT.** Add
  `idempotency_key = Column(String(64), nullable=True, unique=True, index=True)` to
  `ConsumptionRecord` (sha256 hex = 64). The MES path populates it with the R1-derived key; the
  manual `/actuals` path leaves it **NULL**. `nullable + unique` gives exactly the wanted
  semantics on **both** SQLite and Postgres — multiple NULL (manual/legacy) rows are allowed
  (NULLs compare unequal), non-null keys are globally unique — and mirrors the model's own
  `eco_id`/`incident_code` idiom. A pre-insert SELECT is rejected: it has a TOCTOU race under
  at-least-once retry storms; the UNIQUE index is the only safe gate.

- **D2 — `add_actual` gains `idempotency_key: Optional[str] = None`.** Default `None` ⇒ the
  manual path is byte-for-byte unaffected (back-compat). The MES route passes the derived key;
  `add_actual` sets the column. Extend R1's `ConsumptionRecordInputs` + `as_kwargs()` + the
  mapper to carry `idempotency_key` as a first-class field (promoted from inside
  `properties._ingestion`; keep it in `properties` too for observability). **Update the drift
  test** to include the new column + kwarg — this is the expected-red → fix signal, mirrored on
  R1's lifecycle note; do not loosen the assertion.

- **D3 — Route = `POST /consumption/plans/{plan_id}/mes-actuals`.** Path-scoped for consistency
  with the existing `/actuals`. Body = the R1 `MesConsumptionEvent` (unchanged DTO, so
  `plan_id` is present in the body); the route **asserts `body.plan_id == path plan_id`** and
  returns **400** on mismatch (one source of truth, no silent override). Auth
  `Depends(get_current_user)` — same principal model as the manual route.

- **D4 — Outcomes = `CREATED` / `DUPLICATE` (200) / `CONFLICT` (409).** Response body on 200:
  `{disposition: "CREATED"|"DUPLICATE", idempotency_key, id, plan_id, source_type, source_id,
  actual_quantity, recorded_at}`. Fresh insert → `CREATED`; a replay of the same event whose
  **business payload matches** the stored row → `DUPLICATE` carrying the existing row (no second
  insert), `200` (an at-least-once replay is a success, not a client error). A same-key event
  whose payload **diverges** (a different `actual_quantity` **or `source_id`** under the same
  `mes_event_id`) → **`409 IDEMPOTENCY_CONFLICT`, no write** (see D5/OQ5) — never silently
  swallowed.

- **D5 — Write = insert-then-catch, not look-then-insert.** Map → `add_actual` → `db.commit()`.
  On `sqlalchemy.exc.IntegrityError` (the UNIQUE violation, same class on SQLite + Postgres):
  `db.rollback()`, re-query the existing row by `idempotency_key`, then **compare the business
  payload** — the load-bearing fields are `actual_quantity` **and `source_id`** (the latter is
  business-meaningful and, crucially, **not** part of the idempotency key, so a same-key
  workorder-attribution change would otherwise be silently kept as the old `source_id`;
  `recorded_at` and transport-only attributes may legitimately differ on a benign retry and are
  ignored): equal → `DUPLICATE` (200); divergent → `409 IDEMPOTENCY_CONFLICT` with **no write**
  (D4/OQ5). Any other exception keeps the existing route's error mapping.

- **D6 — `variance` double-count protection is the acceptance invariant.** The same event twice
  ⇒ exactly one `ConsumptionRecord` ⇒ `variance()` counts it once. This is the headline test.

- **D7 — Migration: keep a single Alembic head.** New revision `down_revision =
  "ecm_pub_outbox_001"`, adds the `idempotency_key` column + its unique index to
  `meta_consumption_records`; it becomes the new single head. **No backfill** — legacy/manual
  rows keep NULL keys (they are intentionally non-deduped). Verify `alembic heads` shows exactly
  one head after.

- **D8 — Route-count pins +1: 712 → 713.** Update all total-route pins:
  `test_metrics_router_route_count_delta.EXPECTED_TOTAL_ROUTES` (=712, `:46`), plus the literal
  pins in `test_phase4_search_closeout_contracts`, `test_breakage_design_loopback_metrics`, and
  the substring pin in `test_tier_b_3_breakage_design_loopback_portfolio_contract`. The
  version-router owner maps are **not** touched (this route belongs to the consumption router).

- **D8.1 — Consumption-router owner contract (else contracts go red).** Add
  `("POST", "/consumption/plans/{plan_id}/mes-actuals")` to `_CONSUMPTION_ROUTE_KEYS` in
  `tests/test_parallel_tasks_consumption_router_contracts.py:14`. Its
  `test_create_app_registers_consumption_routes_once` (`:51-66`) asserts the live app's
  `/api/v1/consumption/*` set **exactly equals** the owner set **and** each route is registered
  **exactly once** — so the new route must be owned by `parallel_tasks_consumption_router`
  (not re-added on `parallel_tasks_router`) and mounted a single time, or this contract fails.

- **D9 — Auth = `get_current_user` (mirror the manual route).** v1: a MES connector
  authenticates as a principal exactly like the manual actuals route. A dedicated MES service
  credential / entitlement gate is a noted follow-up, not R2.

- **D10 — CI + docs registration.** New test `tests/test_consumption_mes_ingestion_runtime.py`
  dual-registered: `ci.yml` contracts list (alphabetically sorted) **and** `conftest.py`
  `_ALLOWLIST_NO_DB` (it runs on the no-DB / in-memory SQLite path). The R1 drift-test update
  stays in its existing file. Ship a `DEV_AND_VERIFICATION_*` doc and add it to
  `DELIVERY_DOC_INDEX.md` (the completeness contract requires every `DEV_AND_VERIFICATION_*`
  doc be indexed, sorted).

- **D11 — One bounded impl PR.** Column + migration + `add_actual` + mapper/inputs + drift-test
  update + route + pins + tests + CI + DEV/V doc, as a single cohesive slice (~the size of an
  ECM route PR). An optional 2-step split (R2a service/enforcement, R2b route) is acceptable if
  review prefers, but is not required.

## 4. Target output (the later impl PR)

`POST /consumption/plans/{plan_id}/mes-actuals` accepting a `MesConsumptionEvent`, enforcing
idempotency via a new unique `idempotency_key` column, returning `CREATED`/`DUPLICATE`, with
`variance` provably counting each MES event once — built on R1's mapper, with the manual route
and `variance`/`add_actual` (manual) semantics unchanged.

## 5. Acceptance & tests (`test_consumption_mes_ingestion_runtime.py`)

- **CREATED**: a fresh valid event → one row, `disposition=CREATED`, `idempotency_key` persisted
  on the column (not only in `properties`).
- **DUPLICATE / replay**: posting the identical event twice → **one** row, second call
  `200 disposition=DUPLICATE` returning the first row's id; **`variance` counts it once**.
- **Distinct events** differing only in `mes_event_id` → two rows (keys differ).
- **idempotency conflict**: same `mes_event_id` with a **different `actual_quantity` or
  `source_id`** → `409 IDEMPOTENCY_CONFLICT`, **no** second row written, `variance` unchanged
  and the original `source_id` not silently overwritten (the divergent correction is surfaced).
- **plan not found** → 404; **`body.plan_id != path`** → 400; **invalid event** (bad
  `source_type`, negative qty, reserved `_ingestion` in `attributes`) → 422/400 per D5.
- **manual route unaffected**: `/actuals` still inserts with NULL `idempotency_key` and is never
  deduped (two identical manual posts → two rows).
- **drift test**: updated to include the new column + kwarg and still green (not loosened).
- **infra gates**: full no-DB suite green; `len(app.routes) == 713`; `alembic heads` = 1; the
  consumption-router owner contract green (new route in `_CONSUMPTION_ROUTE_KEYS`, registered once).

## 6. Open questions to ratify (pick before build; my recommendation in **bold**)

- **OQ1 route shape**: **`/consumption/plans/{plan_id}/mes-actuals` (path-scoped, body repeats
  `plan_id`, asserted)** vs a top-level `/consumption/mes-actuals` (plan_id body-only).
- **OQ2 replay status**: **`200` + `DUPLICATE`** vs `409 Conflict`.
- **OQ3 manual route**: **leave manual `/actuals` NULL-keyed (never deduped)** vs also keying it.
- **OQ4 uniqueness scope**: **global single-column unique** (the sha256 already binds
  plan+source_type+event) vs a composite `(plan_id, idempotency_key)` index.
- **OQ5 same-key divergent payload**: **`409 IDEMPOTENCY_CONFLICT`, no write** (a corrected
  quantity must arrive as a *new* `mes_event_id` / adjustment event, consistent with the
  immutable-event model) vs first-write-wins silent `DUPLICATE` (which would let an MES quietly
  lose a correction that reuses an event id). Sub-decision: the compared field set — recommended
  minimal = `actual_quantity` **and `source_id`** (the latter is NOT in the key, so a same-key
  workorder-attribution change would otherwise be silently kept as the old `source_id`); a source
  attribution correction must therefore either diverge → 409 or be sent as a new `mes_event_id`.
  `recorded_at`/transport attributes tolerated.

## 7. TODO (ordered implementation checklist — for the build PR, after opt-in)

Phase A — schema + service
- [ ] `ConsumptionRecord`: add `idempotency_key` (`String(64), nullable=True, unique=True, index=True`).
- [ ] Alembic revision `down_revision="ecm_pub_outbox_001"` adding the column + unique index → single head.
- [ ] `add_actual`: add `idempotency_key: Optional[str] = None`; set the column; manual path unchanged.
- [ ] Extend `ConsumptionRecordInputs` + `as_kwargs()` + mapper to carry `idempotency_key`; **update the drift test** (do not loosen).
- [ ] Service idempotent insert: commit, catch `IntegrityError` → rollback → re-query by key → **compare business payload (`actual_quantity` + `source_id`)** → `DUPLICATE` (match) or `409 IDEMPOTENCY_CONFLICT` (divergent).

Phase B — route
- [ ] `POST /consumption/plans/{plan_id}/mes-actuals`: assert `body.plan_id == path` (400); map → `add_actual`; `200` `CREATED`/`DUPLICATE`; `409 IDEMPOTENCY_CONFLICT` on same-key divergent payload (D4/D5/OQ5).
- [ ] Error mapping: 422/400 invalid event, 404 plan, 400 mismatch, 409 conflict.
- [ ] Route-count pins +1 (712 → 713) at the 4 sites in D8.
- [ ] Update `test_parallel_tasks_consumption_router_contracts._CONSUMPTION_ROUTE_KEYS` (+ route owned by `parallel_tasks_consumption_router`, registered exactly once) — D8.1.

Phase C — tests + CI + docs
- [ ] `test_consumption_mes_ingestion_runtime.py` covering all §5 cases.
- [ ] `ci.yml` contracts list (sorted) + `conftest._ALLOWLIST_NO_DB`.
- [ ] `DEV_AND_VERIFICATION_*` doc + `DELIVERY_DOC_INDEX.md` (sorted).
- [ ] Green: no-DB suite + `len(app.routes)==713` + `alembic heads`=1 + drift test green.

## 8. Boundary

R2 is inbound, synchronous, idempotent ingest only. It does not add an outbox/worker, does not
reconcile uom, does not widen `source_type`, does not add a MES credential, and does not change
manual-actuals or `variance` semantics. Each of those is a separate, later, explicitly-opted slice.
