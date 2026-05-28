# DEV & Verification: PLM→ERP Publication Contract — R1-A Taskbook

Date: 2026-05-27

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R1A_TASKBOOK_20260527.md` — the
contract-lock taskbook for the PLM→ERP publication-readiness API. Doc-only: no
code, no API; it pins the contract R1-B will implement. Baseline `main =
ec7daa87`.

## 1. What changed

- New `DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R1A_TASKBOOK_20260527.md`
  (eligibility formula, esign predicate, blocking_reasons taxonomy, minimal
  payload schema with field→source mapping, parameter contract, R1-B API
  boundary / exception-chaining / test catalog, guard surface).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries.

## 2. Grounding verified (against `main ec7daa87`)

The two review findings were grounded against real code before drafting:

- **esign predicate** — `esign/service.py` `get_manifest_status` returns `None`
  or `{manifest_id, item_id, generation, is_complete, completed_at,
  requirements}`. There is **no `status` field**; the field is `is_complete`.
  `web/release_orchestration_router.py` `_plan_steps` computes `esign_incomplete
  = isinstance(dict) AND "is_complete" in manifest AND not bool(is_complete)`.
  The taskbook §3 **mirrors this exactly** (None and missing-`is_complete` do not
  block); "missing manifest blocks" is flagged as a NEW semantic requiring
  explicit declaration.
- **version payload** — `models/item.py:52,102` (`Item.current_version_id` /
  `Item.current_version`) + `version/models.py` `ItemVersion` columns
  (`id`, `generation`, `revision`, `version_label`, `state`, `is_current`,
  `is_released`, `released_at`, `primary_file_id`). The taskbook §5 splits the
  payload into `item{}` + `version{}` (version sourced from `Item.current_version`)
  and flags that `version.generation` is **`ItemVersion.generation`**, distinct
  from `Item.generation` (the earlier draft conflated them).
- **file_refs source** — `version/models.py`: the `VersionFile` columns
  `file_id`, `file_role`, `is_primary`, `sequence`, `snapshot_path` (+
  `ItemVersion.primary_file_id`). The taskbook §5 pins `file_refs[]` to the
  `version{}` version's `version_files` with exactly these columns (no new file
  model).
- **readiness wrap** — `services/release_readiness_service.py`
  `get_item_release_readiness` returns `summary{ok,…}` + `resources[]{kind,errors,
  warnings}` + `esign_manifest`; the taskbook wraps it (no re-derivation).

## 3. Scope / boundaries

Doc-only contract lock. No purchase/sale surface, no real-ERP connection, no Odoo
runtime dependency, no GPL/AGPL reuse, no bypass of latest-released / suspended /
release-readiness, no R1-B implementation (separate opt-in).

## 4. Verification (this doc-only PR)

- doc-contract pytests (delivery-doc-index references + sorting, runbook-index
  completeness, DEV/verification index completeness + sorting, claude-assist
  discipline, p6 plan gate, doc-index sorting) — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only contract lock delivered. Committed locally and **not pushed** — pending
review. R1-B (the read-only `publication-readiness` API; Python/FastAPI, locally
testable, no Windows-CI gate) needs its own explicit opt-in.
