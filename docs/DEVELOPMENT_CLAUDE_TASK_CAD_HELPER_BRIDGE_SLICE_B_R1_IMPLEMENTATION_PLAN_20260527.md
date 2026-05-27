# Claude Execution Plan: CAD Helper Bridge - Slice B R1 Upload Primitive

Date: 2026-05-27

Type: **Implementation execution plan.** This file converts the merged Slice B
build taskbook into a decision-complete checklist for Claude or another agent to
execute. It is still **plan-only**: creating this document does not itself change
bridge behavior.

Parents:

- `DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_B_MULTIPART_TRANSPORT_SEAM_20260527.md`
- `DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_B_BUILD_20260527.md`

## 1. Goal

Implement the cad-desktop-helper bridge-side multipart upload primitive:

```lisp
(yuantus-helper-upload "ENDPOINT" item-id filepath)
```

The primitive may upload only to:

- `/document/checkin`
- `/document/bom-import`

It must not become a generic multipart tunnel. Slice B R1 adds no helper route,
no `.lsp` command wiring, no material-sync C# transport, and no DWG mutation.
Helper route count remains `15`.

## 2. Branch and PR Discipline

1. Start from `main == origin/main`.
2. Create `feat/cad-helper-bridge-slice-b-upload-r1-20260527`.
3. Keep `.claude/` untracked and untouched.
4. Commit the implementation and DEV/verification doc together.
5. Open a PR. The PR body must contain the literal phrase:

```text
C# build/xUnit deferred to Windows CI
```

6. Do not self-merge unless explicitly instructed. Completion requires GitHub
   Windows dotnet build/test, contracts, detect_changes, and
   `mergeStateStatus=CLEAN`.

## 3. Implementation Order

### 3.1 Bridge seams

Add the SDK-free upload path in this order:

1. Add `IBridgeFileSource` and production `BridgeFileSource`.
2. Add filename sanitizer in the bridge core.
3. Extend `IBridgeTransport` with `PostMultipartAsync(...)`.
4. Implement `SharedBridgeTransport.PostMultipartAsync(...)` with
   `MultipartFormDataContent`.
5. Add `BridgeCallService.Upload(...)` and `UploadAsync(...)`.
6. Add AUTOCAD_HOST-gated
   `[LispFunction("yuantus-helper-upload")]` in `AutoCadHostAdapter`.

`Shared HelperTransport` production code must not change. The multipart seam
must reuse `HelperTransport.PostContentAsync<JToken>(endpoint, content, ct)`.

### 3.2 Upload validation flow

`BridgeCallService.UploadAsync` must run in this order:

1. `EndpointValidator.TryValidate(endpoint)`.
2. Exact upload endpoint allowlist:
   `{"/document/checkin", "/document/bom-import"}`.
3. Route-specific `itemId` validation.
4. `IBridgeLocator.EnsureHelperRunningAsync(...)`.
5. `IBridgeFileSource.ReadAllBytes(...)`.
6. `IBridgeTransport.PostMultipartAsync(...)`.
7. `BridgeResult.Success(SerializeDataPayload(data))`.

Locator intentionally runs before file read so a stopped helper fails before
large CAD files are read into memory. Endpoint allowlist and `itemId` validation
still happen before locator, file read, and transport.

### 3.3 Route-specific `itemId`

- `/document/checkin`: blank `itemId` is a bridge-side validation failure before
  locator, file read, or transport.
- `/document/bom-import`: blank `itemId` is allowed and means omit the
  `item_id` multipart part so helper auto-create root policy applies.

## 4. File Source and Filename Rules

### 4.1 File-source policy

Use the P1 policy from the build taskbook:

- `IBridgeFileSource.ReadAllBytes(string path)` is injected into
  `BridgeCallService`.
- Production implementation validates non-empty path, existing file, and regular
  file before reading bytes.
- Tests inject a fake file source.

File-source failures must use fixed reason tokens only. Allowed examples:

- `file_missing`
- `file_not_regular`
- `file_read_error`

Do not concatenate or format `filepath` into any reason or writer line. Add an
explicit assertion that a secret/path-like input is absent from all recorded
writer fields.

### 4.2 Filename sanitizer

Derive the multipart filename in this exact order:

1. `Path.GetFileName(filepath)`.
2. Replace every character outside `[A-Za-z0-9._-]` with `_`.
3. If the result is empty, use `upload.bin`.

Keep `.` so extensions survive. Directory separators must not remain in the
multipart `filename`.

## 5. Multipart Envelope

`SharedBridgeTransport.PostMultipartAsync(...)` builds:

- one `file` part from `fileBytes`;
- file part content type `application/octet-stream`;
- file part filename from the sanitized basename;
- one `item_id` text part only when `itemId` is non-empty.

No workflow flags are sent by the bridge. `create_bom_job` and
`auto_create_part` remain helper-side behavior for `/document/bom-import`.

## 6. Tests and Static Guards

### 6.1 Bridge.Tests

Add or update tests for:

- exact two Lisp primitives:
  `{yuantus-helper-call, yuantus-helper-upload}`;
- `yuantus-helper-upload` accepts exactly three string args; non-string or wrong
  arity returns `nil`;
- valid `/document/checkin` upload forwards one multipart request and returns
  helper data JSON;
- valid `/document/bom-import` upload with blank `itemId` omits the `item_id`
  part;
- structurally valid non-upload endpoints, such as `/document/status` or
  `/diff/preview`, fail before locator, file read, or transport;
- `/document/checkin` blank `itemId` fails before locator, file read, or
  transport;
- file-source missing / not regular / read error returns fixed reason tokens and
  never leaks the supplied path;
- filename sanitizer preserves ASCII safe names, replaces non-ASCII and path
  separators, and falls back to `upload.bin`;
- no direct `HttpClient`, DPAPI, `LocalTokenStore`, business parsing, modal UI,
  or DWG write logic is introduced.

### 6.2 Shared.Tests

Add the 401 retry multipart replay test in
`Shared.Tests/SharedContractTests.cs`:

1. Inject a fake `HttpMessageHandler` through `HelperTransport(Uri, HttpClient)`.
2. First response: `401` with `AUTH_LOCAL_TOKEN_INVALID`.
3. Second response: `200` ok envelope.
4. Use one `MultipartFormDataContent` as input to `PostContentAsync`.
5. Assert both requests preserve the same
   `Content-Type: multipart/form-data; boundary=...`.
6. Assert both request bodies are byte-for-byte equal.

This tests `BufferedContent` header and byte replay without changing Shared
production code.

### 6.3 Static verifiers

Update and run:

- `clients/cad-desktop-helper/verify_bridge_static.py`
- `clients/cad-desktop-helper/verify_lisp_shell_static.py`
- `clients/autocad-material-sync/verify_material_sync_static.py`

`verify_bridge_static.py` must include real guards for:

- exact two Lisp primitive set;
- upload allowlist exactly
  `{"/document/checkin", "/document/bom-import"}`;
- no generic multipart forwarding path;
- no `new HttpClient(` in bridge sources;
- fixed-token file-source failures with no path concatenation into writer
  output.

`verify_lisp_shell_static.py` must still pass because Slice B changes bridge
sources but adds no `.lsp` command wiring. Material-sync must still pass because
this slice does not extend that plugin.

## 7. Required Verification Commands

Run at minimum:

```bash
python3 clients/cad-desktop-helper/verify_bridge_static.py
python3 clients/cad-desktop-helper/verify_lisp_shell_static.py
python3 clients/autocad-material-sync/verify_material_sync_static.py
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index*.py \
  src/yuantus/meta_engine/tests/test_readme_runbooks_are_indexed_in_delivery_doc_index.py \
  src/yuantus/meta_engine/tests/test_runbook_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_claude_code_assist_discipline.py \
  src/yuantus/meta_engine/tests/test_next_cycle_post_p6_plan_gate_contracts.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_doc_index_sorting.py
git diff --check
```

Also run residue scans before pushing:

```bash
rg -n "route count is 14|route-count=14|== 14|route count is 16|route-count=16|== 16" clients docs .github
rg -n "new HttpClient\\(" clients/cad-desktop-helper/Bridge
rg -n "yuantus-helper-upload|PostMultipartAsync|MultipartFormDataContent" clients/cad-desktop-helper
```

If local dotnet is unavailable, record that in the DEV doc and rely on GitHub
Windows CI. If local dotnet is available, also run Bridge.Tests and Shared.Tests.

## 8. DEV/Verification Document

Add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_SLICE_B_UPLOAD_R1_20260527.md
```

It must record:

- files changed by subsystem;
- the upload endpoint allowlist decision;
- file-source fixed-token failure policy and no-path-leak assertion;
- filename sanitizer behavior;
- 401 multipart retry test placement in Shared.Tests;
- local verification commands and results;
- whether C# build/xUnit was local or deferred to Windows CI;
- native-CAD operational signoff remains deferred because Slice B only adds the
  primitive, not repo-owned `.lsp` callers.

Update `docs/DELIVERY_DOC_INDEX.md` in sorted order.

## 9. PR Acceptance Criteria

The PR is ready only when:

- bridge static verifier passes with the new Slice B guards;
- Lisp shell verifier passes;
- material-sync verifier passes;
- doc-contract tests pass;
- `git diff --check` is clean;
- GitHub Windows dotnet build/test passes;
- contracts and detect_changes pass;
- PR `mergeStateStatus` is `CLEAN`;
- worktree remains clean except intentionally untracked `.claude/`.

Slice C remains blocked until Slice B R1 is merged.
