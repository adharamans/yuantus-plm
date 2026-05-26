# CAD Helper Bridge G1-C — BOM Import R1 (Development And Verification)

Date: 2026-05-26

Implements the taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_C_BOM_IMPORT_20260526.md`
(merged at `debdf286` / #650). Grounded against `origin/main = debdf286`.
**Path A** (ratified in the decision memo #649): reuse the async `/cad/import`
pipeline; server-side BOM extraction; no client assembly walker.

## 1. Scope Delivered

One helper route `POST /document/bom-import` (multipart); helper route count
**14 → 15**. It uploads the saved CAD file to backend `POST /cad/import` with
`create_bom_job=true` and returns the `cad_bom` job handle + `file_id`.

- **Seam reuse + extension**: `IPlmBusinessClient.PostMultipartAsync` gains an
  `IDictionary<string,string> formFields` parameter (before `cancellationToken`,
  so `cancellationToken` stays last). Each field is added as a `StringContent`
  part. **G1-B is behaviorally unchanged** — its `/document/checkin` call passes
  `null` formFields (pinned by the existing G1-B tests).
- **Root-item policy (§4.A)**: `DocumentBomImportAsync` forwards `item_id` when
  present; otherwise forwards `auto_create_part=true` (never omits both — the
  `cad_bom` worker requires a root item).
- **Response shaping (§4.C)**: parses `CadImportResponse`, selects the job with
  `task_type == "cad_bom"`, returns `{ file_id, job, cad_bom_url? }`; if no
  `cad_bom` job is returned, maps to `PLM_VALIDATION_FAILED` (no silent success).
- **Status (§4.D)**: client polls the existing `GET /api/v1/cad/files/{file_id}/bom`
  by `file_id` (readiness `job_status` + `bom`). **No** helper status-proxy route
  is added (count stays 14 → 15).
- Uniform `TryReadSession` gate (zero backend call on missing session); no audit;
  backend `/cad/import` and `/cad/files/{file_id}/bom` reused **unchanged**; no
  per-host assembly walker (server extracts).

## 2. Files Changed

- `clients/cad-desktop-helper/Helper/HelperRuntime.cs` —
  `IPlmBusinessClient.PostMultipartAsync` + `HttpPlmBusinessClient` impl gain
  `formFields`; `DocumentBomImportAsync` + `SelectCadBomJob`; `POST
  /document/bom-import` multipart route; G1-B checkin call passes `null` formFields.
- Tests: `Helper.Tests/G1CDocumentBomImportContractTests.cs` (new);
  `Helper.Tests/{G1ADocumentLockRoutes,G1BDocumentCheckin,HelperBusinessAudit}ContractTests.cs`
  — the three recording fakes add the `formFields` parameter to satisfy the
  interface.
- Route-count 14 → 15 + `/document/bom-import` asserted across:
  `verify_bridge_static.py` (+ new `check_g1c_document_bom_import_path_a_no_walker_no_local_read`
  guard), `verify_lisp_shell_static.py`, `verify_material_sync_static.py`,
  `Helper.Tests/{HelperBusinessAudit,HelperSession,HelperReset}ContractTests.cs`,
  `Bridge.Tests/BridgeContractTests.cs`,
  `CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs`.
- `docs/` — this DEV doc + `DELIVERY_DOC_INDEX.md` line.

## 3. Mandatory Tests (taskbook §6)

xUnit `G1CDocumentBomImportContractTests` (**added, not locally executed** — §6):
- `test_g1c_bom_import_requires_plm_session_before_backend_call` (zero backend calls);
- `test_g1c_bom_import_requires_file`;
- `test_g1c_bom_import_forwards_multipart_to_cad_import_with_create_bom_job` (seam, not JSON PostAsync);
- `test_g1c_bom_import_root_item_policy_item_id_or_auto_create` (§4.A);
- `test_g1c_bom_import_returns_cad_bom_job_handle_and_file_id` (§4.C);
- `test_g1c_bom_import_errors_when_no_cad_bom_job_returned`.

Static (Python, **run locally**) — every §6 static guard is a real verifier
check, not documentation:
- route count **== 15** + `/document/bom-import` present + dedup absent
  (`verify_material_sync_static.py`, the two route-count guards);
- `check_g1c_document_bom_import_path_a_no_walker_no_local_read`
  (`verify_bridge_static.py`): bom-import route uses `ReadFormAsync`/`CopyToAsync`,
  **no** local-file read, forwards to `/cad/import` with `create_bom_job`, and
  contains **no** client-side assembly-walker / tree-builder tokens.

Backend-unmodified (§6.C) is a diff property: the PR touches no
`web/cad_import_router.py` or `web/cad_file_data_router.py` (verifiable in the diff).

## 4. Verification Scope

**Run locally (macOS, no .NET):**
- `verify_bridge_static.py` → **All 12 S9 bridge static guards passed** (route count 15 + G1-C no-walker/no-local-read guard).
- `verify_lisp_shell_static.py` → OK (route count 15).
- `verify_material_sync_static.py` → OK (count 15 + `/document/bom-import` present).
- doc-contract pytests → 32 passed.
- `git diff --check` → clean; full-tree residual `route-count == 14` scan → clean.

**C# build/xUnit deferred to Windows CI** (helper targets `net46`/`net6.0-windows`,
unavailable on macOS): `dotnet build` of `Helper` / `Helper.Tests` / `Bridge.Tests`
/ `CADDedupPlugin.Client.Tests`, and the §3 xUnit tests. This PR is considered
complete only after Windows CI build + tests pass.

## 5. Explicit Non-Goals (unchanged from taskbook §8)

No client tree payload / per-host assembly walker (Path A extracts server-side);
no server-side direct BOM route (that is Path B + its own server taskbook); no
helper status-proxy route; no backend route/auth change; no CAD-host command; no
local filesystem read; no audit; no S10 guard relaxation; no new `ErrorCodes`.
