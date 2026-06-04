# Windows AutoCAD 2018 Validation Guide

## Purpose

This guide is the Windows-side acceptance package for the CAD Material Sync AutoCAD client. AutoCAD 2018 is the minimum supported baseline because many customer sites run older CAD versions.

This guide does not replace macOS-side service, fixture, or contract tests. It verifies the remaining part that only a real Windows AutoCAD environment can prove: build, load, command registration, field extraction, and DWG write-back.

## Prerequisites

- Windows 10/11 x64.
- AutoCAD 2018 installed.
- `ACADVER` in AutoCAD returns `R22.0`.
- Visual Studio or Build Tools with MSBuild and .NET desktop build tools.
- .NET Framework 4.6 targeting pack.
- Network access from Windows to the Yuantus server.
- A test DWG with:
  - title block attributes such as `图号`、`名称`、`材料`、`规格`;
  - a table where material fields are stored as adjacent cells, for example `规格 | old-value`.

## Preflight

Run from `clients\autocad-material-sync`:

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1
```

Expected result:

```text
Preflight passed. The Windows machine is ready for AutoCAD 2018 plugin build/smoke.
```

If AutoCAD is installed in a custom directory:

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1 -AutoCADInstallDir "D:\Autodesk\AutoCAD 2018"
```

To include build in the preflight:

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1 -RunBuild
```

## Build

Default build targets AutoCAD 2018:

```batch
build_simple.bat
```

Expected output:

```text
CADDedupPlugin\bin\x64\Release\AutoCAD2018\CADDedupPlugin.dll
```

The build copies the versioned bundle manifest to:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle\PackageContents.xml
```

For a high-version regression build:

```batch
set AUTOCAD_VERSION=2024
set AUTOCAD_INSTALL_DIR=C:\Program Files\Autodesk\AutoCAD 2024
build_simple.bat
```

## Load Smoke

1. Close all AutoCAD instances.
2. Start AutoCAD 2018.
3. Run:

```text
ACADVER
```

Expected: `R22.0`.

4. If bundle autoload does not happen, run:

```text
NETLOAD
```

Select:

```text
%APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle\Contents\CADDedupPlugin.dll
```

5. Run:

```text
DEDUPHELP
DEDUPCONFIG
PLMMATPROFILES
```

Expected:

- `DEDUPHELP` prints command help.
- `DEDUPCONFIG` opens the config dialog.
- `PLMMATPROFILES` lists `sheet/tube/bar/forging`.

## Yuantus Setup

Start Yuantus with `yuantus-cad-material-sync` enabled, then configure the AutoCAD plugin:

- Server URL: your Yuantus base URL, for example `http://127.0.0.1:7910`.
- API key: leave empty if local auth is disabled, otherwise use a valid Bearer token.
- Tenant ID and Org ID: match the Yuantus test tenant/org.
- Material profile: `sheet`.
- Keep dry-run enabled for the first `PLMMATPUSH`.

Before testing write-back, verify the service contract on the Yuantus side:

```bash
PYTHONPATH=src python3 scripts/verify_cad_material_diff_confirm_contract.py
```

## DWG Smoke Checklist

Use a copy of a test DWG, not a production drawing.

### 1. Profile Pull

Command:

```text
PLMMATPROFILES
```

Pass criteria:

- The command reaches Yuantus.
- Profiles include `sheet`, `tube`, `bar`, `forging`.
- No authentication, tenant, or timeout error appears.

### 2. Compose And CAD Write-Back

Command:

```text
PLMMATCOMPOSE
```

Use profile `sheet` and enter sample values:

```text
material = Q235B
length = 1200
width = 600
thickness = 12
```

Pass criteria:

- Yuantus returns a specification such as `1200*600*12`.
- AutoCAD reports at least one updated CAD field.
- The title block or material table `规格` cell changes to `1200*600*12`.
- Independent fields such as `材料` are not unintentionally overwritten.

### 3. CAD To PLM Dry-Run

Command:

```text
PLMMATPUSH
```

Choose dry-run.

Pass criteria:

- CAD fields are extracted from title block/table.
- Yuantus returns `dry_run=true`.
- Result is `created`, `updated`, `conflict`, or candidate matches.
- No PLM item is actually changed during dry-run.

### 4. CAD To PLM Real Write

Run `PLMMATPUSH` again and choose real write only on a test item.

Pass criteria:

- Existing PLM values are not overwritten unless overwrite is explicitly confirmed/configured.
- Created/updated item can be found in PLM.
- Any conflict is reported to the user instead of silently overwritten.

### 5. PLM To CAD Pull

Command:

```text
PLMMATPULL
```

Enter a known test item ID.

Pass criteria:

- A local diff preview window opens before write-back.
- The window lists CAD field, current value, target value, and status.
- Clicking cancel does not change the DWG.
- Clicking confirm writes only the confirmed `write_cad_fields`.
- CAD title block/table fields are updated after confirmation.
- Saved DWG reopens with the updated fields still present.

### 6. Material Assistant

Command:

```text
PLMMATASSIST
```

Enter a profile and run against a test DWG. The command reads current CAD
fields, calls `/material/assistant/resolve` (via Helper Bridge), and prints
exact matches, similar candidates, and `draft_suggested` — display only.

Pass criteria:

- The command-line summary lists status, profile, exact-match count,
  similar-candidate count, and `draft_suggested`.
- Exact matches and similar candidates are shown read-only; there is no
  bind/apply action and no DWG field is written by `resolve`.
- The create prompt defaults to `No`; pressing Enter or choosing `No` or
  cancelling creates nothing and writes nothing to the DWG.
- Choosing `Yes` (explicit) calls `/material/assistant/create` and prints
  `item_id`, `item_number`, `state`, `current_state`, `draft_check`.
- The created item is in the lifecycle start state per the Phase 2 contract;
  on a test tenant only.
- No DWG field is written back after create (assistant/create returns no CAD
  field package in this phase).

Reject the assistant evidence if:

- `PLMMATASSIST` is missing from `DEDUPHELP` or the package commands;
- create happens without the explicit `Yes` confirmation;
- `resolve` changes any PLM business row or DWG field;
- the command writes DWG fields back after create;
- evidence uses production drawings or production customer data;
- any token/password/local helper token appears in screenshots or logs.

## Evidence To Capture

Keep the following evidence for the validation record:

- AutoCAD `ACADVER` output showing `R22.0`.
- Preflight output.
- Build output and DLL path.
- Screenshot or command log for `DEDUPHELP`, `PLMMATPROFILES`, `PLMMATCOMPOSE`, `PLMMATPUSH`, `PLMMATPULL`, `PLMMATASSIST`.
- Screenshot of the `PLMMATPULL` diff preview window before confirmation.
- `PLMMATASSIST` resolve summary log + helper/PLM log excerpt proving `/material/assistant/resolve` was called.
- `PLMMATASSIST` cancel-path evidence: no PLM item created and no DWG field changed.
- `PLMMATASSIST` create-path evidence on a test tenant: explicit confirmation, returned `item_id/item_number/state/current_state/draft_check`, and no DWG write-back.
- Before/after screenshot of the DWG material field.
- Yuantus API/log excerpt for dry-run and real write.

## Failure Triage

| Symptom | Likely Cause | Check |
| --- | --- | --- |
| Build cannot find `accoremgd.dll` | AutoCAD path mismatch | Run preflight with `-AutoCADInstallDir` |
| Build asks for .NET targeting pack | Missing .NET Framework 4.6 developer pack | Install Visual Studio Build Tools .NET desktop workload |
| Unknown command in AutoCAD | Bundle did not load or DLL failed load | Use `NETLOAD`, check AutoCAD command line error |
| `PLMMATPROFILES` timeout | Yuantus URL/network wrong | Test URL in browser or `curl` from Windows |
| `PLMMATPUSH` conflict | Existing PLM value differs | Keep dry-run, inspect conflict details, avoid overwrite until confirmed |
| Table cell not updated | Drawing template does not match adjacent-cell rule | Verify field label and target cell are adjacent |

## Exit Criteria

AutoCAD 2018 support can be marked complete only when:

- Preflight passes on the Windows machine.
- DLL builds against AutoCAD 2018 assemblies.
- AutoCAD 2018 loads the plugin.
- Material sync commands run without command registration errors.
- A real DWG title block or table field is updated and persists after save/reopen.
