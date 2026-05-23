# Claude Taskbook: CAD Helper Bridge S8 - MaterialSync Migration And Apply Audit

Date: 2026-05-23

Type: **Doc-only taskbook.** Changes no runtime, no schema, no service, no
workflow, and no CAD plugin code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook does
NOT authorize that implementation.

## 1. Purpose

CAD Desktop Helper Bridge **S8-R1** is the first slice that crosses from the
helper runtime back into the existing AutoCAD plugin package.

S8-R1 owns only the grounded helper-supported plugin migration:

- `CADDedupPlugin.MaterialSyncApiClient.DiffPreviewAsync(...)` migration from
  direct PLM HTTP to `Yuantus.Cad.Shared` -> helper `/diff/preview`;
- `CADDedupPlugin.MaterialSyncApiClient.SyncInboundAsync(...)` migration to
  helper `/sync/inbound`;
- `CADDedupPlugin.MaterialSyncApiClient.SyncOutboundAsync(...)` migration to
  helper `/sync/outbound`;
- `PLMMATPULL` apply-result reporting through helper `/audit/apply-result` after
  a CAD write attempt;
- SDK-free test/workflow coverage for the migrated client behavior.

S8-R1 deliberately does **not** add helper `POST /dedup/check`, does **not**
migrate `DedupApiClient.CheckDuplicateAsync(...)`, and does **not** add a
dedup-vision configuration/authentication seam. That route is deferred because
the current repository shows `/api/dedup/check` is not a PLM endpoint, while S6
helper forwarding currently owns only PLM-session forwarding.

S8-R1 also does **not** implement S9/S10 LISP bridge code, `/shell/notify`,
`/compose`, `/validate`, `/tasks`, `/diagnostics/snapshot`, CORS, server Python
routes, database/schema migrations, tenant-baseline data, or a CAD drawing write
adapter rewrite. Those remain separate opt-in slices.

## 2. Grounded Current Reality

Grounded against `origin/main = 431b6adf` after S7 merged.

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S8-relevant
surface:

- Lines 490-502 list helper endpoints and include `POST /dedup/check` as an
  intended helper route.
- Lines 671-695 define the SQLite `audit_events` table and include
  `/dedup/check` in the audited endpoint family.
- Lines 971-980 define the existing AutoCAD integration points:
  `MaterialSyncApiClient.cs`, `DedupApiClient.cs`, `CadMaterialFieldService.cs`,
  `MaterialSyncDiffPreviewWindow.xaml(.cs)`, `DedupPlugin.cs`, and
  `UserIdentification.cs`.
- Line 976 says `DedupApiClient.CheckDuplicateAsync(...)` should keep its public
  API shape while eventually migrating through helper `/dedup/check`.
- Lines 975 and 979 require `MaterialSyncApiClient` and `DedupPlugin` to keep
  public API / command behavior stable while their internal transport moves
  through Shared/helper.
- Line 1064 defines S8 as `CADDedupPlugin` refactor, including
  `MaterialSyncApiClient`, `DedupApiClient`, multipart support, and
  `/audit/apply-result` reporting after `PLMMATPULL` applies CAD fields.
- Line 1071 says S8 depends on S1 + S6.

The design is still the architectural north star, but the repository facts in
§2.4 make `/dedup/check` unsafe to implement in S8-R1 without a separate
upstream-service decision.

### 2.2 S1 Shared primitives inherited by S8

S1 already provides the transport primitives S8 should consume:

- `clients/cad-desktop-helper/Shared/Transport/HelperTransport.cs` exposes
  `PostJsonAsync<T>(...)`, `PostContentAsync<T>(...)`, and `GetAsync<T>(...)`.
- `HelperTransport` injects `X-Yuantus-Local-Token` and
  `X-Yuantus-Protocol` on every helper request.
- `HelperTransport` owns helper discovery, local-token refresh, protocol
  versioning, and helper envelope parsing.

S8-R1 must reuse `HelperTransport`. It must not create a second local-token
reader, second helper discovery path, or second response-envelope parser inside
`CADDedupPlugin`.

### 2.3 S6 helper business and audit substrate

S6 already merged:

- `POST /diff/preview`;
- `POST /sync/inbound`;
- `POST /sync/outbound`;
- `POST /audit/apply-result`;
- `HelperBusinessAuditService`;
- `IPlmBusinessClient` for JSON PLM forwarding;
- `SqliteAuditEventStore`;
- H1/H2 audit-write failure policy.

S6 helper routes after S7 are still exactly ten:

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

Because S8-R1 defers `/dedup/check`, the production helper route count remains
exactly ten after S8-R1. Route count 11 belongs to a future dedup-check slice,
not this PR.

### 2.4 `/dedup/check` upstream-service ambiguity

The R3.2 design and existing plugin use the literal path `/api/dedup/check`, but
current server code does not expose that path through PLM:

- `src/yuantus/api/app.py:328` mounts `dedup_router` under `/api/v1`.
- `src/yuantus/meta_engine/web/dedup_router.py:24` declares
  `APIRouter(prefix="/dedup", tags=["Dedup"])`.
- The PLM dedup router exposes `/rules`, `/records`, `/report`, `/batches`, and
  related endpoints, but no `/check` endpoint.
- `clients/autocad-material-sync/CADDedupPlugin/DedupApiClient.cs:66` posts to
  literal `/api/dedup/check`.
- `src/yuantus/config/settings.py:102` sets `DEDUP_VISION_BASE_URL` default to
  `http://localhost:8100`, a separate service/port from the PLM server.
- `src/yuantus/config/settings.py:105` defines `DEDUP_VISION_SERVICE_TOKEN`, a
  separate auth seam from the S5 PLM bearer.
- `src/yuantus/integrations/dedup_vision.py` uses the dedup-vision base URL and
  service token, and probes `/api/v2/search`, `/api/search`, and
  `/api/index/add`.

Therefore a helper implementation that forwards:

```text
POST <server_url>/api/dedup/check
Authorization: Bearer <S5 PLM bearer>
```

would either call PLM and receive 404, or call dedup-vision with the wrong
credential and receive 401. S8-R1 must not bake in either broken path.

### 2.5 Existing CADDedupPlugin HTTP clients

`clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs`
currently:

- constructs a direct `HttpClient` pointed at `DedupConfig.ServerUrl`;
- injects direct PLM bearer and tenant/org headers from `DedupConfig`;
- exposes public methods `GetProfilesAsync`, `GetProfileAsync`, `ComposeAsync`,
  `ValidateAsync`, `SyncInboundAsync`, `SyncOutboundAsync`, and
  `DiffPreviewAsync`;
- sends direct JSON calls to `/api/v1/plugins/cad-material-sync/...`.

`clients/autocad-material-sync/CADDedupPlugin/DedupApiClient.cs` currently:

- constructs a direct `HttpClient` pointed at `DedupConfig.ServerUrl`;
- injects direct bearer from `DedupConfig.ApiKey`;
- `CheckDuplicateAsync(string filePath)` opens the DWG using
  `FileShare.ReadWrite`;
- sends multipart parts `file`, `threshold`, `auto_index`, and `file_path` to
  `/api/dedup/check`;
- updates `_lastResult` and `UsageStatistics` after a successful response.

Because `DedupApiClient` points at the unresolved `/api/dedup/check` upstream,
it remains a documented legacy direct path in S8-R1.

### 2.6 Existing AutoCAD command coupling

`clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs` currently:

- calls `DedupApiClient.CheckDuplicateAsync(...)` from duplicate-check flows;
- calls `MaterialSyncApiClient.SyncInboundAsync(...)` from `PLMMATPUSH`;
- calls `MaterialSyncApiClient.DiffPreviewAsync(...)` from `PLMMATPULL`;
- applies confirmed `write_cad_fields` through
  `CadMaterialFieldService.ApplyFields(...)`.

`MaterialDiffPreviewResponse` currently has no `PullId` property. The S6 helper
`/diff/preview` response shape is:

```json
{
  "pull_id": "PULL-...",
  "server_response": { "...": "..." }
}
```

Therefore S8-R1 cannot simply deserialize the helper response directly into the
existing `MaterialDiffPreviewResponse`. It must unwrap `server_response` and
carry `pull_id` through an added, public-shape-preserving field/property for
`/audit/apply-result`.

### 2.7 Current verification reality

The existing AutoCAD plugin project
`clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj` requires
real AutoCAD managed assemblies (`accoremgd.dll`, `acdbmgd.dll`, `acmgd.dll`,
`AcWindows.dll`, `AdWindows.dll`). The normal GitHub Windows runner does not
have AutoCAD installed.

Existing non-AutoCAD checks include:

- `clients/autocad-material-sync/verify_material_sync_static.py`;
- fixture/e2e scripts under `clients/autocad-material-sync/`;
- `.github/workflows/cad-helper-shared-dotnet.yml`, which currently covers
  Shared, Detector, and Helper projects, but not the AutoCAD plugin package.

S8-R1 implementation must add verification that is honest about this split:

- SDK-free CI for helper/shared/plugin-client contracts;
- manual Windows + AutoCAD evidence deferred to operational signoff unless the
  owner explicitly provides a real Windows AutoCAD environment for the PR.

## 3. S8-R1 Decisions And Boundaries

### 3.A RATIFIED: defer `/dedup/check` from S8-R1

S8-R1 adopts the lowest-risk convergence path from review: defer
`/dedup/check` to a future slice until the upstream-service question is resolved.

Rejected for S8-R1:

- adding a dedup-vision URL/config/auth seam in helper;
- adding a PLM `/api/dedup/check` proxy requirement inside this plugin-migration
  PR;
- forwarding `/dedup/check` through S5 PLM bearer semantics despite the current
  server surface not supporting it.

After S8-R1, helper production route declarations remain exactly ten:

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

No S8-R1 code may add `MapPost("/dedup/check"...)`.

### 3.B RATIFIED: MaterialSync migration scope

S8-R1 migrates only helper-supported `MaterialSyncApiClient` methods:

- `MaterialSyncApiClient.DiffPreviewAsync(...)` -> helper `/diff/preview`;
- `MaterialSyncApiClient.SyncInboundAsync(...)` -> helper `/sync/inbound`;
- `MaterialSyncApiClient.SyncOutboundAsync(...)` -> helper `/sync/outbound`.

S8-R1 does **not** migrate:

- `MaterialSyncApiClient.GetProfilesAsync(...)`;
- `MaterialSyncApiClient.GetProfileAsync(...)`;
- `MaterialSyncApiClient.ComposeAsync(...)`;
- `MaterialSyncApiClient.ValidateAsync(...)`;
- `DedupApiClient.CheckDuplicateAsync(...)`;
- `DedupApiClient.TestConnectionAsync()`.

Those remain direct legacy calls until later, separately opted-in helper routes
or upstream-service decisions exist.

The S8-R1 implementation and PR body must not claim helper is the unique PLM
exit for every AutoCAD plugin command after S8. The honest claim is narrower:
helper becomes the PLM exit for PLMMATPUSH, PLMMATPULL diff preview, and
PLMMATPULL apply-result reporting. Dedup check, profiles, compose, validate, and
test-connection remain explicit legacy direct paths.

Rejected for S8-R1:

- adding helper profiles/compose/validate routes in S8;
- deleting or disabling PLMMATCOMPOSE/profile commands;
- migrating `DedupApiClient.CheckDuplicateAsync(...)` before `/dedup/check`
  upstream-service ownership is ratified.

### 3.C Security and session policy

The migrated MaterialSync methods call existing helper business routes and
inherit their existing security policy:

- S4 local-token auth;
- S4 protocol header;
- S4 origin allowlist;
- no CORS;
- no browser `Authorization` header forwarding;
- S5 `server_url` and PLM bearer required by helper before PLM forwarding.

The plugin must not inject direct PLM bearer headers for the migrated methods.
Session state and PLM bearer are owned by helper S5. Existing config fields may
remain for legacy direct calls not migrated in S8-R1.

### 3.D MaterialSyncApiClient helper adaptation

For the three S6-supported methods, `MaterialSyncApiClient` must keep its public
method signatures and return types stable:

- `DiffPreviewAsync(...)`;
- `SyncInboundAsync(...)`;
- `SyncOutboundAsync(...)`.

The internal transport changes to `HelperTransport`.

`DiffPreviewAsync(...)` must adapt helper response shape:

1. call helper `/diff/preview`;
2. read helper data `{ pull_id, server_response }`;
3. deserialize `server_response` into the existing
   `MaterialDiffPreviewResponse` shape;
4. carry the helper `pull_id` through a new `PullId` property or equivalent
   internal-accessible field so `DedupPlugin.PLMMATPULL` can report
   `/audit/apply-result`;
5. keep existing UI consumption (`WriteCadFields`, `Diffs`,
   `RequiresConfirmation`, etc.) unchanged.

`SyncInboundAsync(...)` and `SyncOutboundAsync(...)` must return the same
`MaterialSyncResponse` shape callers receive today.

### 3.E PLMMATPULL apply-result reporting

After `PLMMATPULL` receives a successful helper-backed diff preview and the user
confirms a CAD write attempt, `DedupPlugin` must call helper
`/audit/apply-result` with:

- `pull_id` from the helper diff preview;
- `outcome = "ok"` if `CadMaterialFieldService.ApplyFields(...)` returns
  without throwing;
- `outcome = "failed"` if `ApplyFields(...)` throws after the preview
  succeeded;
- `applied_fields` equal to the attempted write field dictionary when the write
  returns without throwing;
- `failed_fields` populated with the attempted field dictionary or an error
  marker when `ApplyFields(...)` throws;
- `drawing.filename` and `drawing.filepath` derived from the current AutoCAD
  document path;
- `cad_system = "autocad"`;
- `duration_ms` measured around the CAD write attempt.

If the user cancels the diff preview before any write attempt, S8-R1 does not
call `/audit/apply-result`. The existing `/diff/preview` audit row remains the
record that the preview occurred.

If helper `/audit/apply-result` fails after a successful CAD write, the plugin
must not roll back CAD writes. It should write a clear AutoCAD command-line
warning with the helper error code and continue. This mirrors the reality that
DWG writes are already committed inside the AutoCAD process and the helper audit
write is not transactional with them.

### 3.F CADDedupPlugin project reference and target preservation

S8-R1 implementation must add a `ProjectReference` from
`CADDedupPlugin.csproj` to
`clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj`.

The AutoCAD plugin must consume the Shared **net46** target. It must preserve:

- AutoCAD 2018 default `TargetFrameworkVersion = v4.6`;
- AutoCAD 2024 explicit `v4.8` path;
- `PlatformTarget = x64`;
- existing `PackageContents.*.xml`;
- existing public command names:
  - `DEDUPCHECK`;
  - `DEDUPVIEW`;
  - `PLMMATPROFILES`;
  - `PLMMATCOMPOSE`;
  - `PLMMATPUSH`;
  - `PLMMATPULL`.

### 3.G Legacy direct-call documentation

The implementation DEV/Verification MD and PR body must list the legacy direct
calls that remain after S8-R1:

- `MaterialSyncApiClient.GetProfilesAsync(...)`;
- `MaterialSyncApiClient.GetProfileAsync(...)`;
- `MaterialSyncApiClient.ComposeAsync(...)`;
- `MaterialSyncApiClient.ValidateAsync(...)`;
- `DedupApiClient.CheckDuplicateAsync(...)`;
- `DedupApiClient.TestConnectionAsync()`.

This is not a failure of S8-R1; it is the ratified boundary. These calls are
left direct because helper has no corresponding route for profiles/compose/
validate, and because `/dedup/check` upstream ownership is unresolved.

### 3.H Future `/dedup/check` slice requirements

A future dedup-check slice must not reuse the discarded S8-R1 assumption that
`/dedup/check` is an S5 PLM-bearer endpoint. It must first ratify one of:

- a new helper config/auth seam for dedup-vision URL + service token;
- a PLM proxy endpoint that forwards to dedup-vision server-side;
- another explicitly documented upstream contract.

That future slice must also pin the details intentionally deferred from S8-R1:

- inbound multipart `IFormFile.FileName` preservation as outbound
  `Content-Disposition: form-data; name="file"; filename="<inbound filename>"`;
- whether `cad_system` is always null for dedup-check audit rows or is supplied
  as a helper-only side-band field stripped before upstream forwarding;
- `_lastResult` and `UsageStatistics` ordering for
  `DedupApiClient.CheckDuplicateAsync(...)`, preserving the current behavior of
  setting `_lastResult` only after successful deserialization.

### 3.I Verification and workflow policy

S8-R1 must extend the dedicated Windows .NET workflow so it catches changes that
do not require AutoCAD assemblies.

Required CI posture:

- `.github/workflows/cad-helper-shared-dotnet.yml` path filters include
  `clients/autocad-material-sync/**`;
- workflow continues to build/test Shared, Detector, and Helper;
- workflow runs `python clients/autocad-material-sync/verify_material_sync_static.py`;
- implementation adds SDK-free contract coverage for migrated client behavior.

Recommended SDK-free test shape:

- create an AutoCAD-SDK-free test project that links or compiles only the
  transport/client source files required to test `MaterialSyncApiClient` and the
  PLMMATPULL apply-result adapter behavior;
- do not reference AutoCAD managed assemblies in that test project;
- do not require real AutoCAD installation on GitHub runners.

The real `CADDedupPlugin.csproj` build/load still requires Windows + AutoCAD
2018/2024 and remains manual operational evidence.

### 3.J Manual Windows AutoCAD evidence posture

S8-R1 implementation should continue the risk-downgrade posture used by S7
unless the owner provides a real Windows + AutoCAD environment before merge.

Required documentation if manual evidence is unavailable:

- DEV/Verification MD must explicitly say Windows AutoCAD build/load/smoke was
  not run locally;
- PR body must explicitly list deferred operational evidence;
- merge must not be represented as Windows AutoCAD validation.

Deferred evidence for S8-R1:

- Windows + AutoCAD 2018 build of `CADDedupPlugin.csproj`;
- AutoCAD loads `CADDedup.bundle`;
- `PLMMATPUSH` routes through helper `/sync/inbound`;
- `PLMMATPULL` routes through helper `/diff/preview`, writes CAD fields, and
  posts `/audit/apply-result`;
- helper audit DB contains `/diff/preview` and `/audit/apply-result` rows for
  the smoke run.

This is not a substitute for evidence. It is a conscious implementation-merge
risk posture so the repo can keep moving without pretending this workstation can
prove AutoCAD runtime behavior.

## 4. R1 Target Output

Implementation PR should contain:

- `CADDedupPlugin.csproj` Shared `ProjectReference`;
- `MaterialSyncApiClient` helper-backed implementations for
  `DiffPreviewAsync`, `SyncInboundAsync`, and `SyncOutboundAsync`;
- `MaterialDiffPreviewResponse` or adjacent adapter support for carrying
  helper `pull_id`;
- `PLMMATPULL` apply-result reporting after CAD write attempts;
- SDK-free test coverage for client migration behavior;
- workflow update for AutoCAD material sync static checks and any SDK-free
  client tests;
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md`;
- one `docs/DELIVERY_DOC_INDEX.md` line.

Implementation PR must **not** contain:

- helper `POST /dedup/check`;
- `DedupApiClient.CheckDuplicateAsync(...)` migration;
- dedup-vision URL/token config;
- PLM `/api/dedup/check` proxy code;
- route-count changes in helper runtime.

## 5. Mandatory Tests

S8-R1 implementation must add these exactly named tests, either in one test file
or split by project where appropriate:

1. `test_s8_preserves_helper_route_count_at_ten_and_adds_no_dedup_route`
2. `test_material_sync_client_migrates_s6_supported_methods_to_helper_transport`
3. `test_material_sync_client_unwraps_helper_diff_preview_pull_id_and_server_response`
4. `test_material_sync_client_sync_inbound_uses_helper_not_direct_plm`
5. `test_material_sync_client_sync_outbound_uses_helper_not_direct_plm`
6. `test_material_sync_auxiliary_methods_remain_explicit_legacy_direct_calls`
7. `test_dedup_api_client_remains_legacy_direct_until_upstream_is_ratified`
8. `test_plmmatpull_reports_audit_apply_result_after_successful_write_attempt`
9. `test_plmmatpull_write_failure_reports_failed_apply_result_without_swallowing_cad_error`
10. `test_plmmatpull_cancel_does_not_report_apply_result`
11. `test_caddedup_plugin_references_shared_net46_without_changing_autocad_targets`
12. `test_material_sync_migration_does_not_change_public_method_signatures`
13. `test_s8_static_contract_documents_deferred_dedup_check_upstream_question`
14. `test_s8_keeps_no_lisp_shell_compose_validate_tasks_or_cors_scope`
15. `test_s8_workflow_runs_autocad_static_contracts_without_autocad_sdk`
16. `test_s8_dev_verification_records_deferred_windows_autocad_signoff`

Source/drift guards:

- exactly ten helper production route declarations after S8-R1;
- no `MapPost("/dedup/check"` in helper runtime;
- no `/shell/notify`, `/compose`, `/validate`, `/tasks`,
  `/diagnostics/snapshot`, or HTTP reset route;
- no `UseCors`;
- no browser `Authorization` header forwarding;
- no token string in helper responses, AutoCAD command output, or stderr;
- no direct PLM calls remain in `MaterialSyncApiClient.DiffPreviewAsync`,
  `SyncInboundAsync`, or `SyncOutboundAsync`;
- `MaterialSyncApiClient.GetProfilesAsync`, `GetProfileAsync`, `ComposeAsync`,
  `ValidateAsync`, `DedupApiClient.CheckDuplicateAsync`, and
  `DedupApiClient.TestConnectionAsync` remain explicitly documented legacy
  direct calls;
- AutoCAD project targeting remains v4.6 for 2018 and v4.8 for the 2024 path.

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

Static client checks expected in implementation:

```bash
python3 clients/autocad-material-sync/verify_material_sync_static.py
xmllint --noout \
  clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.xml \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.2018.xml \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.2024.xml
```

.NET verification expected from GitHub Windows workflow:

```bash
dotnet build clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj --configuration Release --no-restore
dotnet test  clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj --configuration Release --no-restore
dotnet build clients/cad-desktop-helper/Helper/Yuantus.Cad.Helper.csproj --configuration Release --no-restore
dotnet test  clients/cad-desktop-helper/Helper.Tests/Yuantus.Cad.Helper.Tests.csproj --configuration Release --no-restore
```

If S8-R1 adds an SDK-free AutoCAD client test project, the workflow must also
run that project's restore/build/test steps.

This workstation does not have a local .NET SDK or AutoCAD runtime. Do not claim
local `.NET` or AutoCAD test success unless those commands are actually run in
an environment that supports them.

## 7. DEV/Verification MD Requirements

The S8-R1 implementation DEV/Verification MD must record:

- final helper route count remains ten;
- exact migrated plugin methods;
- exact legacy direct-call methods still left by §3.B / §3.G;
- the deferred `/dedup/check` upstream-service decision;
- `PLMMATPULL` apply-result behavior;
- workflow changes;
- local verification commands and outputs;
- GitHub Windows workflow run URL / run id;
- manual Windows AutoCAD evidence status, including any deferred operational
  signoff items from §3.J.

## 8. Explicit Non-Goals

- No helper `/dedup/check` in S8-R1.
- No `DedupApiClient.CheckDuplicateAsync(...)` migration in S8-R1.
- No dedup-vision config/auth seam in S8-R1.
- No PLM `/api/dedup/check` proxy in S8-R1.
- No S9/S10 LISP bridge.
- No `/shell/notify`.
- No `/compose` or `/validate` helper route in S8-R1.
- No helper profile routes in S8-R1.
- No CORS.
- No browser `Authorization` forwarding.
- No Python FastAPI or plugin server changes.
- No database/schema/migration/tenant-baseline changes.
- No rewrite of `CadMaterialFieldService` or DWG write algorithms.
- No AutoCAD package metadata widening beyond the Shared reference needed for
  S8-R1.
- No claim that helper is the unique PLM exit for every AutoCAD plugin command
  until the legacy direct-call methods in §3.B / §3.G are handled by later
  route slices.

## 9. Recommended Branch

Implementation branch after this taskbook merges and receives separate opt-in:

```text
feat/cad-helper-bridge-s8-material-sync-migration-r1-20260523
```

The old broader branch name
`feat/cad-helper-bridge-s8-dedup-plugin-migration-r1-20260523` should not be
used for R1 unless the reviewer explicitly re-expands scope to `/dedup/check`.

## 10. Reviewer Focus

Review should focus on:

1. Confirm §3.A Path C convergence: `/dedup/check` is deferred from S8-R1.
2. Confirm helper route count remains ten and no new helper route is added.
3. Confirm §3.B migration scope: only `DiffPreviewAsync`, `SyncInboundAsync`,
   and `SyncOutboundAsync` move to helper in S8-R1.
4. Confirm `DedupApiClient.CheckDuplicateAsync(...)` and `TestConnectionAsync()`
   remain documented legacy direct calls.
5. Confirm `PLMMATPULL` cancel / success / failure audit semantics in §3.E.
6. Confirm the future `/dedup/check` slice requirements in §3.H carry the
   upstream-service, filename, cad_system, and `_lastResult`/UsageStatistics
   details forward.
7. Confirm the workflow and SDK-free test posture in §3.I is sufficient without
   pretending GitHub can build the AutoCAD plugin DLL.
8. Confirm the S8-R1 manual Windows AutoCAD evidence is deferred operational
   signoff unless the owner provides a real Windows AutoCAD environment.
