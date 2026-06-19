# DEV & VERIFICATION — Lifecycle transition-history persistence (Slice 1)

Date: 2026-06-19 · Branch `claude/transition-history-slice1` · base `origin/main`.
Implements **Slice 1** of the taskbook
`docs/development/lifecycle-transition-history-taskbook-20260619.md` (semantics owner-ratified).

## 1. Summary

`promote()` threaded a `comment` into the hook context but the `_record_history(...)` call was
a commented-out placeholder — the comment was dropped and no durable lifecycle-transition
record existed. Slice 1 adds the durable audit: a new per-tenant table written once per
**successful** `promote()`, **best-effort** (a write failure never fails the transition).
Route-count-neutral (no route; the read API is Slice 2).

## 2. What changed (8 files)

- `lifecycle/models.py` — new `LifecycleTransitionHistory` model (table
  `meta_lifecycle_transition_history`). Co-located here so `import_all_models()` (which already
  imports `lifecycle.models`) auto-registers it — **no `tenant_schema` supplement needed**.
- `migrations/versions/txn_history_001_*.py` — create-table migration (single head,
  down_revision `c3_date_obsolete_001`).
- `migrations_tenant/versions/t1_initial_tenant_baseline.py` — **regenerated** (the per-tenant
  baseline now includes the table); deterministic-regen + drift-guard pass.
- `config/settings.py` — `LIFECYCLE_TRANSITION_HISTORY_ENABLED` (default `True`).
- `lifecycle/service.py` — `_record_transition_history(...)` + the single call replacing the
  line-338 placeholder.
- `tests/test_lifecycle_transition_history.py` (+ ci.yml/conftest registration).

## 3. Design

### 3.1 Schema (`meta_lifecycle_transition_history`)

`id`, `item_id` (NOT NULL, indexed), `from_state_id`/`from_state_name`, `to_state_id`/
`to_state_name`, `from_permission_id`/`to_permission_id`, `transition_id`, `lifecycle_map_id`,
`actor_user_id`, `comment`, `outcome` (NOT NULL, `server_default "success"`), `properties`,
`created_at` (indexed) + composite index `(item_id, created_at)`.

### 3.2 Two implementation refinements (beyond the taskbook's illustrative sketch)

- **SAVEPOINT around the history row only — business state flushed FIRST.** A naive
  `session.add()+flush()` that fails marks the session for rollback, so the caller's later
  `commit()` would raise and **lose the transition** — the opposite of best-effort. So the
  history INSERT runs inside `session.begin_nested()` (a SAVEPOINT). Crucially, the
  already-applied **business state is flushed *outside* the audit guard first**: `flush()` (and
  `begin_nested()`'s own pre-flush) flush the *whole* pending set, so doing the business flush
  inside the try/except would let a genuine business flush error be mislabeled "history write
  failed" and swallowed — returning success on a rollback-pending session (the bug a reviewer
  caught). Both halves are verified + mutation-confirmed: a forced duplicate-PK *history* flush
  failure still commits the transition (savepoint isolation), and a forced *business* flush
  failure **propagates** rather than being swallowed.
- **`item_id` is FK-free** (indexed value, not a constraint) — like `actor_user_id` (D1) and
  `DateObsoleteImpact` — so the immutable audit row survives item deletion and a system promote
  with an unvalidated user id can't FK-violate.

### 3.3 Write site & gating

One call at the line-338 placeholder, **after all three rollback returns**, so only committed
transitions are recorded. `from_permission_id` = the `old_permission_id` #808 already captures;
`to_permission_id` = the post-transition `item.permission_id`. Default-on, gated by
`LIFECYCLE_TRANSITION_HISTORY_ENABLED`; `outcome` is always `"success"` in v1 (reserved).

## 4. Decisions

- **Ratified:** best-effort write (never fatal; `actor_user_id` nullable/no-FK); v1 = successful
  transitions only.
- **Implementation refinements (for review):** SAVEPOINT isolation (makes best-effort actually
  safe); `item_id` FK-free (audit immutability). Both are conservative and verified.

## 5. Verification

DB-free sqlite driving the real `promote()`, harness `.venv-wp13`.

- `test_lifecycle_transition_history.py`: success → one row with correct from/to state +
  **from/to permission** + actor + comment + `outcome="success"`; an unvalidated system user
  (`user_id=0`, no FK) still records; the **disabled flag** writes no row; a **construction
  failure** is swallowed (promote succeeds, no row, session usable); a **flush-level failure is
  isolated by the SAVEPOINT** (forced duplicate PK → transition still commits, session usable,
  no spurious row); a **rolled-back** transition (on_enter abort) writes no row.
- No-regression: `test_item_release_gate`, `test_assembly_promotion_service`,
  `test_lifecycle_permission_rollback`, `test_lifecycle_role_hierarchy` stay green **with the
  default-on write** (they now exercise it). Route count **719** unchanged; single Alembic head
  `txn_history_001`; tenant-baseline drift-guard + deterministic-regen pass; full contracts
  green (1761 passed).
- An adversarial-verify pass caught + fixed an `outcome` `server_default` drift (the migration
  had it but the model/baseline didn't — a tenant provisioned from the baseline would have
  lacked the DB default for the NOT-NULL column); the model now declares `server_default` and
  the baseline was regenerated, so all three artifacts agree. The SAVEPOINT isolation is
  **mutation-confirmed**: removing `begin_nested` makes the flush-failure test fail (the
  session is left in a rollback-pending state and the caller's commit raises).

## 6. Out of scope

- **Slice 2** (route +1): `get_transition_history(item_id)` service + read route.
- **All-attempts** logging (denied/condition-failed/hook-aborted/workflow-failed/version-failed)
  — a separate slice with a different write pattern; `outcome` is reserved for it.
