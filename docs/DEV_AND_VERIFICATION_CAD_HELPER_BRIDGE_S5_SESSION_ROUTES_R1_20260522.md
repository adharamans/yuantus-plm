# DEV & Verification: CAD Helper Bridge S5 Session Routes R1

Date: 2026-05-22

Scope: implementation of the merged S5 taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S5_SESSION_ROUTES_20260522.md`.

## 1. Implementation Summary

This slice adds the first CAD helper session-facing route surface on top of the
already-merged S3 startup and S4 auth/origin gate.

Implemented in `clients/cad-desktop-helper/Helper/HelperRuntime.cs`:

- `GET /version` as a bare S4-exempt route returning helper version, protocol
  version, and the S5 feature list only.
- `POST /session/login`, `POST /session/logout`, and `GET /session/status`
  behind S4 local-token, protocol, and origin checks.
- `POST /cad/current-drawing` behind S4 local-token, protocol, and origin
  checks.
- `HelperSessionService` as the testable coordinator for login/logout/status
  and current drawing.
- `JsonHelperSessionConfigStore` for `config.json` read-modify-write with
  same-directory temp file and atomic replace/move.
- `ServerAllowlist` using parsed URI scheme, host, port, and wildcard host
  rules rather than raw string prefix matching.
- `DpapiPlmBearerTokenStore` with the ratified entropy literal
  `yuantus-cad-plm-bearer-v1`.
- `HttpPlmLoginClient` for the outbound PLM `/auth/login` call.
- `CurrentDrawingStore` as process-local memory only.

Updated S4/S3 source guards:

- S4 route guard tests now acknowledge S5's legitimate `/version`,
  `/session/*`, and `/cad/current-drawing` additions while still pinning the S4
  security gate, no CORS, no browser `Authorization` forwarding, and no
  S6/S7/S8 route creep.
- S3 startup guard now similarly defers the session/current-drawing route
  boundary to S5 and keeps later-slice leak guards.

## 2. Contract Boundaries

S5 intentionally does not implement:

- S6 business routes or audit store.
- S7 `--reset-local-token`.
- S8 plugin migration / real CAD API reads.
- SQLite.
- CORS.
- Browser `Authorization` forwarding.
- Python FastAPI route changes.

The helper route declaration surface after this slice is exactly:

- `GET /healthz`
- `GET /version`
- `POST /session/login`
- `POST /session/logout`
- `GET /session/status`
- `POST /cad/current-drawing`

## 3. Test Coverage

Added `clients/cad-desktop-helper/Helper.Tests/HelperSessionRoutesContractTests.cs`
with the 23 mandatory S5 tests from the taskbook:

- `test_version_is_bare_and_reports_helper_protocol_without_session_data`
- `test_session_routes_are_protected_by_s4_security_gate`
- `test_session_login_requires_valid_server_url_tenant_username_password`
- `test_session_login_enforces_server_allowlist_before_plm_call`
- `test_server_allowlist_uses_parsed_uri_host_and_port_not_string_prefix`
- `test_session_login_forwards_only_auth_payload_to_plm_login`
- `test_session_login_stores_bearer_with_dpapi_not_config_json`
- `test_plm_bearer_uses_ratified_dpapi_entropy`
- `test_session_login_persists_server_tenant_org_and_default_profile`
- `test_session_config_write_is_atomic_and_preserves_unknown_fields`
- `test_session_login_response_never_echoes_access_token_or_password`
- `test_session_login_failure_preserves_previous_session_and_token`
- `test_session_status_missing_token_or_tenant_returns_logged_out_not_error`
- `test_session_status_never_calls_plm_and_never_returns_bearer`
- `test_session_logout_clears_bearer_tenant_org_but_preserves_server_and_profile`
- `test_session_logout_is_idempotent_and_does_not_call_plm`
- `test_current_drawing_accepts_caller_supplied_context_without_reading_dwg`
- `test_current_drawing_rejects_missing_filename_and_invalid_cad_system`
- `test_current_drawing_is_memory_only_not_config_or_sqlite`
- `test_s5_adds_exactly_version_session_and_current_drawing_routes`
- `test_s5_does_not_add_s6_s7_s8_routes_or_sqlite_or_reset_token`
- `test_s5_keeps_cad_helper_dotnet_workflow_covering_helper_tests`
- `test_s5_preserves_s4_auth_origin_contract_tests`

These tests cover route surface, security placement, login validation,
server-allowlist matching, PLM payload minimization, DPAPI bearer storage,
config preservation, failure non-overwrite, logout semantics, status semantics,
current drawing memory-only behavior, and later-slice scope guards.

## 4. Verification

Local environment note:

```text
dotnet --version
zsh:1: command not found: dotnet
```

Because this workstation does not have the .NET SDK installed, local
`dotnet build` / `dotnet test` cannot be claimed. The implementation PR must be
gated by the dedicated GitHub workflow:

```text
.github/workflows/cad-helper-shared-dotnet.yml
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj
```

Local checks performed:

```text
git diff --check
```

Pending PR checks:

- `cad-helper-shared-dotnet` must pass for the S5 branch SHA.
- `contracts` must pass.

## 5. Follow-Ups

S6 remains the next CAD helper slice after S5 merge and requires its own
doc-only taskbook and separately opted-in implementation. This S5 PR does not
authorize S6 or later slices.
