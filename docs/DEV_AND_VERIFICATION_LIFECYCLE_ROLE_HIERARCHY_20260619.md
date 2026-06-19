# DEV & VERIFICATION — Lifecycle transition role-hierarchy support

Date: 2026-06-19 · Branch `claude/lifecycle-role-hierarchy` · base `origin/main`.

## 1. Summary

The lifecycle transition role gate (`meta_engine/lifecycle/service.py`) flat-matched
`role.id == transition.role_allowed_id` with a literal placeholder comment
`# Handle role hierarchy if needed (not implemented here yet)`. But `RBACRole` already
models a role hierarchy (`parent` + `has_permission()`/`get_all_permissions()` walking UP
the parent chain), and RBAC permission checks use it. So a role that *inherits* the allowed
role's rights was wrongly denied a transition. This slice makes the gate hierarchy-aware,
consistent with RBAC.

## 2. What changed

One service file + a new DB-free test. **No new route, model, migration, or setting**; route
count and Alembic head unchanged; the only behavioral change is that an inheriting role is
now allowed.

- `meta_engine/lifecycle/service.py`: factor the gate's whole role decision into
  `_user_allowed_for_transition(user, transition_obj)` (no `role_allowed_id` → unrestricted;
  superuser bypass; else any of the user's roles must satisfy the allowed role), which calls
  `_role_satisfies(role, allowed_role_id)` — an iterative, cycle-safe walk up the `parent`
  chain. The gate calls the decision method instead of the inline flat-`==` loop; the
  placeholder comment is removed; superuser bypass and every other branch are unchanged.
- `tests/test_lifecycle_role_hierarchy.py`: unit tests of `_role_satisfies` + **behavioral
  tests of the gate decision** `_user_allowed_for_transition` (superuser bypass, direct
  match, descendant inherits, unrelated/empty denied, unrestricted) + a source contract.
  Registered in `ci.yml` + conftest.

## 3. Design

`_role_satisfies(role, allowed_role_id)` returns True iff `allowed_role_id` is `role` itself
or one of its **ancestors** up the `parent` chain. This mirrors `RBACRole.has_permission`,
which recurses to `self.parent` — i.e. a role inherits its **parent's** rights, so a
**descendant** of the allowed role inherits the transition right. The walk is iterative with
a `seen` set, so a malformed `parent` cycle terminates instead of recursing forever.

## 4. Decision to ratify

**Inheritance direction.** This implements *descendant-inherits-ancestor*, mirroring the
existing `RBACRole.has_permission` semantics exactly (the only codebase-consistent default).
Concretely: if a transition requires role *Approver* and *SeniorApprover* has `parent =
Approver`, then *SeniorApprover* is allowed (it inherits *Approver*'s right). If your org
models the hierarchy the other way (parent = more senior, and a senior should inherit a
junior's rights), the direction should flip — say so and it's a one-line change
(`current = role.parent` → walking children instead). Matching is by role **id** (unchanged
from the prior gate). This PR pins the RBAC-consistent direction so the choice is explicit.

Also noted: a second, **unused** duplicate `services/lifecycle_service.py` has its own flat
role match, but it has no production importer (dead path), so it is intentionally left
untouched to avoid touching dead code.

## 5. Verification

DB-free (in-memory `RBACRole` objects), harness `.venv-wp13` + `PYTHONPATH=<worktree>/src`.

- `test_lifecycle_role_hierarchy.py` — *(helper)* direct match; descendant inherits ancestor
  (1-deep + 3-deep); ancestor does NOT inherit a descendant's right (one-directional);
  unrelated denied; malformed `parent` cycle terminates safely. *(gate decision, behavioral)*
  superuser bypass; unrestricted when no `role_allowed_id`; direct + descendant role pass;
  unrelated + empty roles denied — these pin the actual gate **behavior** (not just a source
  string), so a wiring regression goes red. Plus a source contract that the gate calls
  `_user_allowed_for_transition`. (Added after an adversarial-verify pass flagged that the
  original tests were only the helper + a string contract.)
- No-regression: the real gate-exercising lifecycle tests (`test_item_release_gate`,
  `test_assembly_promotion_service`) stay green. (`test_lifecycle_version_integration` has a
  pre-existing failure on `main` unrelated to this change — a stale session mock — and is not
  in the contracts gate.)

## 6. Out of scope

- The unused `services/lifecycle_service.py` duplicate (dead path).
- Any change to superuser bypass, the transition table, routes, models, or migrations.
- The other surveyed lifecycle items (transition-history persistence; permission rollback on
  promote failure) — separate slices pending owner decisions.
