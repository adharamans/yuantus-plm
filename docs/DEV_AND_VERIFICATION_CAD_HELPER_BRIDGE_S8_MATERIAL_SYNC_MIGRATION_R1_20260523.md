# DEV and Verification: CAD Helper Bridge S8 MaterialSync Migration R1

Date: 2026-05-23

## 1. Scope

This PR implements the narrowed S8-R1 contract from
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S8_DEDUP_PLUGIN_MIGRATION_20260523.md`.

Implemented:

- `CADDedupPlugin` references `Yuantus.Cad.Shared` while preserving the AutoCAD
  2018 `v4.6` default target and the AutoCAD 2024 `v4.8` path.
- `MaterialSyncApiClient.DiffPreviewAsync(...)` now calls helper
  `/diff/preview` through `HelperTransport`.
- `MaterialSyncApiClient.SyncInboundAsync(...)` now calls helper
  `/sync/inbound`.
- `MaterialSyncApiClient.SyncOutboundAsync(...)` now calls helper
  `/sync/outbound`.
- `MaterialDiffPreviewResponse` carries the helper `pull_id` so `PLMMATPULL`
  can report apply-result.
- `PLMMATPULL` calls helper `/audit/apply-result` after a confirmed CAD write
  attempt.
- SDK-free `CADDedupPlugin.Client.Tests` cover the S8-R1 client contract without
  requiring AutoCAD managed assemblies.
- `cad-helper-shared-dotnet` now includes AutoCAD material-sync paths and runs
  the SDK-free client tests plus `verify_material_sync_static.py`.

Not implemented:

- helper `/dedup/check`;
- `DedupApiClient.CheckDuplicateAsync(...)` migration;
- dedup-vision URL/token configuration;
- PLM `/api/dedup/check` proxy;
- profile/compose/validate helper routes;
- S9/S10 LISP bridge or CORS.

## 2. Runtime Behavior

### 2.1 MaterialSync helper calls

The three S6-supported `MaterialSyncApiClient` methods now use
`IMaterialSyncHelperTransport`:

- `/diff/preview`;
- `/sync/inbound`;
- `/sync/outbound`.

The default transport resolves or starts the local helper through
`HelperLocator`, then uses `HelperTransport` so S1 discovery, local-token refresh,
protocol versioning, and helper envelope parsing stay single-sourced.

### 2.2 Legacy direct calls left intentionally

The following methods remain direct legacy calls:

- `MaterialSyncApiClient.GetProfilesAsync(...)`;
- `MaterialSyncApiClient.GetProfileAsync(...)`;
- `MaterialSyncApiClient.ComposeAsync(...)`;
- `MaterialSyncApiClient.ValidateAsync(...)`;
- `DedupApiClient.CheckDuplicateAsync(...)`;
- `DedupApiClient.TestConnectionAsync()`.

This is the ratified S8-R1 boundary. The implementation does not claim helper is
the unique PLM exit for every AutoCAD plugin command.

### 2.3 PLMMATPULL apply-result audit

After a successful helper-backed diff preview and user confirmation:

- successful `CadMaterialFieldService.ApplyFields(...)` reports
  `/audit/apply-result` with `outcome = "ok"`;
- failed CAD write attempts report `outcome = "failed"` with the attempted field
  dictionary plus an `_error` marker, then preserve the original exception path;
- user cancellation reports nothing;
- helper audit failure after a successful CAD write prints an AutoCAD
  command-line warning and does not attempt to roll back CAD state.

## 3. Future Dedup-Check Slice

The future dedup-check slice must first resolve the upstream-service question.
The current repository shows `/api/dedup/check` is not a PLM endpoint, while
dedup-vision uses a separate base URL and service-token seam.

Future slice requirements carried forward:

- decide between dedup-vision config/auth, PLM proxy, or another explicit
  upstream contract;
- pin multipart filename preservation;
- pin `cad_system` audit behavior;
- pin `_lastResult` and `UsageStatistics` ordering for
  `DedupApiClient.CheckDuplicateAsync(...)`.

## 4. Verification

Local commands run:

```bash
python3 clients/autocad-material-sync/verify_material_sync_static.py
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
git diff --check
xmllint --noout \
  clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.xml \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.2018.xml \
  clients/autocad-material-sync/CADDedupPlugin/PackageContents.2024.xml \
  clients/autocad-material-sync/CADDedupPlugin.Client.Tests/CADDedupPlugin.Client.Tests.csproj
```

Local .NET build/test was not run on this workstation:

```text
zsh:1: command not found: dotnet
```

The authoritative .NET signal is the GitHub Windows `cad-helper-shared-dotnet`
workflow.

## 5. Deferred Operational Signoff

Windows AutoCAD build/load/smoke was not run locally. This remains deferred operational signoff, not a substitute for evidence.

Deferred evidence:

- Windows + AutoCAD 2018 build of `CADDedupPlugin.csproj`;
- AutoCAD loads `CADDedup.bundle`;
- `PLMMATPUSH` routes through helper `/sync/inbound`;
- `PLMMATPULL` routes through helper `/diff/preview`, writes CAD fields, and
  posts `/audit/apply-result`;
- helper audit DB contains `/diff/preview` and `/audit/apply-result` rows for
  the smoke run.
