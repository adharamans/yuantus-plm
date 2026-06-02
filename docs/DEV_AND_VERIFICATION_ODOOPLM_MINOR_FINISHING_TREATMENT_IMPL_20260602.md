# DEV & Verification: OdooPLM Minor Gap — Finishing/Treatment R1 Implementation

Date: 2026-06-02

Records the **R1 implementation** of the finishing/treatment minor gap, per the
merged scope-lock taskbook
`DEVELOPMENT_ODOOPLM_MINOR_FINISHING_TREATMENT_TASKBOOK_20260602.md` (#689).
Baseline `main = f81d2218`. Reuses the existing CAD material-sync profile +
mapper extension points — **no migration, no route, no table, no service, no
`Property`-model change**; route count unchanged (691).

## 1. Step-0 grounding re-confirmed (taskbook §8)

- The 4 default profiles (sheet/tube/bar/forging) + the field schema
  (`name/label/type/required/cad_key/[enum]/[required_when]`); `cad_field_package`
  maps a field's `cad_key` even without a profile `cad_mapping` entry.
- `validate_profile_values` still enforces `enum` (`invalid_enum`) and
  `required_when` (`_condition_matches` `{field, exists}`).
- C# mapper dicts: AutoCAD lowercases keys; SolidWorks uses `SW-`/PascalCase.

## 2. What changed

- `plugins/yuantus-cad-material-sync/main.py` — inject an **optional `finish`
  field** (`cad_key="表面处理"`) into **every** default profile after the
  `DEFAULT_PROFILES` literal. Packaged back via the field-level `cad_key` (no
  per-profile `cad_mapping` edit). `heat_treatment` stays forging-only;
  `finish_standard` is a tenant/profile companion (via `required_when`), not
  baked in.
- `clients/autocad-material-sync/CADDedupPlugin/CadMaterialFieldMapper.cs` — add
  `表面处理`/`涂层`/`finish`/`coating` → `finish`. **Deliberately NOT the bare
  `表面`** alias (too broad; per the #689 review).
- `clients/solidworks-material-sync/.../SolidWorksMaterialFieldMapper.cs` — add
  `SW-Coating`/`Coating`/`SW-Finish`/`Finish` → `finish`.
- `clients/autocad-material-sync/verify_material_sync_static.py` — new
  `check_finish_treatment_aliases` static guard: the AutoCAD finish aliases are
  present, `heat_treatment` remains, and bare `表面` is **absent**.
- AutoCAD and SolidWorks SDK-free fixtures now prove finish extraction:
  `涂层` / `SW-Coating@Part` canonicalize to `finish`.
- `src/yuantus/meta_engine/tests/test_plugin_cad_material_sync.py` — 4 new tests.

## 3. Verification

- `test_plugin_cad_material_sync.py` — **45 passed** (existing coverage +
  4 new per the §6 checklist):
  - `finish` is optional on all 4 profiles and packages to `表面处理` when
    non-empty; blank `finish` omitted by default; profile `enum` rejects an
    unmapped `finish` (`invalid_enum`); `finish_standard` required **only when**
    `finish` exists.
- SolidWorks client/fixture contracts — **10 passed**.
- AutoCAD material-sync fixture — pass.
- SolidWorks SDK-free fixture — pass.
- `verify_material_sync_static.py` — pass (incl. the new finish-alias guard).
- `verify_lisp_shell_static.py` — **28 passed**.
- `verify_bridge_static.py` — **13 passed**.
- doc-index family — **11 passed**.
- `git diff --check` clean.
- Route surface unchanged by source diff: no router/app files touched, no route
  count pins moved, no migration file added. No new test file was added, so no
  conftest allowlist / ci.yml / portfolio fan-out.

## 4. C# build/test coverage (honest)

Local C# compilation was not run in this environment. The source-level and
fixture-level checks above pin the mapper semantics. Any C# build/xUnit coverage
for AutoCAD / SolidWorks mapper source is deferred to the relevant GitHub CI or a
Windows-capable toolchain; the PR body must not imply local C# compilation.

## 5. Non-Goals upheld

No SQL migration / table / route / route-count pins; no `Property`-model change;
`operation_type="treatment"` untouched (manufacturing, not a part property); no
built-in enum values (tenant/plugin config); no Odoo/OdooPLM code reuse; no UI.

## 6. Status

Finishing/treatment R1 implemented — canonical `finish`/`heat_treatment`
vocabulary + CAD mapper aliases on the existing profile system. This closes the
finishing/treatment minor gap. The remaining OdooPLM minor gap (`plm_project`) is
separately-opted; the SolidWorks-mapper CI-gate gap is a noted follow-up.
