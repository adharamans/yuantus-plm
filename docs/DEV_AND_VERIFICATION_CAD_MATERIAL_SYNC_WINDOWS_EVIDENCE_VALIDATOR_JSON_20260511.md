# Dev & Verification - CAD Material Sync Windows Evidence Validator JSON

Date: 2026-05-11

Path:
`docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_WINDOWS_EVIDENCE_VALIDATOR_JSON_20260511.md`

## 1. Summary

Added `--json` output to the CAD Material Sync Windows evidence validator. The
same JSON mode now reports the expanded `PLMMATASSIST` evidence gate without
echoing raw operator evidence values.

The JSON output is intended for future operator automation and CI-style
pre-review checks. It reports only validator status and field-level failure
messages, not raw evidence field values.

## 2. Delivered

- `scripts/validate_cad_material_windows_evidence.py --json`
- Template guidance in
  `docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md`
- Delivery scripts index wording for `--json`
- Contract coverage in
  `src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py`
- `docs/DELIVERY_DOC_INDEX.md` entry for this record

## 3. Design

The JSON report has schema version `1` and these fields:

- `schema_version`
- `ok`
- `evidence`
- `require_2024`
- `failure_count`
- `failures`

The failure list uses the same field-level messages as text mode. It does not
include original evidence values, so a detected token/password is reported as a
field-name problem rather than echoing the secret-bearing value.

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

.venv/bin/python scripts/validate_cad_material_windows_evidence.py --json

git diff --check
```

## 6. Verification Results

- External validation contract plus focused script/doc-index suite: 15 passed.
- `py_compile` on validator and contract: passed.
- CLI `--json` smoke: passed; the blank template returns exit code 1 as
  expected and emits parseable JSON.
- `git diff --check`: clean.

## 7. Reviewer Checklist

- Confirm JSON output does not echo secret-bearing field values.
- Confirm JSON failure messages remain field-level and reviewable.
- Confirm missing `PLMMATASSIST` fields are reported as field-level failures
  without echoing evidence values.
- Confirm text-mode behavior remains unchanged.
- Confirm no runtime, AutoCAD source, binary, Phase 5, P3.4, or cutover changes
  are included.
