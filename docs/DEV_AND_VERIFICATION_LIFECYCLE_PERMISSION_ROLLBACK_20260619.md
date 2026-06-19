# DEV & VERIFICATION — Lifecycle permission rollback on promote() failure

Date: 2026-06-19 · Branch `claude/lifecycle-permission-rollback` · base `origin/main`.

## 1. Summary

`LifecycleService.promote()` applies **state-driven permission** — on entering a state it sets
`item.permission_id = target_state.default_permission_id` (or `.permission_id`). But the three
failure **rollback** paths restored only `item.state` / `item.current_state`, **not** the
permission — the first even carried a `# item.permission_id = ... (restore old permission if
necessary)` placeholder. So a rolled-back transition left the item with the *target* state's
permission while its state was reverted: a **stale permission** that a caller committing the
session (e.g. the C3 date-obsolete worker, which commits after each `promote()`) would persist.

This restores `item.permission_id` together with state on every rollback path.

## 2. What changed

One file (`meta_engine/lifecycle/service.py`). **No new route, model, migration, or setting**;
route count and Alembic head unchanged.

- Capture `old_permission_id = item.permission_id` alongside `old_state_*` before the state
  change.
- On all three rollback paths (on_enter_state-hook abort; workflow-start failure; version-
  release failure) add `item.permission_id = old_permission_id` (replacing the placeholder).

## 3. Decision to ratify

**Permission follows state.** `promote()` SETS the permission from the target state on entry,
so on a rolled-back (failed) transition the permission is restored to the pre-transition value
together with the state — symmetric with the set, and matching the existing placeholder's
intent. If instead `permission_id` is meant to be managed independently of lifecycle state
(not reverted on rollback), this should not restore it — but that would leave the
set-on-success behavior asymmetric, so restoring is the consistent default. (Callers that
already `session.rollback()` on failure are unaffected; this only fixes callers that keep and
commit the in-memory item.)

## 4. Verification

DB-free sqlite (real `LifecycleService.promote()`, not mocked), harness `.venv-wp13`.

- `test_lifecycle_permission_rollback.py` — **behavioral coverage of all three rollback
  paths** via a real `promote(Draft→Released)` where `Released` carries
  `default_permission_id="perm_released"`, asserting state reverts to `Draft` **and**
  `permission_id` reverts to the pre-promote `perm_draft` (without the fix it would be the
  stale `perm_released`):
  - **path 1** — an `on_enter_state` hook aborts (the hook is registered on the singleton
    `hook_registry`, which is snapshot/restored so it can't leak into other tests);
  - **path 2** — the state's linked workflow fails to start (`WorkflowService.start_workflow`
    monkeypatched to raise);
  - **path 3** — version release fails (`VersionService.release` monkeypatched to raise).
  Plus a **no-regression** success case (no failure → `perm_released` is applied) and a light
  **placeholder-gone** source check. The file imports the user model explicitly so its FK
  target (`users`) is registered regardless of collection neighbors.
- Existing gate tests (`test_item_release_gate`, `test_assembly_promotion_service`) stay green.

## 5. Out of scope

- Transition-history persistence (the dropped `comment`) — a separate slice pending an owner
  decision.
- Any change to the state-driven permission SET behavior, routes, models, or migrations.
- The `find_effective_version` / role-hierarchy / BOM-line slices (separate PRs).
