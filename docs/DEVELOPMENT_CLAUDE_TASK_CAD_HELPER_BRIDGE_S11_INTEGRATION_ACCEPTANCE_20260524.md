# Claude Taskbook: CAD Helper Bridge S11 - Integration, Acceptance Evidence, and R3.2 Closeout

Date: 2026-05-24

Type: **Doc-only taskbook.** Specifies the contract a later, separately
opted-in implementation PR will deliver. Merging this taskbook does
NOT authorize that implementation. S11 is itself an evidence-and-docs
slice — its later implementation PR adds no runtime, no schema, no
helper Kestrel routes, no new ErrorCodes, no S-numbered code surfaces
beyond what S1-S10 already shipped.

## 1. Purpose

CAD Desktop Helper Bridge **S11-R1** is the closeout slice for the
R3.2 program. Per the R3.2 design `:1067` and `:1071`, S11 owns:

- bundle / packaging composition for the desktop deliverable;
- installation runbook for the four ship artifacts (`yuantus-cad-detector.exe`,
  `yuantus-cad-helper.exe`, `YuantusCadHelperBridge.dll`,
  `yuantus_cad_helper.lsp`) plus the existing AutoCAD plugin
  (`CADDedupPlugin`);
- the consolidated real-host acceptance-evidence runbook covering the
  12 manual tests in R3.2 design `:810-825`;
- the consolidated deferred-operational-signoff collection from
  S7 §4.1, S8 §3.J, S9 §4.1, and S10 §4.1;
- the final R3.2 closeout report (cycle-complete status, anti-drift
  guards, follow-up owner list).

S11-R1 is explicitly a **documentation + evidence-runbook** slice. The
12 acceptance evidence items are NOT collected inside the
implementation PR; the PR ships the runbook that the operator
executes offline (see §3.C and §8). This wording matches the §3.C
runbook-shape contract and the §8 explicit non-goal that prohibits
in-PR evidence collection.
It does **not**:

- add Lisp commands beyond the merged S10 `C:YUANTUS_DIFF_PREVIEW`;
- add helper Kestrel routes beyond the merged ten (`/healthz`,
  `/version`, `/session/{login,logout,status}`, `/cad/current-drawing`,
  `/diff/preview`, `/sync/{inbound,outbound}`, `/audit/apply-result`);
- modify the S9 `Bridge/` source, S6 audit substrate, S5 session model,
  S4 security gate, S3 startup, S1 Shared, or S2 Detector;
- introduce a real installer (MSI / package format) beyond a documented
  install procedure;
- introduce new `ErrorCodes` constants;
- introduce CORS, Python FastAPI changes, or schema / migration / tenant
  baseline edits.

Prerequisites already merged:

- #614 `fff93a2`: CAD helper bridge R3.2 design.
- #616 `bd61af2` + #617 `2740865`: S1 Shared.
- #618 `db1d3de`: S2 Detector.
- #619 `13bf4d2` + #620 `e0c76e8`: S3 Helper startup.
- #621 `91e71f7` + #622 `dce38c0`: S4 Auth / origin allowlist.
- #623 `d40e76f` + #624 `c500398`: S5 Session routes.
- #625 `3b92dad` + #626 `ab31df5`: S6 Business + audit.
- #627 `2be62a5` + #628 `431b6adf`: S7 Reset-token CLI.
- #629 `a69ae656` + #630 `90d80c55`: S8 MaterialSync migration.
- #631 `349ec48d` + #632 `be290cab`: S9 NETLOAD Lisp bridge.
- #633 `de365c01` + #634 `4662dbaf`: S10 ZWCAD/GstarCAD Lisp shell.

## 2. Grounded Current Reality

Grounded against `origin/main = 4662dbaf` after S10 merged.

### 2.1 R3.2 design closeout anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S11
surface:

- Line 1067 (work-breakdown table): *"S11 | 集成 + 验收测试 + 文档 | 2 天"*.
- Line 1071 (slice dependencies): *"S11 收尾"* — S11 is the closeout.
- Lines 776-808 (CI 真机手测 acceptance test list):
  - 22 GitHub Actions Windows-runner tests (already covered by the
    dedicated `cad-helper-shared-dotnet` workflow's existing test
    targets across Shared.Tests, Detector.Tests, Helper.Tests,
    Bridge.Tests, plus `verify_bridge_static.py` and the merged
    `verify_lisp_shell_static.py`);
  - 12 manual real-host evidence rows (lines 810-825) — the canonical
    operational acceptance set S11 must collect.
- Lines 983-1009 (reference graph): the four ship artifacts and their
  dependency layout; multi-target Shared `net46;net6.0-windows`
  carrying both AutoCAD-host (`net46`) and helper-runtime
  (`net6.0-windows`) consumers; explicit non-conflict of the two
  loaded copies inside acad.exe + yuantus-cad-helper.exe per
  acceptance test 12.

### 2.2 Slice-by-slice deferred-signoff carry-forward

S11 consolidates the deferred operational evidence the prior slices
recorded honestly (each marked as owner-accepted risk, not collected
in CI):

- **S7 #628 §4.1** — 5 items: PowerShell y/n cancel paths, running-helper
  refusal, SSH/WinRM/RDP refusal, post-reset CAD re-auth.
- **S8 #630 §3.J / §5** — 5 items: AutoCAD 2018 build of
  `CADDedupPlugin.csproj`, AutoCAD load of `CADDedup.bundle`,
  PLMMATPUSH routes through helper `/sync/inbound`, PLMMATPULL routes
  through helper `/diff/preview` + writes CAD fields + posts
  `/audit/apply-result`, helper audit DB contains `/diff/preview` +
  `/audit/apply-result` rows.
- **S9 #632 §4.1** — 7 items: Windows + AutoCAD/ZWCAD/GstarCAD NETLOAD,
  bridge DLL loads without missing deps, `(yuantus-helper-call ...)`
  starts/finds helper, success returns JSON, failure returns nil +
  sanitized line, no token in CAD command-line output, S10-paired
  display-only `/audit/apply-result not-applied-display-only` row.
- **S10 #634 §4.1** — 8 items: real ZWCAD + GstarCAD load of the .lsp,
  `YUANTUS_DIFF_PREVIEW` available at command line, prompts accept
  input, `(yuantus-helper-call "/diff/preview" ...)` returns JSON,
  displayed lines via production CAD command-line writer, no DWG
  mutation, `audit.db` row with correct outcome + pull_id, pull_id
  cross-row correlation between `/diff/preview` and `/audit/apply-result`.

### 2.3 The 12-item canonical acceptance list (design `:810-825`)

The R3.2 design's manual evidence table is the authoritative list S11
must cover. Verbatim from `:810-825`:

| # | Test | Slice(s) most exercised |
|---|---|---|
| 1 | Windows 11 + AutoCAD 2018: Shared net46 integrated into `CADDedupPlugin`, `PLMMATPULL` → helper auto spawn → `/diff/preview` returns `write_cad_fields` → `CadMaterialFieldService` writes DWG → `/audit/apply-result` lands in `audit.db` | S1 + S3 + S6 + S8 |
| 2 | AutoCAD 2018: `PLMMATPUSH` → helper `/sync/inbound` forwarded to server → server returns updated action → audit row written | S6 + S8 |
| 3 | Procmon recording proves detector does zero registry writes (.pml archived) | S2 |
| 4 | LAN: another machine accesses `http://<host-lan-ip>:7959/healthz` → rejected (loopback-only binding) | S3 |
| 5 | Non-allowlisted process (`curl.exe`) → `403 ORIGIN_PROCESS_NOT_ALLOWED` | S4 |
| 6 | helper `taskkill` → stale `helper-session-{sessionId}.json` → next startup R3.2 §5.1 step 5/6 cleanup works | S3 |
| 7 | Idle 30-minute auto-exit → current-session session file cleaned | S3 |
| 8 | helper + existing `CADDedupPlugin` run together 30 minutes, no port/Mutex conflict, no leak | S6 + integration |
| 9 | ZWCAD real-host: install LISP shell + `YuantusCadHelperBridge.dll` (.NET Framework v4.6) → run `YUANTUS_DIFF_PREVIEW` → command line shows `write_cad_fields` JSON, no DWG write, `/audit/apply-result` records `not-applied-display-only` | S9 + S10 |
| 10 | `--reset-local-token` in PowerShell: prompt → user `y` → DPAPI token replaced → existing AutoCAD session's next `PLMMATPULL` automatically picks up new token | S7 |
| 11 | `--reset-local-token` from SSH / WinRM → rejected with exit code 1 | S7 |
| 12 | AutoCAD 2018: Shared loaded both as net46 (inside acad.exe) and as net6.0-windows (inside helper.exe) without runtime conflict | S1 |

### 2.4 Ship artifacts (per design `:983-1009`)

S11 consolidates the four R3.2 deliverables that ship together:

- `clients/cad-desktop-helper/Detector/` → `yuantus-cad-detector.exe`
  (net6.0 self-contained Windows binary);
- `clients/cad-desktop-helper/Helper/` → `yuantus-cad-helper.exe`
  (net6.0 self-contained Windows binary with `--reset-local-token`
  subcommand);
- `clients/cad-desktop-helper/Bridge/` → `YuantusCadHelperBridge.dll`
  (.NET Framework v4.6 NETLOAD assembly for ZWCAD/GstarCAD);
- `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` (AutoLISP
  shell loaded via `(load ...)` in ZWCAD/GstarCAD).

Plus the existing AutoCAD plugin slice consumed by AutoCAD 2018/2024:

- `clients/autocad-material-sync/CADDedupPlugin/` → `CADDedupPlugin.bundle`
  (.NET Framework v4.6 net46 / v4.8 net48 multi-config).

## 3. S11-R1 Decisions And Boundaries

### 3.A Documentation-and-evidence slice; no runtime

S11-R1 implementation owns exactly these new doc / runbook surfaces:

- `docs/CAD_HELPER_BRIDGE_R3_RELEASE_NOTES_20260524.md` — what changed,
  what shipped, what is deferred, where to look for each prior slice's
  DEV/Verification MD;
- `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` — step-by-step
  install procedure for the four ship artifacts, ordered so DPAPI
  bootstrap, session file, and singleton mutex semantics work first-time;
- `docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md` —
  the consolidated 12-item manual evidence runbook from §2.3 above,
  with one entry per row specifying: required environment, exact CAD
  / PowerShell / curl commands to run, expected observable outcome,
  evidence-artifact format (`.pml` for Procmon, `.png` for screenshots,
  `.txt` for command-line transcripts, SQL excerpt for `audit.db`
  rows), and where the artifact should be archived;
- `docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` — final
  cycle-complete report citing every slice's merge commit and listing
  remaining follow-up owner items (the deferred-signoff packet plus
  any audit-hardening or S6.5+ logging slice still owed);
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_R1_20260524.md`
  — the standard DEV/Verification MD recording what the S11
  implementation PR actually delivered;
- one `docs/DELIVERY_DOC_INDEX.md` entry per new MD above.

S11 implementation must **not** edit:

- any `clients/cad-desktop-helper/{Shared,Detector,Helper,Bridge,Lisp}/`
  source;
- `clients/autocad-material-sync/CADDedupPlugin/` source;
- helper Kestrel route declarations;
- the S6 audit substrate or `audit_events` schema;
- the S9 bridge contract;
- the S10 Lisp shell source;
- the existing test projects (`Shared.Tests`, `Detector.Tests`,
  `Helper.Tests`, `Bridge.Tests`);
- the existing static verifiers (`verify_bridge_static.py`,
  `verify_lisp_shell_static.py`, `verify_material_sync_static.py`);
- Python FastAPI server source;
- schema / migration / tenant-baseline data;
- `.github/workflows/cad-helper-shared-dotnet.yml` (no new path filters
  needed — S11 deliverables are doc-only and the existing `contracts`
  CI workflow already covers doc-index drift).

### 3.B Install runbook contract

The R3 install runbook must pin the load order so DPAPI / mutex /
session-file semantics work first-time on a clean Windows user
profile:

1. install `yuantus-cad-helper.exe` to
   `%APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe`;
2. install `yuantus-cad-detector.exe` alongside (no DPAPI dependency);
3. install `YuantusCadHelperBridge.dll` (NETLOAD path per ZWCAD/GstarCAD
   bundle convention) plus `yuantus_cad_helper.lsp` to a
   user-resolvable location for `(load ...)`;
4. install / register `CADDedupPlugin.bundle` per the existing AutoCAD
   plugin packaging (S8-shipped);
5. first-launch handshake: a CAD plugin or operator-triggered command
   spawns the helper, which bootstraps the local-helper-token via S3
   `LocalTokenBootstrapper` (DPAPI write), publishes the session file,
   and starts Kestrel on a 7959-7999 loopback port.

The runbook must also document the operator-side `--reset-local-token`
recovery path per S7 §3.B if local-token validation drift occurs.

### 3.C Acceptance evidence runbook structure

For each of the 12 acceptance items in §2.3, the runbook entry must
specify:

- **slice attribution** — which prior slice(s) this row exercises;
- **required environment** — exact Windows version, CAD version
  (e.g., AutoCAD 2018 baseline, AutoCAD 2024, ZWCAD 2025, GstarCAD 2025),
  required tools (Procmon for #3, `curl.exe` for #5, PowerShell + SSH
  client for #10/#11);
- **setup steps** — how to install the four ship artifacts on the
  evidence host;
- **execution steps** — the exact CAD / shell commands to run, in
  order, with expected user input at any prompt;
- **expected observable outcome** — verbatim from R3.2 design `:810-825`;
- **evidence artifact** — what to capture (.pml file, screenshot,
  command-line transcript, `audit.db` SQL excerpt, etc.) and where to
  archive it;
- **signoff slot** — operator name + date + artifact path.

The runbook is NOT a CI-runnable test list. It is the operator's
ratified evidence-collection procedure that closes the §4.1 deferred
signoffs from S7/S8/S9/S10.

### 3.D Closeout report contract

`CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` must record:

- the canonical R3.2 slice ledger (taskbook + implementation commit
  hashes for every slice from S1 through S11);
- the final production helper Kestrel route count (10) with the
  precise route list;
- the four ship artifacts with their build configurations;
- known follow-up obligations that are NOT part of R3.2:
  - acceptance-evidence collection (deferred to operational signoff
    per S7/S8/S9/S10 §4.1 — S11 ships the runbook, the operator runs
    it);
  - any future audit-hardening slice (S6 `/diff/preview` `pull_id`
    correlation is currently wired in main per `HelperRuntime.cs:2588`,
    so no carry-forward; the slice is closed);
  - the deferred CAD pool R2 candidate (4 entry conditions unchanged);
  - any `_PKSER` / `ACADVER` per-host divergence in the Lisp shell
    (deferred per S10 §3.I until a real host shows a need);
- explicit cycle-complete language: *"R3.2 is closed pending the
  acceptance-evidence runbook execution."*

### 3.E No new runtime, no new routes, no new ErrorCodes

S11-R1 implementation must **not** introduce:

- new helper Kestrel routes (route count stays 10);
- new Lisp commands (count stays 1: `C:YUANTUS_DIFF_PREVIEW`);
- new `ErrorCodes` constants;
- new `verify_*_static.py` source guards;
- new `xUnit` test projects;
- new bridge primitive functions;
- new S9 / S10 source under `Bridge/` or `Lisp/`;
- changes to `cad-helper-shared-dotnet.yml`.

S11 is purely additive in `docs/`. The existing CI surface continues
to cover prior-slice contracts unchanged.

### 3.F Hygiene side-touches permitted in the S11 PR

The S11 implementation PR may carry one explicitly-named doc-only
hygiene fix in the same PR:

- the S10 DEV/Verification MD count drift identified post-merge of
  #634 (the verifier currently has 20 guards, but the DEV MD at
  `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md:178`
  and `:219` still says 19). The S11 taskbook PR or the S11
  implementation PR — whichever lands first — may correct that count
  in-place. No source file is modified.

Other hygiene items are out of S11 scope and require their own PR or
their own slice.

### 3.G CI / workflow posture

Doc-only PRs do not trigger `cad-helper-shared-dotnet`; they trigger
only the `contracts` CI workflow (doc-index drift, R2 portfolio,
Tier-B drift). S11 implementation PR is doc-only and follows that
posture.

If the S11 implementation PR ends up needing any code path filter
(e.g., a `tools/operator-evidence/` directory with helper scripts),
that is OUT of S11-R1 scope and must be a separate slice.

### 3.H S11 itself has no deferred operational evidence

Unlike S7/S8/S9/S10, S11 does not introduce any new production
seam. The deferred operational evidence S11 consolidates belongs to
S7/S8/S9/S10 — S11 just owns the runbook that the operator executes
to clear those packets.

The S11 implementation PR's DEV/Verification MD will record:

- the new doc files;
- the local doc-index drift / R2 portfolio / Tier-B drift test results;
- a pointer to the consolidated evidence runbook;
- an explicit statement that S11 does **not** introduce any
  deferred-signoff items of its own.

## 4. R1 Target Output

The S11 implementation PR should contain:

- `docs/CAD_HELPER_BRIDGE_R3_RELEASE_NOTES_20260524.md`
- `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md`
- `docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md`
- `docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md`
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_R1_20260524.md`
- doc-index entries for each new MD (5 new lines in
  `docs/DELIVERY_DOC_INDEX.md`, lexically sorted)
- (optional) S10 DEV MD count drift in-place fix per §3.F

The S11 implementation PR must **not** contain:

- code edits under `clients/`;
- workflow YAML edits;
- new ErrorCodes;
- new Lisp commands;
- new helper routes;
- new static verifiers;
- changes to S6 audit substrate / `audit_events` schema.

## 5. Mandatory Tests And Guards

S11 has no `xUnit` mandatory tests; its mandatory checks are
documentation-shape guards plus the existing doc-index drift suite.

The S11 implementation PR must pass:

1. `test_delivery_doc_index_references` (existing) — each new MD is
   referenced from `docs/DELIVERY_DOC_INDEX.md`;
2. `test_dev_and_verification_doc_index_completeness` (existing) — the
   new S11 DEV/Verification MD is indexed;
3. `test_dev_and_verification_doc_index_sorting_contracts` (existing) —
   the 5 new entries are lexically sorted in `DELIVERY_DOC_INDEX.md`;
4. `test_odoo18_r2_portfolio_contract` + `test_tier_b_3_breakage_design_loopback_portfolio_contract`
   — must remain unchanged-passing because S11 does not touch the
   portfolio surfaces.

S11 implementation PR's DEV/Verification MD must explicitly assert:

- exactly 5 new `docs/*.md` files added;
- zero file edits outside `docs/` (except the optional §3.F S10 MD
  hygiene touch);
- helper route count after S11 = 10 (verified by inspection of
  `HelperRuntime.cs` `MapGet(` + `MapPost(` lines);
- S9 bridge sources unchanged (verified by inspection of
  `Bridge/SharedBridgeLocator.cs` + `Bridge/SharedBridgeTransport.cs`);
- S10 Lisp shell source unchanged (verified by inspection of
  `Lisp/yuantus_cad_helper.lsp`);
- S7/S8/S9/S10 DEV/Verification MDs unchanged except the §3.F S10
  hygiene line if elected;
- the 12 acceptance-evidence rows from R3.2 design `:810-825` are
  each represented in the runbook with the §3.C structure;
- the closeout report cites the canonical S1-S11 slice ledger.

## 6. Verification Plan

Local doc-contract checks (this taskbook PR + the later implementation
PR):

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

S11 does not require `.NET build/test`. The `cad-helper-shared-dotnet`
workflow is path-filtered on code paths the S11 PR will not touch, so
it correctly skips. The `contracts` CI workflow is the authoritative
gate for the doc-only PR.

## 7. DEV / Verification MD Requirements

The later S11 implementation PR must add
`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S11_INTEGRATION_ACCEPTANCE_R1_20260524.md`
containing:

- the 5 new MDs and their roles;
- explicit "no code edits" claim with the inspection results from §5;
- the §3.F hygiene line if elected (the S10 DEV MD count drift fix);
- the final slice ledger (S1-S11 with commit hashes);
- the §3.C runbook structure;
- the S7/S8/S9/S10 §4.1 deferred-signoff packet consolidation;
- the §3.H statement that S11 itself adds no deferred-signoff items;
- local doc-index drift / R2 / Tier-B results;
- `contracts` CI run URL / run id.

## 8. Explicit Non-Goals

- No runtime code changes anywhere under `clients/`.
- No helper Kestrel route changes — route count stays 10.
- No Lisp command additions — count stays 1 (`C:YUANTUS_DIFF_PREVIEW`).
- No `ErrorCodes` constant additions.
- No `xUnit` test project additions.
- No `verify_*_static.py` additions.
- No workflow YAML changes.
- No real installer (MSI / `.exe` packaging) — S11 ships only the
  documented install procedure; a future installer slice is a separate
  opt-in.
- No collection of the 12 acceptance evidence items in this PR — S11
  ships the runbook, the operator executes the runbook offline.
- No re-litigation of prior-slice contracts. Bug fixes to merged code
  are not S11 scope; each requires its own slice.
- No Python FastAPI server changes.
- No schema / migration / tenant-baseline edits.
- No CAD pool R2 work (still deferred, 4 entry conditions unchanged).
- No audit-hardening slice (S6 pull_id correlation is already wired
  in main; no carry-forward gap).

## 9. Recommended Branch For Implementation

After this taskbook merges and only after a separate explicit opt-in,
use:

```text
feat/cad-helper-bridge-s11-integration-acceptance-r1-20260524
```

(The `feat/` prefix is by convention for implementation branches even
though S11 is doc-only — to match the S7-S10 pattern.)

Do not start the S11 implementation from this taskbook PR.

## 10. Reviewer Focus

Please review these points before merge:

1. Confirm S11-R1 is purely a documentation-and-evidence-runbook slice
   (ships the runbook only; operator executes it offline) — no
   runtime, no routes, no ErrorCodes, no static verifiers, no test
   projects.
2. Confirm the four ship artifacts in §2.4 are exactly the R3.2
   deliverables and no more.
3. Confirm the 12-item acceptance list in §2.3 matches R3.2 design
   `:810-825` verbatim, including the slice attribution per row.
4. Confirm §2.2's deferred-signoff carry-forward correctly enumerates
   the S7 (5) + S8 (5) + S9 (7) + S10 (8) packets without
   omission or duplication.
5. Confirm §3.A's "no code edits" boundary is enforced by the
   mandatory checks in §5.
6. Confirm §3.F's permitted hygiene side-touch (the S10 DEV MD count
   drift fix from 19 → 20) is correctly bounded — exactly that line
   range, nothing else.
7. Confirm the §3.D closeout report's "R3.2 is closed pending the
   acceptance-evidence runbook execution" framing accurately reflects
   the deferred-signoff posture (not "complete", but "closed pending
   operator evidence").
8. Confirm S11 introduces no new deferred-signoff items of its own
   (per §3.H).

## 11. Status

This taskbook is ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it;
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
