# CAD Helper Bridge S7 Reset Local Token R1 - Development And Verification

Date: 2026-05-22

## 1. Scope Delivered

This implementation delivers the S7 slice ratified in
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_20260522.md`.

Delivered scope:

- `yuantus-cad-helper.exe --reset-local-token` interactive CLI command;
- positive-signal rejection for non-interactive, SSH, WinRM, and RDP invocations;
- cross-session active-helper guard using the existing
  `SingleInstanceCoordinator` PID + image-path staleness rule;
- fresh 32-byte DPAPI token write through the S1
  `LocalTokenStore.WriteLocalToken` primitive without any new entropy literal;
- `endpoint=internal:reset-local-token` audit row written through the S6
  `SqliteAuditEventStore`;
- H1/H2-shaped audit-failure policy preserving exit code 0 after a successful
  DPAPI write with one sanitized stderr `[AUDIT_WRITE_FAILED]` line;
- three new shared error codes `HELPER_RESET_REQUIRES_INTERACTIVE`,
  `HELPER_RESET_CANCELLED`, `HELPER_RESET_HELPER_RUNNING`;
- 15 S7-mandatory contract tests in
  `HelperResetLocalTokenContractTests.cs`;
- narrowly updated S3/S4/S5/S6 scope-leak guards so the `--reset-local-token`
  CLI literal is allowed in Helper production source while remaining absent from
  `Yuantus.Cad.Shared` (`HelperSpawner` bare args) and from any HTTP route
  declaration.

Not implemented:

- S8 `CADDedupPlugin` migration and `/dedup/check`;
- multipart forwarding;
- S9/S10 LISP bridge code;
- S11 integration packaging;
- live helper token reload (running helpers still hold their startup token in
  memory per S4 contract);
- helper-killing on reset;
- stale session-file cleanup as a reset side effect;
- Serilog / file logging;
- HTTP, named-pipe, child-process, or browser reset trigger;
- Python FastAPI or schema/migration changes;
- CAD drawing reads/writes.

## 2. Runtime Design

### 2.1 CLI parsing

`HelperCommand.RunAsync(args, runtime, ct)` now performs a top-level dispatch:

1. exactly `["--reset-local-token"]` (case-sensitive, ordinal match) routes to
   `runtime.ResetCommand.Run()`;
2. any other non-empty `args` array continues to fail with
   `HELPER_INPUT_VALIDATION_FAILED` exactly as before;
3. empty `args` continues to the existing service-mode startup path with no
   behavior change.

`Program.cs` is unchanged. Dispatching at `HelperCommand` keeps service-mode
startup byte-for-byte compatible while making the CLI surface unit-testable
through the existing `HelperRuntime` seam.

### 2.2 S7 seams

The S7 implementation adds the following classes to
`clients/cad-desktop-helper/Helper/HelperRuntime.cs`:

- `IResetLocalTokenCommand` / `ResetLocalTokenCommand` — orchestrator;
- `IResetInvocationContext` / `DefaultResetInvocationContext` — wraps
  `Console.IsInputRedirected`, `Environment.UserInteractive`,
  environment variables, and launcher-process image names collected by
  walking the parent-process chain via `NtQueryInformationProcess`
  (`PROCESS_BASIC_INFORMATION.InheritedFromUniqueProcessId`) up to
  `MaxAncestryDepth = 8` levels with cycle detection;
- `IResetConsole` / `SystemResetConsole` — prompt and info output;
- `IHelperSessionFileScanner` / `FileSystemHelperSessionFileScanner` —
  enumerates `%APPDATA%\YuantusPLM\helper-session-*.json` files;
- `IActiveHelperDetector` / `DefaultActiveHelperDetector` — current-session
  mutex check plus cross-session scan;
- `ActiveHelperDetection` / `HelperSessionFileRecord` — value types.

`HelperRuntime` gains one new optional ctor parameter
`IResetLocalTokenCommand resetCommand = null` and one new property
`ResetCommand`. The existing 13-positional-arg call sites in tests continue to
compile unchanged. `HelperRuntime.Default` is rebuilt through a private
`BuildDefault()` static that wires the production reset command using the same
`DefaultHelperPaths`, `SharedInstallIdProvider`, `SystemNamedMutexFactory`,
`DefaultProcessInspector`, `SharedLocalTokenStore`, `CryptographicRandomBytes`,
`SystemClock`, and `ConsoleErrorWriter` instances already used by service-mode.

### 2.3 Reset flow

`ResetLocalTokenCommand.Run()` executes in this strict order. Any refusal short
circuits and returns exit code 1 after attempting an `outcome=error` audit row.

1. capture started timestamp and generate a `Guid.NewGuid().ToString("N")`
   trace id (matching S6's audit trace format);
2. reject when `Console.IsInputRedirected == true` with
   `HELPER_RESET_REQUIRES_INTERACTIVE`;
3. reject when `Environment.UserInteractive == false` with
   `HELPER_RESET_REQUIRES_INTERACTIVE`;
4. reject when any of `SSH_CLIENT`, `SSH_CONNECTION`, or `SSH_TTY` environment
   variables is non-empty;
5. reject when `SESSIONNAME` starts with `RDP-Tcp` or `rdp-tcp`
   (case-insensitive), covering the Microsoft Learn / `query session`
   convention for Remote Desktop sessions;
6. reject when any of `wsmprovhost.exe`, `winrshost.exe`, or `sshd.exe` appears
   in the walked parent-process image names (`Process.GetCurrentProcess` plus
   ancestry via `NtQueryInformationProcess` →
   `InheritedFromUniqueProcessId`, depth-bounded and cycle-guarded);
7. write the exact confirmation prompt text from R3.2 design `:443` to the
   reset console and read one input line;
8. reject anything other than ordinal `y` or `Y` (including `null`/EOF, empty
   string, `n`/`N`, multi-character responses, leading whitespace) with
   `HELPER_RESET_CANCELLED`;
9. ask `IActiveHelperDetector.Detect()` and reject with
   `HELPER_RESET_HELPER_RUNNING` if any helper is active;
10. generate 32 cryptographic random bytes through the existing
    `IRandomBytes` seam, convert to exactly 64 lowercase hex characters, write
    through `ILocalTokenStore.Write(...)` which forwards to S1
    `LocalTokenStore.WriteLocalToken`; on `HelperException`, propagate the
    original code (typically `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`);
11. emit one info line containing `token_length=64` only and never the token
    value;
12. attempt to write an `outcome=ok` audit row through `IAuditEventStore.Write`;
    on failure emit a single sanitized
    `[AUDIT_WRITE_FAILED] endpoint=internal:reset-local-token trace_id=<id> reason=<short>`
    stderr line and still return exit code 0;
13. return exit code 0.

The duration field on the audit row is computed as
`max(0, (clock.UtcNow - started).TotalMilliseconds)` and rounded to an int.

### 2.4 Active-helper detection

`DefaultActiveHelperDetector.Detect()` returns "active" in any of these cases:

- `IInstallIdProvider.GetOrCreate()` raises `HelperException` (treat as active
  because we cannot reason about other helpers without the install id);
- the per-install-id mutex `Local\YuantusCadHelper-{installId}` cannot be
  acquired without blocking;
- `IHelperSessionFileScanner.Scan()` returns at least one record whose
  `pid` references a running process whose image path matches the recorded
  `image_path` under `StringComparison.OrdinalIgnoreCase` — exactly the S3
  `SingleInstanceCoordinator` rule.

Stale records (`pid` not running, image path mismatched) are ignored; they are
not deleted, kept exactly as S3 wrote them. The detector never returns the
mutex lease to the caller: it disposes the lease immediately after the check.

There is a small race between the scan and the DPAPI write where a new helper
instance could start in another current-user Windows session. The R1 consequence
is bounded: that new helper will continue to hold the old in-memory token until
idle shutdown or restart, matching the no-live-reload behavior defined by the
S4 contract.

### 2.5 Reset audit shape

```text
endpoint            = "internal:reset-local-token"
outcome             = "ok" | "error"
error_code          = null | <stable code>
ts                  = UTC ISO 8601
duration_ms         = elapsed_ms (>= 0)
trace_id            = Guid.NewGuid().ToString("N")
drawing_path        = null
profile_id          = null
item_id             = null
pull_id             = null
cad_system          = null
applied_fields_json = null
failed_fields_json  = null
```

The audit row contains no token value, no environment variable dump, no
command-line replay, and no PII. The sanitized stderr warning uses the same
endpoint label and a `reason=<exception-type-name>` short reason.

### 2.6 No HTTP / IPC exposure

- The production helper Kestrel route table remains exactly the ten S6 routes;
  no `MapGet`/`MapPost`/`MapPut`/`MapDelete` is added or modified.
- The `--reset-local-token` literal appears only in `HelperRuntime.cs`'s
  CLI-parsing constant and inside test files. The `Yuantus.Cad.Shared`
  `HelperSpawner` continues to spawn helper with bare args only.
- No `NamedPipeServerStream` / `NamedPipeClientStream` references appear in
  the Helper source; the reset command is reachable only through console
  invocation of the helper executable.

## 3. Test Coverage

`HelperResetLocalTokenContractTests.cs` implements the 15 S7-mandatory tests:

1. `test_s7_cli_argument_is_program_only_and_service_args_stay_rejected`
2. `test_reset_local_token_requires_interactive_local_console`
3. `test_reset_local_token_rejects_ssh_winrm_or_rdp_remote_invocation`
4. `test_reset_local_token_prompts_and_cancels_unless_user_confirms_y`
5. `test_reset_local_token_rejects_when_helper_mutex_or_active_session_exists`
6. `test_reset_local_token_ignores_stale_session_records_without_deleting_them`
7. `test_reset_local_token_writes_new_64_char_lower_hex_dpapi_token`
8. `test_reset_local_token_never_prints_or_audits_token_value`
9. `test_reset_local_token_writes_internal_audit_ok_event_after_success`
10. `test_reset_local_token_writes_internal_audit_error_event_for_refusals_and_failures`
11. `test_reset_local_token_success_audit_failure_warns_but_keeps_success_exit_code`
12. `test_reset_local_token_has_no_http_route_or_named_pipe_trigger`
13. `test_reset_local_token_does_not_start_kestrel_or_publish_session_file`
14. `test_s7_preserves_s6_route_count_and_business_audit_contracts`
15. `test_s7_keeps_cad_helper_dotnet_workflow_covering_helper_tests`

Additional hardening test (caught a real review finding that the WinRM /
launcher process-name check was test-only because the production seam was
not walking parent ancestry):

- `test_reset_invocation_context_walks_real_parent_ancestry` — exercises the
  production `DefaultResetInvocationContext.CollectLauncherProcessImageNames()`
  directly (not via `FakeInvocationContext`), asserts the returned ancestry
  has at least two entries on the Windows runner (current process plus at
  least one parent), and pins the `NtQueryInformationProcess` /
  `InheritedFromUniqueProcessId` / `MaxAncestryDepth` source references so a
  future refactor cannot silently remove the parent walk.

Existing scope-leak guards narrowed to allow the new CLI literal while
preserving S8+ exclusions:

- `HelperStartupContractTests.test_no_s7_s8_scope_leak_after_s6_business_audit_routes`
  → `test_no_s8_scope_leak_after_s7_reset_token` (asserts `Contains` on
  `--reset-local-token`, still rejects `/dedup/check`, `/shell/notify`,
  `CADDedupPlugin`).
- `HelperAuthOriginContractTests.test_no_s7_s8_scope_leak_after_s6_business_audit_routes`
  → `test_no_s8_scope_leak_after_s7_reset_token` (same shape).
- `HelperSessionRoutesContractTests.test_s5_s6_do_not_add_s7_s8_routes_or_reset_token`
  → `test_s5_s6_s7_do_not_add_s8_routes_or_dedup`.
- `HelperBusinessAuditContractTests.test_s6_does_not_add_dedup_shell_reset_or_later_routes`
  keeps its name (S6 still does not add reset; S7 does) and drops only the
  now-stale `--reset-local-token` assertion.

The Shared-source guard in S7 mandatory test 12 asserts
`--reset-local-token` does not appear anywhere under
`clients/cad-desktop-helper/Shared/`, enforcing the design §3.J pin against
`HelperSpawner` exposure.

## 4. Verification

Local checks run on this workstation:

```bash
xmllint --noout \
  clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj \
  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj \
  clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj \
  clients/cad-desktop-helper/Detector/Yuantus.Cad.Detector.csproj \
  clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj
```

```bash
git diff --check
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

`.NET build/test`: blocked locally because this workstation does not have the
`.NET` SDK installed (`zsh:1: command not found: dotnet`). The dedicated
Windows `cad-helper-shared-dotnet` workflow must run

```bash
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj --configuration Release --no-restore
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj --configuration Release --no-restore
```

as the authoritative merge gate. The PR body cites the workflow run id for the
implementation SHA.

### 4.1 Manual Windows evidence

The taskbook §6 manual evidence still requires:

- interactive PowerShell confirm path (`y` → DPAPI rotated, token never
  printed, exit code 0);
- interactive PowerShell cancel path (`n` → no rotation, exit code 1);
- running-helper active path (reset refuses, token unchanged, exit code 1);
- SSH/WinRM/RDP remote shell path (reset refuses, exit code 1);
- post-reset CAD/Shared call rereads DPAPI and authenticates with a freshly
  spawned helper.

Manual evidence is collected outside this PR and must be appended before the PR
merges. Do not merge this PR before the manual evidence is recorded.

## 5. Explicit Non-Goals

- No `/dedup/check`, `/shell/notify`, `/compose`, `/validate`, `/tasks`, or
  `/diagnostics/snapshot` route.
- No HTTP, named-pipe, child-process, or browser reset trigger.
- No live token reload for running helpers.
- No killing of running helpers.
- No stale session-file deletion as a reset side effect.
- No CORS, no PLM bearer forwarding change, no S6 audit substrate change.
- No Python service / schema / migration / tenant-baseline edits.
- No CAD plugin or LISP bridge edits.

## 6. Next Slices

Each subsequent slice remains separately opted in:

- S8 `CADDedupPlugin` migration plus `/dedup/check` (which must also extend the
  audited endpoint set per R3.2 design `:695`);
- S9 `YuantusCadHelperBridge.dll` LISP bridge;
- S10 ZWCAD/GstarCAD LISP shell;
- S11 integration package and verification runbook.
