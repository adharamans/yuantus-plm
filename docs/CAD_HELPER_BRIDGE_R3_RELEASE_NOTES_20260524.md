# CAD Helper Bridge R3.2 — Release Notes

Date: 2026-05-24

Branch closeout for the **CAD Desktop Helper Bridge R3.2** program
(`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`). S1 through
S10 are merged on `main`; this S11 implementation ships the closeout
documentation + acceptance-evidence runbook only. The R3.2 cycle is
**closed pending operator execution of the acceptance-evidence
runbook**.

## 1. What shipped (four artifacts + existing AutoCAD plugin)

Per design `:983-1009`:

| Artifact | Path | Build | Purpose |
|---|---|---|---|
| `yuantus-cad-detector.exe` | `clients/cad-desktop-helper/Detector/` | net6.0 self-contained Windows | Read-only CAD-host discovery (registry + filesystem scan) |
| `yuantus-cad-helper.exe` | `clients/cad-desktop-helper/Helper/` | net6.0 self-contained Windows | Kestrel loopback service (10 routes); `--reset-local-token` subcommand |
| `YuantusCadHelperBridge.dll` | `clients/cad-desktop-helper/Bridge/` | .NET Framework v4.6 | NETLOAD assembly exposing `(yuantus-helper-call ...)` Lisp primitive for ZWCAD / GstarCAD |
| `yuantus_cad_helper.lsp` | `clients/cad-desktop-helper/Lisp/` | AutoLISP | Lisp shell defining `C:YUANTUS_DIFF_PREVIEW` (display-only, no DWG mutation) |

Plus the existing AutoCAD plugin that consumes the helper:

| Artifact | Path | Build |
|---|---|---|
| `CADDedupPlugin.bundle` | `clients/autocad-material-sync/CADDedupPlugin/` | .NET Framework v4.6 / v4.8 multi-config |

`Yuantus.Cad.Shared` multi-targets `net46;net6.0-windows` so a single
source tree builds the .NET Framework client variant (consumed by
`CADDedupPlugin` and `YuantusCadHelperBridge`) **and** the .NET 6
helper / detector variant from one ProjectReference, with each
consumer auto-selecting the right target. Design `:1003-1009` covers
the rationale; acceptance test #12 in
`docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md`
proves the two loaded copies inside `acad.exe` + `yuantus-cad-helper.exe`
do not collide at runtime.

## 2. Slice ledger (canonical commit list)

Each slice = doc-only taskbook PR followed by a separately-opted-in
implementation PR. Squash-merge SHAs on `main`:

| Slice | Taskbook PR / SHA | Implementation PR / SHA |
|---|---|---|
| R3.2 Design | — | #614 `fff93a2` |
| S1 Shared | #616 `bd61af2` | #617 `2740865` |
| S2 Detector | — | #618 `db1d3de` |
| S3 Helper startup | #619 `13bf4d2` | #620 `e0c76e8` |
| S4 Auth / origin allowlist | #621 `91e71f7` | #622 `dce38c0` |
| S5 Session routes | #623 `d40e76f` | #624 `c500398` |
| S6 Business + audit | #625 `3b92dad` | #626 `ab31df5` |
| S7 Reset-token CLI | #627 `2be62a5` | #628 `431b6adf` |
| S8 MaterialSync migration | #629 `a69ae656` | #630 `90d80c55` |
| S9 NETLOAD Lisp bridge | #631 `349ec48d` | #632 `be290cab` |
| S10 ZWCAD/GstarCAD Lisp shell | #633 `de365c01` | #634 `4662dbaf` |
| S11 Integration / acceptance closeout | #635 `b2c18d07` | (this PR) |

Mid-cycle hotfix: PR #636 `03185519` removed a dangling
`Lisp.Tests/**` path-trigger from
`.github/workflows/cad-helper-shared-dotnet.yml` that the S10 #634
convergence had added without creating the directory; `contracts` CI
was red on `main` between #634 and #636 for that reason. The Lisp
slice uses the Python static verifier `verify_lisp_shell_static.py` —
no managed dotnet test project exists or is planned for the Lisp
slice. Lesson recorded: any new `on.*.paths` glob in a workflow yaml
must point to a real directory created in the same PR;
`test_workflow_trigger_glob_paths_match_repo_targets` enforces this
post-merge.

## 3. Helper Kestrel routes (count = 10)

Verified by inspection of
`clients/cad-desktop-helper/Helper/HelperRuntime.cs` at the time of
this PR — exactly 10 `MapGet(...)` / `MapPost(...)` declarations:

1. `GET  /healthz`
2. `GET  /version`
3. `POST /session/login`
4. `POST /session/logout`
5. `GET  /session/status`
6. `POST /cad/current-drawing`
7. `POST /diff/preview`
8. `POST /sync/inbound`
9. `POST /sync/outbound`
10. `POST /audit/apply-result`

The S11 implementation PR does NOT add, remove, or modify any helper
route. The route count remains 10.

## 4. Lisp commands (count = 1)

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` defines
exactly one Lisp command:

- `C:YUANTUS_DIFF_PREVIEW` — display-only, no DWG mutation, no modal
  dialogs, single external interaction via the S9
  `(yuantus-helper-call ...)` primitive (verified by the static
  verifier `verify_lisp_shell_static.py`).

The S11 implementation PR does NOT add, remove, or modify any Lisp
command. The count remains 1.

## 5. ErrorCodes

Canonical list:
`clients/cad-desktop-helper/Shared/Transport/ErrorCodes.cs` — that
file is the single source of truth for every code the helper /
transport returns.

S7 added three codes:
`HELPER_RESET_REQUIRES_INTERACTIVE`, `HELPER_RESET_CANCELLED`,
`HELPER_RESET_HELPER_RUNNING`.

S6 added the audit-correlation codes
`AUDIT_PULL_ID_UNKNOWN`, `AUDIT_ALREADY_REPORTED`,
`AUDIT_PULL_ID_EXPIRED` (plus the PLM passthrough +
`HELPER_INPUT_VALIDATION_FAILED` set).

Earlier slices (S3 / S4) introduced the host / DPAPI / auth /
origin codes (e.g., `HELPER_PORT_BUSY`,
`HELPER_DPAPI_UNAVAILABLE`, `AUTH_LOCAL_TOKEN_MISSING`,
`ORIGIN_PROCESS_NOT_ALLOWED`).

S11 does NOT add any new `ErrorCodes` constants. The set is frozen at
the post-S10 value — read `ErrorCodes.cs` for the exact field list.

## 6. CI / workflow surface

- `.github/workflows/cad-helper-shared-dotnet.yml` — canonical dotnet
  build/test workflow for CAD helper slices. Path-filtered on
  `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/**`
  + `clients/autocad-material-sync/**` + the two Python static
  verifiers. Doc-only PRs (including S11 implementation) correctly do
  not trigger this workflow; `contracts` CI is the authoritative gate.
- `contracts` CI — covers doc-index drift, R2 portfolio, Tier-B
  breakage drift, workflow-trigger-paths drift. Authoritative for
  doc-only PRs.

S11 implementation PR does not modify any workflow yaml.

## 7. Pointers to per-slice documentation

Each prior slice ships its own DEV/Verification MD; see
`docs/DELIVERY_DOC_INDEX.md` for the canonical index. Key entries:

- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S1_SHARED_LIBRARY_R1_20260520.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S2_DETECTOR_R1_20260520.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S3_HELPER_STARTUP_R1_20260521.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S4_AUTH_ORIGIN_ALLOWLIST_R1_20260522.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S5_SESSION_ROUTES_R1_20260522.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S6_BUSINESS_AUDIT_ROUTES_R1_20260522.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_R1_20260522.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_R1_20260524.md` (this PR)

## 8. Deferred-signoff packet (consolidated)

S7 / S8 / S9 / S10 each carried a §4.1 "deferred operational
signoff" packet — items that environment-prohibited CI seams could
not exercise (Windows native CAD hosts, real DPAPI, real
NETLOAD-into-acad.exe, real PowerShell / SSH / WinRM remote-shell
detection). The consolidated runbook is at
`docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md`;
that is the operator's evidence-collection procedure to close those
packets. S11 does **not** introduce any deferred-signoff items of its
own.

## 9. Known follow-ups (NOT part of R3.2 — each its own opt-in)

- Acceptance-evidence collection per the runbook above (operator
  task, not Claude task).
- A real installer (MSI / signed bundle) — R3.2 ships a documented
  install procedure only; an installer is a separate future slice.
- Per-host Lisp divergence in
  `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` if a real
  ZWCAD or GstarCAD acceptance run exposes `_PKSER` / `ACADVER`
  differences — deferred per S10 §3.I.
- The deferred R2 CAD pool multi-server candidate (4 entry conditions
  unchanged per the Odoo18 R2 portfolio closeout).

## 10. Closeout posture

R3.2 is closed **pending the acceptance-evidence runbook execution**.
This release-notes file is the canonical entry point for that
posture; the runbook, the install runbook, and the closeout report
(`docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md`) are the
operator-facing artifacts.
