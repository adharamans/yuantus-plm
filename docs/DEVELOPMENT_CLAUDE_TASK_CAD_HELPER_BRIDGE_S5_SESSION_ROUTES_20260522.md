# Claude Taskbook: CAD Helper Bridge S5 - Session Routes And Current Drawing

Date: 2026-05-22

## 1. Purpose

CAD Desktop Helper Bridge **S5** implements the helper's session-facing route
surface from the R3.2 design after S3 startup and S4 auth/origin are already
merged.

S5 owns:

- `GET /version`;
- `POST /session/login`;
- `POST /session/logout`;
- `GET /session/status`;
- `POST /cad/current-drawing`;
- PLM bearer-token storage and forwarding primitives needed by later S6
  server-forwarding routes;
- config persistence for `server_url`, `tenant_id`, `org_id`, and
  `default_profile_id`.

S5 does **not** implement S6 business routes, SQLite audit, S7 reset-token CLI,
S8 plugin migration, S9 LISP bridge, S10 shell commands, or S11 integration
packaging. The implementation PR must remain route-surface + session-state only.

## 2. Grounded Current Reality

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the relevant S5
surface:

- Lines 490-497 list `GET /version`, `POST /session/login`,
  `POST /session/logout`, `GET /session/status`, and
  `POST /cad/current-drawing` as R3-required helper endpoints.
- Lines 493-497 mark `/version` as auth/origin exempt and mark all
  `/session/*` plus `/cad/current-drawing` routes as protected by local-token and
  origin checks.
- Lines 744-769 define `%APPDATA%\YuantusPLM\config.json` with `server_url`,
  `tenant_id`, `org_id`, `default_profile_id`, `idle_timeout_minutes`,
  `log_level`, `origin_whitelist`, and `server_allowlist`.
- Line 774 says `tenant_id`, `org_id`, and `default_profile_id` are first
  written by `/session/login` and read back after helper restart.
- Line 1061 defines S5 as helper `/healthz`, `/version`, `/session/*`, and
  `/cad/current-drawing`.

### 2.2 S4 state inherited by S5

`clients/cad-desktop-helper/Helper/HelperRuntime.cs` now provides:

- `HelperCommand.RunAsync(...)` at lines 91-140 bootstraps the local helper
  token, publishes the session file, loads idle timeout and S4
  `HelperSecurityOptions`, then calls `IHelperHostRunner`.
- `KestrelHelperHostRunner.RunAsync(...)` at lines 1318-1364 installs S4
  middleware before route dispatch.
- The only mapped production route today is `GET /healthz` at lines 1366-1371.
- `HelperConfig.LoadIdleTimeout(...)` at lines 1254-1287 reads only
  `idle_timeout_minutes`.

S5 can add the reserved `/version` route without changing S4 exemption policy.
S5 can add protected `/session/*` and `/cad/current-drawing` routes behind the
existing S4 middleware.

### 2.3 Shared transport state inherited by S5

`clients/cad-desktop-helper/Shared/Transport/HelperTransport.cs` already:

- exposes `PostJsonAsync`, `PostContentAsync`, and `GetAsync` at lines 44-69;
- injects `X-Yuantus-Local-Token` and `X-Yuantus-Protocol` into helper calls at
  lines 109-121;
- retries once on `AUTH_LOCAL_TOKEN_INVALID` or `AUTH_LOCAL_TOKEN_MISSING` at
  lines 80-104;
- unwraps helper `ResponseEnvelope<T>` responses and maps non-2xx / ok=false
  envelopes into `HelperException` at lines 125-223.

S5 should not change S1 transport behavior unless the implementation finds a
strict helper-route response-shape mismatch.

### 2.4 Existing server auth contract

The PLM server login route is `POST /api/v1/auth/login`, implemented by
`src/yuantus/api/routers/auth.py`:

- `LoginRequest` at lines 19-24 defines `tenant_id`, `username`, `password`,
  and optional `org_id`.
- `LoginResponse` at lines 26-32 defines `access_token`, `token_type`,
  `expires_in`, `tenant_id`, and `user_id`.
- The login handler at lines 34-65 authenticates the user, optionally checks org
  membership, and returns a JWT bearer token.

S5's helper route is named `/session/login`, but the outbound PLM target remains
server `/auth/login` under the configured `server_url` API base.

### 2.5 Explicit absence in current helper

The helper currently has no:

- `server_url`, `tenant_id`, `org_id`, or `default_profile_id` config model;
- PLM bearer token store;
- outbound PLM HTTP client;
- `/version`, `/session/*`, or `/cad/current-drawing` routes;
- current drawing state model;
- SQLite audit/logging surface.

This taskbook intentionally creates the S5 contract before implementation.

## 3. Ratified S5 Boundaries

### 3.A Route surface

S5 implementation adds exactly these production helper routes:

```text
GET  /version
POST /session/login
POST /session/logout
GET  /session/status
POST /cad/current-drawing
```

After S5, production helper route declarations must be exactly six `Map*`
entries:

```text
GET  /healthz
GET  /version
POST /session/login
POST /session/logout
GET  /session/status
POST /cad/current-drawing
```

No S6+ routes may appear: `/diff/preview`, `/sync/inbound`, `/sync/outbound`,
`/audit/apply-result`, `/dedup/check`, `/shell/notify`, `/compose`, `/validate`,
`/tasks`, and `/diagnostics/snapshot` remain absent.

### 3.B S4 security contract stays in force

S5 must not weaken S4:

- `/healthz` stays bare;
- `/version` stays bare because S4 already exempts it;
- `/session/*` and `/cad/current-drawing` stay protected by local helper token,
  protocol, and origin allowlist;
- no CORS headers or ASP.NET CORS middleware;
- no change to S4 token-first ordering;
- no change to S4 origin allowlist semantics.

S5 route tests must prove the new protected routes are added behind S4 rather
than by bypassing the middleware.

### 3.C `/version` response shape

`GET /version` returns a normal helper envelope:

```json
{
  "ok": true,
  "data": {
    "helper_version": "0.1.0",
    "protocol_version": "1.0",
    "features": ["session", "current_drawing"]
  }
}
```

S5 must not include server URL, tenant, org, bearer-token state, username, local
token, PID, image path, or filesystem paths in `/version`.

`/version` is not an authorization oracle. It reports helper/protocol capability
only.

### 3.D Session config model

S5 introduces a typed helper config model that reads and writes only these S5
fields:

- `server_url`;
- `tenant_id`;
- `org_id`;
- `default_profile_id`.

It must preserve existing S3/S4 fields while writing the file:

- `idle_timeout_minutes`;
- `log_level`;
- `origin_whitelist`;
- `server_allowlist`.

S5 must not corrupt or reorder unknown top-level JSON fields in a way that loses
future settings. The implementation may use `JObject` for preservation, but it
must not rely on non-atomic read-modify-write.

Config persistence must use the same write-safety class as S3 session-file
publish:

1. read the current `config.json` into a JSON object;
2. merge only the S5-owned fields;
3. write to a same-directory temporary file;
4. atomically replace or move into `config.json`;
5. delete the temporary file on failure.

Concurrency invariant: R3.2 explicitly allows two Windows sessions for the same
user to run helper instances concurrently. Because `config.json` is per-user
shared state, S5 must tolerate competing read-modify-write rounds without file
corruption or partial JSON. Last-writer-wins for the same field is acceptable in
R1; truncation, malformed JSON, and dropping unrelated existing fields are not
acceptable.

### 3.E Server URL policy and server_allowlist gate

S5 is the first slice that can make helper outbound requests to PLM. Therefore
S5 owns a minimal server URL gate.

Ratified S5 behavior:

1. `server_url` is required on `POST /session/login`.
2. `server_url` must be absolute `http` or `https`.
3. `server_url` must be normalized before persistence: trim whitespace and
   remove a trailing slash.
4. `server_allowlist` is enforced if present in `config.json` before login.
5. Empty or missing `server_allowlist` means no additional allowlist restriction
   beyond absolute `http/https` validation.
6. Matching is URI-based, not raw string prefix based:
   - parse both the candidate `server_url` and each allowlist entry as absolute
     URIs;
   - scheme must match exactly after lowercasing;
   - host comparison is case-insensitive;
   - exact-host entries match only parsed host equality, so
     `https://plm.example.com.evil.com` must not match
     `https://plm.example.com`;
   - explicit ports must match exactly;
   - absent ports normalize to the scheme default (`80` for `http`, `443` for
     `https`);
   - path, query, and fragment are ignored by the allowlist decision.
7. Wildcard host entries like `https://*.yuantus.internal` match one or more
   parsed subdomain labels, not the bare root and not raw substrings.
8. On allowlist failure, return helper envelope `ok=false` with
   `HELPER_INPUT_VALIDATION_FAILED` and do not call PLM.

Rationale: S4 explicitly deferred server fields; S5 is the first safe point to
prevent the helper from being configured as an arbitrary local PLM proxy.

### 3.F PLM bearer-token storage

S5 implements layer-2 PLM bearer storage deferred from S4.

Ratified behavior:

- `POST /session/login` forwards credentials to PLM `/auth/login`.
- On success, S5 stores the returned access token using DPAPI-backed storage.
- S5 must not store the PLM bearer token in `config.json`.
- S5 must never include the bearer token in `/session/status`, `/version`,
  `/session/logout`, `/cad/current-drawing`, logs, errors, or test snapshots.
- S5 introduces an internal bearer-token reader seam for S6, but no S6 route may
  call it yet because S6 routes do not exist.

S5 may place bearer-token storage beside S1 `LocalTokenStore` primitives or in
Helper-specific code, but it must use the same DPAPI envelope semantics and
error code family. It must use the R3.2 design entropy literal
`yuantus-cad-plm-bearer-v1` for the PLM bearer token. If DPAPI fails, return
`HELPER_DPAPI_UNAVAILABLE`.

### 3.G Login request and response

`POST /session/login` request:

```json
{
  "server_url": "https://plm.example.com/api/v1/",
  "tenant_id": "tenant-acme",
  "org_id": "org-engineering",
  "username": "admin",
  "password": "admin",
  "default_profile_id": "sheet"
}
```

Required: `server_url`, `tenant_id`, `username`, `password`.
Optional: `org_id`, `default_profile_id`.

The helper forwards only these fields to PLM `/auth/login`:

```json
{
  "tenant_id": "tenant-acme",
  "org_id": "org-engineering",
  "username": "admin",
  "password": "admin"
}
```

It must not forward `default_profile_id`, local helper token, origin process
identity, helper PID, or drawing context to PLM login.

`POST /session/login` response:

```json
{
  "ok": true,
  "data": {
    "logged_in": true,
    "server_url": "https://plm.example.com/api/v1",
    "tenant_id": "tenant-acme",
    "org_id": "org-engineering",
    "default_profile_id": "sheet",
    "username": "admin"
  }
}
```

The exact PLM login response may contain additional identity fields. S5 may
retain only safe identity fields needed for status display; it must not echo
`access_token`.

### 3.H Login failure semantics

S5 preserves the helper envelope contract:

- PLM `/api/v1/auth/login` returns a raw server `LoginResponse` on success and
  HTTP errors such as `{"detail":"..."}` on failure; it is not a helper
  envelope.
- PLM HTTP 401/403 becomes helper `ok=false` with
  `AUTH_PLM_NOT_LOGGED_IN`. The PLM `detail` body is the error-message source,
  not a helper-envelope source.
- Network failure to PLM becomes helper `ok=false` with `PLM_VALIDATION_FAILED`
  or a narrower helper-owned code if the implementation adds one already present
  in `ErrorCodes`.
- Invalid helper input returns HTTP 200 with `ok=false` and
  `HELPER_INPUT_VALIDATION_FAILED`, because S4 already owns HTTP-layer auth and
  origin errors.
- On failed login, S5 must not overwrite the previous successful session config
  or bearer token.

### 3.I Logout behavior

`POST /session/logout` clears the stored PLM bearer token and clears session
identity from helper status.

Ratified behavior:

- clear PLM bearer token;
- clear `tenant_id` and `org_id`;
- preserve `server_url` and `default_profile_id` as convenience defaults for the
  next login;
- return `ok=true` even if no PLM bearer token existed;
- do not call the PLM server;
- do not clear the S1 local helper token;
- do not delete helper session file or stop the helper process.

Rationale: the design lists logout as a local helper route with no PLM target.
It is a local session-clear operation, not server revocation.

### 3.J Session status behavior

`GET /session/status` returns a helper envelope:

```json
{
  "ok": true,
  "data": {
    "logged_in": false,
    "server_url": "https://plm.example.com/api/v1",
    "tenant_id": null,
    "org_id": null,
    "default_profile_id": "sheet",
    "username": null
  }
}
```

Status rules:

- `logged_in=true` only when a bearer token exists and `tenant_id` is non-empty.
- Missing bearer token or missing `tenant_id` returns `logged_in=false`, not an
  error. This preserves R3 design acceptance test #9.
- `/session/status` must not call PLM.
- `/session/status` must not validate bearer-token freshness against PLM; token
  expiry is discovered by later PLM-forwarding routes in S6.
- `/session/status` must never include the bearer token.

### 3.K Current drawing behavior

`POST /cad/current-drawing` stores caller-supplied drawing context in memory for
later helper routes.

Request shape:

```json
{
  "drawing": {
    "filename": "J2824002-06.dwg",
    "filepath": "D:\\projects\\demo\\"
  },
  "cad_system": "autocad"
}
```

Ratified behavior:

- S5 does not read DWG files and does not call AutoCAD/ZWCAD/GstarCAD APIs.
- S5 accepts drawing context supplied by S8/S9/S10 callers in the future.
- `filename` is required and non-empty.
- `filepath` is optional but, when present, must be a string.
- `cad_system` is optional; if present it must be one of `autocad`, `zwcad`, or
  `gstarcad`.
- The endpoint returns the normalized stored drawing context.
- The stored current drawing is process-local memory only in S5. It is not
  persisted to `config.json` and not written to SQLite.

Rationale: S5 creates the helper route and state contract. Real CAD extraction
or plugin migration belongs to S8/S9/S10.

### 3.L Current drawing and session coupling

`/cad/current-drawing` is protected by S4 local helper auth/origin but does not
require PLM login.

Rationale: a CAD-side caller can report local drawing context before PLM login.
S6 PLM-forwarding routes will require login before using the context.

### 3.M Audit/logging deferral

R3 design section 5.6 says `/session/login` and `/session/logout` are audited.
S5 does not implement SQLite audit. That belongs to S6 per section 10.

S5 implementation may add minimal in-process seams needed for later audit, but
must not create `audit.db`, SQLite schema, audit routes, or file logging.

### 3.N Workflow and CI

S5 changes Helper and Helper.Tests, so the implementation PR must trigger and
pass the dedicated Windows `.NET` workflow:

```text
.github/workflows/cad-helper-shared-dotnet.yml
```

Local non-dotnet checks are not sufficient for merge.

## 4. R1 Target Output

The S5 implementation PR should contain:

- helper runtime changes for the five S5 routes;
- typed request/response DTOs or equivalent internal classes;
- a config/session state helper that can preserve unrelated config JSON fields;
- a DPAPI-backed PLM bearer-token storage primitive;
- an outbound PLM login client seam with tests;
- a current-drawing in-memory state primitive;
- `clients/cad-desktop-helper/Helper.Tests/HelperSessionRoutesContractTests.cs`;
- any necessary narrow updates to S4 source guards so S5 route additions are
  intentional, not accidental;
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S5_SESSION_ROUTES_R1_20260522.md`;
- one `docs/DELIVERY_DOC_INDEX.md` entry.

## 5. Mandatory Tests

The implementation PR must include these exactly named tests or their direct
xUnit equivalents:

1. `test_version_is_bare_and_reports_helper_protocol_without_session_data`
2. `test_session_routes_are_protected_by_s4_security_gate`
3. `test_session_login_requires_valid_server_url_tenant_username_password`
4. `test_session_login_enforces_server_allowlist_before_plm_call`
5. `test_server_allowlist_uses_parsed_uri_host_and_port_not_string_prefix`
6. `test_session_login_forwards_only_auth_payload_to_plm_login`
7. `test_session_login_stores_bearer_with_dpapi_not_config_json`
8. `test_plm_bearer_uses_ratified_dpapi_entropy`
9. `test_session_login_persists_server_tenant_org_and_default_profile`
10. `test_session_config_write_is_atomic_and_preserves_unknown_fields`
11. `test_session_login_response_never_echoes_access_token_or_password`
12. `test_session_login_failure_preserves_previous_session_and_token`
13. `test_session_status_missing_token_or_tenant_returns_logged_out_not_error`
14. `test_session_status_never_calls_plm_and_never_returns_bearer`
15. `test_session_logout_clears_bearer_tenant_org_but_preserves_server_and_profile`
16. `test_session_logout_is_idempotent_and_does_not_call_plm`
17. `test_current_drawing_accepts_caller_supplied_context_without_reading_dwg`
18. `test_current_drawing_rejects_missing_filename_and_invalid_cad_system`
19. `test_current_drawing_is_memory_only_not_config_or_sqlite`
20. `test_s5_adds_exactly_version_session_and_current_drawing_routes`
21. `test_s5_does_not_add_s6_s7_s8_routes_or_sqlite_or_reset_token`
22. `test_s5_keeps_cad_helper_dotnet_workflow_covering_helper_tests`
23. `test_s5_preserves_s4_auth_origin_contract_tests`

Additional drift/source guards:

- scan production Helper source for exactly these route strings:
  `/healthz`, `/version`, `/session/login`, `/session/logout`,
  `/session/status`, `/cad/current-drawing`;
- assert the production helper source has exactly 6 route declarations across
  `MapGet` and `MapPost`, and no `MapPut`, `MapDelete`, or `MapPatch`;
- assert no `UseCors`, `Access-Control-Allow-*`, `--reset-local-token`,
  `SQLite`, `CADDedupPlugin`, `/diff/preview`, `/sync/inbound`,
  `/sync/outbound`, `/audit/apply-result`, `/dedup/check`, `/shell/notify`;
- assert config writes preserve `origin_whitelist`, `server_allowlist`, and
  unknown top-level fields;
- assert no test or source snapshot contains a literal bearer-token value after
  login except inside a fake DPAPI/token-store test seam.

## 6. Verification

Required implementation verification:

```bash
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj
```

The local macOS workstation may not have the .NET SDK. If so, do not claim local
`.NET` success; rely on the Windows `cad-helper-shared-dotnet` workflow and
record the workflow run id.

Repository checks:

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

Static checks:

```bash
git diff --check
xmllint --noout \
  clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj \
  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj \
  clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj \
  clients/cad-desktop-helper/Detector/Yuantus.Cad.Detector.csproj \
  clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj
```

## 7. DEV / Verification MD Requirements

The implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S5_SESSION_ROUTES_R1_20260522.md
```

It must document:

- final route list;
- config persistence behavior;
- PLM bearer storage behavior;
- server allowlist behavior;
- current-drawing memory-only behavior;
- mandatory test list and results;
- local verification results;
- Windows `.NET` workflow run id or URL;
- explicit S6-S11 non-goals.

## 8. Non-Goals

S5 taskbook non-goals:

- no helper source edit;
- no tests edit;
- no implementation branch authorization beyond this doc-only taskbook.

S5 implementation non-goals:

- no `/diff/preview`;
- no `/sync/inbound`;
- no `/sync/outbound`;
- no `/audit/apply-result`;
- no `/dedup/check`;
- no `/shell/notify`;
- no SQLite;
- no file logging / Serilog;
- no `--reset-local-token`;
- no plugin migration;
- no LISP bridge;
- no AutoCAD/ZWCAD/GstarCAD API calls;
- no DWG file read/write;
- no Python service/schema/migration changes;
- no route-count change in the Python FastAPI app.

## 9. Decision Gate

After this taskbook is merged, the S5 implementation still requires a separate
explicit opt-in. Recommended branch name:

```text
feat/cad-helper-bridge-s5-session-routes-r1-20260522
```

S6 and later slices remain independent opt-ins.

## 10. Reviewer Focus

- Confirm S5 is the correct owner for layer-2 PLM bearer token storage and
  forwarding primitives after S4's explicit deferral.
- Confirm PLM bearer storage uses the R3.2 entropy literal
  `yuantus-cad-plm-bearer-v1`.
- Confirm `server_allowlist` enforcement belongs in S5 login rather than S6,
  because S5 is the first outbound PLM call surface.
- Confirm `server_allowlist` matching is parsed-URI host/scheme/port matching,
  not raw string prefix matching.
- Confirm shared `config.json` writes use temp-write plus atomic replace/move so
  two Windows-session helpers cannot leave partial JSON.
- Confirm `/session/logout` is local-only and preserves `server_url` plus
  `default_profile_id` while clearing bearer, tenant, and org.
- Confirm `/session/status` reports logged-out rather than erroring when token or
  tenant is absent.
- Confirm `/cad/current-drawing` is caller-supplied, memory-only context and not
  a DWG-reading surface.
- Confirm no S6/S7/S8 scope leaks are authorized by this taskbook.
