# Dev & Verification: CAD Helper Bridge S11 — Integration + Acceptance Evidence Closeout (R1)

Date: 2026-05-24

Implementation record for **S11 R1** of the CAD Desktop Helper Bridge
R3.2 program, against the contract pinned in
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_20260524.md`
(squash-merged 2026-05-24 as #635 `b2c18d07`).

## 1. Type and scope

S11 R1 is a **documentation + evidence-runbook** slice (per taskbook
§1 + §3.A). The implementation PR ships docs only — no runtime,
no schema, no helper Kestrel routes, no new ErrorCodes, no new Lisp
commands, no new static verifiers, no workflow yaml changes.

The 12 acceptance-evidence items per the R3.2 design `:810-825` are
**not** collected in this PR. The PR ships the runbook; the operator
executes the runbook offline (per taskbook §3.C + §8).

## 2. Files added (exactly 5 docs + 5 doc-index lines)

| Path | Role |
|---|---|
| `docs/CAD_HELPER_BRIDGE_R3_RELEASE_NOTES_20260524.md` | What shipped, ship-artifact list, slice ledger, helper route table, Lisp command list, ErrorCodes, CI surface, follow-ups |
| `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` | Operator install procedure for the 4 ship artifacts + AutoCAD plugin, in the order DPAPI / mutex / session-file semantics require |
| `docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md` | The 12-item operator acceptance-evidence runbook per design `:810-825`, with one entry per row in the §3.C structure (env / setup / execution / expected / artifact / signoff) |
| `docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` | Final cycle-complete report: canonical S1-S11 slice ledger, route table snapshot, 4 ship artifacts, deferred-signoff consolidation, follow-up owner list, "closed pending acceptance-evidence runbook execution" closeout language |
| `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_R1_20260524.md` | This doc — the standard DEV/Verification MD recording exactly what S11 R1 implementation delivered |

Plus 5 lines appended (lexically sorted) to
`docs/DELIVERY_DOC_INDEX.md`.

Files NOT touched (boundary enforcement per §3.A + §4):

- any `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`
  source;
- `clients/autocad-material-sync/CADDedupPlugin/` source;
- `clients/cad-desktop-helper/Helper/HelperRuntime.cs` (helper Kestrel
  route declarations — verified by inspection in §3);
- the S6 audit substrate or `audit_events` schema;
- the S9 bridge contract sources;
- the S10 Lisp shell source;
- the existing test projects (`Shared.Tests`, `Detector.Tests`,
  `Helper.Tests`, `Bridge.Tests`);
- the existing static verifiers (`verify_bridge_static.py`,
  `verify_lisp_shell_static.py`, `verify_material_sync_static.py`);
- Python FastAPI server source;
- schema / migration / tenant-baseline data;
- `.github/workflows/cad-helper-shared-dotnet.yml` or any other
  workflow yaml.

## 3. §5 mandatory inspection results

Per taskbook §5, the implementation PR explicitly asserts these
inspections:

### 3.1 Helper Kestrel routes (count = 10)

`grep -nE "Map(Get|Post)\(" clients/cad-desktop-helper/Helper/HelperRuntime.cs`
at the time of this PR returns exactly 10 declarations:

```
2955  app.MapGet("/healthz", ...)
2962  app.MapGet("/version", ...)
2970  app.MapPost("/session/login", ...)
2982  app.MapPost("/session/logout", ...)
2993  app.MapGet("/session/status", ...)
3001  app.MapPost("/cad/current-drawing", ...)
3010  app.MapPost("/diff/preview", ...)
3019  app.MapPost("/sync/inbound", ...)
3028  app.MapPost("/sync/outbound", ...)
3037  app.MapPost("/audit/apply-result", ...)
```

S11 does not edit `HelperRuntime.cs`; the route table is unchanged
from post-S10 state.

### 3.2 Lisp commands (count = 1)

`grep -nE "^\(defun c:" clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp`
at the time of this PR returns exactly one declaration:

```
182  (defun c:yuantus_diff_preview (...)
```

S11 does not edit the Lisp source; the command count is unchanged
from post-S10 state.

### 3.3 S9 bridge sources unchanged

`ls clients/cad-desktop-helper/Bridge/*.cs` at the time of this PR
returns the post-S9 source list intact:

```
clients/cad-desktop-helper/Bridge/BridgeCallService.cs
clients/cad-desktop-helper/Bridge/BridgeResult.cs
clients/cad-desktop-helper/Bridge/ConsoleBridgeCommandLineWriter.cs
clients/cad-desktop-helper/Bridge/EndpointValidator.cs
clients/cad-desktop-helper/Bridge/IBridgeCommandLineWriter.cs
(plus other S9-shipped files; full list unchanged)
```

S11 does not edit any file under `Bridge/`.

### 3.4 S10 Lisp shell source unchanged

`wc -l clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` returns
**259** lines (the S10 #634 merged value). S11 does not edit this
file.

### 3.5 S7 / S8 / S9 / S10 DEV / Verification MDs unchanged

The §3.F-permitted hygiene side-touch (the S10 DEV MD "19 vs 20"
guard-count drift) is **not** applied in this PR because the drift
was already converged inside the S11 taskbook PR (#635 `b2c18d07`).
The S10 DEV MD already reads "20 static guards" on `main`; no further
change is needed.

S7 / S8 / S9 / S10 DEV / Verification MDs are unchanged.

## 4. §3.C runbook structure

For each of the 12 acceptance items per design `:810-825`, the
runbook (`docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md`)
specifies all six required fields per row:

- slice attribution;
- required environment;
- setup steps;
- execution steps;
- expected observable outcome (verbatim from taskbook §2.3, which is
  the ratified English translation of design `:810-825`);
- evidence artifact (format + suggested archive filename);
- signoff slot (operator + date + archive path).

A 13th "operator closeout summary" row collects the 12 signoffs into
one closeout table.

## 5. Consolidated deferred-signoff packet (S7 + S8 + S9 + S10)

Per §3.D, the closeout report enumerates the 25 deferred items the
runbook clears:

- **S7** (5 items, from
  `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7_RESET_LOCAL_TOKEN_R1_20260522.md`
  §4.1): PowerShell y/n cancel paths, running-helper refusal,
  SSH / WinRM / RDP refusal, post-reset CAD re-auth.
- **S8** (5 items, from
  `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S8_MATERIAL_SYNC_MIGRATION_R1_20260523.md`
  §3.J / §5): AutoCAD 2018 build of `CADDedupPlugin.csproj`, AutoCAD
  load of `CADDedup.bundle`, `PLMMATPUSH` through `/sync/inbound`,
  `PLMMATPULL` through `/diff/preview` + CAD field write +
  `/audit/apply-result`, helper audit DB rows.
- **S9** (7 items, from
  `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md`
  §4.1): Windows + AutoCAD / ZWCAD / GstarCAD NETLOAD, bridge DLL
  loads without missing deps, `(yuantus-helper-call ...)` starts /
  finds helper, success returns JSON, failure returns nil + sanitized
  line, no token in CAD command-line output, S10-paired display-only
  `/audit/apply-result not-applied-display-only` row.
- **S10** (8 items, from
  `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md`
  §4.1): real ZWCAD + GstarCAD load of `.lsp`, `YUANTUS_DIFF_PREVIEW`
  available, prompts accept input, `(yuantus-helper-call
  "/diff/preview" ...)` returns JSON, displayed lines via production
  CAD command-line writer, no DWG mutation, `audit.db` row with
  correct outcome + `pull_id`, `pull_id` cross-row correlation
  between `/diff/preview` and `/audit/apply-result`.

Total: **25 deferred items consolidated into 12 runbook rows + the
13th operator-closeout summary row**.

## 6. §3.H — S11 introduces no new deferred-signoff items

S11 does not introduce any production seam. No new helper Kestrel
route, no new Lisp command, no new bridge primitive, no new
ErrorCodes, no new static verifier, no new dotnet test project, no
workflow yaml edits.

Therefore S11 R1 has **no §4.1 deferred-signoff packet of its own**.
The consolidated packet in §5 above is entirely carry-forward from
S7 / S8 / S9 / S10.

## 7. Slice ledger (S1-S11)

| Slice | Owner | Taskbook PR / SHA | Implementation PR / SHA |
|---|---|---|---|
| R3.2 Design | Owner | — | #614 `fff93a2` |
| S1 Shared | Claude | #616 `bd61af2` | #617 `2740865` |
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

Mid-cycle hotfix PR #636 `03185519` (workflow path-trigger drift
fix) is recorded in the release notes and closeout report.

## 8. Local verification

The taskbook §6 verification plan was executed locally before
committing:

```text
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_workflow_trigger_paths_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

Result (recorded in the commit message): **all 6 test files pass, 34
test cases total; `git diff --check` clean**.

## 9. `contracts` CI

The S11 implementation PR is doc-only; the `cad-helper-shared-dotnet`
workflow is path-filtered to `clients/cad-desktop-helper/**` +
`clients/autocad-material-sync/**` + the two Python static verifiers,
so it correctly skips. `contracts` CI is the authoritative gate for
this PR.

`contracts` CI run URL: **(to be filled in once the PR is open and the
contracts run completes)**.

## 10. Status

S11 R1 implementation PR is ready for review when:

- the 5 new MDs exist at the canonical paths in §2;
- `docs/DELIVERY_DOC_INDEX.md` contains 5 new lexically-sorted entries
  pointing to the new MDs;
- local doc-index drift + R2 portfolio + Tier-B drift +
  workflow-trigger-paths drift suite all pass (§8);
- `git diff --check` clean;
- `contracts` CI is green;
- a reviewer (operator or peer) has confirmed the boundary in §2
  (no code edits) by inspecting the PR diff.

R3.2 itself moves to **"closed pending the acceptance-evidence
runbook execution"** when this PR merges. The operator's runbook
execution is the next milestone, owned outside this PR.
