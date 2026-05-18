# Odoo18 Breakage Design-Loopback ECO Creation R1 - Development and Verification

Date: 2026-05-18

## 1. Goal

Add the first explicit side-effecting runtime step after the merged
breakage design-loopback preparation slice: create an ECO from an eligible
breakage incident on caller request.

This slice remains service-level only. It does not add a route, UI,
automatic status hook, schema, migration, plugin behavior, or tenant
baseline change.

## 2. Scope

Added:

- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_ECO_CREATION_R1_20260518.md`

Modified:

- `src/yuantus/meta_engine/services/parallel_tasks_service.py`
- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py`
- `docs/DELIVERY_DOC_INDEX.md`

Unchanged by design:

- `src/yuantus/meta_engine/services/breakage_db_resolver_contract.py`
- `src/yuantus/meta_engine/services/breakage_eco_closeout_contract.py`
- `src/yuantus/meta_engine/services/ecr_intake_contract.py`
- `src/yuantus/meta_engine/services/eco_service.py`
- breakage routes, tasks, ORM models, migrations, tenant baselines, UI, plugins

## 3. Runtime Contract

`BreakageIncidentService` now exposes:

- `create_breakage_design_loopback_eco(incident_id, *, user_id, allow_duplicate=False)`

The method:

- reuses `prepare_breakage_design_loopback_intake(incident_id)`;
- rejects ineligible incidents before touching ECO creation;
- derives the existing breakage closeout reference from the frozen descriptor;
- by default looks for an existing ECO whose `description` contains the
  reserved `breakage-eco-closeout` envelope and the same `reference=<hash>`;
- delegates create permission, flush, default stage, and ECO-created event
  behavior to `ECOService.create_eco(...)`;
- passes the explicit caller `user_id` into `ECOService.create_eco(...)`;
- returns `BreakageDesignLoopbackEcoCreation` with `created=True` for new ECOs
  or `created=False` when the existing ECO is reused.

The caller owns commit/rollback. This method flushes only through the existing
`ECOService.create_eco(...)` behavior and does not commit.

## 4. Idempotency Boundary

R1 implements best-effort query-before-create dedupe using existing data only:

- no new column;
- no new unique constraint;
- no hard DB idempotency key;
- no lock;
- no route-level retry token.

This is enough to make normal repeated service calls return the same ECO, but
it is not race-safe under concurrent calls. Durable idempotency needs a later
schema or lock/key slice.

`allow_duplicate=True` deliberately bypasses this best-effort dedupe and
creates another ECO for explicit operator-driven duplicate cases.

## 5. Behavior

- Resolved/closed eligible incidents can create ECOs.
- Open/in-progress incidents raise before ECO creation.
- Missing incidents preserve the existing `Breakage incident not found: <id>`
  error shape.
- ECO kwargs come from the prior pure ECR draft inputs, except `user_id` is
  overridden with the explicit caller.
- ECO descriptions retain both reserved envelopes:
  `breakage-eco-closeout` and `ecr-intake`.
- Default duplicate calls return the existing ECO and do not change its actor.
- Creation does not mutate the source breakage incident status, description, or
  timestamp fields.
- Permission failures from ECO creation propagate to the caller; the service
  does not convert them or mutate the incident as a side effect.

## 6. Test Matrix

New focused tests cover:

- eligible resolved incident creates an ECO with expected type, priority,
  caller, and description envelopes;
- ineligible open incident raises before `ECOService.create_eco(...)`;
- repeated call returns the existing ECO by breakage reference;
- `allow_duplicate=True` creates a second ECO;
- missing incident preserves the existing not-found error;
- explicit creation does not mutate the source incident;
- ECO creation permission error propagates without incident mutation.

The prior preparation test was updated so it no longer forbids `ECOService`
at module scope. It now asserts the preparation path itself still does not call
`create_eco(...)`.

## 7. Verification Commands

```bash
/Users/chouhua/Downloads/Github/Yuantus/.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_eco_closeout_contract.py \
  src/yuantus/meta_engine/tests/test_ecr_intake_contract.py
```

```bash
/Users/chouhua/Downloads/Github/Yuantus/.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_tasks.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py
```

```bash
/Users/chouhua/Downloads/Github/Yuantus/.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
/Users/chouhua/Downloads/Github/Yuantus/.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/parallel_tasks_service.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py
git diff --check
```

Observed on 2026-05-18:

- breakage ECO creation + runtime wiring smoke: `16 passed`
- full breakage creation/runtime/resolver/closeout/ECR matrix:
  `69 passed`
- breakage task/router contract regression: `7 passed`
- doc-index/R2 portfolio tests: `14 passed`
- `py_compile`: clean
- `git diff --check`: clean

Note: this linked worktree reuses the root checkout virtualenv because it does
not have its own `.venv/`.

## 8. Non-Goals

- No API route, router permission surface, OpenAPI update, UI, or plugin
  wiring.
- No automatic call from `update_status(...)`, helpdesk sync, background tasks,
  or closure flows.
- No schema/migration/back-reference for ECO source.
- No durable concurrency-safe idempotency.
- No edit to pure resolver / closeout / ECR intake contracts.
- No edit to `ECOService.create_eco(...)`.

## 9. Follow-Ups

- Add a route only after permission, response shape, duplicate policy, and
  audit expectations are explicitly scoped.
- Add durable idempotency with a persisted source reference if repeated
  operator/API retries become a product requirement.
- Decide separately whether a status transition should offer an explicit
  design-loopback action. R1 keeps it manual and no-op by default.
