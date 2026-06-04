# CAD Material Sync Windows Validation Evidence Template

Status: **template only; not validation evidence**

Use this template after running the CAD Material Sync client on a real Windows
machine with AutoCAD installed. Do not mark the Windows validation complete
until this template is filled with real operator output and reviewed.

## 1. Environment

```text
Operator:
Review date:
Windows version:
AutoCAD primary version:
AutoCAD ACADVER output:
AutoCAD install path:
Yuantus base URL:
Yuantus commit:
Test DWG description:
```

Required primary baseline:

- AutoCAD primary version: `2018`
- AutoCAD ACADVER output: `R22.0`

Optional regression baseline:

- AutoCAD regression version: `2024`
- AutoCAD regression ACADVER output:

## 2. Preflight Evidence

```text
Preflight command:
Preflight result:
Preflight output path:
```

Attach or link the output from:

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_autocad2018_preflight.ps1 -RunBuild
```

## 3. Build Evidence

```text
Build command:
Build result:
Compiled DLL path:
PackageContents path:
```

Acceptance requirements:

- DLL builds against AutoCAD 2018 assemblies.
- Package metadata targets AutoCAD 2018 / R22.0.
- No generated binary is committed to the repository.

## 4. AutoCAD Load Evidence

```text
Load method: NETLOAD | bundle autoload
Loaded DLL path:
AutoCAD command-line output:
Load result:
```

Acceptance requirements:

- AutoCAD 2018 loads the plugin without managed assembly errors.
- Commands are registered after load.

## 5. Command Smoke Evidence

Record each command result:

```text
DEDUPHELP:
DEDUPCONFIG:
PLMMATPROFILES:
PLMMATCOMPOSE:
PLMMATPUSH:
PLMMATPULL:
PLMMATASSIST:
```

Acceptance requirements:

- Commands run without "unknown command" errors.
- Server-backed commands reach the configured Yuantus endpoint.

## 6. DWG Write-Back Evidence

Use a copy of a test DWG, not a production drawing.

```text
DWG file description:
Before material field value:
Diff preview screenshot path:
User action: confirm | cancel
After material field value:
Save/reopen result:
Yuantus dry-run log path:
Yuantus real-write log path:
```

Acceptance requirements:

- Diff preview is shown before write-back.
- Cancel path does not modify the DWG.
- Confirm path writes only confirmed fields.
- Saved DWG reopens with the updated material field still present.

## 7. Material Assistant Evidence

Use a test tenant and a copy of a test DWG. Do not use production drawings or
customer data.

```text
PLMMATASSIST resolve summary log path:
PLMMATASSIST resolve endpoint observed:
PLMMATASSIST resolve PLM log path:
PLMMATASSIST cancel result:
PLMMATASSIST cancel PLM item count check:
PLMMATASSIST cancel DWG unchanged check:
PLMMATASSIST Enter default No result:
PLMMATASSIST create confirmation result:
PLMMATASSIST create endpoint observed:
PLMMATASSIST created item id:
PLMMATASSIST created item number:
PLMMATASSIST created state:
PLMMATASSIST current state:
PLMMATASSIST draft check:
PLMMATASSIST create DWG write-back result:
```

Acceptance requirements:

- `PLMMATASSIST` runs without an "unknown command" error.
- The resolve path reaches `/material/assistant/resolve` and only displays exact
  matches, similar candidates, and draft suggestion.
- Cancel/No creates no PLM item and writes no DWG fields.
- Pressing Enter at the create prompt follows the default `No` path.
- Explicit `Yes` reaches `/material/assistant/create` and returns
  `item_id`, `item_number`, `state`, `current_state`, and `draft_check`.
- The created item satisfies the Phase 2 lifecycle start-state/Draft check.
- Create does not write DWG fields in this phase.

## 8. AutoCAD 2024 Regression Evidence

This section is optional for initial AutoCAD 2018 acceptance but required before
marking the higher-version regression item complete.

```text
AutoCAD 2024 installed: yes | no
AutoCAD 2024 ACADVER output:
AutoCAD 2024 build result:
AutoCAD 2024 load result:
AutoCAD 2024 command smoke result:
AutoCAD 2024 DWG write-back result:
```

## 9. Reviewer Decision

```text
AutoCAD 2018 support complete: no
Real DWG write-back validated: no
Windows client runtime accepted: no
AutoCAD 2024 regression complete: no
Reviewer:
Decision date:
Decision: pending
Reason:
```

Before real evidence is attached and reviewed, every acceptance field above
must remain `no` and the decision must remain `pending`. A future evidence
review may change these values only after the real Windows artifacts are
attached.

## 10. Rejection Rules

Reject the evidence if any item below is true:

- AutoCAD ACADVER is missing or not `R22.0` for the primary validation.
- The DLL did not build against AutoCAD 2018 assemblies.
- The plugin was not loaded in AutoCAD 2018.
- Any command smoke result is missing.
- The DWG write-back result uses a mock fixture instead of a real DWG.
- The saved DWG was not reopened to confirm persistence.
- `PLMMATASSIST` cancel or Enter-default-No creates a PLM item or writes the DWG.
- `PLMMATASSIST` create evidence is missing `item_id`, lifecycle/Draft check, or
  the no-DWG-write-back result.
- Any plaintext token, password, or production customer drawing content appears
  in the evidence.
- The template is submitted with placeholder values.

## 11. Local Pre-Review Check

After filling this template with real Windows output, run the local shape
validator before reviewer sign-off:

```bash
python3 scripts/validate_cad_material_windows_evidence.py \
  docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md
```

For automation, add `--json` to emit a redaction-safe machine-readable result
that lists field-level failures without echoing evidence values.

The validator does not run AutoCAD and does not create validation evidence. It
only checks that this markdown contains the required fields, keeps secrets out
of the record, and does not accept placeholder, mock, or synthetic evidence.

## 12. Current Repository State

As of `main=27afe717`:

- CAD Material Sync delivery package is merged.
- `PLMMATASSIST` command implementation is merged, but AutoCAD runtime evidence
  is not recorded.
- macOS/Linux verification is green.
- Windows + AutoCAD 2018 evidence is not recorded.
- AutoCAD 2024 regression evidence is not recorded.
