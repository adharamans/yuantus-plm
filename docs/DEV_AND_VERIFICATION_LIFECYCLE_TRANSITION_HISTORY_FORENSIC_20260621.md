# DEV & VERIFICATION — Transition-history forensic admin route

Date: 2026-06-21 · Branch `claude/txnhist-forensic-admin-route` · base `origin/main`.
Builds the **#819-archived "deleted-item forensic admin route"** parked item: an admin-gated
query that retrieves an item's transition-history by recorded `item_id` **without** an
item-existence gate, so a *deleted* item's retained (FK-free) history stays reachable.

## 1. Summary

The Slice-2 read (`GET /api/v1/items/{item_id}/transition-history`) resolves the item first and
returns **404** if it is gone — correct for an item-scoped sub-resource, but it leaves a deleted
item's retained audit rows unreachable. This adds a second, forensic surface:
`GET /api/v1/transition-history/forensic/{item_id}` — superuser-gated, queries by `item_id`, no
existence gate. Read-only; no write-path or all-attempts change.

## 2. What changed (route +1)

- `web/lifecycle_transition_history_router.py` — new route `get_forensic_transition_history`
  on the **existing** router. **Reuses** `LifecycleService.get_transition_history(item_id,
  limit=)` unchanged (Slice 2's service already queries by `item_id` and deliberately does *not*
  check item existence), and simply omits the `db.get(Item) → 404` gate. Auth:
  `Depends(require_superuser)`.
- `tests/test_lifecycle_transition_history_router.py` — **+6** forensic cases added to the
  existing (already CI-registered) file — no new test file, so no ci.yml/conftest registration
  needed (avoids the new-test-runs-nowhere trap).
- **Route count 720 → 721**, all four pins bumped together (`test_metrics_router_route_count_delta`
  `EXPECTED_TOTAL_ROUTES` + ladder comment, `test_phase4_search_closeout_contracts`,
  `test_breakage_design_loopback_metrics`, and the substring meta-pin in
  `test_tier_b_3_breakage_design_loopback_portfolio_contract`).
- **No service / app.py / model / migration change** (existing router, existing service).

## 3. Design notes

- **FK-free retention is the premise.** `LifecycleTransitionHistory.item_id` has no FK (the
  `txn_history_001` migration is explicit: "intentionally FK-free") and there is no cascade — so
  history survives an item hard-delete (`DeleteOperation` → `session.delete(item)`). The
  item-scoped route 404s on the missing item; this route, keyed on the recorded `item_id`, returns
  the retained rows.
- **Empty vs 404.** A never-existed `item_id` with no history returns an empty list (200), **not**
  404 — the deliberate inverse of the item-scoped route, since this surface does not resolve the
  item at all.
- **Auth — the deliberately-surfaced decision.** Gated by `require_superuser` (the `is_superuser`
  flag, granted only via CLI bootstrap and the platform-admin router — a strong, sparingly-issued
  privilege). This is the **conservative, most-restrictive default** for a sensitive surface that
  exposes deleted-item history. **Open for ratification:** whether this should stay superuser-only,
  relax to an org/tenant-admin **role**, or fold into a unified **per-item ACL** is precisely the
  auth-model axis reserved on the *per-item-ACL hardening* item — so this route defaults to the
  tightest gate pending that decision, rather than guessing a looser one.

## 4. Verification

`test_lifecycle_transition_history_router.py` (forensic cases, no-DB sqlite + TestClient):
- **deleted-item history returned** — rows inserted for an `item_id` with **no** `Item` row →
  forensic GET returns them (200, correct count, `created_at` desc); the load-bearing case.
- **never-existed id → empty 200, not 404** — the inverse of the item-scoped 404.
- **403 without superuser** — overriding `get_current_identity` to a non-superuser makes
  `require_superuser` reject (403).
- **route registered + owned** by this module; **admin-gated** — `require_superuser` is wired and
  the bare `get_current_user` item-read dependency is **not**.
- Route-count: the four pins assert **721**; the new route is the single added `GET`.

## 5. Out of scope (kept minimal per "small scope")

- **Richer forensic query** — cross-item / time-range / actor search (named as "likely" in the
  #819 note) — not built; the v1 forensic surface is by-`item_id`.
- **The auth-model unification** (superuser vs role vs per-item ACL) — the reserved decision above.
- All-attempts logging (`outcome` still reserved); the write path (unchanged).
