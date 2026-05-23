# Claude Taskbook: CAD Helper Bridge S7 - Reset Local Token CLI

Date: 2026-05-22

Type: **Doc-only taskbook.** Changes no runtime, no schema, no service, no
workflow, and no CAD plugin code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook does
NOT authorize that implementation.

## 1. Purpose

CAD Desktop Helper Bridge **S7** implements the local-only
`--reset-local-token` operator command described by the R3.2 design after S1-S6
are already merged.

S7 owns:

- parsing exactly `yuantus-cad-helper.exe --reset-local-token`;
- rejecting non-interactive and remote-like invocations;
- refusing reset while any current-user helper instance is active;
- generating and writing a fresh 32-byte local helper token through the S1 DPAPI
  primitive;
- recording a special local audit event for the reset attempt;
- proving there is still no HTTP, named-pipe, child-process, or route-triggered
  token reset path.

S7 does **not** implement S8 plugin migration, `/dedup/check`, multipart
forwarding, S9/S10 LISP bridge code, Serilog/file logging, live token reload,
new Python routes, schema migrations, or CAD drawing writes. Those remain
separate opt-in slices.

## 2. Grounded Current Reality

Grounded against `origin/main = ab31df5` after S6 merged.

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S7 surface:

- Lines 428-431 define the self-repair path: when Shared receives
  `401 AUTH_LOCAL_TOKEN_INVALID`, it rereads DPAPI once; if that still fails,
  the user is instructed to run `yuantus-cad-helper.exe --reset-local-token`.
- Lines 433-455 define `--reset-local-token` as a local interactive command
  only: no HTTP endpoint, no remote trigger, no IPC trigger.
- Lines 441-450 define the reset flow: interactive check, confirmation prompt,
  mutex check, fresh 32-byte token write to DPAPI, token length output without
  printing the token, exit code `0` on success.
- Lines 452-455 require source guards proving no service-mode HTTP reset route.
- Lines 695-697 define the special audit row for
  `endpoint=internal:reset-local-token` with `outcome=ok` or `outcome=error`.
- Lines 806-807 define CI cases for non-interactive rejection and no reset route.
- Lines 823-824 define manual Windows evidence for interactive PowerShell reset
  and SSH/WinRM remote rejection.
- Line 1063 assigns S7 to helper `--reset-local-token` interactive command,
  non-interactive rejection, and audit persistence.

### 2.2 S1 local token primitive

`clients/cad-desktop-helper/Shared/Security/LocalTokenStore.cs` already provides
the DPAPI primitive:

- `ReadLocalToken()` reads `%APPDATA%\YuantusPLM\local-token.bin`, returns `null`
  when missing, and maps read failures to `HELPER_DPAPI_UNAVAILABLE`.
- `WriteLocalToken(string hexToken)` writes the encrypted token under entropy
  literal `yuantus-cad-helper-local-token-v1` and maps write failures to
  `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`.

`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S1_SHARED_LIBRARY_20260520.md`
lines 270-298 explicitly says S1 provides the primitive while S3/S7 own
bootstrap/reset flow. S7 must reuse the primitive; it must not create a second
DPAPI entropy literal or alternate token file.

### 2.3 S3/S4 startup and in-memory token reality

`clients/cad-desktop-helper/Helper/HelperRuntime.cs` currently:

- rejects all startup args in `HelperCommand.RunAsync(...)` lines 94-101;
- obtains install id and resolves the single-instance decision before bootstrap
  lines 106-128;
- uses `LocalTokenBootstrapper.EnsureToken()` lines 131-132 before port
  allocation, session-file publish, and host start;
- passes the bootstrapped `localToken` into `IHelperHostRunner.RunAsync(...)`
  lines 142-143.

`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_20260522.md`
records the S4 policy that the helper compares incoming request tokens against
the in-memory token loaded/generated at helper startup. It also records the S7
dependency: a running helper will not observe a DPAPI token rotation until it
restarts or a future slice adds an explicit reload contract.

Therefore S7 must not rotate the DPAPI token while any current-user helper
instance is active. Otherwise active helpers would keep accepting the old
in-memory token while Shared consumers start reading the new DPAPI token.

### 2.4 S6 audit substrate

S6 added:

- `AuditEvent`;
- `IAuditEventStore`;
- `SqliteAuditEventStore`;
- `ConsoleAuditWarningWriter`;
- `audit_events` schema with `endpoint`, `outcome`, `error_code`,
  `duration_ms`, and `trace_id`;
- H2-style sanitized stderr warning format:

```text
[AUDIT_WRITE_FAILED] endpoint=<path> trace_id=<id> reason=<short>
```

The S7 implementation should reuse the S6 audit store and warning shape. It must
not create a second audit database, second schema, or new logging framework.

### 2.5 Current production helper route count

After S6, helper production route declarations are exactly ten:

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

S7 is a CLI slice. The route count must stay ten and no HTTP reset endpoint may
appear.

## 3. Ratified S7 Boundaries

### 3.A CLI surface

S7 adds exactly one supported non-service invocation:

```text
yuantus-cad-helper.exe --reset-local-token
```

The argument match is exact and case-sensitive. The supported argv shape is one
argument only. Any other argument, additional argument, or case variant remains
the existing unsupported-startup-args failure path.

Implementation shape:

- The literal `--reset-local-token` should be parsed at the command entry layer
  (`Program.cs` or an immediately adjacent CLI parser), not in Kestrel route
  registration.
- The reset path should call a testable helper command/service so unit tests do
  not need to invoke a real console process.
- Service-mode startup with no args must remain byte-for-byte compatible except
  for internal refactoring required to share dependencies.

### 3.B Local interactive invocation gate

S7 must reject reset before prompting unless all local-interactive conditions
pass:

- `Console.IsInputRedirected == false`;
- `Environment.UserInteractive == true`;
- no positive remote-shell signal is detected.

The implementation may introduce a fakeable `IResetInvocationContext` or
equivalent seam exposing:

- input redirected;
- user interactive;
- environment variables;
- current process / parent process image names if available.

Remote-shell detection is RATIFIED as best-effort positive-signal rejection for
R1:

- reject when SSH environment signals such as `SSH_CLIENT`, `SSH_CONNECTION`, or
  `SSH_TTY` are present;
- reject when WinRM / remote-management process signals such as
  `wsmprovhost.exe`, `winrshost.exe`, or `sshd.exe` are found in the available
  launcher/parent-process evidence;
- reject when `SESSIONNAME` starts with `RDP-Tcp` / `rdp-tcp` (case-insensitive),
  because RDP-launched `cmd.exe` / PowerShell can otherwise satisfy both stable
  console predicates while still being remote-admin initiated;
- if parent-process inspection is unavailable but the invocation is otherwise a
  normal local interactive terminal, do not fail closed solely because that
  optional inspection failed.

Grounding for the RDP signal:

- Microsoft Learn's Remote Desktop Protocol page states that environment
  variables in a Remote Desktop session are determined by the RDP-Tcp connection
  settings:
  <https://learn.microsoft.com/en-us/windows/win32/termserv/remote-desktop-protocol>.
- Microsoft Learn's `query session` page shows active Remote Desktop sessions
  named `rdp-tcp#1` and defines `SESSIONNAME` as the assigned session name:
  <https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/query-session>.

Rationale: R3.2 requires SSH/WinRM rejection, but process-parent evidence is
Windows-host dependent. S7 must enforce the two stable console predicates,
reject known remote signals including RDP `SESSIONNAME`, and report the exact
Windows signals it covers plus manual SSH/WinRM/RDP evidence results.

### 3.C Confirmation prompt

After passing §3.B and before touching mutex/session/token/audit state, S7 must
prompt:

```text
此操作将作废当前本地配对密钥，所有 CAD 内运行中的会话需要重新调用 helper 才能继续。是否继续？[y/N]
```

Only `y` or `Y` confirms. Empty input, EOF, `n`, `N`, and any other response
cancel the reset:

- exit code `1`;
- no token write;
- no session-file delete;
- no helper host start;
- attempt an audit row with `endpoint=internal:reset-local-token`,
  `outcome=error`, and `error_code=HELPER_RESET_CANCELLED`.

The confirmation prompt and all user-facing success/error output must never
include the token value or bearer token value.

### 3.D Active helper guard

S7 must refuse reset when any current-user helper instance is active.

R3.2 line 445 names the mutex check. S3 later introduced per-session
`helper-session-{sessionId}.json` files. Because the local token is per Windows
user while helper instances are per user session, S7 R1 extends the guard to:

1. Compute the install id and mutex name using the existing S1/S3 primitives.
2. Try the current-session named mutex without blocking.
3. Scan discoverable `helper-session-*.json` files under
   `%APPDATA%\YuantusPLM`.
4. For each session file, use S3-style PID + image-path evidence to decide
   whether a helper instance is still active.

The S7 stale definition is exactly the S3 `SingleInstanceCoordinator` rule:
active means the process exists **and** the process image path matches the helper
exe path recorded in the session file; otherwise the record is stale for S7's
guard decision.

If any active helper is detected:

- exit code `1`;
- no token write;
- no session-file delete;
- no attempt to kill the helper;
- audit `outcome=error`, `error_code=HELPER_RESET_HELPER_RUNNING`.

Stale session records are not a reset command's cleanup target. S7 may ignore
stale records for the purpose of allowing reset, but it must not delete
session files unless the implementation can prove it is reusing the existing S3
cleanup rule exactly. The preferred R1 behavior is **ignore stale, do not
delete**.

Race note: there is an unavoidable small window between active-helper scan and
the DPAPI write where another current-user session could start a helper. The
R1 consequence is bounded: that new helper may hold the old in-memory token
until idle/restart, matching the no-live-reload behavior in §3.H. S7 does not
attempt a cross-session global lock beyond the existing per-install-id mutex and
session-file evidence.

### 3.E Token generation and DPAPI write

On the confirmed path with no active helper:

- generate exactly 32 cryptographic random bytes through the existing
  `IRandomBytes` seam;
- convert to exactly 64 lowercase hex characters;
- write through the existing `ILocalTokenStore.Write(...)` / S1
  `LocalTokenStore.WriteLocalToken(...)` primitive;
- print only the token length and a success message; do not print the token.

Suggested success output:

```text
Local helper token reset complete. token_length=64. 下次 CAD 调用时会自动重新拉取新密钥。
```

If the DPAPI write fails:

- exit code `1`;
- preserve the existing `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED` error code;
- audit `outcome=error`, `error_code=HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED` if
  audit storage is available.

S7 must not change `LocalTokenStore` entropy, file path, or Shared retry
behavior.

### 3.F Reset audit event

S7 uses the existing S6 audit table and writes a special event:

```text
endpoint = "internal:reset-local-token"
outcome  = "ok" | "error"
```

Required fields:

- `ts`: current UTC timestamp;
- `endpoint`: `internal:reset-local-token`;
- `outcome`: `ok` for successful token write, `error` for refusal/failure;
- `error_code`: null on success, stable error code on refusal/failure;
- `duration_ms`: elapsed command duration;
- `trace_id`: `Guid.NewGuid().ToString("N")`, matching S6's 32-character
  lowercase hex audit trace format.

Fields that must remain null for reset rows:

- `drawing_path`;
- `profile_id`;
- `item_id`;
- `pull_id`;
- `cad_system`;
- `applied_fields_json`;
- `failed_fields_json`.

No token value, bearer token, request body, environment variable dump, process
command line, or PII may be written to the audit row.

### 3.G Audit failure policy for reset

S7 reset is not transactionally coupled to SQLite. There is no safe single
transaction spanning DPAPI token replacement and SQLite audit persistence.

RATIFIED policy:

- For refusals and pre-token-write failures, S7 attempts an `outcome=error`
  audit row. If audit write also fails, emit the sanitized S6 warning and exit
  with the original failure code `1`.
- For successful DPAPI token write followed by audit write failure, S7 must not
  claim that reset failed. Exit code remains `0`, and the helper emits one
  sanitized stderr line:

```text
[AUDIT_WRITE_FAILED] endpoint=internal:reset-local-token trace_id=<id> reason=<short>
```

Rationale: after DPAPI write succeeds, returning failure would mislead the
operator into believing the old token is still valid. Restoring the old token is
also unsafe because the rollback write can fail and the old token may already be
unknown to current consumers. Operator visibility is provided by the stderr
warning until a later logging slice exists.

### 3.H No live reload in S7

S7 does not introduce live token reload. Running helpers already hold their
startup token in memory by S4 design.

The active-helper guard in §3.D is the protection: reset only runs when no
active current-user helper is detected. After a successful reset, the next CAD
call through `Yuantus.Cad.Shared` rereads DPAPI, injects the new
`X-Yuantus-Local-Token`, and spawns or talks to a fresh helper.

If a future slice wants live reload, it must be a separate opt-in and must update
S4/S7 tests explicitly.

### 3.I Error codes

S7 may add these shared constants:

- `HELPER_RESET_REQUIRES_INTERACTIVE`;
- `HELPER_RESET_CANCELLED`;
- `HELPER_RESET_HELPER_RUNNING`.

Existing constants used by S7:

- `HELPER_INPUT_VALIDATION_FAILED`;
- `HELPER_INSTALL_ID_UNAVAILABLE`;
- `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`;
- `AUDIT_WRITE_FAILED`.

All S7 command failures are process exit code `1`; success is exit code `0`.

### 3.J No route / IPC exposure

S7 must not add:

- `/admin/reset-token`;
- `/reset-local-token`;
- any other HTTP reset endpoint;
- named-pipe trigger;
- child-process trigger from Shared `HelperSpawner`;
- CORS;
- browser-accessible reset path.

Source guards must prove the reset literal and reset behavior are absent from
Kestrel route mapping and Shared spawner arguments. `Yuantus.Cad.Shared`
continues to spawn helper with bare args only.

## 4. R1 Target Output

Implementation PR should contain:

- Helper CLI parsing for exact `--reset-local-token`.
- Testable reset command/service with fake console/invocation context.
- Active helper detector using existing install-id/mutex/session-file/process
  primitives.
- Token generation/write through existing S1/S3 seams.
- Reset audit write through existing S6 audit store.
- Narrow updates to prior S3/S4/S5/S6 source guards so they allow S7's CLI
  literal while still rejecting S8+ scope and HTTP reset exposure.
- `clients/cad-desktop-helper/Helper.Tests/HelperResetLocalTokenContractTests.cs`
  or equivalent.
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_R1_20260522.md`.
- One `docs/DELIVERY_DOC_INDEX.md` line.

## 5. Mandatory Tests

S7 implementation must add these exactly named tests:

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

Source/drift guards:

- production helper route declarations remain exactly ten after S7;
- no `MapGet`, `MapPost`, `MapPut`, or `MapDelete` route contains `reset`;
- no `/admin/reset-token`, `/reset-local-token`, `/dedup/check`,
  `/shell/notify`, `/compose`, `/validate`, `/tasks`, or
  `/diagnostics/snapshot`;
- no `UseCors`;
- no browser `Authorization` forwarding;
- no `--reset-local-token` in Shared `HelperSpawner`;
- no token value in stdout/stderr/audit row;
- no server Python or AutoCAD plugin edits.

Tests 9-11 must use a fake audit store. They must not depend on machine-local
SQLite state or a real DPAPI profile.

The implementation PR should also keep all S3/S4/S5/S6 protected tests green,
with only minimal source-guard wording updates where S7 intentionally changes
the allowed CLI surface.

## 6. Verification Plan

Local doc-contract checks:

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

Implementation PR CI gate:

```bash
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj
```

This workstation may not have the .NET SDK installed; the implementation PR
must use the dedicated GitHub `cad-helper-shared-dotnet` workflow as merge gate.

Manual Windows evidence expected before or during implementation review:

- interactive PowerShell: confirm prompt, enter `y`, DPAPI token changes, exit
  code `0`, no token printed;
- interactive PowerShell: enter `n`, token unchanged, exit code `1`;
- running helper active: reset refuses, token unchanged, exit code `1`;
- SSH/WinRM/RDP or equivalent remote shell: reset refuses, token unchanged, exit
  code `1`;
- after reset, next CAD/Shared call rereads DPAPI and can authenticate against a
  newly spawned helper.

## 7. DEV / Verification MD Requirements

Implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_R1_20260522.md
```

The DEV MD must document:

- CLI parsing shape;
- interactive and remote-signal rejection predicates, including the
  `SESSIONNAME=RDP-Tcp#...` signal;
- active-helper detector behavior, including current-session mutex and
  cross-session session-file scan;
- token generation/write path and DPAPI primitive reuse;
- audit event shape and audit-failure policy;
- no HTTP/IPC exposure source guards;
- local checks;
- GitHub `cad-helper-shared-dotnet` result;
- manual Windows evidence status, or a clear statement that manual evidence is
  still pending and therefore the PR should not merge.

## 8. Non-Goals

S7 does not authorize:

- S8 CADDedupPlugin migration;
- `/dedup/check`;
- multipart forwarding;
- S9/S10 LISP bridge;
- live helper token reload;
- killing running helpers;
- deleting stale helper-session files as a cleanup operation;
- Serilog/file logging;
- reset over HTTP, named pipe, socket command, browser route, or remote trigger;
- any Python FastAPI changes;
- schema migrations or tenant baseline edits;
- CAD drawing reads/writes.

## 9. Decision Gate / Handoff

Doc-only. Implementation is authorized only after this taskbook merges AND a
separate explicit opt-in is given, on branch:

```text
feat/cad-helper-bridge-s7-reset-local-token-r1-20260522
```

Each later slice remains separately gated:

- S8 plugin migration and `/dedup/check`;
- S9 bridge DLL;
- S10 LISP shell;
- S11 integration / runbook / verification scripts.

## 10. Reviewer Focus

- Confirm §3.B remote-shell rejection is acceptable as positive-signal rejection
  for R1, with `SESSIONNAME=RDP-Tcp#...` included and manual SSH/WinRM/RDP
  evidence required before merge.
- Confirm §3.D intentionally extends the R3.2 mutex-only text with a
  cross-session active-helper scan, because the token is per user while helper
  instances are per session.
- Confirm §3.G success-after-token-write audit failure policy: exit `0` plus
  sanitized stderr warning, not a misleading reset failure.
- Confirm §3.A / §3.J source guard: reset literal is allowed in CLI parsing and
  tests, but not in route handlers or Shared spawner args.
- Confirm S7 adds no HTTP route and preserves the ten-route S6 helper surface.
