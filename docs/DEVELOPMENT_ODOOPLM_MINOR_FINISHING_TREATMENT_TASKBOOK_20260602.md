# Claude Taskbook: OdooPLM Minor Gap — Finishing / Treatment Process Attributes

Date: 2026-06-02

Type: **Doc-only grounding + scope-lock taskbook.** It grounds the remaining
minor OdooPLM gap for finishing / treatment process attributes against current
`main`, defines a deliberately small R1 surface, and authorizes no
implementation. **Merging this taskbook does NOT authorize code changes**; an R1
implementation requires a separate explicit opt-in.

Origin:

- `DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` §2 maps
  `plm_material` / `plm_finishing` / `plm_treatment` to Yuantus material and CAD
  material-sync capabilities, but marks finishing / treatment as partial.
- The same document §5 keeps this as a **minor** gap: `finishing` -> 0,
  `treatment` -> 1 file at the time of the evidence snapshot.
- `DEVELOPMENT_ODOOPLM_GAP_PROGRAM_CLOSEOUT_20260602.md` leaves
  finishing/treatment as an unstarted product-priority decision after the
  code-closable G2/G3/G4/G5 lines closed.

Baseline: current `main = 0f047ee2` after #688.

## 0. What this is

This is a **minor-gap** taskbook. It should not reopen the OdooPLM parity program
or create a new business subsystem by inertia. The goal is to decide whether
Yuantus needs a small, explicit process-attribute vocabulary for surface finish
and heat treatment, and if so, where it should live.

The grounding below shows this is **not** a missing-storage problem. Yuantus
already has:

- dynamic `Item.properties`;
- CAD file properties;
- CAD material sync profiles;
- manufacturing routing operations with a treatment operation type;
- extensible raw material / MBOM / operation properties.

The actual gap is narrower: **there is no first-class, cross-surface canonical
contract for finishing / treatment fields**. Today these values appear as
free-form examples or as heat-treatment-only CAD sync fields.

## 1. Grounding Facts

### 1.A Item and property model already carry process attributes

- `Item.properties` is JSON-backed dynamic data
  (`src/yuantus/meta_engine/models/item.py:86-91`) and is merged into the public
  item dictionary (`item.py:106-128`).
- `Property` supports metadata (`data_type`, `length`, `is_required`,
  `default_value`, `ui_type`, `ui_options`, `is_cad_synced`,
  `default_value_expression`, `data_source_id`) without adding a new process
  table (`src/yuantus/meta_engine/models/meta_schema.py:83-122`).

Conclusion: R1 does **not** need a new SQL table or a new `Property` model
column to store finishing / treatment fields.

### 1.B CAD properties already accept arbitrary finish-like keys

`cad_properties_router` exposes:

- `GET /cad/files/{file_id}/properties`
- `PATCH /cad/files/{file_id}/properties`

and stores the incoming property dictionary directly on `FileContainer`
(`src/yuantus/meta_engine/web/cad_properties_router.py:31-87`). Existing tests
already use `{"finish": "hard-anodized"}` as a CAD file property example
(`src/yuantus/meta_engine/tests/test_cad_properties_router.py:117-142`).

Conclusion: R1 should not add another CAD-property route. If a field is needed,
the missing part is canonical naming and sync profile coverage, not another
endpoint.

### 1.C CAD material sync already has the right extension point

`plugins/yuantus-cad-material-sync/main.py` defines default material profiles:

- sheet/tube/bar profiles have material + dimensions but no `finish`
  (`main.py:29-186`);
- the forging profile already has optional `heat_treatment` and maps it to
  `"热处理"` (`main.py:187-230`);
- `load_profiles` deep-merges tenant/plugin config over defaults
  (`main.py:1085-1115`);
- `compose_profile` validates values and renders composed fields
  (`main.py:1618-1655`);
- `cad_field_package` maps normalized properties back to CAD keys
  (`main.py:1658-1688`);
- `validate_profile_values` already supports `required_when` and `enum`
  validation (`main.py:1221-1228`, `main.py:1315-1370`).

Existing tests prove contextual standards are already possible:
`heat_treatment_standard` can be required only when `heat_treatment` exists
(`src/yuantus/meta_engine/tests/test_plugin_cad_material_sync.py:1241-1291`).

Conclusion: R1 should reuse the profile system. It can declare canonical fields
and tests without introducing a separate process-attribute service.

### 1.D CAD clients already know `heat_treatment`, but not `finish`

Grounded mapper evidence:

- SolidWorks maps `SW-HeatTreatment` / `HeatTreatment` to `heat_treatment`
  (`clients/solidworks-material-sync/SolidWorksMaterialSync/SolidWorksMaterialFieldMapper.cs:40-41`).
- AutoCAD material sync maps `热处理`, `heattreatment`, `heat_treatment` to
  `heat_treatment`
  (`clients/autocad-material-sync/CADDedupPlugin/CadMaterialFieldMapper.cs:58-60`).
- SolidWorks fixture extraction includes `SW-HeatTreatment@Part=none` and
  expects `heat_treatment: "none"`
  (`docs/samples/cad_material_solidworks_fixture.json`).

By contrast, `finish` exists in CAD-property examples, and `SW-Coating@Part`
appears in diff-confirm fixture/tests, but there is no SDK-free mapper entry that
canonicalizes `SW-Coating` / `Coating` / `Finish` into `finish`.

Conclusion: the real R1 client-side gap is **finish/coating alias coverage**,
not heat-treatment coverage.

### 1.E Manufacturing already has treatment as an operation type

`OperationType.TREATMENT = "treatment"` exists
(`src/yuantus/meta_engine/manufacturing/models.py:33-38`), and
`Operation.properties` is JSON-backed (`manufacturing/models.py:118-150`).
MBOM lines also carry `properties` (`manufacturing/models.py:65-85`).

Conclusion: do not overload a generic item property called `treatment` in R1.
Use `heat_treatment` for the part/material attribute and reserve
`operation_type="treatment"` for routing/process steps.

### 1.F Raw material already has extension storage

`RawMaterial` has `material_type`, `grade`, dimensions, stock/cost fields, a
product link, and JSON `properties`
(`src/yuantus/meta_engine/cutted_parts/models.py:65-95`).

Conclusion: finishing/treatment metadata can be attached to raw material or part
properties without a schema migration if product scope later requires it.

## 2. Gap Reframed

The original "finishing/treatment" minor gap is **not**:

- missing item storage;
- missing CAD file property storage;
- missing heat-treatment CAD extraction;
- missing manufacturing support for treatment operations.

It is:

> missing **canonical process-attribute vocabulary + profile coverage** across
> item properties, CAD material sync, and CAD client field aliases.

That is a small implementation, but it should still be scope-locked because
field naming is sticky and cross-client aliases drift easily.

## 3. Canonical Vocabulary (ratify)

R1 canonical field names:

| Concept | Canonical key | Rationale |
|---|---|---|
| Surface finish / coating / finishing | `finish` | Already used in CAD properties tests; short, existing local vocabulary. |
| Heat treatment | `heat_treatment` | Already used in SolidWorks and AutoCAD material-sync mappers. |
| Surface finish standard/spec | `finish_standard` | Optional companion field, profile-configured only. |
| Heat treatment standard/spec | `heat_treatment_standard` | Already proven by `required_when` tests as a contextual companion field. |

Rejected names:

- `finishing`: closer to odooplm module naming, but absent from local code and
  worse as a property key than the already-used `finish`.
- generic `treatment`: conflicts conceptually with manufacturing
  `OperationType.TREATMENT`.
- `surface_treatment`: acceptable domain wording, but R1 should not introduce a
  second synonym when `finish` already exists locally.

## 4. Recommended R1 Shape (ratify)

**Recommendation: implement R1 as CAD material-sync profile vocabulary + mapper
coverage, with no new route, no migration, and no new service.**

R1 implementation, if authorized, should:

1. Add optional `finish` field coverage to the material-sync default profiles
   where applicable (at least sheet/tube/bar/forging), with CAD key aliases that
   can package back to CAD.
2. Preserve the existing optional `heat_treatment` field on forging and decide
   whether heat treatment should remain forging-only or become opt-in for other
   profiles via config. Do **not** silently make it universally required.
3. Add mapper aliases:
   - SolidWorks: `SW-Coating`, `Coating`, `SW-Finish`, `Finish` -> `finish`;
   - AutoCAD: `表面处理`, `表面`, `涂层`, `finish`, `coating` -> `finish`.
4. Keep standards as companion profile fields:
   - `finish_standard` can be required via `required_when: {"field": "finish",
     "exists": true}`;
   - `heat_treatment_standard` keeps the existing pattern.
5. If a tenant needs a finite value list, use the existing profile field `enum`
   validation; do not add a master-data table in R1.

## 5. Non-Goals

- No SQL migration, no new table, no new route, no route-count pin movement.
- No `Property` model changes.
- No new Odoo/OdooPLM code reuse; semantics only.
- No ERP purchase/sale/inventory meaning for finish/treatment.
- No routing scheduler or work-order behavior. `operation_type="treatment"` stays
  a manufacturing operation type, not a part-property synonym.
- No UI rebuild; if UI exposure is needed later, it starts from a separate
  product taskbook.

## 6. R1 Test / Guard Checklist (implementation must satisfy each)

Use existing test files where possible to avoid CI fan-out:

1. `test_plugin_cad_material_sync.py`
   - `finish` is accepted by a profile and included in `cad_field_package` when
     non-empty;
   - blank `finish` is omitted by default (existing `include_empty=False`
     behavior);
   - profile `enum` rejects an unmapped `finish` value with `invalid_enum`;
   - `finish_standard` can be required when `finish` exists;
   - existing `heat_treatment` / `heat_treatment_standard` tests remain green.
2. SolidWorks mapper tests
   - `SW-Coating@Part` / `Coating` / `Finish` canonicalize to `finish`;
   - existing `SW-HeatTreatment` -> `heat_treatment` behavior remains green.
3. AutoCAD material-sync mapper tests
   - `表面处理` / `涂层` / `finish` / `coating` canonicalize to `finish`;
   - existing `热处理` -> `heat_treatment` remains green.
4. Static / residual checks
   - no new migration file;
   - no new router or `include_router`;
   - no route-count pin changes;
   - no GPL/AGPL/Odoo code import or copied snippet.

If implementation touches C# client mapper code, the PR body must explicitly
state that C# build/xUnit verification is deferred to the relevant GitHub CI gate
unless the local Windows toolchain is available.

## 7. Open Decisions Before R1 Implementation

1. **Profile reach:** should `finish` be added to all default profiles, or only
   sheet/forging first? Recommendation: add to all material profiles as optional,
   because finish/coating is not limited to sheet.
2. **Heat-treatment reach:** should `heat_treatment` stay forging-only by default?
   Recommendation: keep forging-only in defaults; tenants can enable it elsewhere
   through profile config until a product need says otherwise.
3. **Enum policy:** should R1 ship any built-in enum values? Recommendation: no.
   R1 should prove `enum` enforcement works, but built-in finish/treatment lists
   are tenant/process-specific and should live in plugin config.

## 8. Step-0 to Enter Implementation

1. Reconfirm all file/line anchors in §1 against current `main`.
2. Reconfirm `validate_profile_values` still enforces `enum` and
   `required_when`.
3. Reconfirm mapper test locations and whether adding mapper aliases creates new
   CI wiring requirements.
4. Run residual scans before commit:
   - no migration added;
   - no route-count changes;
   - no new test file unless intentionally wired into CI.

## 9. Reviewer Focus

1. Is `finish` + `heat_treatment` the right local canonical vocabulary?
2. Is the recommendation to reuse CAD material-sync profiles enough for R1, or
   does product need a first-class process-attribute service?
3. Should `finish` be optional across all default profiles?
4. Should `heat_treatment` remain forging-only by default?
5. Are built-in enums correctly deferred to tenant/plugin config?

## 10. Status

Doc-only grounding + scope-lock. Ready for review once this file and its
DEV/verification record are referenced in `DELIVERY_DOC_INDEX.md` and doc-index
checks pass. Ratifying §3-§7 sets the implementation plan; **a separate explicit
opt-in authorizes R1 implementation**.
