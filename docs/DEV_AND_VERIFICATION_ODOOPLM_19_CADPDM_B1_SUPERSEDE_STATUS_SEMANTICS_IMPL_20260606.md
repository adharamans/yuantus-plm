# DEV & Verification: OdooPLM 19 CAD-PDM B1 supersede / status semantics (impl)

Date: 2026-06-06

Implements **B1** from the ratified grounding taskbook
`DEVELOPMENT_ODOOPLM_19_CADPDM_B1_SUPERSEDE_STATUS_SEMANTICS_TASKBOOK_20260606.md`
(#734). A **version-level** `Superseded` signal + a concurrent-revision guard on
`ItemVersion` — NOT an Item lifecycle state. Last item of the CAD-PDM borrow Phase 2
(after B2 hard gate #731 + readiness aggregation #732). **No new route; no Item
lifecycle / `is_current` / ECO / B2 change.**

## Live re-check (step 1, per D8 — main moves fast)

- **Route baseline = 706** (the taskbook's literal 705 was stale; #733's embed-token
  endpoint bumped it). B1 adds no route → stays **706**, no pin bump.
- **Single Alembic head = `wp13_cad_stale_001`** (re-confirmed via `alembic heads`;
  the phantom 2nd head `f7a8b9c0d1e2` is consumed by `c1d2e3f4a5b6_merge_heads`;
  #733 added no migration). B1 migration `down_revision = wp13_cad_stale_001`.

## As built (against the ratified decisions)

- **D1 — version-level locus.** `ItemVersion.is_superseded` (bool, indexed) +
  `state="Superseded"`. No `LifecycleState`/transition/seeder change; the Item stays
  released (newer version current) while only the prior *version* is superseded.
- **D2 — supersede trigger.** Hook in `VersionService.release()` — the **sole
  runtime** `is_released` setter (promote- and ECO-originated releases route here;
  ECO uses `ECOStage` and never releases directly). On releasing vN+1, the immediate
  `predecessor_id` is set `is_superseded=True` **iff** `is_released and not
  is_superseded` — keeps `is_released=True` (Q-A) and the `not is_superseded` clause
  stops the seeder's Suspend→Release re-cycle (and `release()`'s own
  already-released early-return) from re-superseding. Only the immediate predecessor
  is touched (older ancestors superseded inductively).
- **D3 — under-modification derived.** `VersionService.is_under_modification(item_id)`
  — True iff the current version is an open draft AND a prior released version exists.
  Read-time only, no stored state. A never-released new draft is NOT under modification.
- **D4 — app guard.** `revise()`/`new_generation()` require the source
  `is_released`; else `VersionError` (→ 400 at the router). Existing
  `test_version_service.py` fixtures updated to a released source (revise's realistic
  precondition; each test's intent — revise mechanics / rejection reasons —
  preserved, no assertions removed).
- **D4b — concurrency enforcement.** App guard **+** DB partial-unique
  `UNIQUE (item_id, COALESCE(branch_name,'main')) WHERE is_current IS TRUE AND
  is_released IS NOT TRUE` ("≤1 open current version per item line + branch") —
  race-proof backstop for concurrent `revise()`. Declared in
  `ItemVersion.__table_args__` (built by `create_all` for tests) **and** the
  migration; both verified to **enforce** (the expression index is not reflectable,
  so enforcement is asserted behaviourally — `IntegrityError` on a 2nd open draft).
- **D5 — `merge_branch` branch.** The merged version inherits `target_ver.branch_name`
  (previously omitted → defaulted `'main'`, which would land a branch-target merge as
  a mainline open draft and collide with the D4b index). `merge_branch` is otherwise
  not subject to the D4 guard (separate branch workflow).
- **D6/D7 — additive, version-axis only.** No change to `Item.state`, `Item.is_current`,
  config-generation/relationship `is_current` (B2 / WP1.2 traversal), ECO, or the B2
  release gate.
- **D8 — migration (forward-only).** `b1_supersede_001` off `wp13_cad_stale_001`:
  idempotent, dialect-conditional `batch_alter_table` for SQLite (mirrors WP1.3 /
  is_suspended); adds `is_superseded` (`server_default=sa.false()`); **normalizes
  pre-existing NULLs** (`branch_name`→'main', `is_released`/`is_current`→false) then
  tightens the three to `NOT NULL` + server_default; creates the partial-unique
  index. No backfill / no retro-supersede. Column+index add → no migration-table
  `create_table`, no coverage-contract bump. Verified end-to-end via `alembic upgrade
  head` on SQLite (chains single-head; index enforces; downgrade clean).
- **D9/D10 — no route (706 unchanged); non-goals.** No UI, no version-scheme change,
  no ECO/revision-router rework, no Item lifecycle state, and explicitly disjoint
  from `bom_obsolete`'s part-replacement (`superseded_by`).

## Verification (Python 3.11 venv, requirements.lock)

- `test_version_supersede_b1.py` → **12 passed**: D2 supersede (immediate predecessor,
  keeps is_released, not-re-supersede, leaf), D4 guard (revise/new_generation reject
  unreleased; released-source succeeds), **D4b DB constraint itself** (2nd open draft
  → IntegrityError via create_all; released-prev + open-draft allowed), D3 predicate,
  D5 merge_branch (branch inheritance + branch-target merge not caught by the mainline
  index), and the model↔migration lock-step guard.
- `test_version_service.py` → **12 passed** (existing; revise/new_generation fixtures
  updated to a released source).
- Migration: `alembic heads` → single `b1_supersede_001`; `alembic upgrade head` on
  SQLite runs clean and the index enforces.
- Test dual-registered (`ci.yml` contracts list + `conftest.py` no-DB allowlist),
  `test_migration_table_coverage_contracts` unaffected.
- `create_app()` unchanged at **706 routes**; full CI contracts list green.
- `git diff --check` clean.

## Not in this PR

- B1 is the final borrow Phase-2 item; no follow-on B-series planned. Any
  Superseded read-surface (e.g. a version-history filter) would be a separate slice.
