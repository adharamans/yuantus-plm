# DEV & VERIFICATION — Transition-history item-scoped read: per-item ACL hardening (2a)

Date: 2026-06-21 · Branch `claude/txnhist-per-item-acl` · base `origin/main`.
Goal item #2 (per-item ACL hardening). Owner chose **2a** ("按你推荐"): bring the item-scoped
read into line with `bom_where_used`/`impact`, not the broader unify-audit (2c).

## 1. Summary

The item-scoped read `GET /api/v1/items/{item_id}/transition-history` was **authenticated-only**
(any logged-in user). It now enforces a **per-item ACL** — `check_permission(item_type_id,
AMLAction.get)` → **403** — exactly as `bom_where_used`/`impact` already do. This closes the lone
authenticated-only gap on the item-scoped read surface.

## 2. What changed (no route count change)

- `web/lifecycle_transition_history_router.py` — `get_item_transition_history`: fetch the item
  (404 if absent), then `MetaPermissionService(db).check_permission(item.item_type_id,
  AMLAction.get, user_id=str(user.id), user_roles=user.roles)` → 403 if denied. Mirrors the
  `bom_where_used` pattern + imports (`schemas.aml.AMLAction`, `services.meta_permission_service`).
  The **forensic route is unchanged** (superuser-gated, #827). Modifies an existing route →
  **route count stays 721**, no pins touched.
- `tests/...` — an autouse default-allow for `check_permission` (so the existing read tests still
  exercise the 200 path) + `test_item_route_403_without_read_permission` (deny → 403).

## 3. Design notes

- **404 before 403** — the type-permission check needs the item's `item_type_id`, so the item is
  resolved first; identical ordering to `bom_where_used` (and its minor existence-signal, accepted
  for parity with the established pattern).
- **Two-tier model, now coherent:** item-scoped read = **per-item ACL** (this change); forensic
  retrieval = **superuser** (#827). The item read answers "may you read *this* item", the forensic
  surface answers "are you an audit admin".
- **Scope = 2a** (history-read only). The broader 2c (re-audit BOM/impact + every item-scoped read
  to one model) was the owner's alternative; not taken.

## 4. Verification

- Existing item-scoped read tests (ordering, isolation, empty-vs-404, limit, serialization) stay
  green under default-allow; **deny → 403** is asserted by
  `test_item_route_403_without_read_permission`. Forensic-route + route-count tests unaffected
  (route count unchanged at 721).
- CI: contracts + regression (can't run locally — system python 3.9 vs codebase 3.10+).
