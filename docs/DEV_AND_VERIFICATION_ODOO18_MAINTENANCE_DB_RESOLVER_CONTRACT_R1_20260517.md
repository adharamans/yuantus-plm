# Odoo18 Maintenance DB-Resolver Pure-Contract R1 - Development and Verification

Date: 2026-05-18

## 1. Goal

Implement the doc-only taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_MAINTENANCE_DB_RESOLVER_CONTRACT_20260517.md`.
This is the maintenance companion to the merged pack-and-go DB-resolver
contract: callers fetch rows; this module maps them into the merged
maintenance bridge descriptor.

The implementation is pure and contract-only. It adds no DB query, no
runtime wiring, no service/router/plugin change, no schema, and no
readiness enforcement.

## 2. Scope

Added:

- `src/yuantus/meta_engine/services/maintenance_db_resolver_contract.py`
- `src/yuantus/meta_engine/tests/test_maintenance_db_resolver_contract.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_MAINTENANCE_DB_RESOLVER_CONTRACT_R1_20260517.md`

Modified:

- `docs/DELIVERY_DOC_INDEX.md`

Unchanged by design:

- `src/yuantus/meta_engine/services/maintenance_workorder_bridge_contract.py`
- `src/yuantus/meta_engine/maintenance/models.py`
- `src/yuantus/meta_engine/maintenance/service.py`
- routers, plugins, migrations, tenant baselines

## 3. Contract

`EquipmentRow` mirrors the subset of `meta_maintenance_equipment` the mapper
needs:

- `id`
- `status`
- `workcenter_id`

`MaintenanceRequestRow` mirrors the subset of `meta_maintenance_requests` the
mapper needs:

- `id`
- `equipment_id`
- `state`

`resolve_workcenter_maintenance_descriptor(equipment_row, request_rows)` returns
the merged `WorkcenterMaintenanceDescriptor`.

The active request rule is the taskbook's ratified A1 rule:

- first non-terminal request in caller input order wins;
- non-terminal states are `draft`, `submitted`, `in_progress`;
- `done` and `cancelled` collapse to `None`;
- the caller owns ordering, typically `created_at DESC`;
- `draft` is surfaced as active, but remains non-blocking when evaluated by the
  bridge contract.

The mismatch rule is strict: any request row whose `equipment_id` differs from
the equipment row's `id` raises `ValueError`, even if the mismatched row is
terminal and would otherwise not be selected.

`Equipment.workcenter_id` remains a nullable soft link. The resolver does not
add schema hardening; it lets the merged descriptor validator reject missing or
blank workcenter ids.

## 4. Verification

Implemented tests cover:

- row DTO immutability, `extra="forbid"`, non-empty ids, and live enum domains;
- the exactly-named mandatory active-state selection test;
- A1 active set pinning, including `draft` active and non-blocking;
- strict equipment/request mismatch rejection;
- reuse of the merged `WorkcenterMaintenanceDescriptor`;
- compose proof with `evaluate_workcenter_readiness`;
- batch order preservation and mismatch propagation;
- drift guards against `Equipment` and `MaintenanceRequest` table columns;
- AST purity: no DB/session/router/plugin/service import, no evaluate/assert
  call, and no `assert_*` callable in the resolver module.

Commands:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_maintenance_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_maintenance_workorder_bridge_contract.py
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
  src/yuantus/meta_engine/services/maintenance_db_resolver_contract.py
git diff --check
```

No Alembic or tenant-baseline command is expected because this slice adds no
schema.

Observed on 2026-05-18: resolver + bridge tests passed (`49 passed`);
doc-index + R2 portfolio tests passed (`14 passed`); `py_compile` and
`git diff --check` were clean.

## 5. Non-Goals

- No DB read or `session`.
- No service/router/plugin wiring.
- No edit to the merged bridge contract.
- No readiness enforcement; the resolver only produces descriptors.
- No FK hardening for `Equipment.workcenter_id`.
- No migration, tenant baseline, seed, or feature flag.
- No `.claude/` or `local-dev-env/` tracking.

## 6. Next Separate Opt-Ins

- A real DB resolver/querying layer that fetches equipment and request rows.
- Runtime wiring into a manufacturing-side caller of `assert_workcenter_ready`.
- FK hardening for `Equipment.workcenter_id`.
