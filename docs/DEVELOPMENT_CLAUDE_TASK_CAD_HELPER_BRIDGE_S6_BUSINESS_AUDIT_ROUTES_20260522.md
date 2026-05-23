# Claude Taskbook: CAD Helper Bridge S6 - Business Forwarding And Audit Routes

Date: 2026-05-22

## 1. Purpose

CAD Desktop Helper Bridge **S6** implements the helper's first PLM business
forwarding routes and the local audit substrate, after S3 startup, S4
auth/origin, and S5 session state are already merged.

S6 owns:

- `POST /diff/preview`;
- `POST /sync/inbound`;
- `POST /sync/outbound`;
- `POST /audit/apply-result`;
- `pull_id` cache for `/diff/preview` -> `/audit/apply-result`;
- SQLite audit store at `%APPDATA%\YuantusPLM\audit.db`;
- PLM bearer forwarding from the S5 bearer seam.

S6 does **not** implement `/dedup/check`, `/shell/notify`, S7
`--reset-local-token`, S8 plugin migration, S9/S10 LISP bridge, CORS, or Python
FastAPI changes. Those remain separate opt-in slices.

## 2. Grounded Current Reality

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the relevant
S6 surface:

- Lines 498-501 list `/diff/preview`, `/sync/inbound`, `/sync/outbound`, and
  `/audit/apply-result` as helper endpoints.
- Lines 509-513 narrow `/diff/preview` to the `item_id` path only; helper must
  reject missing `item_id` instead of exposing the server's other request modes.
- Lines 515-556 define `/sync/inbound` request/response as a transparent
  service forwarding path and call out `PLMMATPUSH`.
- Lines 559-606 define `/diff/preview` request/response and the helper-added
  `pull_id`.
- Lines 608-635 define `/audit/apply-result`, allowed outcomes, and
  `pull_id` idempotency/TTL behavior.
- Lines 671-695 define SQLite `audit_events` shape and the audited endpoint
  family.
- Line 1062 defines S6 as `/diff/preview` + `/sync/inbound` +
  `/sync/outbound` + `pull_id` cache + `/audit/apply-result` + SQLite.

The R3.2 endpoint table also lists `/dedup/check` and `/shell/notify`, but the
work-breakdown table assigns `/dedup/check` to S8 and does not assign
`/shell/notify` to S6. This taskbook follows the work-breakdown slice boundary,
not the full table in one PR.

### 2.2 S5 state inherited by S6

Merged S5 provides:

- `HelperSessionService`;
- `JsonHelperSessionConfigStore`;
- `IPlmBearerTokenStore` and `DpapiPlmBearerTokenStore`;
- `CurrentDrawingStore`;
- `HttpPlmLoginClient`;
- Kestrel route mapping and S4 middleware placement.

S5 deliberately kept all S6 route names and SQLite absent. The S6 implementation
is expected to update S5/S4 source guards so they now allow the S6 route names
while still rejecting S7/S8+ scope.

### 2.3 Server plugin anchors

`plugins/yuantus-cad-material-sync/main.py` already defines the server-side
models and endpoints that S6 forwards to:

- `CadDiffPreviewRequest` / `CadDiffPreviewResponse` around lines 381-402;
- `SyncOutboundRequest` / `SyncOutboundResponse` around lines 407-422;
- `SyncInboundRequest` / `SyncInboundResponse` around lines 425-452;
- `POST /diff/preview` around lines 2241-2298;
- `POST /sync/outbound` around lines 2332-2366;
- `POST /sync/inbound` around lines 2371-2480.

S6 does not change these server routes.

## 3. Ratified S6 Boundaries

### 3.A Route surface

S6 implementation adds exactly these production helper routes:

```text
POST /diff/preview
POST /sync/inbound
POST /sync/outbound
POST /audit/apply-result
```

After S6, production helper route declarations must be exactly ten `Map*`
routes:

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

No S7/S8+ routes may appear: `/dedup/check`, `/shell/notify`, `/compose`,
`/validate`, `/tasks`, `/diagnostics/snapshot`, or an HTTP reset-token route.

### 3.B S4/S5 security stays in force

All four S6 routes are protected by the existing S4 gate:

- local helper token required;
- protocol header required;
- origin process allowlist required;
- no CORS;
- no browser `Authorization` header forwarding.

S6 uses the S5 PLM bearer store internally for outbound PLM requests. The PLM
bearer must never be returned in helper responses, error details, logs, audit
rows, or tests except inside fake test seams.

### 3.C Session requirement

S6 business forwarding routes require a logged-in S5 session:

- bearer token exists and can be read;
- `tenant_id` exists in helper config;
- `server_url` exists in helper config.

If any are missing, the route returns helper envelope `ok=false` with
`AUTH_TENANT_MISSING` or `AUTH_PLM_NOT_LOGGED_IN` as appropriate and does not
call PLM.

Rationale: S5 deliberately did not validate token freshness in
`/session/status`; S6 is the first point where missing/expired credentials are
operationally discovered.

### 3.D PLM forwarding contract

S6 forwards only to the S5-configured `server_url` under these paths:

```text
/plugins/cad-material-sync/diff/preview
/plugins/cad-material-sync/sync/inbound
/plugins/cad-material-sync/sync/outbound
```

Outbound PLM requests must include:

- `Authorization: Bearer <S5 token>`;
- `X-Yuantus-Protocol: 1.0` if the helper already uses protocol headers
  internally for PLM-bound calls;
- JSON request body, no multipart in S6.

S6 must not forward to arbitrary request-supplied URLs. `server_allowlist` was
already enforced at S5 login time; S6 reads the persisted `server_url` only.

PLM response handling:

- successful server JSON is wrapped as helper `ok=true` data;
- PLM helper/server envelope with `ok=false` is preserved as helper `ok=false`
  where possible;
- HTTP 401/403 maps to `AUTH_PLM_NOT_LOGGED_IN`;
- known PLM error codes such as `PLM_INBOUND_CONFLICT` are propagated rather
  than collapsed to `PLM_VALIDATION_FAILED`;
- network/non-JSON failures map to `PLM_VALIDATION_FAILED`.

### 3.E `/diff/preview` item_id-only boundary

S6 exposes only the item-id request path:

- `item_id` is required and non-empty;
- `values`, `target_properties`, and `target_cad_fields` request modes are not
  exposed by the helper in S6;
- missing `item_id` returns `ok=false` with `HELPER_INPUT_VALIDATION_FAILED`;
- PLM is not called on missing `item_id`.

The route response adds a helper-generated `pull_id` and stores the pull context
locally for `/audit/apply-result`. The raw server response remains nested under
`server_response`.

### 3.F Pull cache

S6 adds an in-process pull cache with TTL 10 minutes.

Cache entry:

```text
pull_id
drawing_path
write_cad_fields
created_at
reported
```

Rules:

- `pull_id` is generated only after a successful `/diff/preview` PLM response;
- `pull_id` format is `PULL-` + `Guid.NewGuid().ToString("N")`
  (32 lowercase hexadecimal characters; no ULID dependency in S6 R1);
- TTL expiry returns `AUDIT_PULL_ID_EXPIRED`;
- unknown `pull_id` returns `AUDIT_PULL_ID_UNKNOWN`;
- a second `/audit/apply-result` for the same `pull_id` returns
  `AUDIT_ALREADY_REPORTED`;
- helper restart loses in-process pull cache; historical audit rows do not
  recreate active cache entries in S6 R1.

Rationale: R3.2 specifies a 10-minute helper cache. Durable cache recovery is a
larger state-management slice and is not required for R1.

### 3.G SQLite audit

S6 creates and writes local SQLite audit DB at:

```text
%APPDATA%\YuantusPLM\audit.db
```

Schema is the R3.2 `audit_events` table:

```sql
CREATE TABLE audit_events (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  drawing_path TEXT,
  profile_id TEXT,
  item_id TEXT,
  pull_id TEXT,
  cad_system TEXT,
  outcome TEXT NOT NULL,
  error_code TEXT,
  duration_ms INTEGER NOT NULL,
  trace_id TEXT NOT NULL,
  applied_fields_json TEXT,
  failed_fields_json TEXT
);

CREATE INDEX idx_audit_ts ON audit_events(ts);
CREATE INDEX idx_audit_pull ON audit_events(pull_id);
```

S6 audited endpoints:

- `/diff/preview`;
- `/sync/inbound`;
- `/sync/outbound`;
- `/audit/apply-result`;
- `/session/login`;
- `/session/logout`.

S6 implementation may need to route S5 login/logout through an audit seam. That
is allowed only for local audit writes and must not change S5 response shapes or
session semantics.

For `/diff/preview`, `/sync/inbound`, and `/sync/outbound`, the audit row must
be written **after** the PLM response has been received and the helper-visible
outcome has been determined. S6 records the actual outcome, not pre-call intent.
If the helper crashes after PLM success but before SQLite write, that is an
audit gap; it is more recoverable than persisting a pre-PLM row with the wrong
outcome.

S6 must not audit `/healthz`, `/version`, or `/session/status`.

S8 implementation must extend the audited endpoint set to include
`/dedup/check` per R3.2 design line 695. S6 must not add that route or audit it.

### 3.H Audit failure policy

Ratified:

- **H1 fail-closed** for `/audit/apply-result`: if audit write fails, return
  helper envelope `ok=false` with `AUDIT_WRITE_FAILED` and do not report
  success.
- **H2 fail-open** for `/diff/preview`, `/sync/inbound`, and `/sync/outbound`:
  after PLM success and on audit write failure, preserve the PLM-success
  response to the caller.

Under H2, the helper must emit exactly one minimal stderr line:

```text
[AUDIT_WRITE_FAILED] endpoint=<path> trace_id=<id> reason=<short>
```

The bearer token, request bodies, and PII must not appear in this line. This is
the minimum operator-visibility seam until S6.5+ adds Serilog file logging per
R3.2 design §5.6.

Rationale:

- `/audit/apply-result` exists solely to record local audit, so claiming success
  when audit persistence failed is incorrect.
- `/diff/preview`, `/sync/inbound`, and `/sync/outbound` are PLM business calls;
  a local audit failure after a successful PLM response should not necessarily
  mask the PLM result. The stderr line prevents the failure from being invisible
  before the later logging slice exists.

### 3.I Current drawing coupling

S6 does not auto-fill business-route drawing payloads from `CurrentDrawingStore`
in R1.

Rationale:

- R3.2 examples include `drawing` in the business request bodies;
- S5 `/cad/current-drawing` is process-local context for later CAD callers, but
  R3.2 does not explicitly say S6 should silently synthesize missing business
  payload fields from that state;
- implicit auto-fill would make route behavior depend on prior process-local
  calls and complicate replay/debugging.

Implementation may expose a narrow helper method for S8/S9 to read current
drawing later, but S6 routes should require/forward explicit request data.

### 3.J Trace IDs

S6 audit rows require `trace_id`.

Implementation should generate one trace ID per helper request and forward it to
PLM with a stable header such as `X-Yuantus-Trace-Id`. The exact header name is
not part of the server contract today, so S6 tests should pin helper-side
generation and audit storage, not server-side interpretation.

### 3.K Error codes

S6 may add missing shared constants:

- `AUDIT_PULL_ID_UNKNOWN`;
- `AUDIT_ALREADY_REPORTED`;
- `AUDIT_PULL_ID_EXPIRED`.

Existing constants used by S6:

- `AUDIT_WRITE_FAILED`;
- `AUTH_PLM_NOT_LOGGED_IN`;
- `AUTH_TENANT_MISSING`;
- `HELPER_INPUT_VALIDATION_FAILED`;
- `PLM_INBOUND_CONFLICT`;
- `PLM_VALIDATION_FAILED`.

Ratified HTTP status policy: **Option B, the general helper-envelope rule wins
for S6 business errors**.

R3.2 design line 486 says business exceptions return helper envelope `200 OK` +
`ok=false`, while HTTP-layer auth/origin errors use 4xx. R3.2 design line 633
also names audit-specific 404/409 statuses for unknown or duplicate `pull_id`.
S6 R1 intentionally resolves that contradiction in favor of the unified helper
envelope:

- `AUDIT_PULL_ID_UNKNOWN` -> HTTP 200 + `ok=false`;
- `AUDIT_ALREADY_REPORTED` -> HTTP 200 + `ok=false`;
- `AUDIT_PULL_ID_EXPIRED` -> HTTP 200 + `ok=false`;
- `AUDIT_WRITE_FAILED` under H1 fail-closed -> HTTP 200 + `ok=false`.

The 404/409 wording at design line 633 is obsolete for S6 R1. S4 HTTP-layer
auth/origin errors remain 4xx.

Accepted `/audit/apply-result` request outcome values:

- `ok`;
- `partial`;
- `failed`;
- `not-applied-display-only`.

The SQLite `audit_events.outcome` column also permits `error` for helper-created
audit-failure rows such as `AUDIT_WRITE_FAILED`; callers must not submit
`outcome=error` to `/audit/apply-result`.

## 4. R1 Target Output

Implementation PR should contain:

- Helper runtime changes for the four S6 routes.
- PLM forwarding seam with fakeable tests.
- Pull cache primitive with injectable clock.
- SQLite audit store primitive.
- Narrow update to S5/S4 guard tests to allow S6 route names and SQLite while
  still rejecting S7/S8+ scope.
- `clients/cad-desktop-helper/Helper.Tests/HelperBusinessAuditContractTests.cs`
  or equivalent.
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S6_BUSINESS_AUDIT_ROUTES_R1_20260522.md`.
- One `docs/DELIVERY_DOC_INDEX.md` line.

## 5. Mandatory Tests

S6 implementation must add these exactly named tests:

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

Source/drift guards:

- exactly ten production route declarations after S6;
- no `UseCors`;
- no browser `Authorization` forwarding;
- no `/dedup/check`, `/shell/notify`, `/compose`, `/validate`, `/tasks`,
  `/diagnostics/snapshot`, or `--reset-local-token`;
- no server Python or AutoCAD plugin edits;
- no token string in audit rows or helper responses.

Test 15 must cover both arms of §3.H:

- `/audit/apply-result` audit-write failure returns HTTP 200 + `ok=false` with
  `AUDIT_WRITE_FAILED`;
- a post-PLM audit-write failure for each H2 route returns the PLM-success
  response and emits one sanitized `[AUDIT_WRITE_FAILED] ...` stderr line.

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

## 7. DEV / Verification MD Requirements

Implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S6_BUSINESS_AUDIT_ROUTES_R1_20260522.md
```

The DEV MD must document:

- routes added;
- PLM forwarding seam;
- pull cache semantics;
- SQLite audit schema;
- ratified H boundary;
- S5/S4 guard updates;
- local checks;
- GitHub `cad-helper-shared-dotnet` result.

## 8. Non-Goals

Taskbook non-goals:

- No implementation.
- No code/schema/runtime edits.
- No CAD plugin edits.
- No Python FastAPI edits.

S6 implementation non-goals:

- no `/dedup/check`;
- no `/shell/notify`;
- no `/compose`;
- no `/validate`;
- no `/tasks`;
- no `/diagnostics/snapshot`;
- no S7 `--reset-local-token`;
- no S8 AutoCAD plugin migration;
- no S9/S10 LISP bridge;
- no durable pull-cache recovery after helper restart;
- no CORS;
- no server-side plugin behavior changes.

## 9. Decision Gate

Recommended branch after taskbook merge:

```text
feat/cad-helper-bridge-s6-business-audit-r1-20260522
```

Implementation remains a separate explicit opt-in after this taskbook merges.

## 10. Reviewer Focus

Please review especially:

- §3.A route surface: confirm S6 excludes `/dedup/check` and `/shell/notify`
  despite their appearance in the broader R3 endpoint table.
- §3.C login/session requirement: confirm missing bearer/tenant/server blocks
  PLM forwarding.
- §3.D PLM forwarding and error mapping.
- §3.E item-id-only `/diff/preview` boundary.
- §3.F in-process pull cache and no restart recovery.
- §3.G SQLite schema and audited endpoint set, including session login/logout.
- §3.H audit failure policy: ratify H1/H2 hybrid or require a different policy
  before implementation starts.
- §3.I no implicit current-drawing auto-fill.
