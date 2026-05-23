# CAD Helper Bridge S6 Business Audit Routes R1 - Development And Verification

Date: 2026-05-22

## 1. Scope

This implementation delivers the S6 slice ratified in
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S6_BUSINESS_AUDIT_ROUTES_20260522.md`.

Implemented:

- helper routes:
  - `POST /diff/preview`
  - `POST /sync/inbound`
  - `POST /sync/outbound`
  - `POST /audit/apply-result`
- PLM business forwarding through an injectable `IPlmBusinessClient`;
- in-process `PullCache` with 10-minute TTL and `PULL-` + `Guid.N` identifiers;
- SQLite audit store at `%APPDATA%\YuantusPLM\audit.db` via `Microsoft.Data.Sqlite`;
- S5 login/logout local audit seam without changing S5 response contracts;
- S6-specific mandatory contract tests.

Not implemented:

- `/dedup/check`;
- `/shell/notify`;
- S7 reset-token behavior;
- S8 AutoCAD plugin migration;
- S9/S10 LISP bridge;
- CORS;
- Python FastAPI or server plugin changes.

## 2. Runtime Design

S6 keeps the helper architecture from S3-S5 and adds a narrow business/audit
layer inside `clients/cad-desktop-helper/Helper/HelperRuntime.cs`.

New seams:

- `IPlmBusinessClient` / `HttpPlmBusinessClient` posts JSON to the S5 persisted
  `server_url`, forwards only the S5 PLM bearer internally, and maps PLM
  failures into helper error codes.
- `PullCache` stores the `/diff/preview` result context required by
  `/audit/apply-result`; restart recovery remains out of scope.
- `IAuditEventStore` / `SqliteAuditEventStore` creates the R3.2
  `audit_events` table and writes one local row per audited helper event.
- `IAuditWarningWriter` emits the ratified H2 fallback stderr line when a
  post-PLM audit write fails.
- `HelperBusinessAuditService` owns S6 route behavior and leaves
  `HelperSessionService` focused on S5 session state.

The Kestrel runner now maps exactly 10 production helper routes: three GET
routes and seven POST routes.

## 3. Ratified Policies Preserved

S6 follows the taskbook decisions exactly:

- `/diff/preview` exposes only the `item_id` path and rejects `values`,
  `target_properties`, and `target_cad_fields` request modes.
- PLM forwarding requires persisted `server_url`, `tenant_id`, and readable PLM
  bearer token before any PLM call.
- `pull_id` is generated only after PLM success and uses `PULL-` +
  `Guid.NewGuid().ToString("N")`.
- `/audit/apply-result` accepts only `ok`, `partial`, `failed`, and
  `not-applied-display-only`.
- `AUDIT_PULL_ID_UNKNOWN`, `AUDIT_ALREADY_REPORTED`,
  `AUDIT_PULL_ID_EXPIRED`, and H1 `AUDIT_WRITE_FAILED` use HTTP 200 +
  `ok=false`.
- H1 fail-closed applies to `/audit/apply-result`.
- H2 fail-open applies to `/diff/preview`, `/sync/inbound`, and
  `/sync/outbound`; audit-write failure after PLM success emits one sanitized
  `[AUDIT_WRITE_FAILED] endpoint=<path> trace_id=<id> reason=<short>` line and
  still returns the PLM-success response.
- Audit rows for PLM-forwarding routes are written after the PLM result is
  known, not before.
- `/dedup/check` remains excluded and must be added by S8 if that slice lands.

## 4. Tests Added

Added:

```text
clients/cad-desktop-helper/Helper.Tests/HelperBusinessAuditContractTests.cs
```

The file contains all 17 mandatory exactly-named tests from the S6 taskbook:

1. `test_s6_adds_exactly_diff_sync_and_audit_routes`
2. `test_s6_routes_are_protected_by_s4_security_gate`
3. `test_s6_requires_logged_in_session_before_plm_forwarding`
4. `test_diff_preview_requires_item_id_and_does_not_forward_other_request_modes`
5. `test_diff_preview_forwards_to_configured_plm_endpoint_with_bearer_only`
6. `test_diff_preview_wraps_server_response_and_generates_pull_id`
7. `test_pull_cache_expires_after_ten_minutes`
8. `test_audit_apply_result_rejects_unknown_expired_and_duplicate_pull_id`
9. `test_audit_apply_result_persists_successful_apply_row`
10. `test_sync_inbound_forwards_payload_and_preserves_plm_conflict_code`
11. `test_sync_outbound_forwards_payload_and_returns_server_cad_fields`
12. `test_sqlite_audit_schema_matches_r3_contract`
13. `test_session_login_and_logout_are_audited_without_changing_s5_contract`
14. `test_healthz_version_and_session_status_are_not_audited`
15. `test_audit_write_failure_policy_matches_ratified_h_boundary`
16. `test_s6_does_not_add_dedup_shell_reset_or_later_routes`
17. `test_s6_keeps_cad_helper_dotnet_workflow_covering_helper_tests`

Narrow existing guard updates:

- S3/S4/S5 source guards now allow the S6 route names and SQLite audit store.
- The guards still reject S7/S8+ routes, reset-token behavior, CORS, and
  AutoCAD plugin scope.
- The S4 browser `Authorization` guard now asserts no browser request
  `Authorization` header is read while allowing internal PLM bearer forwarding.

## 5. Verification

Local commands run:

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

Result:

```text
32 passed in 0.52s
```

Static contract checks run:

```bash
python3 - <<'PY'
import re, pathlib
expected = re.findall(r'`(test_[^`]+)`', pathlib.Path('docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S6_BUSINESS_AUDIT_ROUTES_20260522.md').read_text())
actual = re.findall(r'public (?:async Task|void) (test_[A-Za-z0-9_]+)\(', pathlib.Path('clients/cad-desktop-helper/Helper.Tests/HelperBusinessAuditContractTests.cs').read_text())
assert set(expected) == set(actual)
assert len(expected) == 17
PY
```

Result:

```text
17 expected / 17 actual, no missing names
```

.NET local verification:

```bash
dotnet --version
```

Result:

```text
zsh:1: command not found: dotnet
```

This workstation still cannot run `dotnet build` or `dotnet test`. The merge
gate for this PR is the dedicated GitHub `cad-helper-shared-dotnet` workflow,
which must run:

```bash
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj --configuration Release --no-restore
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj --configuration Release --no-restore
```

## 6. Review Focus

Review should focus on:

- the S6 route count and absence of S7/S8 scope;
- the H1/H2 audit-write failure behavior;
- the `AUDIT_*` HTTP 200 + `ok=false` policy;
- the PLM bearer not leaking into request bodies, helper responses, audit rows,
  or stderr;
- the SQLite schema matching R3.2 exactly;
- the old S4/S5 tests being relaxed only where S6 made their former negative
  assertions stale.

## 7. Review Round 1 Fixes

Post-review fixes applied before merge readiness:

- `/diff/preview` now passes the created `PullCacheEntry` into the audit writer,
  so the local `/diff/preview` audit row carries the same `pull_id` returned to
  the caller.
- `test_diff_preview_wraps_server_response_and_generates_pull_id` now asserts
  the audit row `PullId` matches the response `pull_id`.
- `PullCache.ClaimForReport(...)` atomically claims `/audit/apply-result`
  reporting under the cache lock, preventing concurrent duplicate audit writes
  for the same `pull_id`.
- session audit warnings now use the generated trace id instead of the literal
  `session`.
- session audit rows can receive a route-level start timestamp, preserving
  login/logout duration when Kestrel calls the audit seam.
- the S5 response-shape guard now snapshots the login/logout helper envelopes.
- the current-drawing memory-only guard now also asserts the S6 audit seams are
  not wired to `/cad/current-drawing`.
