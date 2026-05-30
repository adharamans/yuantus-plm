# DEV & Verification: OdooPLM Gap G3 — 3D Visual Explode Grounding/Scope-Lock Taskbook

Date: 2026-05-29

Records the doc-only delivery of
`DEVELOPMENT_ODOOPLM_G3_3D_EXPLODE_TASKBOOK_20260529.md` — the grounding +
scope-lock for the G3 3D-visual-explode gap. Doc-only: no code; merging it does
**not** authorize the implementation. Baseline `main = 50333c6a` (after G4 #680).

## 1. What changed

- New G3 grounding/scope-lock taskbook with an **honest "thin server surface"**
  conclusion (no per-component geometry server-side; `component_ref` opaque/
  client-defined; v1 = a validated explode-config riding `meta_3d_overlays` with
  no migration; BOM-auto-layout + multiple-preset table deferred; real explode
  client-side, out of repo scope).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = 50333c6a`)

- **Spatial explode absent**: only the data-level `/bom/explode`
  (`query_router.py:88`) + the G5 spare BOM explode exist; no visual/spatial
  explode endpoint/service/model.
- **3D precedent**: `ThreeDOverlay` (`models/parallel_tasks.py:228`, table
  `meta_3d_overlays`) + `ThreeDOverlayService` + `/cad-3d/overlays*` — per-document
  JSON `part_refs`/`properties` with cache + role visibility.
- **No server geometry**: `cad_converter_service` flattens to OBJ/glTF files;
  `cad_mesh_stats` exposes only a whole-document bbox. No per-component transforms.
- **`component_ref` opaque**: `ThreeDOverlayService.resolve_component(s)`
  (`parallel_tasks_service.py:7176`) matches an opaque, client-defined string and
  returns the raw row — no geometry/node handle.

## 3. Locked decisions (summary)

Honest sizing: G3 backend is **light**. v1 = persist/serve a single **validated**
explode config (`factor` + `mode` + per-`component_ref` `offset` vectors) per
`document_item_id`, riding `meta_3d_overlays.properties["explode"]` (**no
migration**), mirroring the overlay endpoints/auth/cache. The viewer applies the
offsets; the server validates structure only, never geometry. **Deferred**:
BOM-derived auto-layout (assumption-laden — needs `component_ref == BOM-item id`,
unguaranteed) and multiple named presets (dedicated table). **§7 binding LOCK**:
`component_ref` is client-defined; applicability is the client's responsibility.
New routes → bump the four 688 pins + the cad-3d contracts route set; extend
`test_parallel_tasks_cad_3d_router_contracts.py`. Non-goals: no server geometry/
mesh/bbox, no client rendering, no BOM-auto-layout/preset-table/migration in v1,
no GPL/AGPL.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13,
  `verify_material_sync_static.py` — pass (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only grounding + scope-lock. Ratifying §3–§9 of the taskbook sets the (thin)
explode implementation plan; the implementation needs its own explicit opt-in. The
remaining minor OdooPLM gaps (finishing/treatment, `plm_project`) remain
separately-opted.
