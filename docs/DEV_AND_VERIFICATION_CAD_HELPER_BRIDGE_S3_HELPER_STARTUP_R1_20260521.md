# CAD Helper Bridge S3 Helper Startup R1 - Development And Verification

Date: 2026-05-21

## 1. Scope

This slice implements the S3 helper startup contract from
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S3_HELPER_STARTUP_20260521.md`.

Included:

- `clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj`
- `clients/cad-desktop-helper/Helper/Program.cs`
- `clients/cad-desktop-helper/Helper/HelperRuntime.cs`
- `clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj`
- `clients/cad-desktop-helper/Helper.Tests/HelperStartupContractTests.cs`
- `.github/workflows/cad-helper-shared-dotnet.yml`
- `docs/DELIVERY_DOC_INDEX.md`

Not included:

- No S4 local-token enforcement or origin allowlist.
- No S5 `/version`, `/session/*`, or `/cad/current-drawing` routes.
- No S6 business endpoints or SQLite audit.
- No S7 `--reset-local-token` CLI.
- No S8 CADDedupPlugin migration.
- No S9/S10 bridge or LISP shell work.
- No Python service route, schema, migration, tenant baseline, or CAD pool work.

## 2. Implementation

The new helper executable is a `net6.0-windows` `Microsoft.NET.Sdk.Web`
project with assembly name `yuantus-cad-helper`.

Startup flow:

1. Reject non-empty startup arguments as `HELPER_INPUT_VALIDATION_FAILED`.
2. Read or create the S1 `install-id.json` through `InstallId.GetOrCreate()`.
3. Try `Local\YuantusCadHelper-{installId}` through a named mutex lease.
4. If another instance owns the mutex, run the R3.2 recovery flow:
   - healthy `/healthz` -> exit `0`;
   - missing session file -> retry 500 ms up to three total attempts;
   - dead PID or image-path mismatch -> delete stale session file and retry;
   - live matching process but unhealthy HTTP -> `HELPER_UNHEALTHY` and do not
     delete the session file.
5. Bootstrap the local helper token before any healthy response:
   - reuse existing non-empty DPAPI token;
   - otherwise generate 32 random bytes, lowercase hex encode to 64 chars, and
     write through S1 `LocalTokenStore`.
6. Allocate the first bindable `127.0.0.1` port from `7959..7999`.
7. Publish the current-session discovery file through temp-file + atomic
   replace/move.
8. Start Kestrel with exactly one route: unauthenticated `GET /healthz`.
9. On normal stop, cancellation, or idle timeout, delete only the current
   session file.

## 3. Endpoint Boundary

S3 intentionally owns only:

```text
GET /healthz
```

The route returns JSON accepted by S1 `HelperProbe`:

```json
{"ok":true}
```

No local token, PLM token, tenant identity, origin process allowlist, CAD
context, session object, or user login is required or inspected in S3.

## 4. Token Bootstrap

Token bootstrap is encapsulated in `LocalTokenBootstrapper`:

- reads through `ILocalTokenStore.Read()`;
- reuses a non-empty token;
- writes a new 64-character lowercase hex token only when missing or empty;
- propagates S1 `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED` on write failure.

`HelperCommand` performs token bootstrap before session-file publish and before
the Kestrel host is started. The tests pin that a bootstrap write failure exits
without publishing a session file and without exposing a healthy helper.

## 5. Session File Lifecycle

`JsonSessionFileStore` publishes the R3.2 session schema:

- `schema_version`
- `session_id`
- `port`
- `pid`
- `image_path`
- `started_at`
- `protocol_version`
- `helper_version`
- `endpoints_base`

The final session file path is
`%APPDATA%\YuantusPLM\helper-session-{sessionId}.json`, matching S1
`HelperSessionFile`.

Publish writes a complete JSON document to a same-directory temp file and then
atomically replaces or moves it into the final path. Cleanup is best effort and
only targets the current session file.

## 6. Idle Timeout

S3 implements a default 30-minute idle timeout. Any accepted HTTP request,
including `/healthz`, refreshes activity.

`config.json` is intentionally limited to one optional field:

```json
{"idle_timeout_minutes": 30}
```

Only integer values in `1..1440` are accepted. Missing config, malformed JSON,
non-integer values, and out-of-range values fall back to 30 minutes.

## 7. Workflow Coverage

`.github/workflows/cad-helper-shared-dotnet.yml` now includes:

- `clients/cad-desktop-helper/Helper/**`
- `clients/cad-desktop-helper/Helper.Tests/**`
- `dotnet restore` for Helper.Tests
- `dotnet build` for Helper
- `dotnet test` for Helper.Tests

The workflow continues to restore/build/test Shared and Detector.

## 8. Verification

Local workstation:

- `.NET build/test`: blocked locally because `dotnet` is not installed
  (`zsh:1: command not found: dotnet`).
- XML project validation: passed for Helper, Helper.Tests, Shared, Detector,
  and Detector.Tests project files.
- Workflow YAML parse: passed.
- Scope-leak source scan over `clients/cad-desktop-helper/Helper`: no matches
  for S4/S7/S8 strings (`X-Yuantus-Local-Token`, `Authorization`,
  `/version`, `/session/`, business routes, `--reset-local-token`, SQLite, or
  CADDedupPlugin).
- Repository doc/drift checks:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

```bash
git diff --check
```

Local repository checks passed as `35 passed` plus clean whitespace check.

Required Windows verification before merge:

```bash
dotnet restore clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj --configuration Release --no-restore
dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj --configuration Release --no-restore
```

The dedicated `cad-helper-shared-dotnet` workflow run for the PR SHA is the
authoritative .NET signal.

GitHub Actions:

- Initial `.NET` run on `2029c3e` failed in `Build Helper` because
  `WebApplication.RunAsync(cancellationToken)` resolved to the .NET 6
  `string url` overload. The fix switched the helper host to
  `StartAsync(...)` + `WaitForShutdownAsync(...)`.
- Follow-up `.NET` run `26271074077`, job `77324419049`, passed for head
  `63f05d6`:
  - Restore Shared/Detector/Helper tests: success.
  - Build Shared/Detector/Helper: success.
  - Test Shared/Detector/Helper: success.
  - The existing `net6.0-windows` out-of-support warning remains inherited from
    S2 and is not introduced by S3.

## 9. Test Coverage

`HelperStartupContractTests.cs` implements the 21 taskbook-mandated tests:

1. loopback-only first-free port allocation;
2. no wildcard/random-port binding;
3. bare `/healthz` JSON response;
4. no healthy route before token bootstrap + session publish;
5. session schema parity with S1 `HelperSessionFile`;
6. current-session filename;
7. atomic publish with no partial final file;
8. shutdown cleanup;
9. new local-token shape;
10. existing local-token reuse;
11. bootstrap failure no session publish;
12. mutex name;
13. second healthy instance exit-zero;
14. missing session retry then `HELPER_SINGLETON_LOST`;
15. dead PID stale deletion;
16. PID-reuse image-path mismatch deletion;
17. unhealthy matching process no-delete;
18. idle timeout cleanup;
19. config override bounds;
20. S4/S7/S8 scope guard;
21. workflow Helper path/build/test guard.

It also adds one lifecycle hardening guard:

- install-id failure before mutex ownership must not delete an existing helper
  session file.

## 10. Next Slices

S4 remains the next logical CAD helper slice: local-token validation and origin
allowlist. It is not started by this PR and needs a separate taskbook and
implementation opt-in.
