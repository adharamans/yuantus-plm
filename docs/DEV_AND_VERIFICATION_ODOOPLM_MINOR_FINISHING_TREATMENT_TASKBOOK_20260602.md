# DEV / Verification — OdooPLM Minor Gap Finishing / Treatment Taskbook

Date: 2026-06-02

Scope: **doc-only grounding + scope-lock** for the OdooPLM minor
finishing/treatment process-attribute gap. No code, routes, migrations, models,
or tests were changed in this slice.

Branch: `docs/odooplm-finishing-treatment-taskbook-20260602`

## Grounding Performed

- Re-read `DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` and
  `DEVELOPMENT_ODOOPLM_GAP_PROGRAM_CLOSEOUT_20260602.md` to confirm the gap is
  still product-priority / minor, not part of the already-closed G2/G3/G4/G5
  lines.
- Searched current `main` for finishing/treatment/coating/heat-treatment
  evidence across `docs`, `src`, `plugins`, and `clients`.
- Grounded storage and extension surfaces:
  - `Item.properties`;
  - `Property` metadata;
  - CAD file properties route;
  - CAD material-sync default profiles and profile config override path;
  - CAD material-sync `enum` / `required_when` validation;
  - SolidWorks and AutoCAD material field mappers;
  - manufacturing `OperationType.TREATMENT`;
  - raw-material `properties`.

## Contract Locked

The taskbook recommends a narrow R1:

- canonical keys: `finish`, `heat_treatment`, `finish_standard`,
  `heat_treatment_standard`;
- implement through CAD material-sync profile vocabulary + mapper aliases;
- no new table, migration, route, route-count movement, `Property` model change,
  or UI;
- built-in enum values deferred to tenant/plugin config.

## Verification

Local verification for this doc-only slice:

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index*.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py
python3 clients/cad-desktop-helper/verify_lisp_shell_static.py
python3 clients/cad-desktop-helper/verify_bridge_static.py
python3 clients/autocad-material-sync/verify_material_sync_static.py
git diff --check
```

Result:

- doc-index family: 11 passed;
- `verify_lisp_shell_static.py`: 28 passed;
- `verify_bridge_static.py`: 13 passed;
- `verify_material_sync_static.py`: passed;
- `git diff --check`: clean.

Because this slice is doc-only, no Python service or C# build tests are required.

## Non-Claims

This taskbook does not claim finishing/treatment implementation is complete. It
only locks a grounded R1 plan. Implementation requires a separate explicit
opt-in and must satisfy the taskbook's R1 checklist.
