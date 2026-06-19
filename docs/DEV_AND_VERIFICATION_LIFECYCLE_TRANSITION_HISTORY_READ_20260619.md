# DEV & VERIFICATION — Lifecycle transition-history read route (Slice 2)

Date: 2026-06-19 · Branch `claude/transition-history-slice2` · base `origin/main`.
Completes the transition-history line: Slice 1 (#814) made `promote()` **write** a durable
audit row; Slice 2 makes it **readable** — turning a write-only table into an auditable
feature.

## 1. Summary

A read API over the audit rows: `GET /api/v1/items/{item_id}/transition-history` — who moved
an item from which state to which, when, with what comment and from/to permission. Read-only;
does not touch the write path or all-attempts.

## 2. What changed (route +1)

- `lifecycle/service.py` — `LifecycleService.get_transition_history(item_id, *, limit=None)`:
  filter by `item_id`, `ORDER BY created_at DESC` (with an `id` tiebreak for stable order),
  optional `limit`.
- `web/lifecycle_transition_history_router.py` — new router with the one GET route; returns
  `{"items": [...], "count": N}`. **404** if the item does not exist; **empty list** for an
  existing item with no history. Auth: `Depends(get_current_user)` (the item-read pattern —
  an authenticated user, not admin), mirroring `esign_router`'s item-scoped audit read.
- `api/app.py` — router mounted **unconditionally** at `prefix="/api/v1"`.
- **Route count 719 → 720**, all four pins bumped together
  (`test_metrics_router_route_count_delta` `EXPECTED_TOTAL_ROUTES`,
  `test_phase4_search_closeout_contracts`, `test_breakage_design_loopback_metrics`, and the
  substring pin in `test_tier_b_3_breakage_design_loopback_portfolio_contract`). The actual
  delta was measured (`len(app.routes)` 719→720, exactly one `GET` route) before touching the
  pins. **No model/migration/tenant-baseline change** (read-only over the Slice-1 table).

## 3. Design notes

- **404 vs empty.** The route resolves the item (`db.get(Item, item_id)`) → 404 if absent;
  otherwise returns the (possibly empty) history. This deliberately diverges from the esign
  template (which returns empty for a missing item) because the read is item-scoped and a
  missing item should surface as not-found.
- **Ordering.** `created_at DESC`, `id DESC` tiebreak. Tests assert the order with
  hand-set distinct `created_at` (not insertion order, not `promote()`), so they are stable
  and decoupled from Slice 1's write path.
- **`limit`** is `Query(None, ge=1, le=500)` — caps to the most-recent N.

## 4. Verification

- `test_lifecycle_transition_history_router.py` (8, TestClient over `create_app()`):
  most-recent-first ordering; item isolation; **empty list for an existing item**; **404 for a
  missing item**; `limit` caps to the most-recent N; serializes the permission/actor/comment
  fields; the route is **registered + owned** by this router module (GET); and the route is
  **auth-gated** (the `get_current_user` dependency is wired).
- Route-count: the four pins pass at **720**; the new route is the single `GET
  /api/v1/items/{item_id}/transition-history`.
- Full contracts green; doc indexed.

## 5. Out of scope

- The write path / `promote()` semantics (Slice 1, unchanged).
- All-attempts logging (`outcome` stays reserved); BOM-line CRUD (parked).
- Pagination beyond `limit` (cursor/offset) — not needed for v1.
