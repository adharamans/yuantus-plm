# Effectivity DELETE create-time guards (impl + verification)

> Follow-up to L3-1 (`docs/DEV_AND_VERIFICATION_EFFECTIVITY_DATE_PATCH_20260627.md`):
> bring `DELETE /api/v1/effectivities/{effectivity_id}` to parity with CREATE/UPDATE.
> Branch `claude/effectivity-delete-guards` · base `origin/main`.

## What
`DELETE /api/v1/effectivities/{effectivity_id}` now applies the same create-time
protection guards that CREATE (`create_effectivity`) and the L3-1 UPDATE
(`update_effectivity`) already enforce. Before today, DELETE had **no** guards — an
effectivity could be removed even when its item/version target was not latest-released
or was suspended, which is inconsistent with the rest of the effectivity lifecycle.

## Scope (consistency-only — no design decision, no new route)
- For each non-null target on the effectivity (`item_id`, `version_id`), call
  `assert_latest_released(session, target_id, context="effectivity")` and
  `assert_not_suspended(session, target_id, context="effectivity")` — identical to
  `create_effectivity` / `update_effectivity`.
- The guards live in the **service** (`EffectivityService.delete_effectivity`), mirroring
  where create/update place them.
- The router (`delete_effectivity`) maps `NotLatestReleasedError` and
  `SuspendedStateError` → **409** with `exc.to_detail()`, `db.rollback()` first —
  mirroring the L3-1 `update_effectivity_dates` exception mapping exactly.
- **404 preserved**: a missing effectivity still returns False from the service →
  `HTTPException(404)`. The guards run only after the record is found, so a non-existent
  id never raises a guard error.

## Route-count (design-lock) — UNCHANGED
`DELETE` already existed; this slice adds **no** route. The four route-count pins
(`test_phase4_search_closeout_contracts`, `test_breakage_design_loopback_metrics`,
`test_metrics_router_route_count_delta`, `test_tier_b_3_breakage_design_loopback_portfolio_contract`)
are **not** touched.

## CI wiring (anti-false-green)
No new test file. The DELETE cases were added to the already-registered
`src/yuantus/meta_engine/tests/test_effectivity_date_patch_router.py` (present in the
`ci.yml` contracts list since L3-1, and reachable via the L3-1 `detect_changes`
**effectivity** case on `effectivity_service.py` / `effectivity_router.py`), so they run
in CI without any new wiring.

## Verification (local)
`cd <worktree> && PYTHONPATH=<worktree>/src YUANTUS_PYTEST_DB=1 \
  /Users/chouhua/Downloads/Github/Yuantus/.venv-wp13/bin/python -m pytest \
  src/yuantus/meta_engine/tests/test_effectivity_date_patch_router.py \
  <doc-index contracts> -q` — green:
- DELETE happy path (guards no-op) → 200 `{"ok": true}`, second delete → 404.
- DELETE unknown id → 404.
- DELETE target not-latest-released → 409 (`detail.target_id`).
- DELETE target suspended → 409.
- All pre-existing PATCH cases still green.
- `test_dev_and_verification_doc_index_completeness` + `_sorting_contracts` green
  (this doc registered in `docs/DELIVERY_DOC_INDEX.md`).
