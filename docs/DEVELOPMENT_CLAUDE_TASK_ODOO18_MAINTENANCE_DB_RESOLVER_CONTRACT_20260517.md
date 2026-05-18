# Claude Taskbook: Odoo18 Maintenance DB-Resolver Pure-Contract R1

Date: 2026-05-17

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

R2 closeout §4 **Tier-A** follow-up #3b (companion to the merged
pack-and-go DB-resolver #588 `fdc1fd9` and follow-up #2 from the
merged maintenance↔workorder bridge #572 `ca6755f`). The merged
bridge contract
(`maintenance_workorder_bridge_contract.py`) consumes
`WorkcenterMaintenanceDescriptor`s the **caller** supplies, but
there is no typed, pure mapping from the persisted rows to those
descriptors. This R1 supplies that pure mapping — **the contract
still does not read the DB**; the caller fetches rows, the pure
function maps them. Mirrors the row→descriptor pattern just landed
in pack-and-go DB-resolver R1.

## 2. Current Reality (grounded — read before implementing)

- Merged `src/yuantus/meta_engine/services/maintenance_workorder_bridge_contract.py`:
  - `WorkcenterMaintenanceDescriptor` (frozen Pydantic v2,
    `extra="forbid"`):
    - `workcenter_id: str` (non-empty)
    - `equipment_id: str` (non-empty)
    - `equipment_status: str` (validated ∈ `EquipmentStatus` values)
    - `active_request_state: Optional[str] = None` (validated ∈
      `MaintenanceRequestState` values when non-null)
  - Blocking sets (lines 46–60):
    - `_BLOCKING_EQUIPMENT_STATUSES = {OUT_OF_SERVICE, DECOMMISSIONED}`
    - `_BLOCKING_REQUEST_STATES = {SUBMITTED, IN_PROGRESS}` —
      **`DRAFT` is deliberately non-blocking** (bridge contract
      lines 50–54: distinct from
      `MaintenanceService.get_maintenance_queue_summary` which
      counts `draft` into the active queue).
  - `_DEGRADED_EQUIPMENT_STATUS = IN_MAINTENANCE`.
  - `evaluate_workcenter_readiness` (pure) and
    `assert_workcenter_ready` (three machine-matchable failure
    prefixes: `workcenter_invalid:`/`workcenter_blocked:`/`workcenter_unknown:`)
    — **out of scope here**.
- Persisted source rows (file
  `src/yuantus/meta_engine/maintenance/models.py`):
  - `Equipment` — table `meta_maintenance_equipment` (line 97).
    Columns of interest:
    - `id: String, primary_key=True` (line 99).
    - `status: String(30), nullable=False, default=EquipmentStatus.OPERATIONAL.value`
      (lines 112–116).
    - `workcenter_id: String, nullable=True` (line 121) — **bare
      string column, NO FK constraint**. It is a soft link only;
      the resolver cannot rely on referential integrity, and an
      equipment row with `workcenter_id=None`/empty has no
      meaningful workcenter descriptor.
  - `MaintenanceRequest` — table `meta_maintenance_requests` (line
    153). Columns of interest:
    - `id: String, primary_key=True` (line 155).
    - `equipment_id: String, ForeignKey("meta_maintenance_equipment.id"), nullable=False, index=True`
      (lines 157–162).
    - `state: String(30), nullable=False, default=MaintenanceRequestState.DRAFT.value`
      (line 170).
    - `created_at: DateTime, server_default=now(), nullable=False`
      (line 189) — the natural sort key for "newest request".
  - `EquipmentStatus` enum (lines 37–41): exactly
    `{OPERATIONAL, IN_MAINTENANCE, OUT_OF_SERVICE, DECOMMISSIONED}`.
  - `MaintenanceRequestState` enum (lines 44–49): exactly
    `{DRAFT, SUBMITTED, IN_PROGRESS, DONE, CANCELLED}`.
- **No existing producer.** `WorkcenterMaintenanceDescriptor` is
  input-only in the merged bridge. There is no service method
  today that picks a single "active request state" for one
  equipment. The R1 resolver must therefore *define* the selection
  rule. In-tree precedents, verified by direct file reads:
  - `MaintenanceService.list_requests`
    (`src/yuantus/meta_engine/maintenance/service.py:241`):
    `q.order_by(MaintenanceRequest.created_at.desc()).all()` —
    pins **newest-first ordering**, but says nothing about which
    states count as "active."
  - `MaintenanceService.get_maintenance_queue_summary`
    (`src/yuantus/meta_engine/maintenance/service.py:372–376`):
    `active_states = {DRAFT, SUBMITTED, IN_PROGRESS}` — answers
    *"is there queued work?"*, **includes `DRAFT`**.
  - `MaintenanceService.get_preventive_schedule`
    (`src/yuantus/meta_engine/maintenance/service.py:313–317`):
    `active_states = {DRAFT, SUBMITTED, IN_PROGRESS}` — answers
    *"what overdue/upcoming preventive work is in the queue?"*;
    **also includes `DRAFT`**, identical to the queue-summary
    active set.
  - The merged bridge contract's `_BLOCKING_REQUEST_STATES =
    {SUBMITTED, IN_PROGRESS}` (lines 55–60) — answers *"is the
    workcenter *blocked* right now?"*, **excludes `DRAFT`** as an
    explicit, in-doc divergence (bridge contract lines 50–54):
    "draft request is not yet active … this deliberately differs
    from `MaintenanceService.get_maintenance_queue_summary`".
  In-tree code therefore has a **uniform *active* definition** in
  `MaintenanceService` (`{DRAFT, SUBMITTED, IN_PROGRESS}` in both
  queue-summary and preventive-schedule); the bridge contract
  intentionally narrows this to `{SUBMITTED, IN_PROGRESS}` for its
  *blocking* decision only. "Active" and "blocking" are NOT the
  same concept in this codebase. The resolver's
  `active_request_state` feeds the bridge — so its selection rule
  shapes whether `DRAFT` is representable in the descriptor
  stream.
- **Equipment.workcenter_id FK hardening** is one of the bridge
  contract's three documented follow-ups (memory:
  maintenance-bridge `ca6755f`) — explicitly **NOT** in this R1's
  scope; an equipment row missing `workcenter_id` is a caller-side
  data state, not something the resolver fixes.

## 3. Row → Descriptor Boundary (the core of this taskbook)

The contract is **pure**: it does **not** query. The caller fetches
and passes typed row views; one descriptor is produced per
`(equipment_row, request_rows)` pair.

| Source rows | Resolver output |
|---|---|
| `equipment_row` with valid `workcenter_id` + 0 request rows | `equipment_status = equipment_row.status`, `active_request_state = None` |
| `equipment_row` + request rows with **any** non-terminal state | `active_request_state = ` the **first non-terminal state in input order** (caller orders, see §3 Policy A) |
| `equipment_row` + request rows where every state is terminal (`done`/`cancelled`) | `active_request_state = None` |

### Policies

**Policy A — Active-request selection: RATIFIED A1 (2026-05-17
re-review of PR #589).** The resolver produces
`active_request_state: Optional[str]` by picking the **first
non-terminal state in input order** from `request_rows`, where
non-terminal = `{DRAFT, SUBMITTED, IN_PROGRESS}` — every
`MaintenanceRequestState` value **except** the terminal
`{DONE, CANCELLED}`. If no non-terminal request exists,
`active_request_state = None`. The caller is responsible for
ordering (typically `created_at DESC` to match
`MaintenanceService.list_requests`); the resolver does not re-sort
internally.

**Rationale (ratified):**

- Both in-tree `MaintenanceService` active-state surfaces
  (`get_maintenance_queue_summary` and `get_preventive_schedule`)
  use exactly `{DRAFT, SUBMITTED, IN_PROGRESS}` — verified by
  direct file read (§2). The resolver's "active" set matches that
  uniform in-tree definition.
- "Active" and "blocking" are **deliberately different concepts**
  in this codebase. The merged bridge contract narrows blocking to
  `{SUBMITTED, IN_PROGRESS}` and *already documents* (bridge lines
  50–54) that the divergence is intentional. The bridge already
  has tests pinning that `DRAFT` is safe and non-blocking; feeding
  `active_request_state=DRAFT` is correctly classified as
  non-blocking by `evaluate_workcenter_readiness`, with `ready=True`.
- A1 preserves the diagnostic *"draft exists"* signal — a
  downstream consumer (or future logging surface) can distinguish
  "no request at all" from "draft-only" from the descriptor alone,
  without adding a separate descriptor field.

**Options considered but NOT ratified (audit trail):**

- **A2** — surface only `{SUBMITTED, IN_PROGRESS}` (collapse `DRAFT`
  to `None`). Rejected: it would make the resolver and the
  `MaintenanceService` active-set definitions diverge, while
  conflating "blocking-relevant" with "active" (two different
  concepts the codebase deliberately keeps apart). A2 was the
  taskbook author's initial recommendation; the recommendation
  rested on a mis-citation of `get_preventive_schedule` as
  excluding `DRAFT` — fixed in §2.
- **A3** — return ALL states (no filter). Rejected for **semantic
  uselessness**, not for any validation issue: the bridge's
  `WorkcenterMaintenanceDescriptor` validator accepts every
  `MaintenanceRequestState` value, including `DONE`/`CANCELLED`,
  so A3 would not raise. But surfacing terminal states on
  `active_request_state` is semantically inconsistent with the
  field's name and would make the descriptor non-discriminating
  for the bridge's blocking rule.

**Policy B — Equipment-request id-mismatch raises (PRE-RATIFIED,
strict reading; deliberate carry-over from #588 case (b)).** If ANY
`request_row.equipment_id != equipment_row.id` in the input pair,
the resolver **raises `ValueError`** — caller bug. The rule is
unconditional on whether the mismatching row would have been the
chosen one. This pre-applies the strict reading reviewer-ratified
at merge time on PR #588 (case (b): "stray row when the link has
no pinned id" extension); not opening this again because the
ratification carried in #588 closed the question. This is
*input-shape validation*, **not** the bridge contract's enforcement
(which remains `assert_workcenter_ready`, untouched). If the
reviewer for this taskbook wants to roll back to the narrower
reading, flag it — but the default is the #588-consistent strict
reading.

**Policy C — Equipment.workcenter_id is a soft link
(documented, not RATIFIED).** Because `Equipment.workcenter_id` is
`nullable=True` with no FK, an equipment row with
`workcenter_id=None`/empty cannot be resolved to a
workcenter-scoped descriptor. The merged descriptor's existing
`_non_empty` validator on `workcenter_id` already raises in that
case; the resolver does not add a separate check. (This keeps the
FK-hardening question — the bridge's documented follow-up (c) —
out of this contract's scope.)

**Policy D — Equipment.status is non-Optional in the row DTO
(documented, not RATIFIED).** Because `Equipment.status` is
`nullable=False` with a default, every persisted row has a string
status; the row DTO mirrors this with `status: str`. The merged
descriptor's `equipment_status` validator already pins the
value-domain against the live `EquipmentStatus` enum (the #570
review lesson).

## 4. R1 Target Output (for the later, separately opted-in impl PR)

New pure module
`src/yuantus/meta_engine/services/maintenance_db_resolver_contract.py`:

- `EquipmentRow` — frozen Pydantic v2, `extra="forbid"`. Subset of
  `meta_maintenance_equipment` columns the mapping needs:
  - `id: str` (non-empty)
  - `status: str` (non-Optional — column is nullable=False; see §3
    Policy D)
  - `workcenter_id: Optional[str] = None` (mirrors the real nullable
    column; the merged descriptor's non-empty validator raises if
    the resolver propagates a falsy value)

  Field names mirror the column names (drift-guarded).
- `MaintenanceRequestRow` — frozen Pydantic v2, `extra="forbid"`.
  Subset of `meta_maintenance_requests` columns the mapping needs:
  - `id: str` (non-empty)
  - `equipment_id: str` (non-empty)
  - `state: str` (validated ∈ `MaintenanceRequestState` values)

  Field names mirror the column names (drift-guarded). `state` is
  NOT typed as the enum — keeping it `str` mirrors the SQLAlchemy
  storage type and the merged descriptor's storage convention.
- Module-level `_ACTIVE_REQUEST_STATES`:
  `frozenset({DRAFT.value, SUBMITTED.value, IN_PROGRESS.value})` —
  exactly the §3 Policy A RATIFIED A1 set. A drift-guard test pins
  this against the live `MaintenanceRequestState` enum as
  `{m.value for m in MaintenanceRequestState} - {DONE.value, CANCELLED.value}`,
  so an enum addition triggers an explicit policy review rather
  than silently mis-classifying. Note: this set is intentionally
  **wider** than the merged bridge's `_BLOCKING_REQUEST_STATES =
  {SUBMITTED, IN_PROGRESS}` — "active" and "blocking" are
  different concepts (§3 Policy A rationale).
- `resolve_workcenter_maintenance_descriptor(equipment_row, request_rows=()) -> WorkcenterMaintenanceDescriptor`
  — pure; applies §3 Policy A (first non-terminal in input order)
  and Policy B (raise on id mismatch); returns the **merged**
  `WorkcenterMaintenanceDescriptor` (imported, not reimplemented).
- `resolve_workcenter_maintenance_descriptors(pairs) -> tuple[...]`
  — batch over `Sequence[Tuple[EquipmentRow, Sequence[MaintenanceRequestRow]]]`;
  deterministic (input order preserved across equipment).

No DB read, no `session`, no `eval`, no plugin edit, no enforcement.
Imports **only** the merged
`maintenance_workorder_bridge_contract.WorkcenterMaintenanceDescriptor`
plus the maintenance enums (value-domain — the same two the merged
bridge already imports).

## 5. Tests Required (in the later impl PR)

New `test_maintenance_db_resolver_contract.py`:

- Row DTOs: frozen, `extra=forbid`, non-empty
  `id`/`equipment_id`/`workcenter_id` (via the merged descriptor's
  validator when propagated), enum-domain `state`.
- **`test_resolver_picks_first_active_request_state_in_input_order`
  (MANDATORY, exactly named)** — single equipment with mixed
  requests; the **first active state in input order** is returned
  for `active_request_state`; terminal-only and empty inputs yield
  `None`. Parametrized to cover each of `DRAFT`/`SUBMITTED`/
  `IN_PROGRESS` winning when it is the first active in input
  order; and `DONE`/`CANCELLED` being skipped.
- **`test_resolver_active_set_pins_ratified_policy_a`
  (MANDATORY, exactly named)** — pins the §3 Policy A A1
  ratification in code: `_ACTIVE_REQUEST_STATES == frozenset(
  {DRAFT.value, SUBMITTED.value, IN_PROGRESS.value})`; equivalently
  `_ACTIVE_REQUEST_STATES == {m.value for m in MaintenanceRequestState}
  - {DONE.value, CANCELLED.value}`. The test also asserts
  `_ACTIVE_REQUEST_STATES != _BLOCKING_REQUEST_STATES` (active is
  strictly wider than blocking) so a future drift-toward-blocking
  fails loudly.
- **`test_resolver_pins_draft_as_active_and_non_blocking`
  (MANDATORY, exactly named)** — compose proof: a descriptor with
  `active_request_state=DRAFT.value` produced by the resolver
  feeds `evaluate_workcenter_readiness` and yields `ready=True`,
  `blocked=[]`, `degraded=[]`. Pins the §3 Policy A "draft surfaced
  AND non-blocking" semantic at the resolver↔bridge seam.
- **`test_resolver_rejects_mismatched_equipment_request_pair`
  (MANDATORY, exactly named)** — any
  `request_row.equipment_id != equipment_row.id` → `ValueError`;
  pins the §3 Policy B strict reading (parametrized: mismatch in
  the only row; mismatch in a row that would otherwise be
  filtered-as-non-active).
- **`test_resolver_output_is_the_merged_workcenter_descriptor`
  (MANDATORY, exactly named)** — the return value is an instance of
  the merged
  `maintenance_workorder_bridge_contract.WorkcenterMaintenanceDescriptor`
  and the resolved descriptors feed `evaluate_workcenter_readiness`
  unchanged (compose proof, no DB).
- Batch: order preserved; one equipment's mismatch propagates as
  `ValueError`.
- **Drift guards**: `EquipmentRow` fields ⊆
  `Equipment.__table__.columns`; `MaintenanceRequestRow` fields ⊆
  `MaintenanceRequest.__table__.columns`; `_ACTIVE_REQUEST_STATES`
  pinned against the ratified §3 Policy A set (see the
  `test_resolver_active_set_pins_ratified_policy_a`
  MANDATORY test above); the produced descriptor's field set
  equals `WorkcenterMaintenanceDescriptor.model_fields` (reuse,
  not reimplement);
  `Equipment.__table__.columns["workcenter_id"].nullable is True`
  (pins the soft-link assumption Policy C is built on).
- **Purity guard** (AST): module imports nothing matching
  `yuantus.database` / `sqlalchemy` / `parallel_tasks_service` /
  `_router` / `plugins` / `_service`; imports **only**
  `maintenance_workorder_bridge_contract` and the maintenance enums
  module; contains no `session`/DB call.
- **No-evaluate/no-assert (AST)**: module does not call
  `evaluate_workcenter_readiness` or `assert_workcenter_ready`; no
  `assert_*` callable is defined.

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) must stay green.

## 6. Verification Commands (for the impl PR)

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

No alembic / tenant-baseline — the contract adds no schema.

## 7. DEV/verification MD requirements (impl PR)

Add `docs/DEV_AND_VERIFICATION_ODOO18_MAINTENANCE_DB_RESOLVER_CONTRACT_R1_20260517.md`
+ index registration. Must document: (a) the pure row→descriptor
boundary (caller fetches; contract never queries); (b) the
non-terminal-state surfacing rule and **why** `DRAFT` is honestly
surfaced rather than collapsed-to-None (bridge's
blocking-vs-non-terminal distinction); (c) the strict-reading
id-mismatch rule and why it is input validation, not bridge
enforcement; (d) the soft-link nature of `Equipment.workcenter_id`
and the explicit non-goal of FK hardening; (e) the merged
`WorkcenterMaintenanceDescriptor` reused unchanged.

## 8. Non-Goals (hard boundaries for the impl PR)

- **No DB read / no `session`** — caller-supplied rows only; the
  actual query is a separate later opt-in.
- **No service/router/plugin wiring** —
  `MaintenanceService`/`maintenance_router`/manufacturing-side
  callers are not edited.
- **No bridge-contract enforcement** —
  `evaluate_workcenter_readiness` /`assert_workcenter_ready` are
  reused **unchanged**; the resolver only produces descriptors, it
  does not decide ready/blocked.
- **No edit to `maintenance_workorder_bridge_contract`,
  `maintenance/models.py`, or `maintenance/service.py`**.
- **No FK hardening for `Equipment.workcenter_id`** — explicitly
  the bridge contract's separate follow-up (c); changing the
  column's nullability is a schema change, out of scope.
- No schema / migration / tenant-baseline / feature flag / runtime
  wiring.
- No contact with the other R2 contracts beyond importing the
  merged `WorkcenterMaintenanceDescriptor` and the maintenance
  enums.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude Code **only after this
taskbook is merged AND a separate explicit opt-in is given**, on
branch
`feat/odoo18-maintenance-db-resolver-contract-r1-20260517`.

Follow-ups, each its own separate opt-in (explicitly NOT in R1):

- An actual DB resolver that **queries**
  `meta_maintenance_equipment` + `meta_maintenance_requests` and
  feeds these row DTOs (touches the DB — separate).
- Wiring the resolved descriptors into a manufacturing-side caller
  of `assert_workcenter_ready` (plugin + runtime — separate; the
  merged `assert_workcenter_ready` is the enforcement seam, also
  separate).
- `Equipment.workcenter_id` FK hardening (schema — separate; the
  bridge contract's documented follow-up (c)).

## 10. Reviewer Focus

- §3 Policy A is **RATIFIED A1** (2026-05-17 re-review of #589):
  resolver surfaces non-terminal incl. `DRAFT`. Active set =
  `{DRAFT, SUBMITTED, IN_PROGRESS}` — uniform with both
  `MaintenanceService.get_maintenance_queue_summary` and
  `get_preventive_schedule`. Confirm the rationale (active ≠
  blocking; bridge already classifies `DRAFT` as non-blocking,
  pinned by the compose-proof test).
- Is the id-mismatch rule (§3 Policy B) read strictly —
  unconditional on whether the mismatching row would have been
  selected?
- Is `EquipmentRow.status` typed as `str` (non-Optional) so the
  `nullable=False` column is mirrored, and
  `EquipmentRow.workcenter_id` typed `Optional[str] = None` to
  mirror the soft link?
- Is the contract pure (no DB/session/service import; allows only
  the merged bridge contract + maintenance enums) and does it
  reuse the merged `WorkcenterMaintenanceDescriptor` unchanged
  (drift-guarded)?
- Are the row DTO field sets proper subsets of the real table
  columns, and is `_NON_TERMINAL_REQUEST_STATES` pinned exactly
  against the live `MaintenanceRequestState` enum?
- Did anything add a DB read, edit the bridge contract /
  maintenance service / maintenance router, harden the
  `Equipment.workcenter_id` FK, or add enforcement? It must not.
