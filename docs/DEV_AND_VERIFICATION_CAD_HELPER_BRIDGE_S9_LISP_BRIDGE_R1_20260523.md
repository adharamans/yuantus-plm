# CAD Helper Bridge S9 NETLOAD Lisp Transport Bridge R1 - Development And Verification

Date: 2026-05-23

## 1. Scope Delivered

This implementation delivers the S9 slice ratified in
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_20260523.md`
(merged at `349ec48d` after the M1 / M2 / L1-L3 convergence).

Delivered scope:

- new `clients/cad-desktop-helper/Bridge/` SDK-style project
  `YuantusCadHelperBridge.csproj` targeting **.NET Framework v4.6** with a
  `ProjectReference` to `Yuantus.Cad.Shared` (net46 target);
- SDK-free core service `BridgeCallService` that validates the Lisp endpoint
  string, parses the Lisp JSON request, calls S1
  `HelperLocator.EnsureHelperRunningAsync`, posts through S1
  `HelperTransport.PostJsonAsync<JToken>`, and returns a
  serialized helper `data` payload (or the literal string `"null"` for
  successful JSON-null responses per §3.E);
- `EndpointValidator` token-exfiltration / URI-confusion guard implementing
  the full §3.C rejection list including the post-convergence rule that
  rejects `%` entirely;
- `ConsoleBridgeCommandLineWriter` emits the §3.G sanitized failure line
  `[YUANTUS_HELPER_CALL_FAILED] code=<code> reason=<short>` to
  `Console.Error`, sanitizing all interpolated values to a narrow
  alphanumeric+`._/-` character class;
- CAD-host adapter `Adapters/AutoCadHostAdapter.cs` containing the
  `[LispFunction("yuantus-helper-call")]` registration and an AutoCAD
  editor-backed command-line writer, **wrapped in `#if AUTOCAD_HOST`** so the
  SDK-free CI build compiles without AutoCAD managed assemblies;
- `clients/cad-desktop-helper/Bridge.Tests/` with 20 mandatory contract tests;
- `clients/cad-desktop-helper/verify_bridge_static.py` static verifier;
- `.github/workflows/cad-helper-shared-dotnet.yml` updated with Bridge path
  filters, Bridge build/test steps, and the static verifier step.

Not implemented:

- the AutoCAD-host-bound code path itself does NOT build or NETLOAD in CI;
  per §3.J this is the deferred "static-verified host adapter, SDK-free core
  unit-tested" posture;
- S10 Lisp shell commands (`YUANTUS_DIFF_PREVIEW` etc.) — explicit S9 non-goal;
- `/shell/notify`, `/dedup/check`, `/compose`, `/validate`, `/tasks`,
  `/diagnostics/snapshot` helper routes — explicit S9 non-goal;
- CORS, browser `Authorization` forwarding, PLM direct calls, DPAPI direct
  access — explicit S9 non-goal;
- DWG mutation, modal UI, business field parsing — explicit S9 non-goal.

## 2. Runtime Design

### 2.1 Project shape

`YuantusCadHelperBridge.csproj` uses the SDK-style `Microsoft.NET.Sdk` with
`<TargetFramework>net46</TargetFramework>`. Per taskbook §3.A this requires
explicit disclosure of the syntax deviation from the R3.2 design's
illustrative `<TargetFrameworkVersion>v4.6</TargetFrameworkVersion>` text;
the build output is the same .NET Framework v4.6 assembly
`YuantusCadHelperBridge.dll`. The `EnableWindowsTargeting` escape hatch
mirrors the rest of the CAD helper stack so non-Windows hosts can at least
type-check the source.

### 2.2 Lisp surface, arity, and string-type enforcement

Exactly one Lisp primitive is registered:

```lisp
(yuantus-helper-call "<endpoint>" "<json-request-string>") -> "<json-data>" | nil
```

`AutoCadHostAdapter.YuantusHelperCall` reads the `ResultBuffer.AsArray()`
output and applies a strict two-string-argument check:

1. `values != null && values.Length == 2`;
2. each value has `TypeCode == 5005` (the documented numeric code for
   `Autodesk.AutoCAD.Runtime.LispDataType.Text`);
3. each `value.Value` is an actual managed `string` (defense in depth
   against type-code/payload mismatch).

Any of: 0 / 1 / 3+ arguments, `null` value, non-string Lisp type (integer,
real, list, T/nil, etc.) deterministically returns `null` to Lisp (which
the host treats as `nil`) and writes the sanitized line
`[YUANTUS_HELPER_CALL_FAILED] code=HELPER_INPUT_VALIDATION_FAILED reason=arity`
to the CAD command line per §3.B. Non-string Lisp values are **never**
coerced through `.ToString()` — they fail closed.

### 2.3 Endpoint validation

`EndpointValidator.TryValidate` implements §3.C as a sequence of rejections:

1. empty / whitespace-only / leading-trailing whitespace → reject;
2. any control char (CR, LF, tab, `<0x20`, `0x7f`) → reject;
3. any backslash → reject;
4. **any `%` character → reject** (the post-convergence rule for
   percent-encoded scheme-confusion, `%2F`, `%5C`, `%00`, `%0A`, `%0D` etc.);
5. first character must be `/` → reject otherwise;
6. second character must NOT be `/` (rejects `//host/...` network paths);
7. literal `http://` / `https://` / `file://` / `ftp://` / `javascript:` /
   `data:` anywhere in the path → reject (defense-in-depth against URI
   confusion regardless of `Uri` parser quirks).

The validator does NOT maintain a business endpoint allowlist; S4/S5/S6
gates in the helper remain authoritative for auth / origin / session
policy. The validator is specifically a token-exfiltration and
URI-confusion guard.

### 2.4 Request handling

`BridgeCallService.CallAsync` parses `jsonRequest` via `JToken.Parse` and
requires the parsed token to be a `JObject`. Arrays, scalars, JSON null,
and invalid JSON all return `HELPER_INPUT_VALIDATION_FAILED` with a
sanitized reason (`json_missing` / `json_not_object` / `json_invalid`).
The bridge does NOT parse business fields (`item_id`, `write_cad_fields`,
`applied_fields`, profile ids, drawing contents); the `JObject` is forwarded
unchanged to `HelperTransport`.

### 2.5 Synchronous sync wrapper

`BridgeCallService.Call(string, string)` wraps `CallAsync(...,
CancellationToken.None)` with `.GetAwaiter().GetResult()` per §3.F. This
preserves the real `HelperException.Code` instead of wrapping it in
`AggregateException` (which `Task.Result` or `Task.Wait()` would do). The
static verifier `verify_bridge_static.py` rejects any `.Result;` or
`.Wait()` patterns in Bridge sources.

### 2.6 Response shape

On `IBridgeTransport.PostJsonAsync` success:

- non-null, non-JSON-null payload → `JsonConvert.SerializeObject(data)`
  returned as the Lisp string;
- `JTokenType.Null` payload (helper returned `{"ok": true, "data": null}`)
  → literal Lisp string `"null"`;
- C# `null` payload (helper returned `{"ok": true}` without a `data` member)
  → literal Lisp string `"null"`.

On failure (any thrown `HelperException`, transport exception,
`OperationCanceledException`, or non-`HelperException` mapped to
`PLM_VALIDATION_FAILED`) the bridge returns `BridgeResult.Failure(code,
reason)`; the CAD-host adapter maps this to Lisp `nil`. The writer writes
exactly one sanitized line.

This keeps the `nil` value reserved for bridge/helper failure and makes a
successful JSON-null payload distinguishable from transport failure, per the
convergence pin in §3.E.

### 2.7 Production wiring (M1 convergence pin) and writer routing

`BridgeCallService.CreateProduction(IBridgeCommandLineWriter writer)`
wires:

- `SharedBridgeLocator` → `new HelperLocator().EnsureHelperRunningAsync(...)`;
- `SharedBridgeTransport` → `new HelperTransport(baseUri)
  .PostJsonAsync<JToken>(...)`;
- the caller-supplied `writer` (or `ConsoleBridgeCommandLineWriter` if
  `null`).

`AutoCadHostAdapter` constructs `AutoCadCommandLineWriter` ONCE and passes
it to `CreateProduction`. The same writer instance is also used directly
for the arity/type-mismatch failure at the adapter level. This is the
post-review fix for the wiring gap that previously sent endpoint /
JSON / locator / transport failure lines to `Console.Error` instead of
the CAD command line.

Per the merged taskbook §5 test 6 disambiguation and new test 20
(`test_s9_static_wiring_reaches_production_helper_locator_and_transport`),
production-seam coverage for `HelperLocator` and `HelperTransport` is
inherited from S1 `Shared.Tests`; S9 adds test 20 + the static verifier so
the bridge cannot silently replace those production Shared seams with local
fakes or duplicate implementations.

### 2.8 CAD-host adapter conditional compilation

`Adapters/AutoCadHostAdapter.cs` is wrapped entirely in
`#if AUTOCAD_HOST` / `#endif`. The default SDK-free CI build does not
define `AUTOCAD_HOST`, so the AutoCAD attribute usage
(`[LispFunction(...)]`, `Autodesk.AutoCAD.ApplicationServices.Application`,
`Autodesk.AutoCAD.DatabaseServices.ResultBuffer`, etc.) is excluded from
compilation and does not require AutoCAD managed assemblies on the GitHub
Windows runner. An operational NETLOAD build defines `AUTOCAD_HOST` (and
adds the AutoCAD reference assemblies) to compile the full bridge.

The source file is still scanned by both the contract tests and the static
verifier regardless of the compile constraint, so the
`[LispFunction("yuantus-helper-call")]` count, the absence of business
logic, and the AutoCAD editor write path are all source-pattern-verified.

## 3. Test Coverage

`Bridge.Tests/BridgeContractTests.cs` implements the 20 S9-mandatory tests
with the names ratified by the merged taskbook §5:

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
20. `test_s9_static_wiring_reaches_production_helper_locator_and_transport`

Test 6 is a fake-based wiring assertion with strict call shape (locator
called before transport, transport receives the exact base URI from the
locator, the exact endpoint string from Lisp, and the exact JSON object
from Lisp after validation). Test 20 plus the static verifier together
pin that the bridge cannot silently bypass the production Shared seams.

Test 15 acts as a structural assertion: the test assembly itself is built
against `net46` with no AutoCAD references, so the fact that it compiles
and exercises `BridgeCallService` is the SDK-free coverage signal.

The static verifier `verify_bridge_static.py` runs the source-pattern guards
listed in taskbook §5 plus the M1 convergence wiring guard. Each check
prints `ok` / `FAIL` and the script exits non-zero on any failure.

## 4. Verification

Local commands run on this workstation:

```bash
xmllint --noout \
  clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj \
  clients/cad-desktop-helper/Bridge.Tests/Yuantus.Cad.Bridge.Tests.csproj
```

```bash
git diff --check
```

```bash
python3 clients/cad-desktop-helper/verify_bridge_static.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

`.NET build/test` is **not** runnable on this workstation (the macOS
workstation has no `dotnet` SDK; `dotnet --version` returns
`zsh:1: command not found: dotnet`). Per §3.J the implementation PR explicit
posture is:

- the SDK-free Bridge core builds on the GitHub Windows runner
  (`dotnet build clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj`)
  with the AutoCAD-host code excluded via the `AUTOCAD_HOST` symbol;
- `dotnet test clients/cad-desktop-helper/Bridge.Tests/Yuantus.Cad.Bridge.Tests.csproj`
  exercises all 20 mandatory contract tests;
- the static verifier runs in CI as a workflow step;
- true NETLOAD build/load inside a real CAD process is deferred to
  operational signoff per §3.K.

### 4.1 Deferred native-CAD operational signoff

Per the same deferred-signoff pattern used in S7 #628 §4.1 and S8 #630
§3.J, the following native-CAD evidence is **not collected by this PR** and
is recorded as deferred operational signoff:

- Windows + AutoCAD 2018 / 2024 (or ZWCAD / GstarCAD) `NETLOAD` of
  `YuantusCadHelperBridge.dll`;
- bridge DLL loads without missing dependency errors inside the CAD
  process;
- `(yuantus-helper-call "/diff/preview" "{...}")` from an AutoCAD command
  invocation starts or finds the helper and returns a JSON string;
- the failure path returns `nil` to Lisp and prints the sanitized error
  line through the **production CAD command-line writer**
  (`AutoCadCommandLineWriter`), not only through the SDK-free
  `ConsoleBridgeCommandLineWriter` seam;
- no local helper token, no full PLM bearer, no full request / response
  body, and no stack trace appears in the CAD command-line output;
- when paired with S10 later, a display-only `YUANTUS_DIFF_PREVIEW` flow
  records `/audit/apply-result` as `not-applied-display-only`.

This is an explicit owner-accepted deviation from the §3.K manual evidence
list, not a substitute for the missing evidence. The PR body records the
same deferred-signoff posture so future readers do not interpret the merge
as native-CAD NETLOAD validation.

## 5. Explicit Non-Goals

- No `/shell/notify`, `/dedup/check`, `/compose`, `/validate`, `/tasks`, or
  `/diagnostics/snapshot` helper route.
- No CORS or browser `Authorization` forwarding.
- No direct `HttpClient`, direct DPAPI access, or direct
  `LocalTokenStore.ReadLocalToken` from the bridge.
- No DWG write-back, no business diff parsing, no `YUANTUS_DIFF_PREVIEW`,
  no `PLMMATPULL` / `PLMMATPUSH` / `PLMMATCOMPOSE` / `PLMMATPROFILES`
  commands (S10 owns Lisp commands).
- No `Yuantus.Cad.Bridge.Lisp/*.lsp` shell command files.
- No `CADDedupPlugin` or `DedupApiClient` edits.
- No Python service / schema / migration / tenant-baseline edits.
- No native-CAD NETLOAD / build-load evidence in CI; that is the
  deferred operational signoff per §4.1.

## 6. Next Slices

Each subsequent slice remains separately opted in:

- **S10** ZWCAD / GstarCAD Lisp shell commands (`YUANTUS_DIFF_PREVIEW`
  display-only flow, command-line presentation, `/audit/apply-result`
  `not-applied-display-only` reporting). Depends on this S9 merge per the
  R3.2 §10 dependency chain.
- **S11** integration package, verification runbook, and end-to-end
  acceptance — including the deferred Windows + native-CAD evidence from
  §4.1.
- The S6 carry-forward `/diff/preview` audit row null `pull_id` bug at
  `HelperRuntime.cs:2529` remains an open audit-hardening obligation.
