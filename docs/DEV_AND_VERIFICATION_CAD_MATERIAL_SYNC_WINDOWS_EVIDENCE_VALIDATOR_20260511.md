# Dev & Verification - CAD Material Sync Windows Evidence Validator

Date: 2026-05-11

Path:
`docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_WINDOWS_EVIDENCE_VALIDATOR_20260511.md`

## 1. Summary

Added a local evidence-shape validator for future CAD Material Sync Windows +
AutoCAD validation records. It now also gates the `PLMMATASSIST` manual
evidence added after the CAD material assistant Phase 3 command implementation.

The validator does not run AutoCAD and does not create acceptance evidence. It
only checks that an operator-filled markdown record is complete enough for
review and does not silently accept placeholders, mock evidence, synthetic
evidence, or plaintext secrets.

## 2. Delivered

- `scripts/validate_cad_material_windows_evidence.py`
- `docs/DELIVERY_SCRIPTS_INDEX_20260202.md` discoverability entry
- `docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md`
  pre-review command section
- Contract coverage in
  `src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py`
- `docs/DELIVERY_DOC_INDEX.md` entry for this record

## 3. Design

The validator is intentionally shape-only:

- It requires the AutoCAD 2018 primary baseline: `AutoCAD primary version:
  2018` and `AutoCAD ACADVER output: R22.0`.
- It requires filled preflight, build, load, command smoke, diff-preview,
  real write, save/reopen, `PLMMATASSIST`, reviewer, and decision fields.
- It requires `PLMMATASSIST` evidence for resolve endpoint observation,
  cancel/no-write, Enter-default-No, explicit create, lifecycle/Draft check, and
  no DWG write-back after create.
- It requires the acceptance fields for AutoCAD 2018, real DWG write-back, and
  Windows runtime to be `yes`, with `Decision: accept`.
- It allows AutoCAD 2024 regression to remain `no` by default, but if the
  record claims `AutoCAD 2024 regression complete: yes` then the 2024 evidence
  fields become required.
- `--require-2024` can force the 2024 regression section when a reviewer wants
  one-command validation for both pending TODO items.

## 4. Scope Controls

- No runtime code changes.
- No AutoCAD client source changes.
- No generated DLL or binary artifacts.
- No Windows evidence fabrication.
- No Phase 5 implementation.
- No P3.4 evidence creation or acceptance.
- No production cutover.

## 5. Verification Commands

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_scripts_index_entries_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m py_compile \
  scripts/validate_cad_material_windows_evidence.py \
  src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py

git diff --check
```

## 6. Verification Results

- External validation contract: 10 passed.
- Focused script/doc-index suite: rerun when this documentation refresh is
  merged; the validator/test pair is the authoritative gate for the new
  `PLMMATASSIST` fields.
- `py_compile` on validator and contract: passed.
- `git diff --check`: clean.

## 7. Reviewer Checklist

- Confirm the script rejects the blank template.
- Confirm the script accepts a filled minimal AutoCAD 2018 evidence shape only
  when `PLMMATASSIST` runtime evidence is present.
- Confirm the script rejects old evidence records that omit `PLMMATASSIST`
  resolve/create/no-write evidence.
- Confirm 2024 evidence is optional unless explicitly claimed or
  `--require-2024` is used.
- Confirm no runtime, AutoCAD source, binary, Phase 5, P3.4, or cutover changes
  are included.
