# DEV & Verification: OdooPLM Gap G3 — 3D Visual Explode Implementation

Date: 2026-05-30

Records the **implementation** of the G3 3D-visual-explode gap, per the merged
grounding/scope-lock taskbook `DEVELOPMENT_ODOOPLM_G3_3D_EXPLODE_TASKBOOK_20260529.md`
(#681). Baseline `main = c180ee57`. **Deliberately thin** per the taskbook: a
validated explode-config persistence keyed by the opaque, client-defined
`component_ref`; the server never touches geometry. **No migration, no new table,
no new model** — the config rides the existing `meta_3d_overlays` row.

## 1. Step-0 grounding re-confirmed (taskbook §11)

- **§7 / §11.1 — `component_ref` opaque**: `ThreeDOverlayService.resolve_component(s)`
  (`parallel_tasks_service.py`) still matches an opaque client-defined string and
  returns the raw row, no geometry/node handle. Offset applicability remains the
  client's responsibility.
- **§11.2 — storage path**: `meta_3d_overlays.properties` is an existing JSON
  `Column` (`models/parallel_tasks.py:237`), `document_item_id` is `unique` — the
  config persists with **no migration**; `get_explode` reuses `get_overlay`'s
  role-visibility + cache.

## 2. What changed

- `services/parallel_tasks_service.py` — `ThreeDOverlayService` gains:
  - `upsert_explode(document_item_id, explode_config)`: load-or-create the overlay
    row (empty `part_refs` if new), set `properties["explode"]`, flush, reuse the
    cache-invalidate/set seam. Stores the config verbatim (no geometry interpretation).
  - `get_explode(document_item_id, user_roles)`: via `get_overlay` (inherits the
    role-visibility gate → PermissionError when hidden); returns
    `properties.get("explode")` or None.
- `web/parallel_tasks_cad_3d_router.py` — `ExplodeOffset` / `ExplodeConfigRequest`
  Pydantic models (validate structure only: `factor` in [0,1000], `mode` non-empty
  ≤40 chars, each `offset` exactly 3 numbers, `component_ref` non-empty) + 2 routes:
  - `PUT /api/v1/cad-3d/explode/{document_item_id}` (upsert; service error → 400)
  - `GET /api/v1/cad-3d/explode/{document_item_id}` (get; visibility denied → 403)
- Route count **688 → 690** (+2): all four pins bumped + the cad-3d contracts
  `_CAD_3D_ROUTE_KEYS` extended; full-tree residual scan clean (no stale 688).
- This DEV/verification record + one sorted `DELIVERY_DOC_INDEX.md` entry.

## 3. Decisions realized (honest sizing held)

- v1 is the thin validated-persistence layer the taskbook locked; the server
  validates structure, never geometry; the viewer applies offsets. Single config
  per document (riding the unique overlay row). BOM-auto-layout and multiple
  named presets remain **deferred** (§5 of the taskbook) — not built.
- Riding the overlay means `upsert_explode` may create an empty-`part_refs`
  overlay as a side effect, and explode reads inherit the overlay's
  `visibility_role` gate — acceptable for v1 (the deferred dedicated table is the
  escape hatch if explode ever needs its own lifecycle/visibility).

## 4. Verification

- DB-backed (`YUANTUS_PYTEST_DB=1`) — **214 passed** across the full
  `test_parallel_tasks_services.py` + `test_parallel_tasks_router.py` +
  `test_parallel_tasks_cad_3d_router_contracts.py` (zero-regression gate). New
  coverage: 4 service tests (upsert/get/update, none-when-absent, **role-gate
  inheritance** against real SQLite) + 6 router tests (valid PUT, 422 on bad
  offset length, 422 on out-of-range factor, GET returns/None, **403 visibility**).
- Route-count pins + cad-3d contracts — 39 passed (route ownership/count + the 4
  pins at 690). `create_app()` builds; **690** routes; the 2 explode routes resolve
  to the cad-3d router, registered once.
- Migration-table-coverage — 4 passed (no new table). CI-wiring fan-out (per
  [[feedback-test-file-ci-wiring-fanout]]): **all changes extend existing files**
  (no new test file) → no conftest allowlist / ci.yml / portfolio change; the
  cad-3d contracts test was already wired.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass.
  `git diff --check` clean.

## 5. Non-Goals upheld

No server-side geometry / mesh / bbox / transform computation; no client-side
rendering; no BOM-derived auto-layout; no multiple-named-presets table; no
migration; no change to overlay/view-state behavior beyond the explode key; no
GPL/AGPL; no revision/version coupling.

## 6. Status

G3 3D-visual-explode implemented and verified — the thin, honest server surface
the taskbook scoped. Remaining OdooPLM items (each separately opted-in): the
deferred BOM-auto-layout / multiple-preset table; G4 category/property token; the
minor gaps (finishing/treatment, `plm_project`).
