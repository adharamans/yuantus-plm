# Odoo18 Breakage Design-Loopback Runtime Wiring R1 - Development and Verification

Date: 2026-05-18

## 1. Goal

Wire the merged breakage DB-resolver, breakage→ECO closeout, and ECR
intake contracts into one narrow runtime consumer.

This slice deliberately stops at **intake preparation**. It reads the
existing `BreakageIncident` row, resolves the pure descriptor, evaluates
eligibility, and prepares pure ECR draft inputs. It does **not** create an
ECO, does **not** add a route, and does **not** hook automatic behavior
into status/helpdesk transitions.

## 2. Scope

Added:

- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_RUNTIME_WIRING_R1_20260518.md`

Modified:

- `src/yuantus/meta_engine/services/parallel_tasks_service.py`
- `docs/DELIVERY_DOC_INDEX.md`

Unchanged by design:

- `src/yuantus/meta_engine/services/breakage_db_resolver_contract.py`
- `src/yuantus/meta_engine/services/breakage_eco_closeout_contract.py`
- `src/yuantus/meta_engine/services/ecr_intake_contract.py`
- `src/yuantus/meta_engine/services/eco_service.py`
- breakage routers, tasks, ORM models, migrations, tenant baselines, UI, plugins

## 3. Runtime Contract

`BreakageIncidentService` now exposes a read-only runtime seam:

- `resolve_breakage_design_loopback_descriptor(incident_id)`
- `prepare_breakage_design_loopback_intake(incident_id)`

The service owns the DB read because it already owns breakage incident
queries and status transitions. It builds a `BreakageIncidentRow` from the
persisted row and reuses the merged pure contracts:

- `resolve_breakage_eco_closure_descriptor(...)`
- `is_breakage_eligible_for_design_loopback(...)`
- `map_breakage_to_change_request_intake(...)`
- `map_change_request_to_eco_draft_inputs(...)`

The returned `BreakageDesignLoopbackPreparation` is a frozen dataclass with:

- `incident_id`
- `descriptor`
- `eligible`
- `intake`
- `eco_draft_inputs`
- `ineligible_reason`

Ineligible incidents still produce a descriptor, but `intake` and
`eco_draft_inputs` remain `None`. Eligible incidents produce a validated
`ChangeRequestIntake` and `EcoDraftInputs`, but no side effect is executed.

## 4. Behavior

- `resolved` and `closed` incidents are eligible per the ratified
  breakage→ECO closeout contract.
- `open` and `in_progress` incidents are not eligible; the preparation
  result explains the status and does not call the intake mapper.
- Dirty severities continue through the ratified closeout contract rule:
  unknown severity maps to ECO priority `normal`.
- A `bom_id` without `product_item_id` remains valid by producing
  product-type intake, matching the merged closeout contract's
  bom/product invariant.
- Missing incidents raise the existing service error shape:
  `Breakage incident not found: <id>`.
- The preparation path is read-only: it does not change `status`,
  `updated_at`, or any incident field.

## 5. Test Matrix

New focused tests cover:

- eligible `resolved` incident resolves descriptor, intake, and draft inputs;
- eligible `closed` incident works;
- `open` / `in_progress` produce descriptor but no intake;
- missing incident raises the existing not-found shape;
- dirty severity maps to `normal` through the contract;
- `bom_id` without `product_item_id` remains product-type and valid;
- service uses the merged resolver / eligibility / mapper functions via spies;
- preparation does not mutate the incident row;
- AST guard: no `ECOService` import and no `create_eco(...)` call.

Existing pure-contract tests continue to guard the resolver, closeout, and ECR
intake semantics.

## 6. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_eco_closeout_contract.py \
  src/yuantus/meta_engine/tests/test_ecr_intake_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_tasks.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/parallel_tasks_service.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py
git diff --check
```

Observed on 2026-05-18:

- breakage runtime wiring + resolver + closeout + ECR intake tests:
  `62 passed`
- breakage task/router contract regression: `7 passed`
- doc-index/R2 portfolio tests: `14 passed`
- `py_compile`: clean
- `git diff --check`: clean

Note: this worktree reused the root checkout virtualenv because a linked
worktree does not have its own `.venv/`.

## 7. Non-Goals

- No ECO creation and no call to `ECOService.create_eco(...)`.
- No dedupe enforcement for derived references.
- No permission or workflow transition side effect.
- No automatic hook in `update_status(...)`, helpdesk sync, or background tasks.
- No route, UI, plugin, feature flag, migration, seed, or tenant baseline.
- No edit to the pure resolver / closeout / ECR intake contracts.

## 8. Follow-Ups

- A later opt-in can expose this preparation through a route.
- A later opt-in can add explicit, idempotent ECO creation using the prepared
  `EcoDraftInputs`, with permission/dedupe/event behavior specified first.
- A later opt-in can decide whether status transitions should offer an explicit
  "spawn design loopback" action. This slice intentionally keeps the behavior
  manual and no-op by default.
