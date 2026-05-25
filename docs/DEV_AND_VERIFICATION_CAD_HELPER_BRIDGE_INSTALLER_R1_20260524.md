# DEV / Verification — CAD Helper Bridge Installer R1

Date: 2026-05-24

Implementation record for the CAD helper bridge installer, delivered
against the taskbook
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_INSTALLER_20260524.md`
(#638 `18b83d73`). This is the **standalone follow-up** installer slice
for the closed R3.2 program; it automates the S11 manual install
runbook into a per-user, no-admin Inno Setup installer.

## 1. What this PR adds

New files under `clients/cad-desktop-helper/Installer/`:

| File | Role |
|---|---|
| `YuantusCadHelper.iss` | The Inno Setup script — per-user (`PrivilegesRequired=lowest`) installer for the four ship artifacts + repackaged CADDedup bundle. |
| `pack.ps1` | The PACK helper — stages pre-built `bin/Release` output into a flat tree and runs `iscc`. Does NOT build (no MSBuild). |
| `verify_installer_static.py` | The static verifier (10 mandatory guards + drift guards) that source-pins the `.iss` invariants. |

Plus:

- `.github/workflows/cad-helper-shared-dotnet.yml` — a new
  `clients/cad-desktop-helper/Installer/**` path filter (on both
  `pull_request` and `push`) and a *"Verify CAD helper installer static
  contracts"* step. The directory is created in this same PR, so
  `test_workflow_trigger_glob_paths_match_repo_targets` is satisfied.
- this DEV/Verification MD + its `docs/DELIVERY_DOC_INDEX.md` entry.
- an *"automated install"* section appended to
  `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` pointing at
  the installer while keeping the manual procedure as the fallback (the
  only edit to an existing doc, per taskbook §4).

## 2. Design decisions realized

- **Per-user, no-admin (Inno).** `PrivilegesRequired=lowest`,
  `DefaultDirName={userappdata}\YuantusPLM`, `DisableDirPage=yes`. No
  HKLM, no Program Files, no `{app}`/`{commonpf}` relocatable layout.
  Files land at the exact `%APPDATA%` paths the S1–S11 runtime spawns
  from:
  - `{userappdata}\YuantusPLM\helper\` — helper + detector `.exe`;
  - `{userappdata}\YuantusPLM\cad-bridge\` — bridge DLL + `.lsp`;
  - `{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle\` — bundle
    (adopt/overwrite in place).
- **Signing is owner-local.** The `[SignTools]` block + the `SignTool=`
  reference are wrapped in `#ifdef SignToolCmd`. CI compiles without
  `/DSignToolCmd=...` → an UNSIGNED installer (graceful skip, no cert in
  CI). The contract is *"sign every first-party signable `.exe`/`.dll`
  the installer lays + the installer `.exe`"*; third-party DLLs
  (`Newtonsoft.Json.dll`, runtime DLLs) are owner policy.
- **Runtime-owned files are never pre-seeded.** No `[Files]`/`[Code]`
  creates `local-helper-token.dat`, `audit.db`, `install-id.json`, or
  `helper-session-*.json` — all created on first helper launch (S3/S6).
- **No Windows Service / auto-start.** The helper is spawn-on-demand and
  idle-exits after 30 min; the installer adds no service and no Run-key.
- **Running-helper handling.** `PrepareToInstall` and the uninstall step
  read `pid` + `image_path` from `helper-session-*.json` at the root
  (the existing `HelperSessionDocument` schema —
  `clients/cad-desktop-helper/Helper/HelperRuntime.cs:410-453`,
  `[JsonProperty("pid")]` at `:421`), confirm the image path is the
  helper we manage, prompt, then `taskkill /PID <pid> /F`. It never
  blind-kills by image name and never deletes the session file (S3
  stale-clean owns it). **No runtime change was needed** — the schema
  already carries `pid`.
- **Scope narrowing vs taskbook §3.D (read this).** The taskbook §3.D
  describes the installer as *"auto-configures CAD-host startup so the
  operator does not have to hand-edit startup files"* for detected
  hosts. **R1 does NOT fully meet that shape** and consciously narrows
  it: R1 writes the startup stub but does **not** auto-register each
  detected CAD host's Support File Search Path (the step that makes the
  stub actually load). Registering the Support path is the operator's
  one-time manual step in R1 (taskbook §3.D explicitly keeps a
  manual-config-required fallback + the `--skip-cad-config` opt-out).
  Full per-host Support-path auto-registration is deferred — see the
  next bullet for why, and §4 item 4 for the operator verification.
- **CAD-startup stub (honest scope).** Gated behind the `cadconfig`
  task (the `--skip-cad-config` opt-out). On post-install it writes an
  idempotent, uniquely-fenced region
  (`; ==== BEGIN/END YUANTUS CAD HELPER ====`) into a stable per-user
  file (`{userappdata}\YuantusPLM\cad-bridge\acad.lsp`) that NETLOADs the
  bridge DLL and `(load)`s the Lisp shell. **This file is not on any CAD
  host's default Support File Search Path**, so a CAD host loads it only
  once that folder is on the host's Support path. R1 therefore writes the
  canonical stub (so the operator's per-host pointer is a one-time
  Support-path entry to a stable file, not hand-authored NETLOAD/load
  lines per host); registering the Support path per host is the
  operator's one-time step (taskbook §3.D manual-config-required
  fallback) or a future per-host HKCU Support-path enhancement.
  Auto-writing speculative per-host HKCU Support-path keys was
  deliberately NOT done in R1 because the per-host key formats are
  version/language-specific and cannot be verified without a real host.
  Repeat installs/repairs strip-then-reappend (no duplication); uninstall
  removes exactly that region.
- **Uninstall = allow-list.** `[UninstallDelete]` lists only the
  installer-laid subdirs (`helper\`, `cad-bridge\`, the bundle). The
  `{userappdata}\YuantusPLM` root is never blanket-deleted, so the
  preserved set survives by default. The preserved root-level files are
  the full set the runtime writes there:
  `local-helper-token.dat` (S3), `audit.db` (S6), `install-id.json`,
  **`plm-bearer-token.bin` (S5 PLM bearer token)**, and `config.json`
  (`HelperRuntime.cs:2912-2913`), plus the S3-owned
  `helper-session-*.json`. A full purge is an explicit opt-in prompt
  (`FullPurgeUserData`) that removes them — **`plm-bearer-token.bin`
  first**, since leaving the PLM bearer token behind would let a
  reinstall stay logged in. `helper-session-*.json` is never touched
  (S3 owns it).

## 3. Verification (local)

```
$ python3 clients/cad-desktop-helper/Installer/verify_installer_static.py
All 15 CAD helper installer static guards passed.
```

Doc-index + portfolio + workflow-trigger drift suite: see §5.
`git diff --check`: clean.

The installer build (`iscc`), signing, and install/uninstall/repair
execution are **owner-local / operator-side** and cannot run on CI (no
Inno compiler guarantee, owner-held cert, no real per-user Windows
profile + CAD host). Coverage is the static verifier above; the
end-to-end behavior is the deferred operational signoff packet in §4.

## 4. Deferred operational signoff packet (§3.I)

This slice introduces its **own** deferred packet (unlike S11). It is
the **4th application** of the
`feedback_production_seam_tests_without_fakes` rule: the installer
build/sign/run seam is environment-prohibited on CI, so coverage is the
static verifier (real `.iss` source-pin) **plus** these operator-side
checks on a real Windows + CAD host with an owner-local signed build:

1. per-user install on a clean Windows 11 profile with a non-admin
   account succeeds without elevation;
2. files land at the exact paths in §2 (helper/detector under `helper\`,
   bridge + `.lsp` under `cad-bridge\`, bundle under
   `Autodesk\ApplicationPlugins\CADDedup.bundle`);
3. first `PLMMATPULL` / `YUANTUS_DIFF_PREVIEW` after install triggers the
   S3 DPAPI bootstrap (token created on first launch, not by the
   installer);
4. with the cad-bridge folder on a CAD host's Support File Search Path
   (operator one-time step, or a future per-host HKCU Support-path
   enhancement), the installer-laid `acad.lsp` stub is auto-loaded and
   `YUANTUS_DIFF_PREVIEW` becomes available in ZWCAD + GstarCAD; for a
   host with no per-user-writable Support path, the manual-config-required
   fallback applies (operator hand-registers it) — verify both the
   stub-resolves path and the fallback;
5. `--skip-cad-config` (unchecked `cadconfig` task) leaves the stub
   unwritten / CAD startup files untouched;
6. install over a running helper prompts and stops it cleanly via the
   session-file `pid`;
7. repair preserves the DPAPI token, `audit.db`, `install-id.json`,
   `plm-bearer-token.bin`, and `config.json`;
8. uninstall (default) removes binaries + fenced startup region +
   `CADDedup.bundle` but preserves the §2 user-data set; the full-purge
   opt-in removes it — and after a full purge a reinstall does NOT stay
   logged in (the PLM bearer token `plm-bearer-token.bin` is gone);
9. the signed release binaries + installer pass Authenticode
   verification (`signtool verify` / right-click → Digital Signatures);
10. an already-installed `CADDedup.bundle` is adopted/overwritten in
    place, not duplicated.

These 10 items are the owner/operator signoff target; they are NOT
collected inside this PR (same posture as S7/S8/S9/S10 §4.1).

## 5. Test results

Local suite:

```
python3 clients/cad-desktop-helper/Installer/verify_installer_static.py   # 15 guards pass
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_trigger_paths_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

## 6. No runtime / route / ErrorCode / Lisp / Bridge change

Verified by inspection: this PR adds only `Installer/` files + the
workflow filter/step + docs. No edit to
`clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`
runtime source or `CADDedupPlugin/` source. Helper Kestrel route count
stays 10; Lisp command count stays 1 (`C:YUANTUS_DIFF_PREVIEW`); no new
`ErrorCodes`. Auto-update remains deferred to a separate slice.

## 7. CI

- `cad-helper-shared-dotnet` — now runs the installer static verifier
  (this PR touches `Installer/**`, so the workflow triggers).
- `contracts` — doc-index / portfolio / workflow-trigger drift.

(CI run URLs filled in on the PR.)
