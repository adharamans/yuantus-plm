# CAD Helper Bridge R3.2 — Install Runbook

Date: 2026-05-24

Operator-side installation procedure for the four R3.2 ship artifacts
plus the existing AutoCAD plugin slice on a clean Windows workstation
that needs to consume the Yuantus PLM CAD helper bridge.

R3.2 ships a **documented install procedure only**; a real installer
(MSI / signed bundle) is a separate future slice and is explicitly
out of S11 scope.

## 0. Required state on the workstation

- Windows 10 or Windows 11 (x64). Acceptance evidence host baseline
  is Windows 11 (acceptance test #1 / #12).
- One supported CAD host installed:
  - AutoCAD 2018 (baseline) or AutoCAD 2024 — for the
    `CADDedupPlugin` integration path (acceptance tests #1, #2, #6,
    #7, #8, #10, #12);
  - ZWCAD 2025 — for the Lisp shell + bridge path (acceptance test
    #9);
  - GstarCAD 2025 — for the Lisp shell + bridge path (acceptance
    test #9).
- .NET 6 Desktop Runtime (x64) installed system-wide, OR run
  `yuantus-cad-helper.exe` / `yuantus-cad-detector.exe` as
  **self-contained** builds (the R3.2 ship pattern) which embed the
  runtime — no separate install needed.
- .NET Framework v4.6 or later already present (always present on
  Windows 10/11 baseline).
- Operator has write access to `%APPDATA%` for the workstation user
  who will run the CAD host.

## 1. Install order (must be in this sequence)

The order matters because:

- the helper owns the DPAPI local-token bootstrap; it must exist on
  disk before any CAD-side caller spawns it;
- the bridge DLL and the Lisp shell are loaded by the CAD host
  itself, so they can be placed before or after the helper but the
  CAD host must NOT be running yet when they are first loaded;
- the AutoCAD plugin (`CADDedupPlugin`) only succeeds at runtime if
  the helper exe is reachable on the standard install path.

### Step 1. Install `yuantus-cad-helper.exe` (helper service)

Target path:

```text
%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe
```

Place the **self-contained** net6.0 single-file build at that path.
Do NOT add to PATH; the helper is spawned by the CAD-side caller via
its known fixed location (S3 startup contract).

The first time the helper is spawned, S3 `LocalTokenBootstrapper`:

- writes the DPAPI-protected local-helper-token under
  `%APPDATA%\YuantusPLM\helper\` (per S3 design);
- publishes `helper-session-{sessionId}.json` (S3 §5.1 step 5/6
  cleanup applies for stale files);
- starts Kestrel on a loopback-only port in the **7959-7999** range
  (S3 contract; `/healthz` LAN-bound traffic is rejected per
  acceptance test #4).

No manual edit to the DPAPI envelope is required or supported.

### Step 2. Install `yuantus-cad-detector.exe` (detector)

Target path:

```text
%APPDATA%\YuantusPLM\helper\yuantus-cad-detector.exe
```

Same install directory as the helper, but the detector has no DPAPI
dependency and is read-only — it scans `HKEY_LOCAL_MACHINE` registry
keys and known CAD installation roots and writes nothing (acceptance
test #3 with Procmon).

### Step 3. Install `YuantusCadHelperBridge.dll` + `yuantus_cad_helper.lsp`

Target paths follow the per-host NETLOAD convention:

- For ZWCAD / GstarCAD: place `YuantusCadHelperBridge.dll`
  (.NET Framework v4.6) in a user-resolvable directory and add that
  directory to the CAD host's NETLOAD search path (per ZWCAD /
  GstarCAD documentation). Suggested location:

  ```text
  %APPDATA%\YuantusPLM\cad-bridge\YuantusCadHelperBridge.dll
  ```

- Place `yuantus_cad_helper.lsp` at a path the CAD host can `(load
  ...)` from. Suggested location:

  ```text
  %APPDATA%\YuantusPLM\cad-bridge\yuantus_cad_helper.lsp
  ```

- In ZWCAD / GstarCAD, configure on-startup `NETLOAD` of the bridge
  DLL and `(load "yuantus_cad_helper.lsp")` of the Lisp shell. After
  reload, `YUANTUS_DIFF_PREVIEW` is available at the command line
  (acceptance test #9).

The Lisp shell exposes exactly one command: `C:YUANTUS_DIFF_PREVIEW`
(verified at S10 merge; no other commands are defined or added by
S11).

### Step 4. Install `CADDedupPlugin.bundle` (existing AutoCAD plugin)

Use the existing AutoCAD `.bundle` packaging that S8 shipped under
`clients/autocad-material-sync/CADDedupPlugin/`. The bundle is
multi-config (.NET Framework v4.6 net46 / v4.8 net48) and is consumed
by AutoCAD 2018 (baseline) and AutoCAD 2024 the same way it was
before R3.2.

The S8-shipped change is internal: `MaterialSyncApiClient` (Diff /
Sync Inbound / Sync Outbound) now routes through the helper instead
of calling PLM HTTP directly. The plugin's public commands
(`PLMMATPULL`, `PLMMATPUSH`, etc.) are unchanged. Re-registration of
the bundle follows AutoCAD's standard procedure; no manual
configuration is required.

### Step 5. First-launch handshake

1. Start the CAD host (AutoCAD 2018 baseline, AutoCAD 2024, ZWCAD
   2025, or GstarCAD 2025).
2. From the CAD host: execute a command that talks to the helper —
   `PLMMATPULL` in AutoCAD, or `YUANTUS_DIFF_PREVIEW` in
   ZWCAD/GstarCAD.
3. On first-call:
   - the CAD plugin / Lisp shell auto-spawns the helper exe from the
     install path;
   - S3 `LocalTokenBootstrapper` writes the DPAPI envelope and
     publishes the session file;
   - Kestrel starts on a loopback 7959-7999 port; `/healthz` returns
     200 (acceptance test #4 also confirms LAN address is rejected);
   - the CAD-side caller reads the loopback port from the session
     file, attaches the `X-Yuantus-Local-Token` header (DPAPI-protected
     local-helper-token bootstrapped by S3) and the
     `X-Yuantus-Protocol: 1.0` header to every request per S4 (see
     `clients/cad-desktop-helper/Shared/Transport/HelperTransport.cs`
     for the canonical header set), then calls `/session/login` to
     establish the helper-side session — there is no Bearer token and
     no HTTP cookie; the two custom headers are sent on every
     subsequent S5 / S6 route call.
4. Subsequent helper calls reuse the running helper process
   (single-instance via S3 mutex; acceptance test #8 covers the
   30-minute coexistence with `CADDedupPlugin`).
5. Idle timer: 30 minutes of no calls → helper exits cleanly; session
   file is removed (acceptance test #7).

## 2. Operator recovery: `--reset-local-token`

If a workstation's DPAPI envelope drifts (helper rejects with a
local-token error after CAD update or user-profile reset), use the S7
recovery path:

```text
%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe --reset-local-token
```

Requirements (per S7 §3.B + acceptance tests #10 / #11):

- **Must be run from an interactive console session** (PowerShell or
  cmd.exe in an interactive desktop session). The S7 parent-ancestry
  walk via `NtQueryInformationProcess` enforces this: SSH / WinRM /
  remote-shell invocations exit with code 1 (acceptance test #11).
- The command prompts `y/n`. Input `y` to confirm → DPAPI envelope is
  replaced.
- An existing CAD session's next call (e.g., `PLMMATPULL`) picks up
  the new token automatically.

Acceptance test #11 confirms that running `--reset-local-token` from
a non-interactive remote shell is rejected.

## 3. Uninstall

Manual: stop the CAD host, delete `%APPDATA%\YuantusPLM\` and the
NETLOAD directory you chose for the bridge DLL + Lisp shell. The
AutoCAD bundle uninstall follows the existing AutoCAD `.bundle`
removal procedure (S8 did not change this).

## 4. Out of scope for this runbook (separate future slices)

- A signed installer (MSI / `.exe` bundle).
- Auto-update for the helper / detector / bridge artifacts.
- Network deploy via Group Policy / SCCM.
- A workstation-wide PATH entry for the helper (intentionally
  omitted; CAD-side spawns use the known fixed install path).
