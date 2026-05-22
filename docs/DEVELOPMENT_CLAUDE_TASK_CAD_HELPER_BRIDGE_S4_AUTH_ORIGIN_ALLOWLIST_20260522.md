# Claude Taskbook: CAD Helper Bridge S4 - Auth And Origin Allowlist

Date: 2026-05-22

Type: **Doc-only taskbook.** Changes no runtime, no schema, no service, no
workflow, and no CAD plugin code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook does
NOT authorize that implementation.

## 1. Purpose

CAD Desktop Helper Bridge **S4** (per #614 `fff93a2` section 10) adds the first
helper-side security gate on top of the S3 startup executable:

- local-helper-token validation for every non-exempt helper request;
- protocol-version validation for every non-exempt helper request;
- origin process allowlisting by socket peer PID plus image-name/path matching;
- JSON error envelopes for auth/origin/protocol failures;
- explicit `/healthz` exemption preserving the S3 bare health probe;
- source and test guards preventing S5-S11 scope creep.

S4 is deliberately a security-pipeline slice. It does **not** add
`/session/*`, `/version`, `/cad/current-drawing`, business forwarding routes,
SQLite audit, `--reset-local-token`, plugin migration, LISP bridge code, or CAD
writes. Those remain S5-S11, each requiring its own taskbook and implementation
opt-in.

Prerequisites already merged:

- #614 `fff93a2`: CAD helper bridge R3.2 design.
- #616 `bd61af2`: S1 Shared taskbook.
- #617 `2740865`: S1 Shared implementation.
- #618 `db1d3de`: S2 Detector implementation and Windows .NET CI wiring.
- #619 `13bf4d2`: S3 Helper startup taskbook.
- #620 `e0c76e8`: S3 Helper startup implementation.

## 2. Current Reality (grounded by direct reads)

Grounded against `origin/main = e0c76e8`.

### 2.1 R3.2 design source

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S4 security
surface:

- Section 5.3 names two token layers:
  - layer 1 `local-helper-token`, stored with Windows DPAPI current-user scope;
  - layer 2 `plm-user-token`, also DPAPI-backed but requiring session/login
    state.
- Section 5.3 explicitly positions `X-Yuantus-Local-Token` as browser/cross-site
  accidental-call protection, not the only local security boundary.
- Section 5.3 states the helper is the local-token producer and `Yuantus.Cad.Shared`
  is the local-token consumer.
- Section 5.3 states each protected helper request must include
  `X-Yuantus-Local-Token: <hex>`.
- Section 5.3 states the helper should compare the request header against the
  in-memory token loaded/generated at helper startup.
- Section 5.3 states the main local boundary is origin PID plus process image
  allowlisting.
- Section 5.3 states `--reset-local-token` is an interactive local command and
  must not be exposed through any HTTP route.
- Section 5.4 defines `/healthz` as auth-exempt and origin-exempt.
- Section 5.4 defines `X-Yuantus-Protocol: 1.0`; incompatible protocol returns
  `426 Upgrade Required`.
- Section 5.8 says CORS must not be enabled for browser calls to become viable.
- Section 5.9 defines `origin_whitelist` in `%APPDATA%\YuantusPLM\config.json`.
- Section 10 defines S4 as "helper: DPAPI token layer 1/2 auth + source PID +
  path allowlist".

### 2.2 S1 Shared implementation already available

`clients/cad-desktop-helper/Shared/` already provides the client-side surfaces
S4 must respect:

- `Security/LocalTokenStore.cs`
  - `ReadLocalToken()` and `WriteLocalToken()` use the S1 DPAPI envelope;
  - local-token write failure maps to `HELPER_LOCAL_TOKEN_BOOTSTRAP_FAILED`;
  - local-token read failure maps to `HELPER_DPAPI_UNAVAILABLE`.
- `Transport/HelperTransport.cs`
  - injects `X-Yuantus-Local-Token` when a non-empty token is available;
  - always injects `X-Yuantus-Protocol: 1.0`;
  - retries exactly once after `401` with `AUTH_LOCAL_TOKEN_INVALID` or
    `AUTH_LOCAL_TOKEN_MISSING`, by re-reading DPAPI through its token reader.
- `Transport/ErrorCodes.cs`
  - already declares `AUTH_LOCAL_TOKEN_MISSING`;
  - already declares `AUTH_LOCAL_TOKEN_INVALID`;
  - already declares `ORIGIN_PROCESS_NOT_ALLOWED`;
  - already declares `PROTO_VERSION_UNSUPPORTED`.

S4 must not edit S1 transport behavior unless the implementation finds a direct
contract contradiction. The expected S4 work is helper-side enforcement.

### 2.3 S3 Helper startup implementation now available

`clients/cad-desktop-helper/Helper/` now provides the host S4 will extend:

- `HelperRuntime.Default` wires paths, install-id provider, token store, random
  bytes, port allocator, session-file store, mutex factory, health probe, process
  inspector, delay, clock, error writer, and Kestrel host runner.
- `HelperCommand.RunAsync(...)` currently rejects all CLI args; S7 owns
  `--reset-local-token`.
- `HelperCommand.RunAsync(...)` performs local-token bootstrap before session-file
  publish and before Kestrel host start.
- `LocalTokenBootstrapper.EnsureToken()` returns the existing token or writes a
  new 64-character lowercase hex token.
- `KestrelHelperHostRunner` currently exposes exactly one endpoint:
  `GET /healthz`.
- S3 source and tests intentionally assert no S4/S7/S8 scope leak.

This means S4 can enforce security globally without adding production routes:

- `/healthz` remains exempt and returns `200`.
- `/version` is recognized as a future exempt path but remains unimplemented
  until S5; it should fall through to routing after exemption, not return an
  auth error.
- All other paths are protected by the S4 gate. If auth/origin/protocol passes
  and no route exists yet, routing returns `404`. This is expected before S5.

### 2.4 Current Windows .NET workflow

`.github/workflows/cad-helper-shared-dotnet.yml` now restores, builds, and tests:

- Shared;
- Detector;
- Helper.

The S4 implementation PR must keep this workflow covering Helper and
Helper.Tests. No new workflow is required unless the implementation adds a
separate project, which S4 must not do.

## 3. Ratified S4 Boundaries

### 3.A S4 owns a helper-side security gate, not new helper routes

S4 implementation owns helper-side primitives and Kestrel integration for:

- local-token validation;
- protocol-version validation;
- origin process allowlisting;
- error-envelope responses for security failures.

S4 must not add production `MapGet`, `MapPost`, `MapPut`, `MapDelete`, or
equivalent endpoint declarations beyond S3's existing `/healthz`.

Tests may create a test-only route or call the security gate directly. That
test-only route must live only in `Helper.Tests`; it must not appear in
`clients/cad-desktop-helper/Helper/`.

### 3.B Exempt paths

S4 recognizes exactly these exempt paths:

```text
GET /healthz
GET /version
```

Exempt matching is ordinal case-insensitive and exact on the normalized request
path:

- query strings do not affect matching;
- `/healthz/` and `/version/` are not exempt;
- `/Healthz` and `/Version` are exempt because path comparison is
  case-insensitive;
- method must match exactly (`GET` only).

`/healthz` is implemented by S3 and remains:

- no local token;
- no protocol header;
- no origin process check;
- no CORS header;
- response body remains `{"ok":true}`.

`/version` is reserved for S5. S4 must treat it as exempt so S5 can later add
the route without changing auth policy. Before S5, `GET /version` may fall
through to `404`, but it must not return `401`, `403`, or `426` from S4.

No other path is exempt. In particular, `/session/status`, `/session/login`,
`/cad/current-drawing`, `/diff/preview`, `/sync/inbound`, `/sync/outbound`,
`/dedup/check`, `/shell/notify`, `/audit/apply-result`, and future business
routes are protected even before the routes exist.

### 3.C Local token policy

S4 validates `X-Yuantus-Local-Token` before protocol and origin checks.

Rationale: token-first prevents unauthenticated protocol probing. A stale or
missing-token client sees `401` first, refreshes through S1 `HelperTransport`,
and only then sees a `426` protocol error if it is genuinely incompatible.

Rules:

- Missing header, empty header, or whitespace-only header returns HTTP `401`
  with error code `AUTH_LOCAL_TOKEN_MISSING`.
- Header present but not exactly equal to the bootstrapped in-memory local token
  returns HTTP `401` with error code `AUTH_LOCAL_TOKEN_INVALID`.
- Malformed token strings are invalid, not missing.
- S4 must compare using fixed-time byte comparison after UTF-8 encoding. It must
  not use `==`, `string.Equals`, or case-insensitive comparison for token
  equality.
- S4 uses the token returned by S3 `LocalTokenBootstrapper.EnsureToken()` and
  keeps it in memory for request validation. It must not re-read DPAPI on every
  request.
- S4 must not log or echo the token value. Error messages must mention the error
  class, not the supplied token.

This preserves S1 `HelperTransport`'s "retry after 401 by re-reading DPAPI"
behavior: the client side refreshes, while the helper side validates against
its current in-memory token.

S7 `--reset-local-token` must account for this in-memory token decision. A
running helper will not observe a DPAPI token rotation until it restarts or S7
explicitly adds a separate restart/reload contract. S4 does not solve reset
semantics.

### 3.D Protocol version policy

After local-token validation succeeds, S4 validates `X-Yuantus-Protocol`.

Rules:

- Missing header returns HTTP `426` with error code `PROTO_VERSION_UNSUPPORTED`.
- Any value other than exactly `1.0` returns HTTP `426` with error code
  `PROTO_VERSION_UNSUPPORTED`.
- Multiple header values are unsupported and return `426`.
- Protocol comparison is ordinal and exact.

Protocol validation is exempt for `/healthz` and `/version`.

### 3.E Origin process allowlist policy

After local-token and protocol validation succeed, S4 validates the origin
process. This is the main same-user local boundary described in #614.

Rules:

- S4 must resolve the peer process from the accepted loopback TCP connection,
  not from HTTP headers.
- The resolver must use the request connection's local endpoint plus remote
  endpoint and a Windows TCP table source such as `GetExtendedTcpTable`.
- The resolver must not trust `Origin`, `Referer`, `X-Forwarded-For`,
  `User-Agent`, or any caller-supplied HTTP header as the origin identity.
- If the peer PID cannot be resolved, returns no process, the process has
  exited, or the image path cannot be read, S4 fails closed with HTTP `403` and
  error code `ORIGIN_PROCESS_NOT_ALLOWED`.
- If the peer process image name and image path do not match an allowlist entry,
  S4 fails closed with HTTP `403` and error code `ORIGIN_PROCESS_NOT_ALLOWED`.
- Matching is case-insensitive on Windows.
- `image_name` is an exact file-name match.
- `path_pattern` is a full-path glob. `*` and `?` are allowed. Partial substring
  matching is not allowed; the pattern must match the whole image path.

Default allowlist entries mirror #614:

```json
[
  {
    "image_name": "acad.exe",
    "path_pattern": "C:\\Program Files\\Autodesk\\AutoCAD*\\acad.exe"
  },
  {
    "image_name": "ZWCAD.exe",
    "path_pattern": "C:\\Program Files\\ZWSOFT\\ZWCAD*\\ZWCAD.exe"
  },
  {
    "image_name": "gscad.exe",
    "path_pattern": "C:\\Program Files\\Gstarsoft\\GstarCAD*\\gscad.exe"
  },
  {
    "image_name": "yuantus-tauri-companion.exe",
    "path_pattern": "*\\YuantusPLM\\companion\\yuantus-tauri-companion.exe"
  }
]
```

### 3.F `origin_whitelist` config policy

S4 may read only the `origin_whitelist` field from
`%APPDATA%\YuantusPLM\config.json`, in addition to S3's existing
`idle_timeout_minutes`.

Rules:

- Missing config file uses defaults.
- Missing `origin_whitelist` uses defaults.
- Malformed JSON uses defaults.
- Malformed `origin_whitelist` uses defaults.
- Valid configured entries extend defaults; they do not replace defaults.
- Duplicate entries are allowed but must be normalized deterministically in
  memory.
- S4 must not implement `server_url`, `tenant_id`, `org_id`,
  `default_profile_id`, `server_allowlist`, `log_level`, or any PLM/session
  behavior from `config.json`. Those belong to later slices.

This "extend, not replace" policy avoids an accidental config typo removing the
CAD executable defaults and turning S4 into a self-inflicted outage.

### 3.G RATIFIED: PLM bearer token is deferred to S5

#614's S4 row says "DPAPI token layer 1/2 auth". The layer-2 `plm-user-token`
requires session/login state, tenant/org identity, and PLM server configuration.
Those are S5 surfaces, not S4 surfaces.

Reviewer ratification for #621: S4 R1 intentionally implements only layer-1
local-helper-token auth plus origin allowlisting. Layer-2 PLM bearer storage and
forwarding are deferred to S5.

- S4 implements local-helper-token auth and origin allowlisting.
- S4 must not add PLM bearer-token storage.
- S4 must not add `Authorization: Bearer ...` forwarding.
- S4 must not add `/session/login`, `/session/status`, `/session/logout`, or
  tenant/org/default-profile state.
- `AUTH_PLM_NOT_LOGGED_IN` remains declared in S1 `ErrorCodes`, but S4 does not
  emit it.

### 3.H Error envelope policy

Security failures return HTTP-layer errors, not business `200 OK` envelopes.

Response shape:

```json
{
  "ok": false,
  "error": {
    "code": "AUTH_LOCAL_TOKEN_MISSING",
    "message": "Local helper token is missing.",
    "retryable": false,
    "details": {}
  }
}
```

Rules:

- Missing local token: `401 AUTH_LOCAL_TOKEN_MISSING`.
- Invalid local token: `401 AUTH_LOCAL_TOKEN_INVALID`.
- Unsupported protocol: `426 PROTO_VERSION_UNSUPPORTED`.
- Origin denied: `403 ORIGIN_PROCESS_NOT_ALLOWED`.
- `Content-Type` is `application/json; charset=utf-8`.
- `retryable` is `false` for all S4 security failures.
- `details` may be empty or may include non-sensitive classification strings,
  but must not include token values, full request headers, or unredacted secrets.
- Error envelopes must be generated by helper-side code, not by throwing raw
  framework exceptions.

### 3.I CORS remains disabled

S4 must not enable ASP.NET CORS middleware and must not add
`Access-Control-Allow-Origin`, `Access-Control-Allow-Headers`, or
`Access-Control-Allow-Credentials` on `/healthz` or security-error responses.

This is part of #614's browser/cross-site defense: browsers should not get a
successful CORS preflight path to the helper.

### 3.J Production route table remains S3-sized

After S4 implementation, production helper source still declares only:

```text
GET /healthz
```

The security gate may cause unknown protected paths to return `401`, `403`, or
`426` before routing if the request lacks required security material. If a
request passes all S4 checks and the route is still unimplemented, normal
routing may return `404`.

S4 must not implement `/version`. `/version` is only an auth-exempt reserved path
until S5.

## 4. R1 Target Output

The later S4 implementation PR should contain:

- edits under `clients/cad-desktop-helper/Helper/`;
- edits under `clients/cad-desktop-helper/Helper.Tests/`;
- any minimal S4-specific helper test fixtures needed for middleware testing;
- no new production project;
- no new workflow unless the existing workflow stops covering Helper tests;
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_R1_20260522.md`;
- one `docs/DELIVERY_DOC_INDEX.md` line for that DEV/verification MD.

Implementation shape recommendation:

```text
HelperCommand
  -> LocalTokenBootstrapper.EnsureToken() returns localToken
  -> KestrelHelperHostRunner.RunAsync(..., localToken, securityOptions, ...)
  -> middleware:
       if IsExempt(request): next()
       ValidateLocalToken(localToken, request)
       ValidateProtocol(request)
       ValidateOrigin(request)
       next()
  -> MapGet("/healthz", ...)
```

This intentionally mutates the S3-shipped `IHelperHostRunner.RunAsync(...)`
shape. The S4 implementation PR may update S3 test fakes such as
`RecordingHostRunner` to match the new signature, provided S3 behavior remains
covered and unchanged.

The exact class names are implementation details, but the public test names and
behavioral contracts in §5 are mandatory.

## 5. Mandatory Tests And Guards

The S4 implementation PR must include these exactly named tests or their
language-equivalent xUnit names:

1. `test_healthz_remains_bare_no_token_no_origin_no_protocol`
2. `test_version_path_is_reserved_exempt_but_not_implemented`
3. `test_protected_path_missing_local_token_returns_401_auth_local_token_missing`
4. `test_protected_path_invalid_local_token_returns_401_auth_local_token_invalid`
5. `test_protected_path_valid_token_uses_bootstrapped_in_memory_token`
6. `test_local_token_compare_is_fixed_time_and_exact`
7. `test_protected_path_missing_protocol_returns_426_proto_version_unsupported`
8. `test_protected_path_wrong_protocol_returns_426_proto_version_unsupported`
9. `test_origin_resolver_uses_tcp_peer_not_http_headers`
10. `test_origin_unresolvable_returns_403_origin_process_not_allowed`
11. `test_origin_process_name_and_path_must_both_match`
12. `test_origin_path_glob_is_case_insensitive_full_path_match`
13. `test_default_origin_allowlist_contains_autocad_zwcad_gstarcad_and_companion`
14. `test_config_origin_whitelist_extends_defaults_without_replacing_them`
15. `test_malformed_origin_whitelist_falls_back_to_defaults`
16. `test_auth_errors_use_http_status_and_json_error_envelope`
17. `test_auth_error_responses_and_logs_do_not_leak_local_token`
18. `test_s4_does_not_enable_cors_headers`
19. `test_s4_adds_no_production_routes_beyond_healthz`
20. `test_s4_does_not_implement_plm_bearer_session_or_authorization_forwarding`
21. `test_no_s5_s6_s7_s8_scope_leak`
22. `test_dotnet_workflow_still_covers_helper_tests`

Required source/drift guards:

- source scan: production Helper has exactly one `MapGet("/healthz"` and no
  other `MapGet`/`MapPost`/`MapPut`/`MapDelete` route declarations;
- source scan: no `--reset-local-token` in production Helper route/middleware
  code;
- source scan: no `Authorization` header injection in production Helper;
- source scan: no `server_url`, `tenant_id`, `org_id`, or
  `default_profile_id` handling in production Helper;
- source scan: no SQLite package/reference;
- source scan: no edits under `clients/autocad-material-sync/`, `plugins/`, or
  Python service paths;
- workflow scan: `cad-helper-shared-dotnet.yml` still includes Helper and
  Helper.Tests restore/build/test coverage;
- route-table guard: S4 must not touch the Python service; the Python
  `len(app.routes) == 677` pin remains the structural sentinel that this helper
  slice did not leak into FastAPI.

## 6. Verification Commands

Expected Windows-capable verification for the implementation PR:

```bash
dotnet restore clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj --configuration Release --no-restore
dotnet test clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj --configuration Release --no-restore
```

The existing Windows workflow must continue to run Shared and Detector:

```bash
dotnet test clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj --configuration Release --no-restore
dotnet test clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj --configuration Release --no-restore
```

Repository verification:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_workflow_checkout_fetch_depth_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_inline_shell_syntax_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_runner_policy_contracts.py
```

Local macOS can run doc-index and workflow contract checks, but the merge gate
for S4 implementation must include the dedicated Windows .NET workflow. Do not
claim Helper build/test success from this workstation unless `dotnet` is
installed and the commands above actually run.

## 7. DEV/Verification MD Expectations

The later implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_R1_20260522.md
```

That document must include:

- exact scope delivered;
- decisions from §3, especially the ratified S4 layer-2 deferral in §3.G;
- list of protected vs exempt paths;
- the 22 mandatory tests and any additional hardening tests;
- source-guard results;
- Windows `cad-helper-shared-dotnet` run URL or run id;
- local command output honestly scoped to what the workstation can run;
- explicit "not implemented" list for S5-S11.

## 8. Non-Goals

S4 taskbook non-goals:

- no runtime implementation;
- no source edit outside docs;
- no workflow edit;
- no CAD helper code edit;
- no test edit;
- no merge authorization for a later implementation PR.

S4 implementation non-goals:

- No `/session/login`.
- No `/session/status`.
- No `/session/logout`.
- No `/version` route implementation.
- No `/cad/current-drawing`.
- No `/diff/preview`, `/sync/inbound`, `/sync/outbound`, `/dedup/check`,
  `/audit/apply-result`, `/shell/notify`, or any business route.
- No PLM bearer-token storage.
- No `Authorization: Bearer ...` forwarding.
- No tenant/org/default-profile state.
- No `server_url` or `server_allowlist`.
- No SQLite audit.
- No `--reset-local-token`.
- No CADDedupPlugin or MaterialSyncApiClient migration.
- No LISP bridge.
- No Python service/schema/migration change.
- No CAD pool multi-server work.

## 9. Decision Gate / Handoff

Doc-only. Implementation may start only after:

1. this taskbook merges;
2. the user gives a separate explicit opt-in for
   `feat/cad-helper-bridge-s4-auth-origin-allowlist-r1-20260522`.

Recommended branch name for the implementation PR:

```text
feat/cad-helper-bridge-s4-auth-origin-allowlist-r1-20260522
```

Follow-ups after S4 remain:

- S5 session/version/current-drawing endpoints;
- S6 business endpoints and audit;
- S7 reset-token CLI;
- S8 CADDedupPlugin migration;
- S9 bridge;
- S10 ZWCAD/GstarCAD LISP shell;
- S11 integration and verification package.

Each remains its own explicit opt-in.

## 10. Reviewer Focus

- Confirm the S4 route-free security-pipeline boundary: middleware/primitives
  are in scope, new production routes are not.
- Confirm `/healthz` stays completely bare and `/version` is reserved/exempt
  but not implemented.
- Confirm token-validation order: local token first, then protocol, then origin.
- Confirm fixed-time exact token comparison and in-memory bootstrapped token use.
- Confirm origin identity is OS TCP-peer PID/image-path based, not HTTP
  `Origin`/`Referer`/`X-Forwarded-For` based.
- Confirm `origin_whitelist` extends defaults instead of replacing them.
- Verify the implementation follows the §3.G ratified layer-2 deferral instead
  of adding PLM bearer storage or forwarding.
- Confirm the 22 mandatory tests are sufficient and include the CORS/no-route/no
  scope-creep guards.
