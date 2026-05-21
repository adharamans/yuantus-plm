# Claude Taskbook: CAD Helper Bridge S1 — `Yuantus.Cad.Shared` Library

Date: 2026-05-20

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

CAD Desktop Helper Bridge **S1** (per #614 `fff93a2` §10, the
first of 11 slices). Establish the foundational
**`Yuantus.Cad.Shared`** multi-target library that every later
slice depends on:

- helper discovery (read `helper-session-{sessionId}.json`,
  spawn `yuantus-cad-helper.exe` if missing, probe `/healthz`);
- DPAPI envelope (read + write for the local token under fixed
  entropy);
- HTTP transport (`X-Yuantus-Local-Token` + `X-Yuantus-Protocol`
  header injection; unified `{ok,data?,error?}` envelope
  unwrap; multipart pass-through);
- error envelope (`HelperException` carrying §5.5 codes);
- `install-id.json` atomic generator (per #614 §5.1.1, R3.2);
- read-only HKCU/HKLM registry abstraction (consumed by S2
  detector).

**S2–S11 are all out of scope** — see §8. S1 is a *library*,
not a service, not a CLI, not a plugin. It exposes primitives
the later slices consume; it implements no helper-internal
behavior (Kestrel, singleton recovery, endpoints, audit) and no
plugin-internal behavior (CADDedupPlugin migration, LISP
bridge).

Prerequisites (merged): CAD desktop helper bridge R3.2 design
(#614 `fff93a2`). S1 implements primitives the design specifies.

## 2. Current Reality (grounded — direct file reads)

All citations verified by direct read against `origin/main =
fff93a2` (per [[feedback_verify_grounding_facts]]).

### Existing CAD-side client code (the future S8 consumer)

`clients/autocad-material-sync/CADDedupPlugin/`:

- **`CADDedupPlugin.csproj:16`** — AutoCAD 2018 baseline is
  `<TargetFrameworkVersion>v4.6</TargetFrameworkVersion>`;
  multi-config v4.6 / v4.8. Shared's `net46` target lines up
  exactly.
- **`MaterialSyncApiClient.cs:8`** — `using Newtonsoft.Json;` —
  Newtonsoft is the established JSON library on the CAD side.
  Lines 173/185 use `JsonConvert.SerializeObject` /
  `DeserializeObject`; lines 191/194/200/203/209/212/215 use
  `[JsonProperty]` attributes.
- **`MaterialSyncApiClient.cs:20-31`** — `private readonly
  HttpClient _httpClient = new HttpClient { ... };
  ConfigureHeaders()` per-request reconfiguration; async public
  surface (`Task<MaterialDiffPreviewResponse> DiffPreviewAsync`
  / `SyncInboundAsync` / `SyncOutboundAsync` / `ComposeAsync` /
  `ValidateAsync`). S8's migration target shape.
- **`DedupApiClient.cs:50-54`** —
  `MultipartFormDataContent` + `StreamContent` (multipart file
  upload for `/api/dedup/check`). Shared's transport must
  forward multipart bodies opaquely.

### Merged #614 design (the source of S1's contract)

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` (R3.2):

- **§5.1.1** (`install-id.json` atomic generation): `FileStream(
  path, FileMode.CreateNew, FileAccess.Write, FileShare.None)`
  → IOException(ERROR_FILE_EXISTS) → re-read; error code
  `HELPER_INSTALL_ID_UNAVAILABLE`.
- **§5.1.1 isolation table**: `install-id.json` is
  per-user-per-machine; `helper-session-{sessionId}.json` is
  per-session; `Local\YuantusCadHelper-{installId}` Mutex is
  per-user-per-session; cross-RDP-session sharing is **out of
  R3 scope** (open question #8). **Shared 端发现逻辑**: use
  `Process.GetCurrentProcess().SessionId` to compose the
  session-file path.
- **§5.2** discovery-file schema:
  `{schema_version, session_id, port, pid, image_path,
   started_at, protocol_version, helper_version,
   endpoints_base}`.
- **§5.3.1** DPAPI bootstrap — `(scope=CurrentUser,
  entropy="yuantus-cad-helper-local-token-v1")`; helper writes,
  Shared reads; **token never traverses HTTP**.
- **§5.3** key invariants — token injection at request layer;
  PLM layer-2 token (`Authorization: Bearer ...`) + `x-tenant-id`
  / `x-org-id` headers; primary defense is **PID + image-path
  whitelist** (S4 helper-side; out of S1).
- **§5.4** envelope: `Content-Type: application/json;
  charset=utf-8`; all responses `{ok:bool, data?:T, error?:
  {code, message, retryable, details}}`; **business errors
  return `200 OK` + `ok=false`**; only HTTP-layer (auth/origin)
  use 4xx. Protocol header: `X-Yuantus-Protocol: 1.0`; helper
  returns `426 Upgrade Required` on mismatch.
- **§5.5** error code prefixes (`HELPER_*` / `AUTH_*` /
  `ORIGIN_*` / `CAD_*` / `AUDIT_*` / `PLM_*` / `PROTO_*` /
  `HELPER_INPUT_*`). S1 ships the constants for **all** codes
  (Shared is the cross-cutting library) but **throws** only
  the ones derived from its own primitives:
  `HELPER_INSTALL_ID_UNAVAILABLE` (atomic-create both legs
  failed), `HELPER_DPAPI_UNAVAILABLE` (DPAPI read/write failure),
  `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED` (DPAPI write failure
  during bootstrap — write API ships in S1 even though the
  bootstrap *policy* of WHO calls it is S3),
  `AUTH_LOCAL_TOKEN_INVALID` / `AUTH_LOCAL_TOKEN_MISSING`
  (HTTP layer; S1 *decodes* these from server responses),
  `PROTO_VERSION_UNSUPPORTED` (426 decode).

### #614 §10 S1 work-breakdown entry (verbatim)

> S1 | `Yuantus.Cad.Shared`：多目标 `net46;net6.0` 工程结构；
> helper discovery + DPAPI 封装（bootstrap + reset） + HTTP
> transport + 错误信封 + 注册表抽象 | **2 天**（R2 1.5 天 +
> 多目标 / bootstrap 复杂度）

S1's dependency slice (per #614 §10 line 1071): "S1 是基础，
必须先做". S3/S4/S8/S9/S10 all transitively consume S1.

## 3. Design decisions

### 3.A Module layout — PRE-RATIFIED

`clients/cad-desktop-helper/Shared/` (new directory; one
csproj):

```
Yuantus.Cad.Shared/
├── Yuantus.Cad.Shared.csproj         <TargetFrameworks>net46;net6.0-windows</TargetFrameworks>
├── Identity/
│   ├── Paths.cs                      — deterministic %APPDATA% paths (install-id.json, helper.exe, session-file)
│   ├── InstallId.cs                  — atomic generator (§3.C)
│   └── SessionContext.cs             — Process.GetCurrentProcess().SessionId
├── Security/
│   ├── DpapiEnvelope.cs              — Protect / Unprotect (§3.D)
│   └── LocalTokenStore.cs            — typed accessor; entropy = "yuantus-cad-helper-local-token-v1"
├── Discovery/
│   ├── HelperSessionFile.cs          — read/parse helper-session-{sessionId}.json (schema §5.2)
│   ├── HelperSpawner.cs              — Process.Start deterministic path, bare args (§3.K)
│   ├── HelperProbe.cs                — parametrized GET /healthz, no token, 500 ms timeout (§3.J)
│   └── HelperLocator.cs              — high-level "ensure helper up → return base URL"
├── Transport/
│   ├── HelperTransport.cs            — HttpClient wrapper; injects X-Yuantus-Local-Token + X-Yuantus-Protocol; accepts HttpContent body (multipart-safe)
│   ├── ResponseEnvelope.cs           — { ok, data?, error? } DTO (Newtonsoft)
│   ├── ErrorCodes.cs                 — string constants for §5.5 codes (full list — see §2 above)
│   └── HelperException.cs            — typed exception { code, message, retryable, details }
└── Registry/
    └── HkcuRegistry.cs               — read-only HKCU/HKLM key wrapper (consumed by S2)
```

No `Logging/` — S1 is a library; logging is the caller's
responsibility. No `Hosting/` — Kestrel is S3. No `Cli/` — CLI
entry points are S7's helper command.

### 3.B Multi-target `net46;net6.0-windows` — RATIFIED (Medium fix 2026-05-20)

- `<TargetFrameworks>net46;net6.0-windows</TargetFrameworks>`
  in the csproj.
- **The net6.0 target uses the Windows-specific TFM
  `net6.0-windows`** (not bare `net6.0`). Rationale:
  `System.Security.Cryptography.ProtectedData` (DPAPI),
  `Microsoft.Win32.Registry`, and the helper-process consumer
  ecosystem (`yuantus-cad-helper.exe`, AutoCAD/ZWCAD plugins)
  are **Windows-only by design** — the `net6.0-windows` TFM
  expresses that constraint at the SDK / NuGet metadata layer
  and propagates it to consumers automatically. The CA1416
  platform-compatibility analyzer recognizes the `-windows`
  TFM suffix and treats the whole assembly as Windows-targeted
  with no additional attribute needed.
- **Medium finding fix (2026-05-20):** an earlier draft of
  this taskbook proposed `<SupportedOSPlatform>Windows</SupportedOSPlatform>`
  as an MSBuild property. That property does **not** exist in
  the standard .NET SDK schema and would have been silently
  ignored. The standard .NET SDK mechanisms are: (a) the TFM
  suffix `net6.0-windows` (this taskbook's ratified choice);
  (b) the assembly/type-level attribute
  `[SupportedOSPlatform("windows")]` from
  `System.Runtime.Versioning` (recorded rejected — the TFM
  already constrains the assembly, attribute would be
  redundant); (c) the build-time property
  `<EnableWindowsTargeting>true</EnableWindowsTargeting>`
  (only relevant when cross-building a `net6.0-windows`
  project from a non-Windows host — Yuantus CI runs on
  GitHub Actions Windows runners per #614 §5.10, so this is
  **not required** here; documented for future cross-platform
  CI changes).
- Single-source: aim for **zero `#if` blocks** in production
  files. Only one expected `#if NETFRAMEWORK` site (if any) —
  in `DpapiEnvelope.cs` if the net6.0 NuGet's `using` namespace
  differs (it does not on the current
  `System.Security.Cryptography.ProtectedData` package; verify
  at impl time). State "zero `#if` is the goal" as a
  hard-to-enforce style rule (not test-pinned, but reviewer
  guidance).
- AutoCAD 2018 baseline is `v4.6`; netstandard2.0's official
  matrix requires `v4.6.1+`; multi-target `net46;net6.0-windows`
  is the correct choice — netstandard2.0 is explicitly
  **rejected**.

NuGet packages (pinned versions are the impl PR's call;
the taskbook pins **identity**):

- `Newtonsoft.Json` (both targets — matches existing CADDedupPlugin)
- `System.Security.Cryptography.ProtectedData` (net6.0 only;
  net46's BCL has DPAPI natively)
- `System.Net.Http` (already in net46 BCL; net6.0 BCL has
  HttpClient — no extra package needed for basic use)

### 3.C `InstallId` atomic generator — PRE-RATIFIED

Mirrors #614 §5.1.1 exactly:

```
public static class InstallId {
    public static Guid GetOrCreate() {
        // 1. Try FileMode.CreateNew exclusive create
        // 1a. Success → write { schema_version: "1.0",
        //                       install_id: Guid.NewGuid(),
        //                       created_at: <iso8601> }, flush, close
        // 1b. IOException(ERROR_FILE_EXISTS) → fall through to step 2
        // 2. Open with FileShare.Read, deserialize, return install_id
        // Any other Exception → throw HelperException(HELPER_INSTALL_ID_UNAVAILABLE)
    }
}
```

`Paths.InstallIdFile` constant: `%APPDATA%\YuantusPLM\install-id.json`.

### 3.D `DpapiEnvelope` + `LocalTokenStore` — PRE-RATIFIED

`DpapiEnvelope`:

```
public static class DpapiEnvelope {
    public static byte[] Protect(byte[] data, byte[] entropy);
    public static byte[] Unprotect(byte[] encrypted, byte[] entropy);
}
```

Wraps `ProtectedData.Protect` / `Unprotect` with
`DataProtectionScope.CurrentUser`. Both targets use identical
source (net46 BCL + net6.0 NuGet). On read/write failure throw
`HelperException(HELPER_DPAPI_UNAVAILABLE)`.

`LocalTokenStore`:

```
public static class LocalTokenStore {
    // entropy = UTF8 bytes of "yuantus-cad-helper-local-token-v1"
    public static string ReadLocalToken();              // → hex string, or null if absent
    public static void WriteLocalToken(string hexToken); // throws HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED on failure
}
```

The `WriteLocalToken` API ships in S1 even though the **policy**
of WHO calls it (helper during bootstrap, only) is S3's. S1 is
the primitive provider; S3/S7 own the bootstrap/reset flow.

### 3.E Discovery primitives — PRE-RATIFIED

`HelperSessionFile`:

```
public sealed class HelperSessionFile {
    public string SchemaVersion;
    public int SessionId;
    public int Port;
    public int Pid;
    public string ImagePath;
    public DateTimeOffset StartedAt;
    public string ProtocolVersion;
    public string HelperVersion;
    public string EndpointsBase;

    public static HelperSessionFile? Read();  // current SessionId; null if file absent
    public static string Path { get; }        // %APPDATA%\YuantusPLM\helper-session-{sessionId}.json
}
```

`HelperSpawner`: see §3.K.

`HelperProbe`: see §3.J.

`HelperLocator` — high-level orchestrator:

```
public sealed class HelperLocator {
    public Task<Uri> EnsureHelperRunningAsync(CancellationToken ct);
    // 1. Read HelperSessionFile.Read() for current session
    // 2. If present → HelperProbe.HealthAsync(port) → if 200, return Uri
    // 3. If absent or unhealthy → HelperSpawner.Spawn() → poll /healthz up to 5s
    // 4. On 5s timeout → throw HelperException(HELPER_PORT_BUSY / HELPER_SINGLETON_LOST as appropriate)
}
```

`HelperLocator` does NOT implement the §5.1 step-5/6 PID +
image-path forensics or the `HELPER_UNHEALTHY` vs
`HELPER_SINGLETON_LOST` decision branch — that is **S3's
helper-internal startup flow** (see §3.J).

### 3.F Transport primitives — PRE-RATIFIED

`HelperTransport`:

```
public sealed class HelperTransport {
    public HelperTransport(Uri baseUri, HttpClient? httpClient = null);

    // Generic JSON post that handles envelope unwrap + typed exceptions
    public Task<T> PostJsonAsync<T>(string path, object payload, CancellationToken ct);
    // Multipart pass-through (DedupApiClient migration in S8)
    public Task<T> PostContentAsync<T>(string path, HttpContent content, CancellationToken ct);
    // Bare GET (for /version etc.)
    public Task<T> GetAsync<T>(string path, CancellationToken ct);

    // Header injection (every request EXCEPT probe):
    //   X-Yuantus-Local-Token: <hex from DPAPI>
    //   X-Yuantus-Protocol:    1.0
    //   Content-Type:          application/json; charset=utf-8  (PostJsonAsync only)

    // Envelope unwrap:
    //   200 OK + {ok:true,  data:T}     → return data
    //   200 OK + {ok:false, error:{...}} → throw HelperException
    //   401 + {error:{code:AUTH_LOCAL_TOKEN_INVALID|MISSING}} → reread DPAPI once, retry once; if still 401 → throw
    //   426 Upgrade Required             → throw HelperException(PROTO_VERSION_UNSUPPORTED)
    //   other 4xx/5xx                    → throw HelperException(synthesize code from status)
}
```

The 401-reread-once is the §5.3.1 "Token 失效自修复" path —
implemented inside S1's transport, not at every call site.

### 3.G Registry abstraction — PRE-RATIFIED

`HkcuRegistry`: read-only wrapper around `Microsoft.Win32.Registry`
hives (`HKCU`, `HKLM`). Used by S2 detector to scan
`HKLM\SOFTWARE\Autodesk\AutoCAD\*` / `HKLM\SOFTWARE\ZWSOFT\*`
/ `HKLM\SOFTWARE\Gstarsoft\GstarCAD\*`. Read-only is enforced
at API surface (no `SetValue` / `Create` methods). Both targets
have `Microsoft.Win32.Registry` in BCL.

### 3.H JSON library — RATIFIED: Newtonsoft.Json

The existing CADDedupPlugin uses Newtonsoft uniformly
(`MaterialSyncApiClient.cs:8` + `JsonConvert` + `[JsonProperty]`
throughout). Shared **MUST** match so S8's migration is a
textual swap (replace `_httpClient.PostAsync(url,
new StringContent(JsonConvert.SerializeObject(payload)))` →
`_helperTransport.PostJsonAsync<T>(path, payload)`), not an
attribute-replacement exercise across DTOs.

Alternative `System.Text.Json` is **rejected** — it would
force every existing `[JsonProperty]`-attributed DTO in
CADDedupPlugin to be retrofitted with `[JsonPropertyName]` at
S8 time, and would put two JSON libraries in the same acad.exe
process.

### 3.I HTTP client pattern — PRE-RATIFIED

Mirror the existing `MaterialSyncApiClient` shape so S8's
migration is mechanical:

- `private readonly HttpClient _httpClient` (one per
  HelperTransport instance);
- `async Task<T>` public surface;
- per-request header injection inside the transport (the
  existing `ConfigureHeaders()` pattern becomes per-request
  rather than per-instance — necessary because `X-Yuantus-Local-
  Token` is re-read on 401, can change at runtime);
- accept `HttpContent` (not just JSON) as request body so
  `MultipartFormDataContent` from `DedupApiClient` works
  unchanged.

### 3.J `HelperProbe` — DUAL-CONSUMER, PRE-RATIFIED

The probe primitive is `(host: "127.0.0.1", port: int, timeout:
500 ms) → 200|non-200|timeout`. **Bare GET, NO `X-Yuantus-Local-
Token` injected.** Two distinct consumers in the full system:

- **Discovery side (S8 / S9 / Tauri-out-of-R3 → S1
  `HelperLocator`)** — "is the helper I just discovered
  serving?" — result drives spawn-if-missing.
- **Helper-internal singleton recovery (S3 inside helper.exe
  → §5.1 step 4)** — "is another helper instance live?" — runs
  against potentially-stale port from a session file the
  current process does not own.

S1 owns the **primitive** (parametrized on host+port). S1 does
**NOT** own the §5.1 step-5/6 PID + image-path forensics or
the `HELPER_UNHEALTHY` vs `HELPER_SINGLETON_LOST` decision —
that lives in S3's helper-internal startup flow and must NOT
leak into Shared.

### 3.K `HelperSpawner` — PRE-RATIFIED

```
public static class HelperSpawner {
    public static Process Spawn();  // throws HelperException on Start failure
}
```

Pinned constants in `Paths.cs`:

- `HelperExePath` = `%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe`
- spawn arguments: **empty** (helper is service-mode-by-default
  per #614 §5.1 line 288). S1's spawner **MUST NEVER** pass
  `--reset-local-token` (S7's interactive-only CLI flag).
  Pinned by an AST/source-scan guard test (§5).

Wait-for-`/healthz` is `HelperLocator`'s job, not `HelperSpawner`'s
— Spawner returns the started Process; Locator polls.

Polling: 100 ms interval × max 50 attempts = **5 s ceiling**
(matches #614 §5.1 line 286). On timeout → throw
`HelperException(HELPER_PORT_BUSY)` (or `HELPER_SINGLETON_LOST`
if the spawn itself raced).

## 4. R1 Target Output (for the impl PR)

- New `clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj`
  + the module-layout source files in §3.A.
- `<TargetFrameworks>net46;net6.0-windows</TargetFrameworks>`
  in the csproj (the Windows-specific TFM is the SDK-standard
  mechanism; see §3.B for the full rationale + Medium fix).
- NuGet refs per §3.B (Newtonsoft.Json,
  System.Security.Cryptography.ProtectedData).
- No solution-file edit, no CADDedupPlugin edit, no helper exe,
  no detector exe. The S1 PR contains **only** the
  `clients/cad-desktop-helper/Shared/` subtree + tests + the
  S1 DEV/verification MD + the index line.
- No service registration, no DI container wiring, no
  configuration files added to deploy targets, no HKCU/HKLM
  writes (S1's Registry abstraction is read-only).

## 5. Tests Required (in the impl PR)

MANDATORY exactly-named (new file
`clients/cad-desktop-helper/Shared.Tests/SharedContractTests.cs`
or equivalent — final test-file naming is impl-PR's call, but
the test **names** are mandatory). Both targets compile + run.

- **`test_install_id_atomic_create_race`** — two threads /
  tasks racing `InstallId.GetOrCreate()` against a clean
  `%APPDATA%\YuantusPLM\install-id.json`: exactly one writes,
  both return identical Guid; one `FileMode.CreateNew`
  succeeds, the other receives `IOException` and reads.
- **`test_install_id_existing_file_is_read_not_overwritten`**
  — pre-create the file with a known Guid; call
  `GetOrCreate()`; assert the Guid is returned unchanged and
  the file's `created_at` unchanged.
- **`test_install_id_non_io_exception_throws_helper_install_id_unavailable`**
  — mock `FileStream` ctor to throw `UnauthorizedAccessException`
  → `HelperException` with code `HELPER_INSTALL_ID_UNAVAILABLE`.
- **`test_dpapi_local_token_round_trip`** — `WriteLocalToken`
  then `ReadLocalToken` → byte-for-byte equality.
- **`test_dpapi_unavailable_throws_helper_dpapi_unavailable`**
  — patch the entropy or mock `ProtectedData.Protect` to throw
  → `HelperException(HELPER_DPAPI_UNAVAILABLE)`.
- **`test_helper_probe_bare_get_no_token_header_injected`** —
  capture the outbound `HttpRequestMessage` (test
  `HttpMessageHandler`); assert no `X-Yuantus-Local-Token`
  header is present on the probe request.
- **`test_helper_transport_injects_local_token_and_protocol_header`**
  — non-probe request through `HelperTransport.PostJsonAsync`;
  assert both `X-Yuantus-Local-Token` and `X-Yuantus-Protocol`
  headers present + correct values.
- **`test_helper_transport_unwraps_ok_false_envelope_to_typed_exception`**
  — `200 OK` + `{"ok":false,"error":{"code":"PLM_VALIDATION_FAILED",
  "message":"...","retryable":true}}` → `HelperException` with
  `Code="PLM_VALIDATION_FAILED"`, `Retryable=true`.
- **`test_helper_transport_426_throws_proto_version_unsupported`**.
- **`test_helper_transport_401_invalid_token_rereads_dpapi_once_and_retries`**
  — first request returns `401 AUTH_LOCAL_TOKEN_INVALID`;
  assert `LocalTokenStore.ReadLocalToken()` called a second
  time; assert one retry; if still 401, exception propagates.
- **`test_helper_spawner_uses_deterministic_path_no_args_service_mode`**
  — AST or spy guard: `HelperSpawner.Spawn()` invokes
  `Process.Start` (or `ProcessStartInfo` constructor) with
  `FileName == Paths.HelperExePath` and
  `Arguments` empty/whitespace. Source-scan also asserts
  `--reset-local-token` is **not** referenced anywhere in
  `Spawner.cs` (S1 never spawns the CLI command).
- **`test_helper_locator_waits_up_to_5s_then_throws_on_unresponsive`**
  — mock probe never returns 200; assert ~5 s elapsed (with
  reasonable tolerance), typed timeout exception.
- **`test_helper_locator_returns_uri_when_probe_succeeds_first_try`**
  — existing session file + probe returns 200 immediately →
  return `Uri` without spawn attempt.
- **`test_helper_session_file_parses_full_schema`** — feed
  the §5.2 schema JSON; assert all 9 fields populated
  correctly.
- **`test_helper_session_file_path_includes_current_session_id`**
  — `HelperSessionFile.Path` ends with
  `helper-session-{Process.GetCurrentProcess().SessionId}.json`.
- **`test_response_envelope_deserializes_ok_true_payload_data`**
  — `{"ok":true,"data":{...}}` → typed `data` populated.
- **`test_registry_abstraction_is_read_only_no_set_or_create_methods`**
  — Reflection / API-surface guard: `HkcuRegistry` class
  exposes only `Get*` methods; no `SetValue` / `Create*` /
  `Delete*` in the public surface.

**Multi-target acceptance:** the test project itself is
multi-target `net46;net6.0-windows`; `dotnet test` runs both.
CI matrix must include both target runs (impl-PR's CI config
concern).

## 6. Verification Commands (impl PR)

```bash
# Build both targets
dotnet build clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj
dotnet test  clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj

# Doc-index regression
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

`len(app.routes)` stays at the current main value (677 from
Tier-B #3 work; S1 adds no Python service route).

## 7. DEV/verification MD requirements (impl PR)

`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S1_SHARED_LIBRARY_R1_20260520.md`
+ index line. Must document: the §3.B multi-target build
verification (both `lib/net46/` and `lib/net6.0-windows/`
produced under the package output);
the §3.C atomic-`install-id` race-test proof; the §3.D DPAPI
round-trip proof; the §3.J probe-token-decoupling proof; the
§3.K spawner deterministic-path + no-args proof; the §3.H
Newtonsoft.Json ratification rationale; inter-slice status
(S2–S11 unstarted; per-slice opt-in still required).

## 8. Non-Goals (hard boundaries for the impl PR)

**S2–S11 are explicitly OUT** — Shared is a *library*:

- **S2 detector exe** — separate `yuantus-cad-detector.csproj`;
  S1 only ships the `HkcuRegistry` abstraction it will consume.
- **S3 helper exe** — Kestrel host, port allocation, singleton
  recovery decision (HELPER_UNHEALTHY / HELPER_SINGLETON_LOST
  branches in §5.1 step 5/6), idle timeout, /healthz endpoint
  implementation, helper-session file lifecycle (write on
  start, delete on exit). S1 only ships the *primitives* the
  helper will use.
- **S4** helper auth + origin allowlist (PID + image-path
  whitelist; signature check).
- **S5** helper `/session/login` / `/session/logout` /
  `/session/status` / `/cad/current-drawing` endpoints.
- **S6** helper `/diff/preview` / `/sync/inbound` /
  `/sync/outbound` / `/audit/apply-result` business logic;
  SQLite audit DB; `pull_id` cache.
- **S7** helper `--reset-local-token` CLI command (interactive
  terminal detection; non-HTTP exposure). S1 provides the
  `LocalTokenStore.WriteLocalToken` primitive but **NEVER**
  the CLI; S1's spawner is forbidden from passing
  `--reset-local-token`.
- **S8** `CADDedupPlugin` refactor (`MaterialSyncApiClient` +
  `DedupApiClient` migration to Shared with multipart support).
- **S9** `YuantusCadHelperBridge.dll` (NETLOAD adapter +
  `(yuantus-helper-call …)` LISP function).
- **S10** ZWCAD/GstarCAD LISP thin shell.
- **S11** integration testing, verification scripts, runbook
  documentation.

**Shared library hard boundaries:**

- No Kestrel / no ASP.NET Core hosting. S1 is a library, not a
  service.
- No SQLite / no audit storage.
- No business endpoints (`/diff/preview` etc.) implemented in
  Shared.
- No CLI entry point in Shared (`Program.cs` is forbidden in
  Shared).
- No singleton-recovery PID + image-path forensics — that's
  S3's helper-internal flow.
- No CADDedupPlugin edit.
- No DWG read/write (CAD-side concern; never enters Shared).
- No HKCU/HKLM **write** — registry abstraction is read-only.
- No new Python service route; `len(app.routes)` unchanged.
- No new schema / migration / tenant baseline.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude or the project owner
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/cad-helper-bridge-s1-shared-library-r1-20260520`.

Follow-ups (each its own opt-in): S2 detector, S3 helper, S4
auth, S5 session endpoints, S6 business endpoints + audit, S7
CLI, S8 plugin refactor, S9 LISP bridge, S10 LISP thin shell,
S11 integration. **Never parallelize Tier-B-class slices**
(serialization rule per [[feedback_phase_optin]]).

## 10. Reviewer Focus

- **§3.A module layout** — five top-level namespaces
  (Identity / Security / Discovery / Transport / Registry).
  Push back if any module belongs elsewhere or if a missing
  module is required for S1.
- **§3.H JSON library — RATIFIED Newtonsoft.Json.** Recorded
  rejected: System.Text.Json (would require attribute
  retrofit across CADDedupPlugin's DTOs at S8 time + two JSON
  libraries in the same process). Push back only if a concrete
  reason to use STJ emerges (security CVE, perf, etc.).
- **§3.J `HelperProbe` dual consumer.** Confirm S1 owns the
  parametrized primitive and S1 does **NOT** own the §5.1
  step-5/6 PID + image-path forensics (which is S3). The
  taskbook is explicit so the impl PR doesn't accidentally
  pull S3 logic into Shared.
- **§3.K spawner contract** — deterministic path constant,
  bare args, **never** `--reset-local-token`. Confirm the
  source-scan guard test (§5) catches a future drift.
- **§3.B multi-target — RATIFIED `net46;net6.0-windows`**
  (Medium-fix 2026-05-20). netstandard2.0 explicitly rejected
  due to the AutoCAD 2018 v4.6 baseline; the Windows-specific
  TFM `net6.0-windows` is the SDK-standard mechanism for
  expressing the Windows-only constraint (DPAPI / Registry /
  helper.exe consumer ecosystem). The invented
  `<SupportedOSPlatform>` MSBuild property from an earlier
  draft is **rejected** (does not exist in the .NET SDK schema
  and would be silently ignored); the
  `[SupportedOSPlatform("windows")]` attribute is **redundant**
  given the TFM suffix; `<EnableWindowsTargeting>true</EnableWindowsTargeting>`
  is **not required** under current Windows-runner CI but is
  documented as the SDK escape hatch for future cross-Linux
  builds.
- **§5 MANDATORY tests** — confirm the 17 named tests cover
  the §3 contract floor; flag any §3 primitive that lacks a
  test pin.
- **§8 non-goals** — confirm S2–S11 are correctly listed; any
  S1 leakage into another slice's scope must be flagged.
- Did anything pre-decide a S2+ slice or touch CADDedupPlugin /
  helper exe / detector exe? It must not.
