# CAD Helper Bridge G1-B — Document Checkin Multipart R1 (Development And Verification)

Date: 2026-05-26

Implements the taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_G1_B_CHECKIN_MULTIPART_20260526.md`
(merged at `e4399da4` / #647). Grounded against `origin/main = e4399da4`.

## 1. Scope Delivered

One helper route, `POST /document/checkin` (multipart upload bridge); helper
route count **13 → 14**. It uploads the **already-saved** file bytes and
proxies to the existing backend `POST /cad/{item_id}/checkin` (unchanged).

- **Multipart seam**: new `IPlmBusinessClient.PostMultipartAsync` (+
  `HttpPlmBusinessClient` impl) — `MultipartFormDataContent` with the file
  part named `file`; reuses bearer/protocol/trace headers; reads the
  `X-Quota-Warning` response header. JSON `PostAsync`/`GetAsync` unchanged.
- **Entry shape**: client → helper sends **multipart file bytes** (`item_id`
  form field, filename from the file part). The helper does **not** read the
  local filesystem (route uses `ReadFormAsync`; no `File.OpenRead`).
- **Uniform session gate**: `DocumentCheckinAsync` validates `item_id` + file
  + filename, then `TryReadSession`; a missing session short-circuits with
  `AuthPlmNotLoggedIn` and **zero** backend calls.
- **No audit** (pure proxy, taskbook §3.B).

## 2. Quota Mapping (taskbook §4.C — inside the fixed-200 envelope)

- **Hard** (`429` + `detail.code == QUOTA_EXCEEDED`): `PostMultipartAsync`
  parses the FastAPI `detail` object and returns
  `PlmBusinessResponse.Error(ErrorCodes.QuotaExceeded, …, detail)`.
  `DocumentCheckinAsync` maps it to `HelperRouteResult.Error("QUOTA_EXCEEDED",
  …, {"quota": detail})` → envelope `ok=false`, `error.code="QUOTA_EXCEEDED"`,
  `error.details.quota=<payload>` (**not** `PLM_VALIDATION_FAILED`).
- **Soft** (`200` + `X-Quota-Warning`): the seam captures the header and
  returns `PlmBusinessResponse.Success(data, warning)`; the service adds
  `data.quota_warning`.
- New helper constant `ErrorCodes.QuotaExceeded = "QUOTA_EXCEEDED"` (same wire
  string as the backend).

## 3. Envelope Extension (backward compatible — taskbook §7 / decision §7)

- `PlmBusinessResponse` gains `Details` (JToken) + `QuotaWarning` (string);
  existing `Success(data)` / `Error(code,message)` factories unchanged.
- `HelperRouteResult` gains `Details` (`Dictionary<string,object>`) + an
  `Error(code, message, details)` overload; existing factories unchanged.
- `HelperRouteResponse.WriteAsync` / `ToJson` now emit
  `Details = result.Details ?? new Dictionary<string,object>()`. **Existing
  error responses are unchanged**: with `Details == null` they still serialize
  `error.details == {}` (pinned by
  `test_g1b_existing_error_responses_still_emit_empty_details_object`).

## 4. Files Changed

- `clients/cad-desktop-helper/Shared/Transport/ErrorCodes.cs` — `QuotaExceeded`.
- `clients/cad-desktop-helper/Helper/HelperRuntime.cs` — `PlmBusinessResponse`
  + `HelperRouteResult` + `HelperRouteResponse` extensions;
  `IPlmBusinessClient.PostMultipartAsync` (+ impl + `ReadQuotaWarning`);
  `DocumentCheckinAsync`; `POST /document/checkin` multipart route.
- Tests: `Helper.Tests/G1BDocumentCheckinContractTests.cs` (new);
  `Helper.Tests/HelperBusinessAuditContractTests.cs` (count 14 + `/document/checkin`;
  `PostMultipartAsync` on the existing fake);
  `Helper.Tests/G1ADocumentLockRoutesContractTests.cs` (`PostMultipartAsync` on its fake);
  `Bridge.Tests/BridgeContractTests.cs` (count 14 + `/document/checkin`);
  `clients/autocad-material-sync/CADDedupPlugin.Client.Tests/MaterialSyncClientS8ContractTests.cs`
  (count 14 + `/document/checkin`).
- Route-count guards: `verify_bridge_static.py`, `verify_lisp_shell_static.py`,
  `verify_material_sync_static.py` (13 → 14; material-sync also asserts
  `/document/checkin`; bridge verifier also pins that `/document/checkin` uses
  multipart bytes and does not read a local file path).
- `docs/` — this DEV doc + `DELIVERY_DOC_INDEX.md` line.

## 5. Mandatory Tests (taskbook §6)

xUnit `G1BDocumentCheckinContractTests` (**added, not locally executed** — §6):
- `test_g1b_checkin_requires_plm_session_before_backend_call` (zero backend calls);
- `test_g1b_checkin_forwards_multipart_post_to_cad_checkin_with_bearer` (multipart seam, not JSON PostAsync);
- `test_g1b_checkin_requires_item_id_and_file`;
- `test_g1b_checkin_maps_hard_quota_429_to_quota_envelope_not_validation_failed`;
- `test_g1b_checkin_surfaces_soft_quota_warning_in_success_envelope`;
- `test_g1b_hard_quota_envelope_contains_error_details_quota` (wire shape via `ToJson`);
- `test_g1b_soft_quota_envelope_contains_data_quota_warning` (wire shape);
- `test_g1b_existing_error_responses_still_emit_empty_details_object` (backward compat).

Route-count == 14 + `/document/checkin` present is pinned by the three Python
verifiers and the C# count tests in `HelperBusinessAuditContractTests`,
`BridgeContractTests`, `MaterialSyncClientS8ContractTests`.

Note: the seam's HTTP-level 429-body / `X-Quota-Warning` parsing inside
`HttpPlmBusinessClient.PostMultipartAsync` is exercised by the build and is a
candidate for a later integration test; the service-level mapping
(`DocumentCheckinAsync`) is unit-pinned above by injecting a quota
`PlmBusinessResponse`.

## 6. Verification Scope

**Run locally (macOS, no .NET):**
- `verify_bridge_static.py` → OK (route count 14 + checkin no local-file read guard).
- `verify_lisp_shell_static.py` → OK (route count 14).
- `verify_material_sync_static.py` → OK (count 14 + `/document/checkin` present + dedup absent).
- doc-contract pytests (index references / completeness / sorting / R2 / Tier-B) → 32 passed.
- `git diff --check` → clean.

**C# build/xUnit deferred to Windows CI** (helper targets `net46`/`net6.0-windows`,
unavailable on macOS): `dotnet build` of `Helper` / `Helper.Tests` /
`Bridge.Tests` / `CADDedupPlugin.Client.Tests`, and the §5 xUnit tests. This
PR is considered complete only after Windows CI build + tests pass.

## 7. Explicit Non-Goals (unchanged from taskbook §8)

No CAD-host command (helper-routes-only); no BOM; no CAD entity write / no S10
guard relaxation; no backend route or backend auth change; no dedup change; no
audit; no local filesystem read; no new `ErrorCodes` beyond `QuotaExceeded`.
