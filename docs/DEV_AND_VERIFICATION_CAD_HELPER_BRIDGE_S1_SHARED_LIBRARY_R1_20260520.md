# Development And Verification: CAD Helper Bridge S1 Shared Library R1

Date: 2026-05-20

## 1. Goal

Implement the CAD Desktop Helper Bridge S1 contract from
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S1_SHARED_LIBRARY_20260520.md`.

This slice creates only the shared client-side library primitives. It does not
start the helper process as a server, implement Kestrel endpoints, migrate
`CADDedupPlugin`, write DWG data, scan CAD installations, or add any LISP bridge
behavior.

## 2. Delivered Files

Added:

- `.github/workflows/cad-helper-shared-dotnet.yml`
- `clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj`
- `clients/cad-desktop-helper/Shared/Identity/Paths.cs`
- `clients/cad-desktop-helper/Shared/Identity/SessionContext.cs`
- `clients/cad-desktop-helper/Shared/Identity/InstallId.cs`
- `clients/cad-desktop-helper/Shared/Security/DpapiEnvelope.cs`
- `clients/cad-desktop-helper/Shared/Security/LocalTokenStore.cs`
- `clients/cad-desktop-helper/Shared/Discovery/HelperSessionFile.cs`
- `clients/cad-desktop-helper/Shared/Discovery/HelperProbe.cs`
- `clients/cad-desktop-helper/Shared/Discovery/HelperSpawner.cs`
- `clients/cad-desktop-helper/Shared/Discovery/HelperLocator.cs`
- `clients/cad-desktop-helper/Shared/Transport/ErrorCodes.cs`
- `clients/cad-desktop-helper/Shared/Transport/HelperException.cs`
- `clients/cad-desktop-helper/Shared/Transport/ResponseEnvelope.cs`
- `clients/cad-desktop-helper/Shared/Transport/HelperTransport.cs`
- `clients/cad-desktop-helper/Shared/Registry/HkcuRegistry.cs`
- `clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj`
- `clients/cad-desktop-helper/Shared.Tests/SharedContractTests.cs`

Changed:

- `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`
- `docs/DELIVERY_DOC_INDEX.md`

## 3. Design

`Yuantus.Cad.Shared` is a multi-target library:

- `net46` for AutoCAD 2018-era .NET Framework plugin consumers.
- `net6.0-windows` for the future helper / detector Windows processes.

The library owns these S1 primitives:

- `%APPDATA%\YuantusPLM` path calculation and session-scoped helper file names.
- `InstallId.GetOrCreate()` with `FileMode.CreateNew`, IOException reread,
  parent-directory creation, and explicit corrupt-file classification.
- DPAPI CurrentUser protect / unprotect envelope and local token read / write.
- `HelperSessionFile` parse and current-session path selection.
- Bare `/healthz` probe that deliberately omits `X-Yuantus-Local-Token`.
- Deterministic helper spawn path with no CLI arguments.
- `HelperLocator` polling primitive for current-session discovery.
- `HelperTransport` for JSON, multipart-capable `HttpContent`, protocol header,
  local token header, response envelope unwrap, and one retry after local-token
  401.
- Read-only registry abstraction with `RegistryView.Registry64` default and
  explicit view override.

The implementation intentionally avoids Kestrel hosting, HTTP route handlers,
SQLite audit storage, PID/image-path singleton forensics, tenant / PLM token
headers, CAD command code, and plugin migration.

### 3.1 Impl-Time Decisions From The Taskbook Open Questions

The merged taskbook's §3.M left four implementation-time choices open. R1
resolves them as follows:

- Nullable reference types: disabled uniformly for R1. The Shared code stays on
  `LangVersion` 7.3 to keep the `net46` and `net6.0-windows` targets on one
  source profile with no public nullable-annotation drift. A future nullability
  tightening can be a separate source-only opt-in.
- Test framework: xUnit, with test parallelization disabled because S1 exposes
  test hooks for process-wide primitives such as `%APPDATA%`, DPAPI, and the
  registry backend.
- CI workflow: this PR now includes a focused Windows GitHub Actions workflow
  for `Yuantus.Cad.Shared` because macOS local verification cannot execute
  `net46;net6.0-windows` build/test and PR review explicitly needs a real
  Windows .NET gate.
- Logging: no logger dependency inside `Yuantus.Cad.Shared`. Primitive failures
  surface through `HelperException` with structured `Code`, `Retryable`, and
  `Details`; S3 helper hosting is the first slice that should wire logging.

The `net46` target also carries explicit `System.Net.Http` and
`System.Security` references. This mirrors the existing AutoCAD plugin's
explicit framework-reference style and reduces SDK-style net46 build ambiguity
for `HttpClient` and DPAPI.

The `net6.0-windows` target uses `Microsoft.Win32.Registry` `5.0.0` because
GitHub Actions restore proved there is no stable `6.0.0` package on nuget.org;
the first failed Windows run reported nearest `6.0.0-preview.5.21301.5`.

The test project pins `xunit` `2.4.2` and `xunit.runner.visualstudio` `2.4.5`
because the initial runner `2.5.6` requires `net462` and cannot restore for the
S1 baseline `net46` target.

## 4. Contract Tests

`SharedContractTests.cs` implements the 25 mandatory S1 test names from the
taskbook, plus three hardening regressions for reviewer / implementation risk:

- install-id atomic create, high concurrency, existing-file, parent-dir, and
  corruption cases; the extra hardening regression verifies the loser retries
  until a just-created existing file is readable instead of failing while the
  first writer still holds the exclusive handle or the file is momentarily
  zero-length.
- DPAPI local-token round trip and DPAPI failure mapping.
- bare health probe without local token header, plus rejection of unrelated
  plain-200 responses that do not carry the expected helper health JSON body.
- helper transport header injection, envelope unwrap, 426 handling, and 401
  local-token reread retry.
- helper spawner deterministic path / no-args / no reset-flag source guard.
- helper locator success and timeout behavior.
- helper session file parse, partial-write tolerance, and session-id path
  behavior.
- response envelope deserialization.
- read-only registry API surface and Registry64 / Registry32 view behavior.

## 5. R3.3 Micro-Amend

The implementation updates `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`
to match the merged S1 taskbook:

- Shared target framework is now `net46;net6.0-windows`.
- `InstallId.GetOrCreate()` is owned by S1 as a Shared primitive.
- S3 consumes that primitive to assemble the Mutex/session startup flow.
- S1 estimate is 2.5 days, and the overall plan estimate is 16 workdays.

## 6. Verification Commands

Expected Windows-capable verification:

```bash
dotnet build clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj
```

```bash
dotnet test clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj
```

Static and repository verification used on this workstation:

```bash
rg -n "Kestrel|WebApplication|MapGet|MapPost|SQLite|Sqlite|CommandMethod|CADDedupPlugin|MaterialSyncApiClient|DedupApiClient|--reset-local-token|SupportedOSPlatform" clients/cad-desktop-helper/Shared
```

```bash
xmllint --noout \
  clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj \
  clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py
```

```bash
git diff --check
```

## 7. Verification Results

- Source scope guard on production Shared files:
  `rg -n "Kestrel|WebApplication|MapGet|MapPost|SQLite|Sqlite|CommandMethod|CADDedupPlugin|MaterialSyncApiClient|DedupApiClient|--reset-local-token|SupportedOSPlatform" clients/cad-desktop-helper/Shared`
  -> no matches. The broader `clients/cad-desktop-helper` scan finds only the
  intentional `SharedContractTests.cs` source guard that asserts
  `HelperSpawner.cs` does not contain `--reset-local-token`.
- Registry write guard:
  `rg -n "SetValue|CreateSubKey|DeleteSubKey|DeleteValue|RegistryKey\.Set|RegistryKey\.Create|RegistryKey\.Delete" clients/cad-desktop-helper/Shared clients/cad-desktop-helper/Shared.Tests`
  -> no matches.
- Contract test inventory:
  `rg -n "public (async )?(Task|void) test_" clients/cad-desktop-helper/Shared.Tests/SharedContractTests.cs`
  -> 28 test methods present: 25 taskbook-mandatory names plus
  `test_install_id_create_race_retries_until_existing_file_is_readable`,
  `test_helper_probe_rejects_plain_200_without_expected_health_body`, and
  `test_helper_session_file_partial_write_returns_null`.
- XML validation:
  `xmllint --noout clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj`
  -> passed.
- Windows GitHub Actions verification:
  `.github/workflows/cad-helper-shared-dotnet.yml` runs `dotnet restore`,
  `dotnet build`, and `dotnet test` on `windows-latest` for the Shared test
  project.
- Doc index contract bundle:
  `4 passed in 0.03s`.
- `git diff --check`: clean.
- `.NET build/test`: blocked locally because this workstation does not have the
  .NET SDK installed (`zsh:1: command not found: dotnet`). These commands must
  be run on a Windows-capable .NET SDK environment before S1 can be merged as
  fully verified.

## 8. Next Slices

S2 through S11 remain unstarted and require separate opt-in. The next likely
implementation slice is S2 detector or S3 helper startup, but neither is implied
by this S1 implementation.
