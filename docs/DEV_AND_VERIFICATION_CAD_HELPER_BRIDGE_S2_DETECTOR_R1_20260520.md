# CAD Helper Bridge S2 Detector R1 - Development And Verification

Date: 2026-05-20

## 1. Scope

This slice implements the S2 CAD environment detector from
`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`.

Included:

- `clients/cad-desktop-helper/Detector/yuantus-cad-detector` console exe.
- `net6.0-windows` target with `RuntimeIdentifiers=win-x64;win-x86`; publish can
  be self-contained per release packaging.
- Read-only HKLM registry scanning through S1 `Yuantus.Cad.Shared.Registry`.
- File-system existence checks for CAD executables and Autodesk bundle metadata.
- JSON output schema and table output mode.
- `--output <path>` explicit report writing with silent stdout.
- Windows GitHub Actions coverage for Shared and Detector.
- Direct `Microsoft.Win32.Registry` and `System.Security.Principal.Windows`
  `5.0.0` package references are used because these are the latest stable
  package lines available on nuget.org for the Windows-only APIs used by the
  detector.

Not included:

- No helper/Kestrel endpoint.
- No Mutex, port allocation, session lifecycle, or local-token bootstrap.
- No install, repair, registry write, file association, or plugin-copy action.
- No CADDedupPlugin migration.

## 2. Implementation

New projects:

- `clients/cad-desktop-helper/Detector/Yuantus.Cad.Detector.csproj`
- `clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj`

Detector CLI:

```bash
yuantus-cad-detector.exe [--output <path>] [--format json|table] [--verbose]
```

Exit codes:

- `0`: at least one supported CAD product was detected.
- `2`: scan completed but no supported product was detected.
- `64`: non-Windows platform.
- `1`: invalid arguments or internal failure.

The detector rejects write-oriented switches such as `--install`, `--repair`,
`--fix`, `--write`, `--register`, and `--uninstall`.

## 3. Detection Matrix

Implemented registry roots:

- Autodesk AutoCAD / AutoCAD Plant 3D:
  `HKLM\SOFTWARE\Autodesk\AutoCAD\<R*.*>\<profile>`
- ZWCAD: `HKLM\SOFTWARE\ZWSOFT\ZWCAD\<year>`
- GstarCAD: `HKLM\SOFTWARE\Gstarsoft\GstarCAD\<year>`
- SolidWorks: `HKLM\SOFTWARE\SolidWorks\SOLIDWORKS\<year>`

Both `RegistryView.Registry64` and `RegistryView.Registry32` are scanned.
Duplicates are collapsed by normalized install root, preferring a non-orphan
entry over a registry-orphan entry.

## 4. Output Contract

The report schema lives at:

```text
clients/cad-desktop-helper/Detector/Schemas/cad-detector-report.schema.json
```

Top-level fields:

- `schema_version`
- `scanned_at`
- `host`
- `products`
- `recommendations`
- `warnings`

Each product includes:

- `id`, `vendor`, `product`, `release_key`, `marketing_version`, `language`
- `install_root`, `exe_path`, `support_dirs`, `plugin_bundle_dirs`
- `yuantus_bundle`
- `compatibility`
- `errors`

Compatibility values:

- `supported`
- `supported-no-bundle`
- `bundle-mismatch`
- `experimental`
- `registry-orphan`
- `unknown`

## 5. Verification Commands

Expected Windows-capable verification:

```bash
dotnet restore clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj
```

```bash
dotnet build clients/cad-desktop-helper/Detector/Yuantus.Cad.Detector.csproj --configuration Release --no-restore
```

```bash
dotnet test clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj --configuration Release --no-restore
```

Static and repository verification used on this workstation:

```bash
xmllint --noout \
  clients/cad-desktop-helper/Detector/Yuantus.Cad.Detector.csproj \
  clients/cad-desktop-helper/Detector.Tests/Yuantus.Cad.Detector.Tests.csproj \
  clients/cad-desktop-helper/Shared/Yuantus.Cad.Shared.csproj \
  clients/cad-desktop-helper/Shared.Tests/Yuantus.Cad.Shared.Tests.csproj
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_workflow_checkout_fetch_depth_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_inline_shell_syntax_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_runner_policy_contracts.py
```

```bash
git diff --check
```

```bash
rg -n "SetValue|CreateSubKey|DeleteSubKey|DeleteValue|RegistryKey\.Set|RegistryKey\.Create|RegistryKey\.Delete" \
  clients/cad-desktop-helper/Detector clients/cad-desktop-helper/Detector.Tests
```

## 6. Verification Status

Local macOS workstation:

- XML project validation: passed.
- Doc/workflow contract pytest: `7 passed in 0.41s`.
- Registry write source scan: no matches for registry write APIs.
- S2 non-goal source guard: no matches for Kestrel, endpoint mapping, Mutex,
  reset-token CLI, AutoCAD command methods, or CADDedupPlugin client migration.
- `git diff --check`: clean.
- `.NET build/test`: not available locally because this workstation does not
  have the .NET SDK installed; Windows GitHub Actions is the authoritative
  build/test gate.

GitHub Actions:

- `.github/workflows/cad-helper-shared-dotnet.yml` now restores, builds, and
  tests both `Shared.Tests` and `Detector.Tests` on a Windows runner.
- PR #618 Windows `.NET` gate:
  `cad-helper-shared-dotnet` run `26209193581` passed in 2m25s.
- PR #618 repository contract gate:
  `CI` run `26209193521` passed `contracts` in 4m16s.
- The Windows `.NET` run emits a non-blocking `net6.0-windows` out-of-support
  annotation. S2 intentionally keeps the R3.4 `net6.0-windows` target; any
  upgrade to `net8.0-windows` should be a separate design decision because S1
  and S2 currently share the R3 target-framework contract.

## 7. Remaining Manual Gate

True Windows CAD-machine validation remains external:

- Windows 11 + AutoCAD 2018 should produce a product with
  `release_key = "R22.0"`.
- Installed bundle state should match `yuantus_bundle.present`.
- `--output` should write the report and keep stdout silent.
- Procmon evidence should prove zero registry writes by the detector.

These are S2 manual acceptance items, not CI-closeable in this macOS
development environment.

## 8. Next Slices

S3 helper startup remains unstarted and requires a separate opt-in. S2 does not
start Kestrel, does not allocate ports, and does not consume
`InstallId.GetOrCreate()` for Mutex/session startup.
