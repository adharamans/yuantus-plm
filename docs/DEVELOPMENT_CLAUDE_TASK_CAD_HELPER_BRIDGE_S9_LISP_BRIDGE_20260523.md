# Claude Taskbook: CAD Helper Bridge S9 - NETLOAD Lisp Transport Bridge

Date: 2026-05-23

Type: **Doc-only taskbook.** Changes no runtime, no schema, no workflow, and no
CAD bridge code. It specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT authorize that
implementation.

## 1. Purpose

CAD Desktop Helper Bridge **S9-R1** adds the native-CAD bridge DLL boundary from
the R3.2 design:

- new `clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.dll` source;
- .NET Framework v4.6 / `net46` NETLOAD compatibility;
- `ProjectReference` to `Yuantus.Cad.Shared` using the `net46` target;
- exactly one Lisp-callable transport primitive:

```lisp
(yuantus-helper-call "<endpoint>" "<json-request-string>")
```

S9-R1 is only the thin transport bridge from native CAD Lisp into the already
merged helper process. It does **not** add Lisp command files, does **not** add
`YUANTUS_DIFF_PREVIEW`, does **not** parse business diff payloads, does **not**
write DWG fields, and does **not** add helper server routes. Those are S10/S11
or later opt-in slices.

S9-R1 also does **not** implement `/dedup/check`, `/shell/notify`, `/compose`,
`/validate`, `/tasks`, `/diagnostics/snapshot`, CORS, PLM routes, database
schema, tenant baseline data, or AutoCAD `CADDedupPlugin` behavior. S8 already
migrated the supported AutoCAD plugin MaterialSync calls; S9 is for the
native-CAD Lisp route only.

## 2. Grounded Current Reality

Grounded against `origin/main = 90d80c55` after S8 merged.

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S9 surface:

- Lines 107-112 define `YuantusCadHelperBridge.dll` as the native-CAD route:
  .NET Framework v4.6, Shared `net46`, and only
  `(yuantus-helper-call endpoint json) -> json`.
- Lines 115-120 define constraints: helper is an external process, CAD-side
  clients do not directly call PLM, Lisp does not directly send HTTP, read
  DPAPI, or hold tokens, and `CADDedupPlugin` does not go through the Lisp
  bridge.
- Lines 130-131 reject `netstandard2.0` for Shared/Bridge compatibility and
  pin the Bridge to full .NET Framework v4.6.
- Lines 700-724 define the mechanics: NETLOAD `YuantusCadHelperBridge.dll`,
  export one Lisp function, use Shared to discover/start helper, read the local
  helper token through Shared, synchronously call helper, and print sanitized
  error information to the CAD command line.
- Lines 810-825 list manual evidence. Case 9 is the native-CAD smoke:
  ZWCAD true-machine load of the Lisp shell plus bridge DLL, run
  `YUANTUS_DIFF_PREVIEW`, display `write_cad_fields`, and record
  `/audit/apply-result` as `not-applied-display-only`.
- Lines 983-987 list the intended `clients/cad-desktop-helper/Bridge/` project.
- Lines 991-1009 define the reference graph: Shared multi-targets
  `net46;net6.0-windows`, and Bridge references Shared's `net46` target.
- Lines 1065-1071 define S9 as the NETLOAD adapter plus
  `(yuantus-helper-call ...)`; S10 depends on S9.

### 2.2 Current repository state

Current CAD-helper directories:

```text
clients/cad-desktop-helper/Shared/
clients/cad-desktop-helper/Shared.Tests/
clients/cad-desktop-helper/Detector/
clients/cad-desktop-helper/Detector.Tests/
clients/cad-desktop-helper/Helper/
clients/cad-desktop-helper/Helper.Tests/
```

There is no `clients/cad-desktop-helper/Bridge/` and no
`YuantusCadHelperBridge.dll` source yet.

### 2.3 S1 Shared primitives inherited by S9

S1 already provides the primitives S9 must consume:

- `Yuantus.Cad.Shared` multi-targets `net46;net6.0-windows`.
- `HelperLocator.EnsureHelperRunningAsync(...)` resolves a healthy helper base
  URI or starts `yuantus-cad-helper.exe`, using a 5 second max wait and the
  session-file / `/healthz` probe flow.
- `HelperTransport` injects `X-Yuantus-Local-Token` and
  `X-Yuantus-Protocol`.
- `HelperTransport` parses the helper response envelope and throws
  `HelperException` on helper errors.
- `LocalTokenStore` keeps DPAPI token access inside Shared.

S9-R1 must reuse Shared. It must not duplicate helper discovery, local-token
reading, response-envelope parsing, or helper process spawning.

### 2.4 Helper route surface inherited by S9

After S8, production helper routes remain exactly ten:

```text
GET  /healthz
GET  /version
POST /session/login
POST /session/logout
GET  /session/status
POST /cad/current-drawing
POST /diff/preview
POST /sync/inbound
POST /sync/outbound
POST /audit/apply-result
```

S9-R1 must not add or remove helper server routes. It is a CAD-host transport
DLL only.

### 2.5 Native-CAD SDK reality

The bridge must be NETLOAD-compatible inside CAD processes. The host adapter may
need CAD managed runtime APIs for Lisp registration, argument buffers, and
command-line output.

The normal GitHub Windows runner can build/test SDK-free .NET code, but it does
not provide AutoCAD, ZWCAD, or GstarCAD managed assemblies. Therefore S9-R1 must
split verification honestly:

- CI covers SDK-free bridge-core behavior and static source guards.
- True NETLOAD/build/load behavior remains deferred Windows + native-CAD
  operational signoff unless the owner supplies a real CAD SDK/host for the PR.

Deferred evidence is not a substitute for real validation; it is explicit risk
acceptance for a repo-only slice.

## 3. S9-R1 Decisions And Boundaries

### 3.A Project shape and target

S9-R1 adds:

```text
clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj
clients/cad-desktop-helper/Bridge/*.cs
clients/cad-desktop-helper/Bridge.Tests/Yuantus.Cad.Bridge.Tests.csproj
clients/cad-desktop-helper/Bridge.Tests/*.cs
```

The bridge DLL output must be .NET Framework v4.6 / `net46`.

The implementation may use either:

- classic `<TargetFrameworkVersion>v4.6</TargetFrameworkVersion>`; or
- SDK-style `<TargetFramework>net46</TargetFramework>`.

If SDK-style `net46` is used, the PR body and DEV/Verification MD must disclose
the syntax deviation from the R3.2 illustrative text and show that the output is
still .NET Framework v4.6.

`YuantusCadHelperBridge.csproj` must reference
`..\Shared\Yuantus.Cad.Shared.csproj` and consume Shared's `net46` target.

### 3.B Single Lisp callable primitive

S9-R1 exposes exactly one Lisp-callable primitive:

```lisp
(yuantus-helper-call "<endpoint>" "<json-request-string>")
```

The primitive accepts exactly two string arguments:

1. `endpoint` - helper endpoint path;
2. `json-request-string` - JSON object request body.

No third argument, implicit profile selector, drawing-path auto-fill, or CAD
business option belongs to S9. Those are S10+ command concerns.

If the CAD host requires an uppercase registration symbol, the lower-case Lisp
spelling must still work and the implementation PR must document the host API
behavior.

### 3.C Endpoint validation is mandatory

S9 introduces a generic endpoint string from Lisp. This is the new S9-specific
security risk.

`HelperTransport` creates request URIs from a base URI and caller path. If S9
forwarded an absolute URL such as `https://evil.example/collect`, the bridge
could send the local helper token outside loopback.

Therefore S9-R1 must validate `endpoint` before any transport call:

- non-empty string;
- starts with exactly one `/`;
- rejects `//...` network-path references;
- rejects absolute URI schemes such as `http://`, `https://`, `file://`;
- rejects Windows UNC paths such as `\\host\share`;
- rejects backslashes;
- rejects CR, LF, tab, and other control characters;
- rejects whitespace-only or leading/trailing whitespace variants;
- treats the value as a helper-relative path only.

S9-R1 does **not** maintain a business endpoint allowlist. The helper's S4/S5/S6
gates remain authoritative for auth/source/session/business policy. The S9 guard
is specifically a token-exfiltration and URI-confusion guard.

### 3.D JSON request handling

`json-request-string` must be parsed before forwarding:

- it must be valid JSON;
- it must be a JSON object;
- invalid JSON returns `nil` and writes a sanitized bridge error to the CAD
  command line;
- valid objects are forwarded through Shared transport;
- S9 must not parse business fields such as `item_id`, `write_cad_fields`,
  `applied_fields`, profile ids, CAD properties, or drawing contents.

The bridge may deserialize to `JObject` / `JToken` only to preserve JSON shape
for transport. That is not business parsing.

### 3.E Response shape

S9-R1 uses existing `HelperTransport` semantics:

- successful helper responses return the helper `data` payload serialized as a
  JSON string;
- helper `ok=false`, HTTP 4xx/5xx, protocol mismatch, local-token failure,
  helper-startup failure, DPAPI failure, and transport exceptions return `nil`;
- failures also write one sanitized command-line message containing an error
  code and short reason;
- token values, bearer values, full request bodies, and full response bodies
  must not be printed.

This is a deliberate S9-R1 contract: the Lisp bridge returns the data payload,
not the full helper envelope. S10 command Lisp must consume that data shape.

### 3.F Synchronous CAD-host boundary

Lisp calls are synchronous. S9-R1 may block the CAD command while the helper call
is in flight.

The implementation should use an explicit sync wrapper around async Shared calls
such as `.GetAwaiter().GetResult()`, rather than `Task.Result` or `Task.Wait()`,
so exceptions are not hidden behind `AggregateException`.

S9-R1 does not implement cancellation propagation. Existing AutoCAD plugin calls
also do not propagate command cancellation into HTTP requests; this is not a new
regression.

### 3.G Error output to CAD command line

On failure, S9-R1 writes a short sanitized message to the CAD command line.

Minimum format:

```text
[YUANTUS_HELPER_CALL_FAILED] code=<code> reason=<short>
```

Rules:

- no local helper token;
- no PLM bearer;
- no request JSON;
- no response body;
- no filesystem paths except sanitized categories/basenames;
- no stack traces;
- one line per call failure.

The adapter may use the CAD host's editor/command-line API. SDK-free tests
should validate formatting through a narrow message-writer seam.

### 3.H No helper server route changes

S9-R1 is a client DLL slice. It must not edit helper Kestrel route declarations.

Specifically, S9-R1 must not add:

- `/shell/notify`;
- `/dedup/check`;
- `/compose`;
- `/validate`;
- `/tasks`;
- `/diagnostics/snapshot`;
- CORS;
- any IPC route for Lisp.

The production helper route count remains exactly ten after S9-R1.

`/shell/notify` appears in the R3.2 endpoint table, but it is not assigned to
S9 in the work-breakdown row. It remains S10/S11+ scope unless separately
opted in.

### 3.I No DWG or business logic

`YuantusCadHelperBridge.dll` must not:

- write DWG fields;
- inspect block attributes;
- parse `write_cad_fields`;
- decide `applied_fields` or `failed_fields`;
- implement `YUANTUS_DIFF_PREVIEW`;
- display diff tables;
- open modal UI;
- call PLM directly;
- use `HttpClient` directly except through Shared transport;
- read DPAPI directly except through Shared;
- parse CAD-specific business payloads.

S10 owns native-CAD Lisp commands and command-line presentation. Any future
native-CAD write-back adapter is a later slice with real host validation.

### 3.J CI and workflow posture

S9-R1 implementation must update `.github/workflows/cad-helper-shared-dotnet.yml`
so changes under these paths trigger the Windows dotnet workflow:

```text
clients/cad-desktop-helper/Bridge/**
clients/cad-desktop-helper/Bridge.Tests/**
```

The workflow must at least run SDK-free bridge contract tests and a static
verifier. The implementation PR must explicitly state which of these is true:

- the actual Bridge project builds on the GitHub Windows runner; or
- the actual Bridge host adapter is static-verified while SDK-free core logic is
  unit-tested, with true NETLOAD build/load deferred to operational signoff.

Silent "tests green" language is not allowed unless the relevant build/load
actually ran.

### 3.K Deferred native-CAD operational evidence

S9-R1 implementation may merge with deferred operational signoff only if the PR
body and DEV/Verification MD state it plainly.

Deferred evidence must include:

- Windows + AutoCAD or ZWCAD/GstarCAD host with `NETLOAD`;
- bridge DLL loads without missing dependency errors;
- `(yuantus-helper-call "/diff/preview" "{...}")` starts or finds helper;
- success returns a JSON string with helper data;
- error path returns nil and prints sanitized error code;
- no token appears in CAD command-line output;
- if paired with S10 later, `YUANTUS_DIFF_PREVIEW` display-only flow records
  `/audit/apply-result` as `not-applied-display-only`.

If this evidence is not collected in S9-R1, it remains a carried-forward S11 or
operational signoff obligation.

## 4. Implementation Shape For The Later PR

Recommended implementation shape:

1. Add `clients/cad-desktop-helper/Bridge/`.
2. Add `YuantusCadHelperBridge.csproj` targeting .NET Framework v4.6 / `net46`.
3. Reference `..\Shared\Yuantus.Cad.Shared.csproj`.
4. Add a small SDK-free core service, for example `BridgeCallService`, that:
   - validates endpoint;
   - parses the JSON object;
   - calls `HelperLocator.EnsureHelperRunningAsync(...)`;
   - creates `HelperTransport`;
   - posts the JSON object to the validated helper-relative endpoint;
   - serializes the returned data payload to a JSON string;
   - maps failures to a bridge result.
5. Add the CAD-host adapter that registers the Lisp primitive and delegates to
   the core service.
6. Add a narrow command-line writer seam so tests can verify sanitized failure
   output without AutoCAD.
7. Add `clients/cad-desktop-helper/Bridge.Tests/` SDK-free tests and source
   guards.
8. Update `cad-helper-shared-dotnet` workflow path filters and test steps.
9. Add `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md`.
10. Update `docs/DELIVERY_DOC_INDEX.md`.

If the host adapter cannot compile on the repo's Windows runner without native
CAD assemblies, the PR must not fake the result. Keep host-specific code
static-verified, keep core logic unit-tested, and document deferred NETLOAD
evidence.

## 5. Mandatory Tests For The Later PR

S9 implementation must add these exactly named tests:

1. `test_s9_bridge_project_targets_net46_and_references_shared_net46`
2. `test_s9_bridge_exposes_exactly_one_lisp_function_yuantus_helper_call`
3. `test_s9_lisp_function_accepts_endpoint_and_json_string_arguments_only`
4. `test_s9_rejects_absolute_uri_network_path_backslash_and_control_char_endpoint_before_transport`
5. `test_s9_rejects_missing_or_non_object_json_request_without_calling_transport`
6. `test_s9_posts_valid_json_object_to_helper_through_shared_locator_and_transport`
7. `test_s9_returns_helper_data_payload_as_json_string_on_success`
8. `test_s9_returns_nil_and_writes_sanitized_error_on_helper_exception`
9. `test_s9_error_output_never_contains_local_token_request_body_or_response_body`
10. `test_s9_sync_wrapper_preserves_helper_exception_code_without_aggregate_exception`
11. `test_s9_adds_no_helper_server_routes_and_preserves_route_count_ten`
12. `test_s9_does_not_add_shell_notify_dedup_check_compose_validate_tasks_or_diagnostics_routes`
13. `test_s9_bridge_contains_no_dwg_write_business_diff_parsing_or_modal_ui_logic`
14. `test_s9_bridge_uses_shared_helper_locator_transport_and_local_token_store_only`
15. `test_s9_bridge_core_is_sdk_free_contract_testable_without_native_cad`
16. `test_s9_workflow_runs_bridge_contracts_and_static_verifier`
17. `test_s9_static_verifier_rejects_absolute_uri_forwarding_and_direct_httpclient_token_reads`
18. `test_s9_dev_verification_records_deferred_native_cad_netload_signoff`
19. `test_s9_s10_dependency_is_documented_and_no_lisp_shell_command_files_are_added`

Recommended static/source guards:

- no `MapGet(` / `MapPost(` additions in helper source;
- no `/shell/notify`, `/dedup/check`, `/compose`, `/validate`, `/tasks`, or
  `/diagnostics/snapshot` route declarations;
- no direct `HttpClient` construction in Bridge core;
- no `ProtectedData` / DPAPI access in Bridge;
- no local-token direct read outside Shared transport;
- no direct `Process.Start` in Bridge except through Shared `HelperLocator` /
  `HelperSpawner`;
- no `CadMaterialFieldService`, `write_cad_fields`, `AppliedFields`, or DWG
  mutation logic;
- no `CADDedupPlugin` edits;
- no `DedupApiClient` edits;
- workflow includes Bridge path filters and Bridge tests/static verifier;
- DEV/Verification MD exists and is indexed.

## 6. Verification Commands For This Taskbook PR

This doc-only taskbook PR should run:

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

The S9 implementation PR should additionally run:

```bash
python3 clients/cad-desktop-helper/verify_bridge_static.py
dotnet test clients/cad-desktop-helper/Bridge.Tests/Yuantus.Cad.Bridge.Tests.csproj --configuration Release
```

If the real host Bridge project can build in GitHub Windows CI, also run:

```bash
dotnet build clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj --configuration Release
```

If it cannot build without native CAD managed assemblies, the PR must state that
plainly and record deferred Windows native-CAD NETLOAD evidence.

## 7. DEV / Verification MD Requirements

The later S9 implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md
```

That MD must record:

- exact implementation scope;
- target framework representation and actual output target;
- whether the real Bridge project built in CI or was static-verified only;
- all mandatory tests and their results;
- workflow run URL/status for `cad-helper-shared-dotnet`;
- static verifier output;
- explicit helper route count after S9;
- whether Windows native-CAD NETLOAD evidence was collected or deferred;
- all known deferred evidence items.

## 8. Non-Goals

S9-R1 does not include:

- S10 Lisp shell commands;
- `YUANTUS_DIFF_PREVIEW`;
- CAD command-line diff display;
- native-CAD DWG write-back;
- `/shell/notify`;
- `/dedup/check`;
- `DedupApiClient` migration;
- dedup-vision URL/token configuration;
- `/compose`;
- `/validate`;
- `/tasks`;
- `/diagnostics/snapshot`;
- CORS;
- server Python routes;
- helper route additions;
- SQLite schema changes;
- PLM schema changes;
- AutoCAD `CADDedupPlugin` behavior changes;
- SolidWorks client changes;
- installer/packaging changes beyond Bridge project files needed for R1;
- S11 end-to-end acceptance package.

## 9. Recommended Branch For Implementation

After this taskbook merges and only after a separate explicit opt-in, use:

```text
feat/cad-helper-bridge-s9-lisp-bridge-r1-20260523
```

Do not start S9 implementation from this taskbook PR.

## 10. Reviewer Focus

Please review these points before merge:

1. Confirm S9-R1 is limited to `YuantusCadHelperBridge.dll` transport bridge and
   does not include S10 Lisp commands.
2. Confirm endpoint validation in §3.C is mandatory and sufficient to stop
   absolute-URI / network-path token exfiltration.
3. Confirm response shape in §3.E: return helper `data` JSON string, not the
   full helper envelope.
4. Confirm S9 may use SDK-free core tests plus static adapter verification if
   native CAD assemblies are unavailable in CI.
5. Confirm `/shell/notify` remains out of S9 even though it appears in the R3.2
   endpoint table.
6. Confirm helper route count remains ten.
7. Confirm native-CAD NETLOAD evidence may be deferred only if explicitly
   recorded as operational signoff, not claimed as tested.

## 11. Status

This taskbook is ready for review once:

- the doc exists;
- `docs/DELIVERY_DOC_INDEX.md` references it;
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.

