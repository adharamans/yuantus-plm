# DEV & Verification: CAD Helper Bridge — Slice B Upload Primitive (R1)

Date: 2026-05-27

Implements the Slice B R1 plan
(`DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_B_R1_IMPLEMENTATION_PLAN_20260527.md`,
itself built on the merged build taskbook #657 `a5b727c5`): the
cad-desktop-helper bridge-side multipart upload primitive
`(yuantus-helper-upload "ENDPOINT" item-id filepath)`. No helper route, no
`.lsp` command wiring, no material-sync change, no DWG mutation. Helper route
count stays `15`.

## 1. Files changed by subsystem

- **Bridge core (SDK-free, net46):**
  - `Bridge/IBridgeFileSource.cs` (new) — `IBridgeFileSource.TryReadAllBytes`
    seam + production `BridgeFileSource`.
  - `Bridge/IBridgeTransport.cs` — added `PostMultipartAsync(...)`.
  - `Bridge/SharedBridgeTransport.cs` — implemented `PostMultipartAsync` building
    the canonical multipart envelope and reusing
    `HelperTransport.PostContentAsync<JToken>` (Shared production code
    unchanged).
  - `Bridge/BridgeCallService.cs` — added the upload-endpoint allowlist, dual
    constructor (3-arg preserved, new 4-arg injects `IBridgeFileSource`),
    `Upload`/`UploadAsync`, and `SanitizeFileName`.
- **CAD-host shim (AUTOCAD_HOST-gated):**
  - `Bridge/Adapters/AutoCadHostAdapter.cs` — added
    `[LispFunction("yuantus-helper-upload")]` with strict three-string-arg
    reading.
- **Tests:** `Bridge.Tests/BridgeContractTests.cs` (two-primitive set, upload
  arg-shape, upload behavior, file-source, sanitizer, production file source),
  `Shared.Tests/SharedContractTests.cs` (401 multipart retry replay).
- **Static guards / docs:** `clients/cad-desktop-helper/verify_bridge_static.py`
  (two-primitive set + upload allowlist guard), this DEV doc, and
  `docs/DELIVERY_DOC_INDEX.md`.

## 2. Upload endpoint allowlist decision

`yuantus-helper-upload` is **not** a generic multipart tunnel. `UploadEndpointAllowlist`
in `BridgeCallService` is exactly `{ "/document/checkin", "/document/bom-import" }`.
`UploadAsync` runs structural `EndpointValidator.TryValidate` then the exact
allowlist **before** locator, file read, or transport. A new
`verify_bridge_static.py` guard asserts both endpoints are present, the
allowlist is defined, and the allowlist check precedes the `PostMultipartAsync`
call site in source order.

## 3. File-source fixed-token failure policy (no path leak)

`IBridgeFileSource.TryReadAllBytes` returns `false` with a **fixed reason
token** — one of `file_path_missing`, `file_not_regular`, `file_missing`,
`file_read_error` — never the supplied path. `UploadAsync` passes that token (not
the path) to the sanitized writer line. A contract test
(`...file_source_failure_returns_fixed_token_and_never_leaks_path`) uploads with
a secret-looking path and asserts the writer line carries the fixed token and
does **not** contain the secret substring. The production `BridgeFileSource` is
exercised against a real temp directory/file for all four conditions.

## 4. Filename sanitizer

`BridgeCallService.SanitizeFileName`: `Path.GetFileName` → keep ASCII
`[A-Za-z0-9._-]`, replace every other char (including directory separators) with
`_` → fall back to `upload.bin` when empty. Verified through the public upload
path (`...filename_is_sanitized_to_ascii_basename_via_upload`): `a b#c.dwg` →
`a_b_c.dwg`; trailing-separator path → `upload.bin`. RFC 5987 `filename*`
deferred.

## 5. Multipart envelope

`SharedBridgeTransport.PostMultipartAsync` builds one `file` part
(`application/octet-stream`, sanitized base filename) and an `item_id` text part
**only when non-empty**. No workflow flags from the bridge; `create_bom_job` /
`auto_create_part` remain helper-side for `/document/bom-import`.

## 6. 401 multipart retry test placement

The replay test lives in `Shared.Tests/SharedContractTests.cs`
(`...post_content_multipart_replays_same_boundary_and_bytes_on_401_retry`)
because it verifies Shared `HelperTransport.PostContentAsync` `BufferedContent`
behavior (Shared production code is unchanged). It injects a queued
`HttpMessageHandler` (401 `AUTH_LOCAL_TOKEN_INVALID` then 200), posts a
`MultipartFormDataContent`, and asserts both attempts carry the same
`Content-Type: multipart/form-data; boundary=...` and byte-for-byte identical
bodies.

## 7. Local verification

- `python3 clients/cad-desktop-helper/verify_bridge_static.py` → 13 guards pass
  (two-primitive set + upload allowlist).
- `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` → 23 pass
  (bridge-sources guard is presence-only; still green).
- `python3 clients/autocad-material-sync/verify_material_sync_static.py` → pass.
- doc-contract pytests → pass; `git diff --check` → clean.
- Residue scans for stale route counts (`== 14` / `== 16`) and `new HttpClient(`
  in `Bridge/` → none.

## 8. C# build / xUnit status

This machine has no `dotnet` and the bridge targets `net46` (Windows), so the
C# build and xUnit (`Bridge.Tests`, `Shared.Tests`) are **C# build/xUnit
deferred to Windows CI**. Completion requires GitHub Windows `dotnet build/test`,
`contracts`, `detect_changes`, and `mergeStateStatus=CLEAN`.

## 9. Deferred native-CAD operational signoff

Slice B adds only the bridge primitive, **not** any repo-owned `.lsp` caller
(command wiring is Slice C). The `[LispFunction("yuantus-helper-upload")]` shim is
AUTOCAD_HOST-gated; on-host NETLOAD evidence (real ZWCAD/GstarCAD/AutoCAD upload
against a live helper) remains **deferred** to native-CAD operational signoff.
Slice C (multipart command wiring) stays blocked until this R1 merges.
