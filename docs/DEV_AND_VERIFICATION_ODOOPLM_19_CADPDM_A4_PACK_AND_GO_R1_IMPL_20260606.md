# DEV & Verification: OdooPLM 19 CAD-PDM A4 pack-and-go R1 (impl)

Date: 2026-06-06

Implements the ratified A4-R1 taskbook
(`DEVELOPMENT_ODOOPLM_19_CADPDM_A4_PACK_AND_GO_R1_STALE_DRYRUN_TASKBOOK_20260606.md`):
WP1.3 drawing-staleness injected into the pack-and-go manifest + a synchronous
dry-run / manifest-first mode. **Extend-existing** (the plugin is ~2333 lines); no
traversal-engine swap, **no new route**, no migration.

## Live re-check (taskbook step 1)

- **Route baseline = 706**, **single Alembic head = `b1_supersede_001`** — both
  unchanged (main moved to `8d499918` #737 PLM-Collab doc-only since B1). A4 adds no
  route and no migration → neither changes.

## As built (against the ratified decisions)

- **D1 — no traversal swap.** `_collect_item_ids`/`_flatten_bom_tree` untouched.
- **D2 / Q-A — synchronous dry-run.** `PackAndGoRequest.dry_run`; in POST it
  short-circuits **before** cache/async/FileResponse/cleanup, calls
  `build_pack_and_go_package(dry_run=True)` (which returns the fully-built manifest
  **before** the zip), and returns `{ok, dry_run: true, manifest, plan, warnings}`
  synchronously — no job, no zip, no cache entry, no download URL. `dry_run` takes
  precedence over `async_flag`.
- **D3 / Q-C — staleness in the manifest.** Each **drawing-role** entry gains
  `needs_update`, `staleness_reason`, `source_batch_id`, `model_import_batch_id`; a
  bundle `drawing_staleness_summary` (total/needs_update/stale_file_ids/excluded) is
  added — a **separate key** from `version_lock_summary`. The four columns are added
  to `_MANIFEST_CSV_COLUMNS` (default-on, selectable) so the **CSV** carries them too.
- **D6 / Q-B — file-scope-aware, no N+1.** Staleness read straight off the link the
  file came from (captured into `file_links`): `file_scope="item"` →
  `ItemFile`; `="version"` → `VersionFile`; version-fallback → `ItemFile`.
  `model_import_batch_id` is sourced from the item's 3d-model link in the **same**
  loaded fileset (file-scope-consistent). `staleness_reason` is a materialized column
  on both links — no `CadConsistencyService.get_staleness()` call.
- **D4 — warn, not exclude.** Stale drawings stay in the bundle
  (`drawing_staleness_summary.excluded == False`); a `warnings` entry is surfaced in
  the dry-run response. `exclude_stale_drawings` is deferred to R2.

## Surfaced & fixed (B1 follow-up + CI gap)

- `test_pack_and_go_db_resolver_contract` pinned `ItemVersion.is_current.nullable is
  True`; **B1 (#735) tightened it to NOT NULL**, so the assertion was stale — updated
  to `is False`. The resolver's §3 `Optional[bool]` None-handling is now defensively
  moot but harmless. It went undetected because the pack-and-go **contract/wiring
  tests were not in any CI list**.
- **CI gap closed:** registered all four pack-and-go tests (the new
  `test_plugin_pack_and_go_stale_dryrun` + the three previously-unregistered
  `*_version_lock_wiring` / `*_db_resolver_contract` / `*_version_lock_contract`) in
  the **plugin-tests job**, plus a `plugins/yuantus-pack-and-go/*` detect_changes arm
  so plugin-source changes trigger the suite.

## Verification (Python 3.11 venv, requirements.lock)

- `test_plugin_pack_and_go_stale_dryrun.py` → **8 passed** (calls
  `build_pack_and_go_package(dry_run=True)` with an injected fake file_service +
  seeded item/model/drawing): item-scope staleness fields + `model_import_batch_id`;
  summary distinct from `version_lock_summary` + warn-not-exclude; CSV carries the
  four columns; non-drawing entry has no staleness; **version-scope reads from
  `VersionFile`** (D6); **dry-run leaks no temp dir**; **`model_import_batch_id`
  prefers the 3D `native_cad` model over a 3D geometry** (WP1.3 model M); **dry-run is
  manifest-first — a local-cache miss must NOT download** (P1 regression: a
  `get_local_path→None` + `download_file`-raises fake; the manifest still builds, no temp dir).
- Full pack-and-go suite (5 files) → **79 passed** (78 + the manifest-first
  regression test); workflow YAML/trigger contracts → pass.
- Route-count **706 unchanged** (no route); no migration (head stays
  `b1_supersede_001`).

## Not in this PR (R2 / non-goals)

- `exclude_stale_drawings=true` (drop stale drawings).
- Migrating the traversal onto WP1.2 `get_relationship_tree`.
- A3 workstation checkout (heavier, separate line).
