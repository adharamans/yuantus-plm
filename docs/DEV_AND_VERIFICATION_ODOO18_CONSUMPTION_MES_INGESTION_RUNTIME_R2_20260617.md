# Dev & Verification: Consumption ↔ MES Ingestion **Runtime R2** (route + idempotency enforcement)

Date: 2026-06-17
Status: **IMPLEMENTED** — pending gate review + merge
Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_INGESTION_RUNTIME_R2_TASKBOOK_20260617.md`
Builds on R1 (contract-only, #567).

## 1. Summary

R2 closes the two gaps R1's module docstring deliberately left — *a route* and *idempotency
enforcement*. An external MES can now `POST` a typed consumption event to a dedicated route;
a retried at-least-once delivery is deduplicated at the database (so `variance` never
double-counts), and a same-key event whose business payload diverges is surfaced as a `409`
conflict rather than silently dropped. The manual `/actuals` path is byte-for-byte unchanged.

## 2. Open-question resolutions (as built, per the taskbook recommendations)

- **OQ1** route shape → path-scoped `POST /api/v1/consumption/plans/{plan_id}/mes-actuals`,
  body is the R1 `MesConsumptionEvent`, `body.plan_id` asserted `== path` (400 on mismatch).
- **OQ2** replay status → `200` + `disposition` (`CREATED` / `DUPLICATE`).
- **OQ3** manual route → unchanged; `idempotency_key` is NULL on `/actuals`, never deduped.
- **OQ4** uniqueness → global single-column unique on `idempotency_key`.
- **OQ5** divergent same-key payload → `409 IDEMPOTENCY_CONFLICT`, no write; compared business
  fields are `actual_quantity` **and** `source_id` (the latter is not in the key).

## 3. Design (as built)

- **Column** (`models/parallel_tasks.py`): `ConsumptionRecord.idempotency_key =
  Column(String(64), nullable=True, unique=True, index=True)`. `nullable + unique` gives the
  wanted semantics on SQLite and Postgres alike (NULL manual/legacy rows coexist; non-null keys
  globally unique), reusing the table's own `BreakageIncident.eco_id`/`incident_code` idiom.
- **`add_actual`** (`services/parallel_tasks_service.py`): gained `idempotency_key:
  Optional[str] = None` (default None ⇒ manual path unchanged). The R1 mapper now carries the
  key as a first-class `ConsumptionRecordInputs` field (`as_kwargs()` includes it), so the R1
  generic drift tests pass **without modification** — they introspect that the mapper kwargs,
  the dataclass fields, and the columns stay aligned, which they do.
- **Idempotent ingest** (`ConsumptionPlanService.ingest_mes_consumption`): **insert-then-catch**,
  never look-then-insert. It INSERTs inside a **SAVEPOINT** (`session.begin_nested()`) and lets
  the unique constraint arbitrate; on `IntegrityError` the savepoint unwinds **only the failed
  insert** (not the caller's outer transaction), then it re-reads the winning row by key and
  compares `actual_quantity` + `source_id` → `DUPLICATE` (equal) or `CONFLICT` (divergent).
  Race-safe under retry storms; a missing plan raises `ValueError` before any insert. Because the
  unwind is savepoint-scoped, the method is **safe inside a larger caller transaction** — a
  DUPLICATE/CONFLICT never discards other uncommitted writes (regression-tested).
- **Route** (`web/parallel_tasks_consumption_router.py`): `ingest_mes_consumption_actual`.
  `200 {disposition, idempotency_key, id, plan_id, source_type, source_id, actual_quantity,
  recorded_at}` on CREATED/DUPLICATE; `409` on CONFLICT; `404` missing plan; `400` plan_id
  mismatch / reserved-key collision; `422` invalid event (FastAPI DTO validation). A
  **transient** DB failure (deadlock/serialization/connection — not an `IntegrityError`) is a
  retryable `503`, and any other server-side error is `500` — never a `4xx`, so an
  at-least-once producer retries safely rather than dropping the event (see §4.6). Auth mirrors
  the manual route (`get_current_user`).
- **Migration** `consumption_mes_idem_001` (`down_revision = ecm_pub_outbox_001`): adds the
  column + a unique index whose name matches the model's auto-generated index, so Postgres and
  the test `create_all` agree. Idempotent guards; no backfill. New single Alembic head.

## 4. Verification

Run: `.venv-wp13`, `unset YUANTUS_PYTEST_DB …`.

### 4.1 Service layer (`test_consumption_mes_ingestion_runtime.py`, real SQLite)
- first ingest → `CREATED`, key persisted on the column, one row.
- replay (identical) → `DUPLICATE`, same row id, still one row.
- **`variance` counts once**: two identical ingests → `records == 1`, `actual_quantity == 4.0` (not 8.0).
- distinct `mes_event_id`s → two rows, variance sums both.
- same key + divergent `actual_quantity` → `CONFLICT`, no write, original kept.
- same key + divergent `source_id` (attribution change) → `CONFLICT`, original `source_id` not overwritten.
- manual `/actuals` → two identical entries both persist, both NULL-keyed (never deduped).
- missing plan → `ValueError` before any insert.

### 4.2 Route layer (TestClient HTTP contract)
- CREATED then DUPLICATE (200, same id); CONFLICT → 409 **with an asserted body shape**
  (`code` + `context.idempotency_key` / `existing_record_id` / `plan_id`); plan_id mismatch →
  400; missing plan → 404; invalid `source_type` → 422; negative quantity → 422; reserved
  `_ingestion` in `attributes` → 400; transient `OperationalError` → **503**; unexpected
  server error → **500** (never 4xx).

### 4.3 Infra gates
- **Route count**: `len(app.routes) == 713` (712 → +1). All 4 pins updated
  (`EXPECTED_TOTAL_ROUTES`; the two literal `assert len(app.routes) == 713`; the tier-b
  substring-of-phase4 pin).
- **Owner contract**: `_CONSUMPTION_ROUTE_KEYS` gains
  `("POST", "/consumption/plans/{plan_id}/mes-actuals")`; the live `/api/v1/consumption/*` set
  exactly equals the owner set, each route registered once.
- **R1 drift guard**: green unchanged (generic introspection; alignment preserved).
- **Single Alembic head**: `alembic heads` → `consumption_mes_idem_001`.
- **Doc-index**: references/sorting/completeness contracts green (this doc indexed).

### 4.4 CI registration — also closes an R1 false-green
The R1 contract test (`test_consumption_mes_ingestion_contract.py`) was in **neither** the
`ci.yml` contracts list **nor** the `conftest._ALLOWLIST_NO_DB`, so on the no-DB contracts job
it was silently ignored — a false green. R2 registers **both** the R1 contract test and the
new R2 runtime test in both places (sorted), so the whole MES ingestion feature is genuinely
exercised in CI.

### 4.5 Adversarial verification (7 lenses → refute each finding)
A multi-lens adversarial review (idempotency/race, conflict-OQ5, route-HTTP, migration/dialect,
contracts/pins, manual-regression, completeness-critic), each finding then independently
refuted, returned **0 must_fix** and confirmed the dedup core sound (race-safe insert-then-catch;
CREATED/DUPLICATE/CONFLICT; no double-count; no write on conflict). One real **should_fix** was
fixed: the route catch-all mapped a **transient** DB error (a Postgres deadlock/serialization is
an `OperationalError`, *not* the `IntegrityError` the service catches) to `400`, which would tell
an at-least-once MES producer to drop the event — a silent `variance` **undercount**, the
symmetric failure to the over-count idempotency prevents. Now `OperationalError → 503` (retryable)
and other server errors → `500`; the only client-caused failures (invalid event, missing plan)
keep their `4xx` upstream. Two nits also addressed (409 body-shape now asserted). Tests added:
`test_route_transient_db_error_is_retryable_503`, `test_route_unexpected_error_is_500_not_400`.

A subsequent gate review (P2) noted that `ingest_mes_consumption` did a **full**
`session.rollback()` on the dup/conflict branch — fine for the one-request route, but a footgun
now that the method is public service API: a future caller running it inside a larger
transaction would have its uncommitted writes discarded on a DUPLICATE/CONFLICT. **Hardened**:
the insert now runs inside a `session.begin_nested()` SAVEPOINT, so only the failed insert is
unwound. Regression-tested: `test_duplicate_preserves_caller_uncommitted_writes`,
`test_conflict_preserves_caller_uncommitted_writes` (an uncommitted plan + first record both
survive the dup/conflict).

### 4.6 Pre-existing unrelated failure (NOT introduced here)
`test_parallel_tasks_services.py::test_workorder_doc_pack_refresh_updates_only_matching_item`
fails on the local no-DB path with an `uq_itemversion_open_current_per_line` error. It fails
**with this change stashed** and is **not** in the CI contracts list (so it does not gate CI);
it is unrelated to consumption and out of scope for R2.

## 5. Boundary (unchanged from the taskbook)

R2 is inbound, synchronous, idempotent ingest only: no outbox/worker, no uom reconciliation, no
`source_type` widening, no MES service credential, no change to manual-actuals or `variance`
semantics. Each remains a separate, later, explicitly-opted slice.

## 6. Files changed

- `models/parallel_tasks.py` — `idempotency_key` column.
- `services/parallel_tasks_service.py` — `add_actual` kwarg + `ingest_mes_consumption`.
- `services/consumption_mes_contract.py` — `ConsumptionRecordInputs.idempotency_key` + mapper + docstring.
- `web/parallel_tasks_consumption_router.py` — `ingest_mes_consumption_actual` route.
- `migrations/versions/consumption_mes_idem_001_add_idempotency_key.py` — new migration.
- `tests/test_consumption_mes_ingestion_runtime.py` — new (service + route).
- route-count pins (×4) + `_CONSUMPTION_ROUTE_KEYS` owner contract.
- `ci.yml` + `conftest.py` — register R1 + R2 tests (sorted).
- `docs/DELIVERY_DOC_INDEX.md` — this doc.
