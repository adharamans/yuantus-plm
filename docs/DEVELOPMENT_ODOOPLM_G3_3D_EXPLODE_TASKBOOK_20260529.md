# Claude Taskbook: OdooPLM Gap G3 — 3D Visual Explode Grounding + Scope-Lock

Date: 2026-05-29

Type: **Doc-only taskbook (grounding + scope-lock).** It re-verifies the G3
3D-visual-explode gap against current `main`, grounds what the **backend can and
cannot** contribute, and scope-locks a deliberately **thin** server surface. It
changes no code. **Merging this taskbook does NOT authorize the implementation** —
that requires its own explicit opt-in.

Origin: `DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` §5 G3 ("3D 视觉爆炸图
(explode)（markup/overlay 已有）", impact **中** / fixability **中**, "仅数据级
`/bom/explode`；3D overlay 有，spatial explode 未见") + §6.3 ("在已有 overlay/
view-state 基础上补 spatial explode"). Baseline `main = 50333c6a` (after G4 #680).

## 0. What this is (and is not) — read the honest conclusion first

- **The backend contribution to G3 is LIGHT.** Grounding (below) shows the server
  holds **no per-component 3D geometry or transforms** and that a component
  identity (`component_ref`) is an **opaque, client-defined string**. The real
  explode — computing/validating spatial offsets against geometry, mapping
  components to viewer nodes, and rendering — is a **3D-viewer (client)** concern,
  **out of this repo's scope**.
- So this taskbook scope-locks a **minimal, validated explode-config persistence**
  that mirrors the existing 3D overlay, and is explicit that it is *not* a
  geometry feature. A grounding taskbook concluding "largely client-side; backend
  support is thin" is the honest, useful outcome — not a reason to manufacture a
  table + router to make G3 look larger than the grounding supports.
- **No GPL/AGPL**: aligns with `plm_web_3d` *semantics* only. No odooplm code read.

## 1. Gap re-verified (against `main = 50333c6a`)

- **Spatial explode is genuinely absent.** The only `explode` on the server is the
  **data-level** BOM explode (`web/query_router.py:88` `/bom/explode` — a
  flattened multi-level BOM structure) and the G5 spare-parts explode (BOM
  traversal). No spatial/visual explode endpoint, service, or model exists.
- The existing 3D surface (the precedent to build on):
  - **3D overlay** — `web/parallel_tasks_cad_3d_router.py` (`/cad-3d/overlays*`),
    `ThreeDOverlayService`, model `ThreeDOverlay` (`models/parallel_tasks.py:228`,
    table `meta_3d_overlays`): per-`document_item_id`, columns `version_label`,
    `status`, `visibility_role`, `part_refs` (JSON), `properties` (JSON), with a
    cache layer + role visibility, one-row-per-document upsert.
  - **2D view-state** — `web/cad_view_state_router.py`: `hidden_entity_ids` +
    per-entity `notes` stored inline on `FileContainer.cad_view_state` (JSON),
    keyed by **integer entity ids** validated against a CAD-document JSON. This is
    a 2D-entity surface — **not** a 3D assembly with components, and not a fit for
    spatial explode.
  - **Geometry** — `cad_converter_service.py` converts to OBJ/glTF/GLB **files**;
    `cad_mesh_stats_router.py` exposes only whole-document `bounds`/`bbox` from a
    metadata JSON when present. Geometry is parsed **client-side**.

## 2. Pivotal grounding facts (these define the boundary)

1. **No per-component geometry/transforms server-side.** The converter flattens to
   mesh files; mesh-stats expose only an optional *whole-document* bbox. The
   server cannot compute a geometry-correct per-component explode offset.
2. **`component_ref` is opaque and client-defined.** `ThreeDOverlayService.
   resolve_component(s)` (`parallel_tasks_service.py:7176`) matches a
   `part_ref.component_ref` string case-insensitively and returns the raw row;
   the server assigns it **no geometry meaning**. Whether a server-stored offset
   is applicable depends entirely on the **client** using a `component_ref`
   convention that maps to its viewer nodes (glTF node id / handle) — a convention
   the server neither defines nor validates.
3. **BOM hierarchy is available** (`/bom/explode`, `BOMService.get_bom_structure`)
   — a geometry-free grouping source, but keyed by BOM-item id, which is *not*
   guaranteed to equal the client's `component_ref`.

## 3. Scope conclusion (the honest sizing)

G3 backend = **persist and serve a validated explode configuration** keyed by the
**same opaque `component_ref`** the overlay already uses: an explode `factor`, a
`mode`, and an optional list of per-`component_ref` offset vectors. The viewer
applies them to the geometry it holds. The server validates **structure only**
(numeric offsets, well-formed refs) — never geometry. Everything visual is
client-side and out of scope.

## 4. Recommended v1 (ratify) — a thin validated explode-config, mirroring the overlay

- An **explode config** = `{ factor: float, mode: str (e.g. "radial"|"axial"),
  offsets: [ { component_ref: str, offset: [x,y,z] (numbers) } ] }`, **one per
  `document_item_id`** (not multiple named presets — §5).
- Persisted **with no migration** by riding the existing `meta_3d_overlays` row:
  store under a reserved `properties["explode"]` key (the explode endpoints upsert
  the overlay row if absent). Keyed by `document_item_id`, exactly like the
  overlay; reuses its cache-invalidation + role-visibility.
- API mirrors the overlay (ratify exact shape at impl): an upsert
  (`PUT/POST /cad-3d/explode/{document_item_id}`) + a get
  (`GET /cad-3d/explode/{document_item_id}`). Validation: `factor` numeric and
  bounded; each `offset` a 3-number list; `component_ref` non-empty.
- **Why this isn't "just an overlay field":** it adds a typed, validated contract
  (the overlay `properties` bag is free-form/unvalidated) and a discoverable
  endpoint; but it is honestly a light layer over the same identity model.

## 5. Deferred (separate ratify-points — do NOT fold into v1)

- **BOM-derived auto-default layout.** Tempting (reuse `BOMService`), but
  assumption-laden: it can only assign offsets keyed by **BOM-item id**, which is
  applicable **only if** the client's `component_ref == BOM-item id` — a
  convention the server can't guarantee — and the server cannot validate visual
  quality. If ever taken, it must be a trivial, clearly-labeled "starting-point
  guess" gated on that convention being real. **Defer.**
- **Multiple named explode presets per document.** Would justify a dedicated
  `meta_3d_explode_views` table (mirroring `meta_3d_overlays`, a migration). Only
  warranted on a **grounded** multiple-presets need — not assumed. **Defer.**

## 6. Persistence (ratify)

- **No migration in v1** — the explode config rides `meta_3d_overlays.properties`
  (existing JSON column). Migration-table-coverage contract unaffected.
- A dedicated nullable `explode_config` column on `meta_3d_overlays`, or a
  separate presets table, is a deferred clean-up if §5 materializes.

## 7. The `component_ref`↔node binding (LOCK — the lynchpin)

Every shape of G3 produces "offset per `component_ref`" that the **client** applies
to its geometry. This works **only** if `component_ref` maps to a viewer-addressable
node. Grounded: `component_ref` is opaque/client-defined (`resolve_component`
returns the raw row, no geometry handle). Therefore:

- The server stores offsets keyed by `component_ref` and guarantees nothing about
  their applicability; the **client owns** the `component_ref`↔node convention.
- Impl step-0 MUST re-confirm `resolve_component` still returns the opaque `hit`
  with no geometry/node field, and the taskbook/DEV doc MUST state this dependency
  explicitly (the way G4 §6 / G5 §9.5 named their load-bearing assumptions). If a
  geometry-node handle is later added to `part_refs`, G3's applicability story
  changes and this lock is revisited.

## 8. API surface (ratify)

Mirror the overlay router (`parallel_tasks_cad_3d_router.py`): an upsert + a get on
`/cad-3d/explode/{document_item_id}` (delete optional). Auth mirrors the overlay
endpoints (`get_current_user`, role visibility). Route count moves by the number
of routes added (§10).

## 9. Non-Goals

No server-side geometry / mesh / bbox / transform computation (CAD-core /
`cad_converter_service` / dedup-vision territory); no client-side rendering or
viewer; no BOM-derived auto-layout in v1; no multiple-named-presets table in v1;
no migration in v1; no change to the overlay/view-state behavior beyond adding the
explode key; no GPL/AGPL; no revision/version coupling.

## 10. Route-count + test wiring (per [[feedback-test-file-ci-wiring-fanout]])

- New explode routes → **+N**: full-tree `grep -rn 'len(app.routes)'` residual scan
  + bump all four 688 pins, AND extend the route set in
  `test_parallel_tasks_cad_3d_router_contracts.py` (which pins the `/cad-3d/*`
  routes at the router level).
- **Extend** `test_parallel_tasks_cad_3d_router_contracts.py` rather than add a new
  file. If a new test file is unavoidable, run the fan-out sweep
  (`glob("test_*")|_PORTFOLIO_|disk_contracts|ALLOWLIST`) and satisfy every match.

## 11. Step-0 to enter the IMPLEMENTATION

1. **Re-confirm §7**: `resolve_component(s)` returns the opaque `hit` (no geometry
   node handle); `component_ref` is client-defined.
2. Confirm the `meta_3d_overlays.properties` storage path + `ThreeDOverlayService`
   upsert/cache/visibility seam to reuse (no migration).
3. If the BOM-default (§5) is opted later, ground `BOMService.get_bom_structure`
   and the `component_ref == BOM-item-id` assumption explicitly.
4. Test wiring (§10): extend the cad-3d contracts test; route-count pins.

## 12. Preconditions to enter the IMPLEMENTATION

1. §3 honest scope (thin validated explode-config; geometry/rendering client-side)
   ratified;
2. §4 v1 shape (single config per document, riding `meta_3d_overlays`, validated)
   ratified;
3. §5 deferrals (BOM-auto-layout, multiple-preset table) accepted;
4. §6 no-migration + §7 `component_ref` binding LOCK acknowledged;
5. §8 API surface ratified; §9 non-goals ratified; §10 route-count/test discipline
   acknowledged.

A **separate explicit opt-in** then authorizes the implementation.

## 13. Reviewer Focus

1. §2/§3 — is the "thin server surface, geometry/rendering client-side" conclusion
   correct and honest (not under- or over-built)?
2. §7 — `component_ref` opaque/client-defined binding lock right?
3. §4/§6 — single validated config riding `meta_3d_overlays`, no migration?
4. §5 — BOM-auto-layout + multiple-preset table correctly deferred (assumption-
   laden / ungrounded for v1)?
5. §9 — server geometry/mesh + client rendering + GPL/AGPL stay OUT?

## 14. Status

Doc-only grounding + scope-lock. Ready for review once the doc exists at the
canonical path; `DELIVERY_DOC_INDEX.md` references it + its DEV/verification record
(sorted under `## Development & Verification`); doc-index / sorting / completeness
checks pass; `git diff --check` clean. Ratifying §3–§9 sets the (thin) explode
implementation plan; **a separate explicit opt-in authorizes the implementation.**
The remaining minor OdooPLM gaps (finishing/treatment, `plm_project`) stay
separately-opted.
