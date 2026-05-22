# CAD Helper Bridge S4 Auth And Origin Allowlist R1 - Development And Verification

Date: 2026-05-22

## 1. Scope Delivered

This slice implements the S4 contract from
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_20260522.md`.

Delivered scope:

- helper-side local-token validation for protected requests;
- protocol-version validation for protected requests;
- origin process allowlisting by TCP peer process plus image-name/path matching;
- JSON error envelopes for S4 security failures;
- `origin_whitelist` config parsing that extends defaults;
- S3 host-runner signature update to pass the bootstrapped in-memory token and
  security options into Kestrel;
- 22 S4 mandatory tests;
- source guards for no route growth, no CORS, no PLM bearer/session behavior, and
  no S5-S8 scope leak.

S4 remains a security-pipeline slice. It does not add session routes, business
routes, reset-token CLI, PLM bearer forwarding, SQLite audit, plugin migration,
or CAD writes.

## 2. Runtime Design

S4 adds `HelperSecurityGate` as a testable helper-side gate:

1. exempt paths are allowed first;
2. local-token header is validated against the S3-bootstrapped in-memory token;
3. `X-Yuantus-Protocol` is validated against `1.0`;
4. origin process is resolved from loopback TCP connection information and
   checked against the allowlist.

`GET /healthz` remains the only production route. `GET /version` is recognized
as an auth/origin/protocol-exempt future path, but S4 does not implement the
route.

## 3. Ratified Decisions

### 3.1 Layer-2 Deferral

S4 follows the ratified #621 decision: layer-2 PLM bearer storage and
`Authorization: Bearer ...` forwarding are deferred to S5. S4 implements only
layer-1 local-helper-token auth plus origin allowlisting.

Reason: layer-2 requires session/login state, tenant/org identity, and PLM
server configuration. Those are S5 surfaces.

### 3.2 Token-First Ordering

Protected requests validate local token before protocol and origin checks.

Reason: this prevents unauthenticated protocol probing. A stale-token client
gets `401`, lets S1 `HelperTransport` re-read DPAPI once, then sees `426` only
if it is genuinely protocol-incompatible.

### 3.3 In-Memory Token

The helper validates against the token returned by S3
`LocalTokenBootstrapper.EnsureToken()`. It does not re-read DPAPI on every
request.

S7 `--reset-local-token` must account for this later: a running helper will not
observe DPAPI token rotation until restart or until S7 explicitly introduces a
separate reload contract.

### 3.4 Exempt Path Matching

Exempt path matching is ordinal case-insensitive and exact on normalized path:

- query strings do not affect matching;
- trailing slash is not exempt;
- method must be `GET`;
- `/Healthz` and `/Version` are exempt because path comparison is
  case-insensitive.

## 4. Implementation Notes

Files changed:

- `clients/cad-desktop-helper/Helper/HelperRuntime.cs`
- `clients/cad-desktop-helper/Helper.Tests/HelperAuthOriginContractTests.cs`
- `clients/cad-desktop-helper/Helper.Tests/HelperStartupContractTests.cs`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_R1_20260522.md`
- `docs/DELIVERY_DOC_INDEX.md`

Notable implementation details:

- `HelperSecurityOptions.Load(...)` reads only `origin_whitelist` and extends
  defaults.
- `HelperSecurityGate.FixedTimeTokenEquals(...)` uses byte-wise fixed-time
  comparison semantics instead of string equality.
- `WindowsTcpOriginProcessResolver` resolves origin identity from TCP endpoints
  and `GetExtendedTcpTable`, then uses the existing process-inspection seam to
  read image path.
- Security errors use `ResponseEnvelope<object>` with `ok=false` and the S1
  error-code constants.
- Kestrel middleware calls the gate before route dispatch; `/healthz` remains
  bare.

## 5. Mandatory Tests

`HelperAuthOriginContractTests.cs` implements the 22 S4 mandatory tests:

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

`HelperStartupContractTests.cs` was updated only where S4 intentionally changes
the helper surface:

- `IHelperHostRunner.RunAsync(...)` test fake now accepts local token and
  security options.
- The old S3 no-S4 source guard is narrowed to no S5/S7/S8 leak after S4.

## 6. Verification

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
grep -R "MapGet\\|MapPost\\|MapPut\\|MapDelete\\|Authorization\\|Bearer\\|--reset-local-token\\|SQLite\\|CADDedupPlugin\\|server_url\\|tenant_id\\|org_id\\|default_profile_id" \
  -n clients/cad-desktop-helper/Helper
```

The source scan returned only `app.MapGet("/healthz", ...)`, confirming no
production route growth and no S5-S8 scope strings.

Observed local results:

- `xmllint --noout ...`: clean.
- `git diff --check`: clean.
- workflow YAML parse for `.github/workflows/cad-helper-shared-dotnet.yml`:
  clean.
- source scan: only `clients/cad-desktop-helper/Helper/HelperRuntime.cs`
  `app.MapGet("/healthz", ...)` matched.

Repository contract checks:

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

Observed result: `35 passed`.

`.NET build/test`: blocked locally because this workstation does not have the
`.NET` SDK installed (`zsh:1: command not found: dotnet`). The dedicated
Windows `cad-helper-shared-dotnet` workflow remains the authoritative .NET
build/test gate for this PR and must pass before merge.

## 7. Explicit Non-Goals

Not implemented:

- `/version` route body;
- `/session/login`, `/session/status`, `/session/logout`;
- `/cad/current-drawing`;
- `/diff/preview`, `/sync/inbound`, `/sync/outbound`, `/dedup/check`,
  `/audit/apply-result`, `/shell/notify`;
- PLM bearer-token storage;
- `Authorization: Bearer ...` forwarding;
- tenant/org/default-profile state;
- server URL or server allowlist behavior;
- SQLite audit;
- `--reset-local-token`;
- plugin migration;
- LISP bridge;
- Python service/schema/migration changes.

## 8. Next Slices

Next CAD helper bridge slices remain separately opted in:

- S5: `/version`, `/session/*`, `/cad/current-drawing`;
- S6: business routes and local audit;
- S7: reset-token CLI;
- S8: CADDedupPlugin migration;
- S9-S11: bridge, LISP shell, integration package.
