# Claude Taskbook: CAD Helper Bridge Installer — Per-User Packaging, Signing, Install / Uninstall / Repair

Date: 2026-05-24

Type: **Doc-only taskbook.** Specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this
taskbook does NOT authorize that implementation.

> **Unlike the S11 closeout taskbook, the implementation PR for this
> slice is NOT doc-only.** It introduces a real packaging artifact
> (`clients/cad-desktop-helper/Installer/`), a static verifier, and a
> CI path filter. The runtime/route/ErrorCode/Lisp/Bridge surfaces
> stay frozen, but new build-and-pack files land under `clients/`.

## 1. Purpose And Program Framing

The CAD Desktop Helper Bridge **R3.2 program (slices S1–S11) is
closed** — its slice ledger is complete and merged on `main` (S11 impl
#637 `a8648875`). R3.2 shipped a **documented manual install
procedure** only
(`docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md`), and the R3.2
closeout report listed *"a real installer (MSI / signed bundle /
auto-update)"* as a known follow-up that is **NOT part of R3.2 and
requires its own opt-in**.

This taskbook is that follow-up. It is a **standalone follow-up
deliverable**, not a new R-numbered design cycle: there is **no new
design doc**. The install architecture is already fully constrained by
the R3.2 design + the S11 install runbook, so this taskbook
self-contains the installer design. (If a future slice adds
auto-update — see §8 — that may warrant its own design note; this
slice does not.)

The installer **automates** the S11 manual install runbook for a
clean per-user Windows profile:

- lays the four R3.2 ship artifacts + the existing AutoCAD plugin to
  the exact `%APPDATA%` paths the architecture already spawns from;
- Authenticode-signs the shipped binaries (owner-local release build);
- optionally auto-configures CAD-host startup so `YUANTUS_DIFF_PREVIEW`
  / NETLOAD work without manual startup-file editing;
- supports clean uninstall and repair while preserving user data
  (DPAPI token, `audit.db`, `install-id.json`).

It does **not**:

- require administrator privileges or write to `HKEY_LOCAL_MACHINE`
  (per-user, no-admin — matching the existing `%APPDATA%` deployment
  model);
- pre-seed the DPAPI local-helper-token (first-launch bootstrap owns
  that — see §3.E);
- register the helper as a Windows Service (the helper is
  spawn-on-demand with a 30-minute idle exit — see §3.E);
- add helper Kestrel routes, Lisp commands, `ErrorCodes`, or any
  `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`
  runtime source change;
- implement auto-update (explicitly deferred to a separate slice — §8);
- introduce a Tauri / Electron Companion shell (out of R3 scope per
  design `:5`).

Prerequisites already merged (R3.2 complete):

- #614 `fff93a2`: CAD helper bridge R3.2 design.
- S1–S10 implementations (see the R3.2 closeout report ledger).
- #635 `b2c18d07` + #637 `a8648875`: S11 taskbook + implementation
  (install runbook + acceptance-evidence runbook + closeout report).

## 2. Grounded Current Reality

Grounded against `origin/main = a8648875` after S11 merged.

### 2.1 Existing deployment model — per-user, no admin

Every R3.2 artifact deploys per-user to `%APPDATA%` with no
administrator privilege:

| Artifact | Install path (per S11 install runbook) | Build |
|---|---|---|
| `yuantus-cad-helper.exe` | `%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe` | net6.0 self-contained |
| `yuantus-cad-detector.exe` | `%APPDATA%\YuantusPLM\helper\yuantus-cad-detector.exe` | net6.0 self-contained |
| `YuantusCadHelperBridge.dll` | `%APPDATA%\YuantusPLM\cad-bridge\YuantusCadHelperBridge.dll` | .NET Framework v4.6 |
| `yuantus_cad_helper.lsp` | `%APPDATA%\YuantusPLM\cad-bridge\yuantus_cad_helper.lsp` | AutoLISP (plain text) |
| `CADDedupPlugin.bundle` | `%APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle` | .NET Framework v4.6 net46 / v4.8 net48 multi-config |

The R3.2 design `:19` confirms the AutoCAD bundle is **免管理员安装**
(no-admin install) to `%APPDATA%\Autodesk\ApplicationPlugins\`. The
helper / detector / bridge follow the same per-user pattern under
`%APPDATA%\YuantusPLM\`.

### 2.2 Runtime semantics the installer must respect

- **DPAPI token bootstrap (S3):** the local-helper-token is written by
  `LocalTokenBootstrapper` on the helper's **first launch** to
  `%APPDATA%\YuantusPLM\local-helper-token.dat` — at the **root** of
  `%APPDATA%\YuantusPLM\`, NOT under the `helper\` subdir (see
  `clients/cad-desktop-helper/Shared/Identity/Paths.cs:23`,
  `LocalTokenFile = RootDirectory \ "local-helper-token.dat"`). The
  design `:1044` acceptance test 7 pins *"全新机器首次安装 helper，第一次
  `PLMMATPULL` 不卡住，DPAPI 中 token 正确生成"*. The installer must NOT
  pre-create or pre-seed this envelope.
- **Runtime-created files all sit at the `%APPDATA%\YuantusPLM\` root**
  (per `Paths.cs`): `local-helper-token.dat` (`:23`), `audit.db`
  (`HelperRuntime.cs:97`), `install-id.json` (`:18`), and
  `helper-session-{sessionId}.json` (`:38`). Only the **binaries** live
  in subdirs (`helper\` for the two `.exe`s, `cad-bridge\` for the
  bridge DLL + `.lsp`). The §3.F preserve set names the root-level files
  explicitly so the installer protects the correct paths.
- **Spawn-on-demand + idle exit (S3):** the helper is started by a
  CAD-side caller (plugin / Lisp bridge), discovered via
  `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json`, single-instance
  via an S3 mutex, and exits after 30 minutes idle (design acceptance
  test 7). It is NOT a Windows Service and must not be registered as
  one.
- **Session-file schema (S3) carries `pid` + `image_path`:** the
  `HelperSessionDocument` serialized to
  `helper-session-{sessionId}.json` includes `port`, **`pid`**
  (`Process.GetCurrentProcess().Id`), and `image_path` — see
  `clients/cad-desktop-helper/Helper/HelperRuntime.cs:410-453`
  (`[JsonProperty("pid")]` at `:421`, `[JsonProperty("image_path")]` at
  `:424`). This is what makes the §3.E running-helper detection
  satisfiable **without** any runtime change: the installer reads `pid`
  + `image_path` from the existing schema; it does not need a new
  field.
- **Loopback-only Kestrel (S3/S4):** binds 127.0.0.1 on a 7959-7999
  port; LAN access is rejected (acceptance test 4). The installer must
  not open firewall ports or change binding.
- **Session-file lifecycle is S3-owned:** `helper-session-{sessionId}.json`
  is created/cleaned by the helper itself (stale-clean on next
  startup). The installer must never create, edit, or delete it.

### 2.3 What the manual runbook currently leaves to the operator

`docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` §1 step 3
instructs the operator to **manually** configure CAD-host startup:
*"In ZWCAD / GstarCAD, configure on-startup `NETLOAD` of the bridge
DLL and `(load "yuantus_cad_helper.lsp")` of the Lisp shell."* This
manual step is the primary friction the installer removes (see §3.D).

### 2.4 The four ship artifacts + existing plugin

Identical to the R3.2 closeout report §2:
`yuantus-cad-detector.exe`, `yuantus-cad-helper.exe`,
`YuantusCadHelperBridge.dll`, `yuantus_cad_helper.lsp`, plus the
existing `CADDedupPlugin.bundle`. The installer packages all five.

## 3. Installer Decisions And Boundaries

### 3.A Packaging technology — Inno Setup, per-user, no-admin

The implementation must use **Inno Setup** configured for per-user
installation:

- `PrivilegesRequired=lowest` — installs without elevation, into the
  current user's `%APPDATA%`;
- lays files to the **exact** paths in §2.1 (the architecture spawns
  the helper from a fixed path, so the install location is a hard
  contract, not a user choice);
- writes **no** `HKEY_LOCAL_MACHINE` keys; per-user uninstall
  registration under `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall`
  only (the standard per-user Add/Remove Programs entry);
- does not add anything to the system `PATH`.

**MSIX was considered and rejected.** MSIX installs into a virtualized
`WindowsApps` location and runs the app inside an AppContainer. That
breaks three load-bearing R3.2 assumptions: (1) the CAD-side callers
spawn the helper from a **fixed `%APPDATA%` path**, which MSIX
virtualization relocates; (2) DPAPI user-scope semantics + the
`helper-session-{sessionId}.json` discovery file live in real
`%APPDATA%`, not the container's redirected store; (3) loopback Kestrel
+ on-demand process spawn from a foreign process (acad.exe / zwcad.exe)
fights AppContainer isolation. Inno per-user preserves the exact
filesystem layout the merged S1–S11 runtime already depends on.

A plain machine-wide MSI (WiX → Program Files, admin-required) is also
rejected: it contradicts the no-admin `%APPDATA%` model and would
introduce a dual-track install location.

### 3.B Signing — owner-local release; CI builds unsigned

- The release installer must Authenticode-sign **every first-party
  signable `.exe` / `.dll` the installer lays down**, **and** the
  installer `.exe` itself. This is broader than the four top-level ship
  artifacts: the CADDedup bundle's `Contents\` ships first-party
  `CADDedupPlugin.dll` + `Yuantus.Cad.Shared.dll` (net46/net48 copies —
  see `clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj:148-150`),
  and a net6.0 self-contained helper/detector lays first-party managed
  DLLs (e.g. the `Yuantus.Cad.Shared.dll` net6.0 copy) next to the
  apphost `.exe`. All of these must be signed. The contract is
  expressed as *"sign every first-party signable binary the installer
  delivers"*, not an enumerated whitelist that drifts as the build
  output changes.
- **Third-party DLLs** the installer carries (e.g. `Newtonsoft.Json.dll`
  in the bundle `Contents\`, the .NET runtime DLLs inside a
  self-contained publish) are handled by **explicit owner policy** —
  typically left with their upstream signatures, not re-signed. The
  impl must state which third-party binaries it ships and whether they
  are re-signed; this is an owner decision, not a default.
- `yuantus_cad_helper.lsp` is plain-text AutoLISP and is **not**
  Authenticode-signable; the taskbook notes this explicitly so the
  impl does not attempt it. (Its integrity is covered by the signed
  installer that delivers it.)
- **The code-signing certificate is owner-provided and lives outside
  the repository.** Procuring / storing the cert is an owner-policy
  decision, NOT part of this slice.
- **CI builds the Inno output UNSIGNED**, for smoke-shape and
  static-verifier coverage only. **Signed release artifacts are
  produced by an owner-local build** with the cert available on the
  build machine. The impl PR must NOT place a real cert in GitHub
  Actions secrets and must NOT make CI depend on one. The signing step
  in the Inno script must degrade gracefully (skip signing) when no
  cert/`SignTool` is configured, so the unsigned CI build succeeds.
- Feeds forward to the design's deferred *"进程映像签名校验"* (process
  image signature validation) enhancement at design `:389` / `:479` —
  signed binaries are a prerequisite, but implementing the helper-side
  signature check is OUT of this slice.

### 3.C CADDedup re-packaging — consume pre-built artifacts

The installer **packages** the existing `CADDedupPlugin.bundle`; it
does **not** rebuild or modify the plugin source:

- the Inno script consumes pre-built outputs from each project's
  `bin/Release/` (helper, detector, bridge, and the CADDedup bundle's
  net46/net48 multi-config output). The installer build pipeline does
  NOT invoke MSBuild for the consumed projects — build and pack are
  separate steps;
- the bundle target is the existing `%APPDATA%\Autodesk\ApplicationPlugins\CADDedup.bundle`
  path (design `:19`);
- **already-installed bundle conflict:** if `CADDedup.bundle` is
  already present (installed via the old manual procedure), the
  installer **adopts and overwrites in place** (same per-user path, no
  version downgrade guard required for R1 — the installer always lays
  the version it ships). It does NOT refuse, and does NOT create a
  second copy. This matches the detector's `bundle-mismatch` /
  `supported-no-bundle` status model (design `:257-258`).

### 3.D CAD-host startup auto-configuration — in scope, with opt-out

The installer **auto-configures** CAD-host startup so the operator
does not have to hand-edit startup files (closing the §2.3 friction):

- for each detected supported CAD host (using
  `yuantus-cad-detector.exe`'s JSON report — the installer MAY invoke
  the detector, which is read-only), append the NETLOAD of
  `YuantusCadHelperBridge.dll` and `(load "…/yuantus_cad_helper.lsp")`
  to that host's startup script. The impl must target the
  **per-user-writable** startup file for each host (e.g., a user-search-path
  `acad.lsp` rather than a machine-scoped support-path `acaddoc.lsp`;
  the ZWCAD/GstarCAD per-user startup equivalents) so the no-admin
  contract holds; if a host has no per-user-writable startup location,
  document it as **manual-config-required** (see below) and fall back
  to `--skip-cad-config`;
- the appended block must be **idempotent and uniquely fenced** (a
  marked begin/end region) so repeated installs/repairs do not
  duplicate it and uninstall can remove exactly that region;
- a **`--skip-cad-config`** install flag (or an unchecked-by-default
  Inno task) lets operators who manage CAD startup files centrally opt
  out;
- this is the **only** place the installer writes outside
  `%APPDATA%\YuantusPLM\` + the CADDedup bundle path. The uninstall /
  repair surface in §3.E must account for the fenced startup-file
  region.

If the impl finds that a given CAD host's startup-file location is not
reliably per-user-writable without admin, it must document that host
as **manual-config-required** in the DEV/Verification MD rather than
silently failing — and the `--skip-cad-config` path remains the
fallback.

### 3.E Install / uninstall / repair contract

**Install (idempotent, no-admin):**

- lay the five artifacts to the §2.1 paths;
- register the per-user Add/Remove Programs entry (HKCU only);
- optionally write the §3.D fenced startup block (unless
  `--skip-cad-config`);
- **must NOT** pre-create the DPAPI token, the
  `helper-session-{sessionId}.json`, `install-id.json`, or `audit.db`
  — those are runtime-owned (S3 / S6) and created on first helper
  launch;
- **must NOT** register a Windows Service or auto-start entry for the
  helper — it is spawn-on-demand;
- re-running install over an existing install overwrites the binaries
  and re-lays the startup block idempotently (no duplicate fenced
  region), preserving the §3.F user-data set.

**Running-helper handling (install / upgrade / uninstall / repair):**

The helper `.exe` may be running (and file-locked) when the operator
runs any of these. The installer must:

1. detect a running helper by reading `pid` + `image_path` from
   `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json` (schema per
   §2.2 — both fields already exist, no runtime change needed);
2. confirm the `image_path` matches the helper this installer manages
   before acting on the `pid` (avoid killing an unrelated process that
   reused the PID);
3. prompt the operator that the helper must stop;
4. on confirmation, either wait for the helper's idle exit or
   `taskkill` the PID read from the session file (the installer must
   NOT delete the session file itself — S3 stale-clean owns that on the
   next startup).

It must not blindly `taskkill` by image name without reading the
session file, and must not proceed with a file-locked binary replace.

**Uninstall:**

- remove the binaries the installer laid under
  `%APPDATA%\YuantusPLM\helper\` + `%APPDATA%\YuantusPLM\cad-bridge\`
  and the `CADDedup.bundle`;
- remove the §3.D fenced startup-file region (and only that region);
- remove the HKCU uninstall registration;
- **preserve the §3.F user-data set by default**; offer an explicit
  *"also remove my Yuantus data (token, audit history)"* opt-in for a
  full purge.

**Repair:**

- re-lay the binaries + the fenced startup block;
- **preserve the entire §3.F user-data set** unconditionally (repair
  never touches user data).

### 3.F User-data preserve set (finite enumeration; default-deny)

On uninstall (default) and repair (always), the installer must
**preserve exactly** the following under `%APPDATA%\YuantusPLM\` and
must treat everything else it did not itself lay down as
preserve-by-default (default-deny on deletion of un-owned files):

1. `%APPDATA%\YuantusPLM\local-helper-token.dat` — the DPAPI
   local-helper-token envelope (S3), at the **root**, not under
   `helper\` (`Paths.cs:23`);
2. `%APPDATA%\YuantusPLM\audit.db` — S6 SQLite audit store, at the
   **root** (`HelperRuntime.cs:97`);
3. `%APPDATA%\YuantusPLM\install-id.json` — per-user-per-machine atomic
   id, at the **root** (design `:133`, `Paths.cs:18`);
4. `%APPDATA%\YuantusPLM\helper-session-{sessionId}.json` — at the
   **root** (`Paths.cs:38`); never touched by the installer at all
   (S3-owned lifecycle), neither created nor removed;
5. any `%APPDATA%\YuantusPLM\` file or subdirectory the installer did
   not itself create (forward-compatibility: a future slice may add
   user-side state such as a persisted `server_allowlist`; the
   installer must not delete what it does not own).

The installer's removal logic must be an allow-list of installer-laid
paths, not a blanket `rmdir /s %APPDATA%\YuantusPLM\`.

### 3.G No runtime / route / ErrorCode / Lisp / Bridge changes

The implementation must **not** edit:

- any `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`
  runtime source;
- `clients/autocad-material-sync/CADDedupPlugin/` source;
- helper Kestrel route declarations (count stays 10);
- Lisp commands (count stays 1: `C:YUANTUS_DIFF_PREVIEW`);
- `ErrorCodes` constants;
- the S6 audit substrate / `audit_events` schema;
- the existing static verifiers (`verify_bridge_static.py`,
  `verify_lisp_shell_static.py`, `verify_material_sync_static.py`);
- Python FastAPI server source;
- schema / migration / tenant-baseline data.

New files the implementation MAY add (the installer slice's own
surface):

- `clients/cad-desktop-helper/Installer/` — the Inno Setup script
  (`.iss`), a build/pack helper script, and any fenced-startup-block
  templates;
- `clients/cad-desktop-helper/Installer/verify_installer_static.py` (or
  a repo-root verifier path consistent with the other verifiers) — the
  static guard set in §5;
- a new path-filter entry in `.github/workflows/cad-helper-shared-dotnet.yml`
  pointing at `clients/cad-desktop-helper/Installer/**` **only if that
  directory is created in the same PR** (per the #636 lesson: every
  `on.*.paths` glob must point to a real directory created in the same
  PR, enforced by `test_workflow_trigger_glob_paths_match_repo_targets`).

### 3.H Production-seam coverage — 4th application of the without-fakes rule

Building, signing, and running a Windows per-user installer are
**environment-prohibited on CI** (Windows host + GUI install/uninstall
+ owner-held signing cert). This is the **fourth application** of the
`feedback_production_seam_tests_without_fakes` rule (after S7's
parent-ancestry walk, S9's bridge transport, and S10's Lisp shell):
the seam cannot be exercised end-to-end on CI, so the coverage shape is
**static verifier + deferred operational signoff**, not a fake.

- the **static verifier** source-pins the Inno-script invariants that a
  fake could otherwise paper over (see §5);
- the **deferred operational signoff** packet (§3.I) lists the real
  install/uninstall/repair behaviors an operator must verify on a real
  Windows host with a real CAD install.

The verifier must assert against the **real** `.iss` script content,
not a mock — exactly as `verify_lisp_shell_static.py` pins the real
`.lsp` source.

### 3.I Deferred operational signoff packet (installer slice)

The impl PR's DEV/Verification MD must enumerate these operator-side
checks (real Windows + CAD host, owner-local signed build), deferred
exactly like the S7/S8/S9/S10 §4.1 packets:

1. per-user install on a clean Windows 11 profile with a non-admin
   account succeeds without elevation;
2. files land at the exact §2.1 paths;
3. first `PLMMATPULL` / `YUANTUS_DIFF_PREVIEW` after install triggers
   the S3 DPAPI bootstrap (token created on first launch, not by the
   installer);
4. CAD-host startup auto-config makes `YUANTUS_DIFF_PREVIEW` available
   in ZWCAD + GstarCAD with no manual startup-file editing;
5. `--skip-cad-config` install leaves CAD startup files untouched;
6. install over a running helper prompts and stops it cleanly via the
   session-file PID;
7. repair preserves the DPAPI token, `audit.db`, and `install-id.json`;
8. uninstall (default) removes binaries + fenced startup block +
   `CADDedup.bundle` but preserves the §3.F user-data set; the
   full-purge opt-in removes it;
9. the signed release binaries + installer pass Authenticode
   verification (`signtool verify` / right-click → Digital Signatures);
10. already-installed `CADDedup.bundle` is adopted/overwritten in
    place, not duplicated.

This slice introduces these as its own deferred packet (unlike S11,
which added none).

## 4. R1 Target Output

The installer implementation PR should contain:

- `clients/cad-desktop-helper/Installer/` with the Inno `.iss` script,
  build/pack helper, fenced-startup-block template(s);
- `clients/cad-desktop-helper/Installer/verify_installer_static.py` (or
  the repo-consistent verifier path);
- a `.github/workflows/cad-helper-shared-dotnet.yml` path-filter entry
  for `clients/cad-desktop-helper/Installer/**` (created same-PR);
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_INSTALLER_R1_20260524.md`;
- a `docs/DELIVERY_DOC_INDEX.md` entry for that MD (lexically sorted);
- (optional, if needed) an update to
  `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` adding an
  *"automated install (installer)"* section that points at the new
  installer while keeping the manual procedure as the fallback. If
  elected, this is the only edit to an existing doc and must be named
  in the DEV/Verification MD.

The implementation PR must **not** contain:

- runtime code edits under
  `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`;
- `clients/autocad-material-sync/CADDedupPlugin/` source edits;
- new helper routes, Lisp commands, or `ErrorCodes`;
- auto-update logic (deferred — §8);
- a real signing cert in CI secrets;
- schema / migration / tenant-baseline edits.

## 5. Mandatory Static Guards

`verify_installer_static.py` must source-pin (against the real `.iss`
and pack scripts) at minimum:

1. `PrivilegesRequired=lowest` is present (per-user, no-admin);
2. no `HKLM` / `HKEY_LOCAL_MACHINE` write directive anywhere in the
   script;
3. the helper / detector / bridge / lsp / bundle install paths are
   pinned to the **Inno per-user constants** that expand to the §2.1
   targets — i.e. `{userappdata}\YuantusPLM\...` for helper/detector
   (`helper\`) + bridge/lsp (`cad-bridge\`), and
   `{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle\...` for
   the bundle (or an explicitly equivalent Inno expansion). The guard
   must **reject** a literal `%APPDATA%` string (Inno does not expand
   env vars in `[Files]` destinations) and **reject** a relocatable
   `DefaultDirName` / `{app}`-rooted layout that would move the helper
   off its fixed spawn path (no Program Files, no `{commonpf}`, no
   user-chosen install dir);
4. the signing step is present **and** is guarded so it is skipped when
   no cert/`SignTool` is configured (CI builds unsigned) — pin both the
   presence of the sign step and the graceful-skip guard;
5. no Windows Service / auto-start registration directive;
6. no DPAPI token / `audit.db` / `install-id.json` / session-file
   pre-creation in the install steps;
7. the CAD-startup auto-config block is uniquely fenced (begin/end
   marker constants present) and gated behind a `--skip-cad-config`
   task/flag;
8. uninstall logic is an allow-list of installer-laid paths — reject a
   blanket recursive delete of `%APPDATA%\YuantusPLM\` (guard against
   `rmdir /s` / `{app}`-wide `Type: filesandordirs` on the whole
   YuantusPLM root);
9. the §3.F preserve set is honored — the uninstall/repair sections do
   not list the token / `audit.db` / `install-id.json` for deletion
   (except inside the explicit full-purge opt-in branch);
10. the pack step consumes pre-built `bin/Release/` artifacts and does
    not invoke MSBuild for the consumed projects.

The impl PR must also pass the existing doc-index drift suite for the
new DEV/Verification MD, and:

- `test_workflow_trigger_glob_paths_match_repo_targets` — the new
  `Installer/**` path filter points at a real directory created in the
  same PR;
- `test_odoo18_r2_portfolio_contract` +
  `test_tier_b_3_breakage_design_loopback_portfolio_contract` — must
  remain unchanged-passing (installer slice does not touch portfolio
  surfaces).

## 6. Verification Plan

```bash
# new installer static verifier
python3 clients/cad-desktop-helper/Installer/verify_installer_static.py

# doc-index + portfolio + workflow-trigger drift
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_trigger_paths_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

The actual installer build (`iscc` Inno compile), signing, and
install/uninstall/repair execution are **owner-local / operator-side**
(§3.B, §3.I); CI runs the static verifier + an optional unsigned
`iscc` smoke-compile if a Windows runner with Inno is available, never
a signed build.

## 7. DEV / Verification MD Requirements

The implementation PR must add
`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_INSTALLER_R1_20260524.md`
containing:

- the installer artifact layout under
  `clients/cad-desktop-helper/Installer/`;
- the packaging-tech decision (Inno per-user) + the MSIX/MSI rejection
  rationale (§3.A);
- the signing posture (owner-local signed, CI unsigned — §3.B);
- the §3.D CAD-startup auto-config design + `--skip-cad-config`
  fallback + any host documented as manual-config-required;
- the §3.E install/uninstall/repair contract + running-helper handling;
- the §3.F preserve-set enumeration;
- the static-guard results from §5;
- the §3.I deferred operational signoff packet (the 10 operator
  checks);
- an explicit statement that no runtime / route / ErrorCode / Lisp /
  Bridge surface changed (verified by inspection: helper route count
  still 10, Lisp command count still 1, Bridge sources unchanged);
- the `contracts` + `cad-helper-shared-dotnet` CI run URLs.

## 8. Explicit Non-Goals

- **No auto-update.** Version check / download / replace / rollback is
  a separate follow-up slice with its own opt-in. R1 ships
  install/uninstall/repair only. (Auto-update may warrant its own
  design note when opted in.)
- No administrator-required / machine-wide MSI (rejected, §3.A).
- No MSIX / AppContainer packaging (rejected, §3.A).
- No Windows Service registration / auto-start of the helper.
- No DPAPI token / `audit.db` / `install-id.json` pre-seeding.
- No firewall / port / binding changes.
- No helper Kestrel route, Lisp command, or `ErrorCodes` additions.
- No runtime source edits under `clients/cad-desktop-helper/{Shared,
  Detector,Helper,Bridge,Lisp}/` or `CADDedupPlugin/`.
- No real signing certificate in CI / repo.
- No Tauri / Electron Companion shell (out of R3 scope, design `:5`).
- No `server_allowlist` runtime update mechanism (design `:740` defers
  it).
- No Python FastAPI / schema / migration / tenant-baseline edits.
- No CAD pool R2 work (still deferred, 4 entry conditions unchanged).
- No collection of the §3.I operator evidence inside the PR — the impl
  ships the installer + verifier; the operator runs the install
  evidence offline (same posture as S7/S8/S9/S10 §4.1).

## 9. Recommended Branch For Implementation

After this taskbook merges and only after a separate explicit opt-in,
use the program's `feat/` implementation-branch convention:

```text
feat/cad-helper-bridge-installer-r1-20260524
```

(This taskbook itself is authored on the doc-only branch
`docs/cad-helper-bridge-installer-taskbook-20260524`.)

Do not start the installer implementation from this taskbook PR.

## 10. Reviewer Focus

Please review these points before merge:

1. Confirm the slice is correctly framed as a **standalone follow-up**
   to the closed R3.2 program (no new R-numbered design cycle, no new
   design doc) and that the installer scope is fully constrained by the
   R3.2 design + S11 install runbook (§1).
2. Confirm the **per-user / no-admin Inno** decision and the MSIX/MSI
   rejection rationale correctly protect the merged S1–S11 runtime's
   fixed-path-spawn + DPAPI + loopback assumptions (§3.A).
3. Confirm the installer **must not pre-seed** the DPAPI token / session
   file / `install-id.json` / `audit.db` and **must not** register a
   Windows Service (§3.E) — these are runtime-owned.
4. Confirm the **CAD-startup auto-config** is in-scope, idempotently
   fenced, opt-out via `--skip-cad-config`, and is the only write
   outside `%APPDATA%\YuantusPLM\` + the bundle path (§3.D).
5. Confirm the **signing posture** (owner-local signed, CI unsigned,
   no cert in CI secrets, graceful-skip guard) does not create a CI
   dead-end (§3.B).
6. Confirm the **preserve-set** is a finite allow-list with default-deny
   deletion, and that uninstall is not a blanket recursive delete
   (§3.F + §5 guard 8).
7. Confirm this slice's **deferred operational signoff packet** (§3.I)
   is its own (the 10 operator checks), distinct from the S7/S8/S9/S10
   packets that S11 already consolidated.
8. Confirm the **4th application** of the
   `feedback_production_seam_tests_without_fakes` rule is correctly
   shaped: real `.iss` static verifier + deferred operational signoff,
   not a fake (§3.H, §5).
9. Confirm **auto-update is cleanly deferred** (§8) and nothing in the
   R1 scope implies it.
10. Confirm the §3.C **no-version-downgrade-guard** simplification (the
    installer always lays the version it ships and adopts/overwrites an
    existing `CADDedup.bundle` in place) is acceptable for R1, or push
    back if a downgrade guard is wanted before first ship.

## 11. Status

This taskbook is ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it;
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
