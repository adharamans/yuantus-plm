# CAD Helper Bridge R3.2 — Closeout Report

Date: 2026-05-24

Final cycle-complete report for the **CAD Desktop Helper Bridge R3.2**
program as defined in
`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md`.

> **Status: R3.2 is closed pending the acceptance-evidence runbook
> execution.**

This phrasing is deliberate. All 11 design-required slices (S1
through S11) are merged on `main`; the implementation surface is
complete. What remains is operator execution of the 12-item
acceptance-evidence runbook on real Windows + CAD hosts to clear the
deferred-signoff packets from S7/S8/S9/S10. S11 ships the runbook;
operator execution is not a Claude task.

## 1. Canonical slice ledger

Each slice = doc-only taskbook PR followed by a separately-opted-in
implementation PR. Squash-merge SHAs on `main`:

| Slice | Owner | Taskbook PR / SHA | Implementation PR / SHA |
|---|---|---|---|
| R3.2 Design | Owner | — | #614 `fff93a2` |
| S1 Shared (`Yuantus.Cad.Shared`) | Claude | #616 `bd61af2` | #617 `2740865` |
| S2 Detector | Claude | — | #618 `db1d3de` |
| S3 Helper startup | Claude | #619 `13bf4d2` | #620 `e0c76e8` |
| S4 Auth / origin allowlist | Claude | #621 `91e71f7` | #622 `dce38c0` |
| S5 Session routes | Owner | #623 `d40e76f` | #624 `c500398` |
| S6 Business + audit | Claude | #625 `3b92dad` | #626 `ab31df5` |
| S7 Reset-token CLI | Claude | #627 `2be62a5` | #628 `431b6adf` |
| S8 MaterialSync migration | Owner | #629 `a69ae656` | #630 `90d80c55` |
| S9 NETLOAD Lisp bridge | Claude | #631 `349ec48d` | #632 `be290cab` |
| S10 ZWCAD/GstarCAD Lisp shell | Claude | #633 `de365c01` | #634 `4662dbaf` |
| S11 Integration / acceptance closeout | Claude | #635 `b2c18d07` | (this PR) |

Mid-cycle hotfix: PR #636 `03185519` removed a dangling
`Lisp.Tests/**` path-trigger from
`.github/workflows/cad-helper-shared-dotnet.yml`. The S10 #634
convergence had added these path entries without creating the
directory; the Lisp slice intentionally has no managed dotnet test
project (its CI signal is the Python static verifier
`verify_lisp_shell_static.py`). The
`test_workflow_trigger_glob_paths_match_repo_targets` contract test
caught the dangling globs and turned `contracts` CI red on `main`
between #634 and #636. The hotfix closed the gap by removing the
dangling lines.

## 2. Ship artifacts (per design `:983-1009`)

Four R3.2 deliverables plus the existing AutoCAD plugin:

| Artifact | Source path | Build configuration |
|---|---|---|
| `yuantus-cad-detector.exe` | `clients/cad-desktop-helper/Detector/` | net6.0 self-contained Windows binary |
| `yuantus-cad-helper.exe` | `clients/cad-desktop-helper/Helper/` | net6.0 self-contained Windows binary; `--reset-local-token` subcommand |
| `YuantusCadHelperBridge.dll` | `clients/cad-desktop-helper/Bridge/` | .NET Framework v4.6 |
| `yuantus_cad_helper.lsp` | `clients/cad-desktop-helper/Lisp/` | AutoLISP (single source for ZWCAD + GstarCAD; sniffs `(getvar "PROGRAM")` for per-host adaptation) |
| `CADDedupPlugin.bundle` (existing) | `clients/autocad-material-sync/CADDedupPlugin/` | .NET Framework v4.6 net46 / v4.8 net48 multi-config |

`Yuantus.Cad.Shared` multi-targets `net46;net6.0-windows`. Each
consumer (`CADDedupPlugin` net46, `YuantusCadHelperBridge` v4.6,
helper net6.0, detector net6.0) auto-selects the right target from a
single ProjectReference. Acceptance test #12 proves the two loaded
copies inside `acad.exe` + `yuantus-cad-helper.exe` do not collide at
runtime.

## 3. Production helper Kestrel route table (exactly 10)

Verified by inspection of
`clients/cad-desktop-helper/Helper/HelperRuntime.cs` at the time of
this PR. The route table is **frozen** at the S6 / S5 / S3 final
state — neither S7, S8, S9, S10, nor S11 add, remove, or modify any
route:

```
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

S11 does not touch this route table.

## 4. Lisp commands (exactly 1)

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp`:

- `C:YUANTUS_DIFF_PREVIEW` — display-only command; no DWG mutation;
  no modal dialogs (only `(princ)`); single external interaction
  via the S9 `(yuantus-helper-call ...)` primitive.

The S10 static verifier (`verify_lisp_shell_static.py`, 20 guards)
enforces these constraints on every CI run.

## 5. Bridge primitives (exactly 1)

`clients/cad-desktop-helper/Bridge/`:

- `(yuantus-helper-call <endpoint> <json>)` — sole Lisp primitive
  exposed by `YuantusCadHelperBridge.dll` via the NETLOAD adapter
  (`#if AUTOCAD_HOST` conditional compilation, static-verified by
  `verify_bridge_static.py`).

## 6. ErrorCodes (frozen at post-S10 state)

The post-S10 `Shared/Transport/ErrorCodes.cs` set is the closeout
state. The full list lives in
`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_R1_20260522.md`
(the most recent slice that added codes). S11 adds nothing.

## 7. Acceptance-evidence packets (consolidated deferred signoffs)

The four prior slices each carried a §4.1 "deferred operational
signoff" packet — environment-prohibited CI seams that need to be
exercised on real Windows + CAD hosts before the cycle is fully
clear. Counts (verified against the per-slice §4.1 sections):

- **S7** — 5 items (PowerShell y/n cancel paths, running-helper
  refusal, SSH / WinRM / RDP refusal, post-reset CAD re-auth).
- **S8** — 5 items (AutoCAD 2018 build of `CADDedupPlugin.csproj`,
  AutoCAD load of `CADDedup.bundle`, `PLMMATPUSH` through helper
  `/sync/inbound`, `PLMMATPULL` through helper `/diff/preview` +
  CAD field write + `/audit/apply-result`, helper audit DB rows).
- **S9** — 7 items (Windows + AutoCAD / ZWCAD / GstarCAD NETLOAD,
  bridge DLL loads without missing deps, `(yuantus-helper-call ...)`
  starts/finds helper, success returns JSON, failure returns nil +
  sanitized line, no token in CAD command-line output, S10-paired
  display-only `/audit/apply-result not-applied-display-only` row).
- **S10** — 8 items (real ZWCAD + GstarCAD load of the .lsp,
  `YUANTUS_DIFF_PREVIEW` available, prompts accept input,
  `(yuantus-helper-call "/diff/preview" ...)` returns JSON,
  displayed lines via production CAD command-line writer, no DWG
  mutation, `audit.db` row with correct outcome + pull_id, pull_id
  cross-row correlation between `/diff/preview` and
  `/audit/apply-result`).

Total deferred items consolidated: **25**.

These 25 environment-specific items are the operator's signoff
target; the consolidated execution runbook is at
`docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md`.
That runbook structures the 12 design-required `:810-825` acceptance
rows; each row exercises one or more of the 25 deferred items.

## 8. S11 itself adds no deferred-signoff items (per §3.H)

Unlike S7 / S8 / S9 / S10, S11 introduces no new production seam — no
runtime code, no new Kestrel route, no new Lisp command, no new
ErrorCodes, no new static verifier, no new dotnet test project, no
workflow yaml edits. The S11 implementation PR is purely additive
documentation. Therefore S11 has no §4.1 deferred-signoff packet of
its own.

## 9. Anti-drift guards in place

These tests are the authoritative anti-drift checks for the
post-R3.2 surface — run them rather than trusting this report:

| Test | What it pins |
|---|---|
| `test_delivery_doc_index_references` | each MD listed in `docs/DELIVERY_DOC_INDEX.md` exists |
| `test_dev_and_verification_doc_index_completeness` | every `DEV_AND_VERIFICATION_*.md` is indexed |
| `test_dev_and_verification_doc_index_sorting_contracts` | doc-index entries are lexically sorted |
| `test_workflow_trigger_paths_contracts` | every `on.*.paths` glob in every workflow yaml matches a real repo target |
| `test_odoo18_r2_portfolio_contract` | Odoo18 R2 portfolio surface unchanged (S11 must not regress) |
| `test_tier_b_3_breakage_design_loopback_portfolio_contract` | Tier-B #3 §3 catalog surface unchanged |
| `verify_bridge_static.py` | S9 Bridge static contracts |
| `verify_lisp_shell_static.py` | S10 Lisp shell static contracts (20 guards) |
| `verify_material_sync_static.py` | S8 AutoCAD material sync static contracts |

The `cad-helper-shared-dotnet` workflow (Windows runner) is the
dotnet build/test signal; doc-only PRs (including this S11 impl)
correctly do not trigger it. `contracts` CI is the authoritative
gate for doc-only PRs.

## 10. Known follow-ups (NOT part of R3.2 — each its own opt-in)

- **Operator acceptance-evidence collection** (the 12-row runbook;
  closes the 25 consolidated deferred items). Not a Claude task.
- **Real installer** (MSI / signed bundle / auto-update). R3.2 ships
  a documented install procedure only; a real installer is a future
  slice.
- **Per-host Lisp divergence** in
  `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` — per S10
  §3.I, only justified if real ZWCAD / GstarCAD acceptance runs
  expose `_PKSER` / `ACADVER` differences. Single-source is the
  R3.2 contract.
- **Audit-hardening / S6+ logging slice** — the S6 `/diff/preview`
  `pull_id` correlation is correctly wired in main per
  `HelperRuntime.cs:2588`; the S6 contract test at
  `HelperBusinessAuditContractTests.cs:123` pins it. No carry-forward
  gap from R3.2 itself. Any future audit hardening is a separate
  opt-in.
- **Deferred CAD pool R2 candidate** — 4 entry conditions unchanged
  per the Odoo18 R2 portfolio closeout
  (`docs/DEV_AND_VERIFICATION_ODOO18_R2_PORTFOLIO_CLOSEOUT_20260516.md`):
  dedicated owner opt-in, concrete operational driver, decide
  reuse-vs-new P6 CircuitBreaker, non-prod env to rehearse
  concurrency/failover.

## 11. Cycle-complete language

> **R3.2 is closed pending the acceptance-evidence runbook
> execution.**

The R3.2 design `:1067` work-breakdown row reads *"S11 | 集成 + 验收
测试 + 文档 | 2 天"* (Integration + acceptance test + documentation,
2 days). S11 implementation delivers the **integration documentation
+ acceptance-test runbook** portion. The runbook's actual execution
against real CAD hosts is the operator-side signoff that converts
"closed pending" into "fully closed". That signoff is intentionally
**outside** the S11 implementation PR per §8 explicit non-goal.

R3.2 is done; the runbook awaits the operator.
