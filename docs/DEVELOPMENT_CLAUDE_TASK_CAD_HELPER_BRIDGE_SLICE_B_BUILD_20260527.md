# Claude Taskbook: CAD Helper Bridge — Slice B Build (yuantus-helper-upload C# Seam)

Date: 2026-05-27

Type: **Doc-only build/implementation taskbook.** It pins the exact C# seam for
the `yuantus-helper-upload` multipart primitive, the file-source policy, the
non-ASCII filename strategy, and the two-primitive verifier/xUnit shape for the
future Slice B R1. It changes no code itself. **Merging this taskbook does NOT
authorize the Slice B R1 implementation** — that requires its own explicit
opt-in. This is the build plan, not the change.

Parent design/envelope-pin: `DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_B_MULTIPART_TRANSPORT_SEAM_20260527.md`
(#656, `6b49182a`), which pinned the canonical envelope and ratified D-B1(a) (the
cad-desktop-helper bridge primitive, C# transport deferred as a material-sync
charter question) and D-B2 (`yuantus-helper-upload`). This taskbook makes that
buildable.

## 1. Scope

Build the client-side multipart transport seam in the cad-desktop-helper bridge
`.dll`: a second Lisp primitive `(yuantus-helper-upload "ENDPOINT" item-id
filepath)` plus the SDK-free core that POSTs the canonical envelope (#656 §2) to
`/document/checkin` and `/document/bom-import`. No in-CAD command (Slice C), no
helper route, no `.lsp` change. The upload primitive is **not** a generic
multipart tunnel: R1 must allow exactly `/document/checkin` and
`/document/bom-import`.

## 2. Grounded seam — what each layer adds

The call path mirrors the merged `yuantus-helper-call` chain
(`AutoCadHostAdapter` → `BridgeCallService` → `IBridgeTransport` →
`SharedBridgeTransport` → Shared `HelperTransport`):

- **Shared `HelperTransport` — NO change.** It already exposes
  `PostContentAsync<T>(path, HttpContent, ct)` (buffered retry, envelope parse,
  `X-Yuantus-Local-Token` / `X-Yuantus-Protocol` injection). The multipart seam
  reuses it; the bridge never builds its own `HttpClient`.
- **`IBridgeTransport` — add one method:**
  `Task<JToken> PostMultipartAsync(Uri baseUri, string endpoint, string itemId,
  byte[] fileBytes, string fileName, CancellationToken ct)`.
- **`SharedBridgeTransport` — implement it:** build a `MultipartFormDataContent`
  per #656 §2 — a `ByteArrayContent` file part named `file` with
  `Content-Type: application/octet-stream` and the base `fileName`; **and**, only
  when `itemId` is non-empty, a `StringContent` text part named `item_id`
  (UTF-8). Then `transport.PostContentAsync<JToken>(endpoint, content, ct)`.
- **`BridgeCallService` — add `UploadAsync` + sync `Upload`:**
  `EndpointValidator.TryValidate(endpoint)` **plus** an upload endpoint allowlist
  of exactly `/document/checkin` and `/document/bom-import`; then apply
  route-specific `itemId` policy; then apply the §3 file-source policy to obtain
  bytes + base filename; call `PostMultipartAsync`; return
  `BridgeResult.Success(SerializeDataPayload(data))`; map `HelperException` /
  failures to `BridgeResult.Failure` + one sanitized writer line (same `Fail`
  path as `CallAsync`). For `/document/checkin`, blank `itemId` is a bridge-side
  validation failure **before file read and before transport**. For
  `/document/bom-import`, blank `itemId` is allowed and means omit the `item_id`
  multipart part so the helper applies auto-create root policy.
- **`AutoCadHostAdapter` — add `[LispFunction("yuantus-helper-upload")]`:** reads
  **three** string args (endpoint, item-id, filepath) with the same strict
  string-type check as `yuantus-helper-call` (non-string → `nil`); delegates to
  `Service.Upload(...)`; returns the Lisp string payload or `null`/`nil`.
  AUTOCAD_HOST-gated, like the existing shim.

## 3. File-source policy (D-build-1 — must be TESTABLE; per the #656 amend)

The primitive takes a `filepath`; it must **not** be described as accepting an
arbitrary user path. Decide:

- **P1 (recommended) — SDK-free-core validated read via an injected seam.**
  Introduce `IBridgeFileSource` with `byte[] ReadAllBytes(string path)` +
  existence/regular-file validation; production impl wraps `File.ReadAllBytes`,
  tests inject a fake. The core returns a sanitized failure (→ `nil`) when the
  path is missing / not a regular file. **Plus** a Slice-C static guard that
  repo-owned LISP callers pass only the active document path (`(strcat DWGPREFIX
  DWGNAME)`), never a prompted path. This keeps the read **unit-testable** in
  CI and bounds the source at the LISP layer.
- **P2 — host-adapter derive/validate against the active document.** The
  AUTOCAD_HOST shim ignores or validates the supplied path against
  `Application.DocumentManager.MdiActiveDocument.Name`. Stronger, but the
  enforcement is host-bound (not CI-buildable) → its verification defers to
  operational signoff.

Recommendation: **P1** (the `IBridgeFileSource` seam) so "file-source policy" is
a tested contract, with P2-style active-document binding noted for the future
host track.

## 4. Non-ASCII filename (D-build-2)

First cut: restrict the file part `filename` to an **ASCII-safe base name**
(transliterate/replace non-ASCII), record the limitation, and **unit-test the
sanitizer**; defer RFC 5987 `filename*` encoding. The sanitizer lives in the
SDK-free core so it is CI-tested.

## 5. Two-primitive verifier / xUnit shape (deliverables — every guard is a deliverable)

The `BridgeContractTests.cs` line numbers below are a drafting-time snapshot; the
xUnit **test names are the stable anchors** and the R1 must re-verify the line
numbers at the start of work (that file is rewritten across slices). The
`verify_bridge_static.py` citations are stable.

- **`verify_bridge_static.py:73-80` `check_single_lisp_function`** — currently
  exactly one `[LispFunction(` and it is `yuantus-helper-call`. R1 updates it to
  the **exact two-primitive set** `{yuantus-helper-call, yuantus-helper-upload}`
  (strict set, not "≥1") and adds a presence assertion for the new name.
- **New `verify_bridge_static.py` guard** — assert the upload endpoint allowlist is
  exactly `{"/document/checkin", "/document/bom-import"}` and that the bridge does
  not expose a generic multipart forwarding path. This is a deliverable static
  guard, not only an xUnit behavior check.
- **`Bridge.Tests/BridgeContractTests.cs:41`
  `test_s9_bridge_exposes_exactly_one_lisp_function_yuantus_helper_call`** —
  update to the two-primitive set.
- **`BridgeContractTests.cs:51`
  `test_s9_lisp_function_accepts_endpoint_and_json_string_arguments_only`** — add
  a parallel test for `yuantus-helper-upload` taking **three** string args
  (endpoint, item-id, filepath); non-string → `nil`.
- **New xUnit (SDK-free Bridge + Shared tests):** (a) the multipart envelope has a `file` part and
  includes `item_id` only when non-empty; (b) `IBridgeFileSource` missing/invalid
  path → failure/`nil`; (c) the filename sanitizer; (d) endpoint validation is
  reused **and** the upload endpoint allowlist is strict (invalid structural
  endpoint or structurally valid non-upload endpoint → `nil` before any file read
  or transport); (e) `/document/checkin` blank `itemId` → validation failure
  before file read/transport, while `/document/bom-import` blank `itemId` omits
  the part; (f) **401→retry with multipart** — this belongs in
  `Shared.Tests/SharedContractTests.cs` because it verifies Shared
  `HelperTransport.PostContentAsync`, even though Shared production code remains
  unchanged. `HelperTransport.PostContentAsync` buffers the content and
  **replays a fresh `ByteArrayContent` from a header snapshot** on a single
  `AUTH_LOCAL_TOKEN_*` retry (`HelperTransport` `BufferedContent`). The R1 must
  add a test asserting the replayed request preserves the **same
  `Content-Type: multipart/form-data; boundary=…`** (and identical bytes) as the
  first attempt, so the helper's `HasFormContentType` check still passes on
  retry. This is the one non-obvious edge in reusing `PostContentAsync` for
  multipart.
- **`BridgeContractTests.cs:307`
  `test_s9_bridge_still_adds_no_routes_while_helper_has_g1a_document_routes`** and
  the route-count `== 15` invariant (`verify_bridge_static.py:167`,
  `verify_lisp_shell_static.py:348`, `verify_material_sync_static.py:234`, the 5
  C# count tests, the G1-C DEV doc) — **re-assert unchanged**; Slice B adds no
  helper route.
- **`BridgeContractTests.cs:341`
  `test_s9_bridge_contains_no_dwg_write...`** — still must pass; the file read +
  multipart build introduce no DWG/business/modal logic. (It scans for
  CadMaterialFieldService / write_cad_fields / MessageBox / Transaction.Start /
  BlockReference — none added.)

## 6. S10 / security

- The bridge reading a local file is **permitted**: the helper-side no-local-read
  guard (`verify_bridge_static.py:175`
  `check_g1b_document_checkin_does_not_read_local_filesystem`) is **helper-scoped**
  (it parses the `HelperRuntime.cs` `/document/checkin` route block), not
  bridge-scoped. The helper still receives bytes over multipart and never reads a
  path — that invariant stays green.
- The `IBridgeFileSource` seam (P1) is the new attack surface; §3 bounds it
  (validated read + Slice-C caller guard).
- No DWG entity mutation; no `.lsp` command in Slice B (commands are Slice C); no
  helper route (count stays **15**).

## 7. CI / build

- The bridge core (`BridgeCallService`, `IBridgeTransport` /
  `SharedBridgeTransport`, `IBridgeFileSource`, the sanitizer) is **SDK-free** and
  unit-tested in `Bridge.Tests` without AutoCAD references — the file-source
  policy, envelope shape, and filename sanitizer are all CI-tested there.
- The `[LispFunction("yuantus-helper-upload")]` shim is AUTOCAD_HOST-gated;
  operational NETLOAD evidence is **deferred to native-CAD operational signoff**,
  like `yuantus-helper-call`.
- This is a **C#** change: the R1 PR body must carry the literal phrase **"C#
  build/xUnit deferred to Windows CI"** and is gated on Windows CI green.

## 8. Non-Goals

This taskbook does NOT: authorize the Slice B R1 implementation (separate
opt-in); add the C# `IMaterialSyncHelperTransport` multipart method (deferred
material-sync charter track, #656 D-B1(b)); wire any in-CAD command (Slice C);
add/remove a helper route; change the Shared `HelperTransport`; finalize P1-vs-P2
or the RFC 5987 question beyond the §3/§4 recommendations (the R1 ratifies them).

## 9. Preconditions to enter the Slice B R1

1. §2 seam shape ratified (the five layer-edits; exact upload endpoint allowlist;
   Shared `HelperTransport` production code unchanged);
2. §3 file-source policy ratified (recommended P1 `IBridgeFileSource` + Slice-C
   caller guard);
3. §4 filename strategy ratified (ASCII-safe + tested sanitizer);
4. §5 guard/test shifts enumerated as concrete edits, route count held at `15`,
   the two-primitive sets kept strict, and the `PostContentAsync` multipart retry
   test placed in Shared.Tests;
5. acknowledgement of the C# Windows-CI gate + the literal deferral phrase in the
   PR body.

## 10. Reviewer Focus

1. Confirm §2: the seam mirrors `yuantus-helper-call`; the upload primitive is
   endpoint-allowlisted to exactly `/document/checkin` and
   `/document/bom-import`; Shared `HelperTransport` already has
   `PostContentAsync`, so no Shared production change.
2. Ratify §3 D-build-1 (P1 `IBridgeFileSource` so the file-source policy is a
   tested contract, not "arbitrary path"), and §4 D-build-2 (ASCII-safe filename
   + tested sanitizer).
3. Confirm §5 lists the two-primitive verifier/xUnit shifts by file:line, keeps
   the sets strict, holds route count `15`, and tests route-specific `itemId`
   behavior before file read/transport.
4. Confirm §6: bridge file read is permitted (helper no-local-read guard is
   helper-scoped) and the helper-side invariant stays intact.
5. Confirm §7: bridge core is SDK-free / CI-tested; only the LispFunction shim is
   AUTOCAD_HOST-deferred; the C# Windows-CI deferral phrase is required.

## 11. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / sorting / Tier-B
drift checks pass; `git diff --check` is clean. Ratifying §2–§5 sets the Slice B
R1 build plan; **a separate explicit opt-in authorizes the implementation**, and
Slice C (multipart command wiring) remains gated on Slice B merging.
