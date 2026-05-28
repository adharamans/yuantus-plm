# DEV & Verification: CAD Helper Bridge — Native-CAD Operational Signoff Runbook (R1)

Date: 2026-05-27

Records the doc-only delivery of
`CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md` — the
operator evidence checklist that converts the deferred native-CAD operational
signoff of the CAD helper bridge last-mile into an executable runbook. This PR
is **doc-only**: it changes no code, no installer, no helper route, and it does
**not** collect or assert native-CAD evidence.

## 1. What changed

- New `docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md`
  — purpose, evidence-archive convention, preflight, eight command rows (six
  commands + the CHECKIN/BOM_IMPORT negatives), failure classification, and a
  per-host operator summary table.
- This DEV/verification record.
- Two sorted entries in `docs/DELIVERY_DOC_INDEX.md`.

## 2. Scope / boundaries

- Binds to the last-mile merged at `main` baseline `befd519d`: G1-A/B/C helper
  routes + Slice A/B/C in-CAD commands + the original S10 `YUANTUS_DIFF_PREVIEW`.
- Covers AutoCAD 2018/2024, ZWCAD 2025, GstarCAD 2025 and all six commands.
- **No** code / installer / helper-route / `.lsp` change.
- The runbook PR is **not** native-CAD evidence; it is the entry point for a
  later, real-host evidence/signoff PR.

## 3. Key contract points captured

- **Per-command audit-evidence boundary**: only `YUANTUS_DIFF_PREVIEW` emits a
  helper `/audit/apply-result` (`not-applied-display-only`) row; the
  workflow/upload commands do not — their evidence is PLM/backend effect, helper
  transcript, and `cad_bom` job/status.
- **Upload path is a confirmation, not a likely failure**: the LISP binds the
  upload filepath to `DWGPREFIX + DWGNAME` and a static guard enforces it. The
  runbook uses an ASCII-safe test drawing and asks the operator to confirm
  server-side file metadata (filename, and size/checksum where exposed) against
  the currently open DWG evidence; a contradiction is a blocker.
- **Fail-closed save model** is exercised for both `CHECKIN` and `BOM_IMPORT`
  negatives (dirty `DBMOD ≠ 0` → save-first notice, no upload).

## 4. Naming / indexing

The runbook uses the `..._RUNBOOK_<date>.md` **suffix** form, matching the
existing R3 CAD runbooks (`CAD_HELPER_BRIDGE_R3_*_RUNBOOK_*.md`). These are
**not** matched by the `docs/RUNBOOK_*.md` **prefix** glob in
`test_all_runbooks_are_indexed_in_readme_and_delivery_doc_index`, so — like the
R3 runbooks — they are indexed in `DELIVERY_DOC_INDEX.md` only and are **not**
required in the README `## Runbooks` section. Both new docs are added to
`DELIVERY_DOC_INDEX.md` in sorted order.

## 5. Verification (this doc-only PR)

- `python3 -m pytest -q` over the doc-contract suite (delivery-doc-index
  references + sorting, README runbook indexing, runbook-index completeness,
  DEV/verification index completeness + sorting, claude-assist discipline, p6
  plan gate, doc-index sorting) — pass.
- `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` → 28 guards
  pass (unchanged — no `.lsp` change).
- `python3 clients/cad-desktop-helper/verify_bridge_static.py` → 13 pass;
  `python3 clients/autocad-material-sync/verify_material_sync_static.py` → pass.
- `git diff --check` → clean.

## 6. Status

Doc-only; native-CAD evidence **not** collected. The runbook is the executable
entry point for the real-host signoff; a separate evidence PR asserts
"native-CAD operational signoff completed" once the §6 summary table is filled
and artifacts are archived. With that, the CAD helper bridge last-mile is closed
end to end (static contracts in CI + operational signoff on real hosts).
