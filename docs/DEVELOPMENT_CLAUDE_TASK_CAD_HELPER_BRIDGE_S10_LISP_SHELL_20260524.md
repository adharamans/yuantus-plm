# Claude Taskbook: CAD Helper Bridge S10 - ZWCAD/GstarCAD LISP Shell Commands

Date: 2026-05-24

Type: **Doc-only taskbook.** Changes no runtime, no schema, no workflow,
and no helper / bridge / plugin code. It specifies the contract a later,
separately opted-in implementation PR will deliver. Merging this taskbook
does NOT authorize that implementation.

## 1. Purpose

CAD Desktop Helper Bridge **S10-R1** adds the first native-CAD Lisp shell
command on top of the S9 NETLOAD transport bridge:

- new `clients/cad-desktop-helper/Lisp/` directory containing one
  AutoLISP-compatible source file targeting ZWCAD and GstarCAD;
- exactly one Lisp command:

```text
C:YUANTUS_DIFF_PREVIEW
```

- command body calls the S9 `(yuantus-helper-call ...)` primitive to invoke
  helper `/diff/preview`, displays the response in the CAD command line,
  and reports the result through helper `/audit/apply-result` with
  `outcome = "not-applied-display-only"`;
- ZWCAD/GstarCAD are explicitly **display-only** in R3 per design `:724`;
  no DWG writes, no entity creation, no entity modification.

S10-R1 is the native-CAD command surface that completes the
`YuantusCadHelperBridge.dll` → helper round-trip evidence path defined by
R3.2 acceptance test 9 (`:822`). It does **not** implement other Lisp
commands (push, outbound, dedup, etc.), does **not** modify helper Kestrel
routes, does **not** change S6 audit semantics, does **not** modify the
S9 bridge DLL, and does **not** introduce S11 integration packaging.

Prerequisites already merged:

- #614 `fff93a2`: CAD helper bridge R3.2 design.
- #616 `bd61af2` + #617 `2740865`: S1 Shared.
- #618 `db1d3de`: S2 Detector.
- #619 `13bf4d2` + #620 `e0c76e8`: S3 Helper startup.
- #621 `91e71f7` + #622 `dce38c0`: S4 Auth/origin allowlist.
- #623 `d40e76f` + #624 `c500398`: S5 Session routes.
- #625 `3b92dad` + #626 `ab31df5`: S6 Business + audit.
- #627 `2be62a5` + #628 `431b6adf`: S7 Reset-token CLI.
- #629 `a69ae656` + #630 `90d80c55`: S8 MaterialSync migration.
- #631 `349ec48d` + #632 `be290cab`: S9 NETLOAD Lisp transport bridge.

## 2. Grounded Current Reality

Grounded against `origin/main = be290cab` after S9 merged.

### 2.1 R3.2 design anchors

`docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` defines the S10 surface:

- Line 110 says the native-CAD route uses `YuantusCadHelperBridge.dll` plus a
  Lisp shell, with v4.6 + Shared net46.
- Line 119 says `CADDedupPlugin` does **not** go through the Lisp bridge;
  the Lisp shell is for ZWCAD/GstarCAD only.
- Lines 699-724 define the S9 NETLOAD bridge interface that S10 must
  consume via `(yuantus-helper-call "<endpoint>" "<json-request-string>")`.
- Line 724 explicitly states that DWG field writing in non-AutoCAD CAD is
  out of R3 scope: *"DWG 字段写入由各 CAD 自己的 .NET 适配负责。AutoCAD =
  现有 `CadMaterialFieldService.cs`；ZWCAD/GstarCAD R3 阶段**不做**"*.
- Line 822 (acceptance test 9) ratifies the canonical S10 round-trip:
  ZWCAD true-machine load of the Lisp shell plus the `YuantusCadHelperBridge.dll`,
  run `YUANTUS_DIFF_PREVIEW`, display `write_cad_fields` JSON in the CAD
  command line, do **not** auto-write DWG, and record
  `/audit/apply-result` with `outcome = "not-applied-display-only"`.
- Line 1066 (work-breakdown table) defines S10 as
  *"ZWCAD/GstarCAD LISP 瘦壳：`YUANTUS_DIFF_PREVIEW` 等命令 + 命令行展示
  （不写 DWG） | 1 天"*.
- Line 1071 (slice dependency) confirms S10 depends on S9.

### 2.2 S9 bridge contract inherited by S10

S9 (`be290cab`) provides the exact Lisp primitive S10 must consume:

```text
(yuantus-helper-call "<endpoint>" "<json-request-string>")
  → "<json-data-payload-string>"  ; helper success: serialized data field
  → "null"                         ; helper success with missing / JSON-null data
  → nil                            ; bridge or helper failure; sanitized error
                                   ;   line already on CAD command line
```

S10 must reuse this primitive verbatim. It must **not** add a second Lisp
function, must **not** open its own HTTP connection, and must **not** read
DPAPI tokens directly. The S9 NETLOAD bridge is the single transport
egress for native-CAD Lisp.

S9 endpoint validation (taskbook §3.C) rejects absolute schemes, network
paths, backslashes, percent-encoding, and control characters. S10 must
pass helper-relative paths starting with a single `/` only.

### 2.3 S6 audit semantics inherited by S10

S6 (`ab31df5`) accepts these `/audit/apply-result` outcomes per merged
contract §3.K:

- `ok`
- `partial`
- `failed`
- `not-applied-display-only`

S10's display-only command exclusively uses `outcome = "not-applied-display-only"`
to mark the CAD-side action as "diff was previewed in command line but no
DWG write was attempted". This matches design `:822` acceptance test 9 and
keeps the SQLite `audit_events` schema unchanged.

S10 must **not** submit `outcome = "error"` to `/audit/apply-result`; per S6
§3.K that enum value is reserved for helper-created audit-failure rows.

### 2.4 Production helper route count inherited by S10

After S8 + S9 merged, production helper Kestrel routes remain exactly ten:

```text
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

S10 is a CAD-host Lisp slice. It must **not** add or remove helper Kestrel
routes. The production route count remains exactly ten after S10.

### 2.5 ZWCAD / GstarCAD AutoLISP compatibility

Both ZWCAD and GstarCAD support AutoLISP-compatible syntax (`defun`,
`princ`, `getstring`, `getvar`, `vl-string-search`, etc.) and load `.lsp`
files via standard `(load "...")` mechanism. The S9 bridge's
`(yuantus-helper-call ...)` is registered as a global Lisp function via
the AutoCAD `[LispFunction]` attribute (which ZWCAD/GstarCAD support via
their AutoCAD-compatible runtime).

Both hosts also implement the AutoLISP convention `C:<command-name>` for
typeable commands — a function `(defun c:yuantus_diff_preview (...))`
becomes the command `YUANTUS_DIFF_PREVIEW` at the CAD command line.

R3.2 acceptance test 9 explicitly targets ZWCAD; GstarCAD is the parallel
target per design `:1066`. S10-R1 commits to a single AutoLISP-compatible
source file that works on both, **not** per-host variant files. Per-host
divergence is deferred to a later slice if any host requires it.

### 2.6 Current repository state

There is no `clients/cad-desktop-helper/Lisp/` directory and no
`*.lsp` file anywhere in `clients/cad-desktop-helper/`. The S10
implementation creates the directory and the file.

The S9 static verifier already enforces that `.lsp` files do **not** exist
under `clients/cad-desktop-helper/Bridge/`. S10 places its file outside
`Bridge/` (under a sibling `Lisp/` directory), so S9's guard remains true
post-S10.

### 2.7 Lisp-side JSON realities

AutoLISP has no native JSON parser. S10 chooses a deliberately minimal
approach:

- **JSON building**: hand-construct request strings with a small helper
  function that string-concatenates `{"item_id":"...","profile_id":"..."}`
  shapes, escaping embedded quotes and backslashes;
- **JSON parsing**: extract only the fields S10-R1 needs, via narrow
  string operations (search for `"pull_id":"` and read the next quoted
  value);
- **Display**: print the helper `data` JSON string raw to the CAD command
  line via `(princ ...)`. Pretty-printing in Lisp is out of scope.

This is acceptable for R1 because S10-R1 is explicitly display-only and
needs only `pull_id` for the follow-up `/audit/apply-result` call. A
proper Lisp JSON parser would be a separate enhancement slice.

## 3. S10-R1 Decisions And Boundaries

### 3.A LISP source layout

S10-R1 owns exactly these new paths:

```text
clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp
clients/cad-desktop-helper/Lisp.Tests/test_yuantus_cad_helper_static.py
```

Single AutoLISP source file shared between ZWCAD and GstarCAD. The
`Lisp.Tests/` directory contains the Python-based static verifier tests
(no separate .NET test project is required because Lisp files are
verified by Python source-pattern checks, not xUnit). If the implementer
prefers, the static verifier may live alongside the existing
`clients/cad-desktop-helper/verify_bridge_static.py`; both layouts are
acceptable as long as the workflow path filter triggers correctly.

S10-R1 must **not** add per-host variant files
(`yuantus_cad_helper_zwcad.lsp`, `yuantus_cad_helper_gstarcad.lsp`, etc.)
unless the implementation PR demonstrates a real host-incompatibility
that cannot be resolved with `(if (vl-string-search "zwcad" (getvar "PROGRAM")) ...)`.

### 3.B Lisp commands

S10-R1 exposes exactly one Lisp command:

```text
C:YUANTUS_DIFF_PREVIEW
```

Defined as:

```lisp
(defun c:yuantus_diff_preview (/ <locals>) ...)
```

The lower-case `c:yuantus_diff_preview` form is the AutoLISP / ZWCAD /
GstarCAD convention for typeable commands; the CAD command line invokes
it as `YUANTUS_DIFF_PREVIEW` (case-insensitive).

S10-R1 must **not** define other `C:*` commands (`YUANTUS_SYNC_INBOUND`,
`YUANTUS_SYNC_OUTBOUND`, `YUANTUS_AUDIT_APPLY`, `YUANTUS_RESET_TOKEN`,
etc.). Those are deferred to later slices.

### 3.C Command flow (`YUANTUS_DIFF_PREVIEW`)

Ratified sequence:

1. Prompt the user for the required PLM `item_id` via `(getstring T
   "\nPLM item id: ")`. Empty input cancels.
2. Optionally prompt for `profile_id` via `(getstring T "\nProfile id
   (optional): ")`. Empty input is permitted.
3. Read the current drawing filename from `(getvar "DWGNAME")` and the
   drawing path from `(getvar "DWGPREFIX")`. Both are CAD-built-in
   variables present on AutoCAD/ZWCAD/GstarCAD.
4. Build the `/diff/preview` request body as a JSON object string with
   required `item_id`, optional `profile_id`, optional `cad_system`
   (`"zwcad"` or `"gstarcad"` selected by sniffing `(getvar "PROGRAM")`
   case-insensitively), and a `drawing` object carrying `filename` and
   `filepath`.
5. Call `(yuantus-helper-call "/diff/preview" <json-string>)`. If the
   return value is `nil`, write a sanitized one-line cancel notice via
   `(princ "\n[YUANTUS_DIFF_PREVIEW] diff preview failed (bridge already
   logged error)")` and exit without calling `/audit/apply-result`.
6. On non-`nil` return, parse the response JSON string only to extract
   `pull_id` (required). Aligned with §2.7: S10-R1 does **not** introduce
   a brace-balanced JSON extractor in Lisp; the response payload is
   displayed raw rather than navigating `server_response.write_cad_fields`
   in Lisp.
7. Display the diff in the CAD command line via `(princ ...)` calls:
   - one header line `[YUANTUS_DIFF_PREVIEW] item=<item> pull_id=<pull_id>`;
   - the **full helper `data` JSON string** verbatim (raw, no parsing;
     this is the string `(yuantus-helper-call ...)` returned, which
     already contains `pull_id` and `server_response` as nested fields
     including `write_cad_fields`);
   - one footer line `[YUANTUS_DIFF_PREVIEW] display only; no DWG write.`
8. Build the `/audit/apply-result` request body with `pull_id` from step
   6, `outcome = "not-applied-display-only"`, the same `drawing` object
   from step 3, and the same `cad_system`.
9. Call `(yuantus-helper-call "/audit/apply-result" <json-string>)`. If
   the return value is `nil`, write a sanitized one-line audit-failure
   notice `(princ "\n[YUANTUS_DIFF_PREVIEW] audit report failed (bridge
   already logged error); diff was displayed.")`. Do **not** retry.
10. Return `(princ)` so the AutoLISP REPL prints nothing after the
    explicit display lines.

The implementation may factor steps 4-5 and 8-9 into helper functions
like `(yuantus--build-diff-request ...)` and `(yuantus--parse-pull-id
...)` as long as the user-visible command boundary remains
`YUANTUS_DIFF_PREVIEW`.

### 3.D Display-only contract: NO DWG mutation

`yuantus_cad_helper.lsp` must **not** invoke any AutoLISP function that
mutates the drawing. The static verifier enforces absence of all of:

- `(entmake` / `(entmakex`;
- `(entmod` / `(entupd`;
- `(entdel`;
- `(vla-put-` (ActiveX property setter);
- `(vlax-invoke` followed by mutation methods such as `AddText`,
  `AddLine`, `Modify`, `Delete`, `Update`;
- `(command "` followed by any AutoLISP-callable command that mutates
  the database (e.g., `"TEXT"`, `"LINE"`, `"INSERT"`, `"_ERASE"`,
  `"_-PURGE"`).

If a later S10+ slice needs to write to DWG, that slice must explicitly
relax this guard.

### 3.E Display-only contract: only `(princ)` for user-visible output

`(princ)`, `(prompt)`, and `(write-line)` to standard output are the only
allowed display mechanisms. Specifically forbidden:

- `(alert "...")` modal popups;
- `(initdia)` / `(getfiled)` modal dialogs;
- `vla-` ribbon / palette manipulation;
- writing to log files via `(open ... "w")` (S10 has no logging surface);
- any modal blocking call other than the standard `(getstring)` prompts in
  §3.C steps 1-2.

The S9 bridge already prints sanitized error lines for transport failures;
S10 must **not** echo bridge error content again (no log duplication).

### 3.F Transport surface: only S9 `(yuantus-helper-call)`

S10 must invoke the S9 primitive `(yuantus-helper-call ...)` for every
external interaction. Specifically forbidden:

- direct HTTP via shell-out (`(startapp "curl" ...)`, `(command "_-SHELL"
  ...)`, etc.);
- direct file IO to `%APPDATA%\YuantusPLM\` (no DPAPI token reads, no
  session file reads);
- any other native-CAD .NET DLL load (no `(arxload)`, no `(autoarxload)`,
  no `(vl-load-com)` for HTTP/DPAPI);
- spawning `yuantus-cad-helper.exe` directly (the S9 bridge handles
  helper start/discovery).

S10 may use `(vl-load-com)` only for built-in AutoLISP ActiveX support if
strictly required for `(getvar)`, `(setvar)`, or display formatting; that
permitted use is for in-process Lisp utility only, **not** for HTTP or
file IO.

### 3.G No new helper routes, no new bridge functions

S10-R1 must **not**:

- modify the S9 `Bridge/` source;
- modify any helper Kestrel route declaration;
- add a second Lisp function alongside `(yuantus-helper-call ...)`;
- modify S6 `IPlmBusinessClient`, `HelperBusinessAuditService`, or
  `SqliteAuditEventStore`;
- modify the S6 `/audit/apply-result` schema or accepted outcome enum.

If a future slice introduces a new Lisp command that requires a new
helper route, that slice must open its own helper-side taskbook before
the Lisp slice.

### 3.H CI workflow posture

S10-R1 implementation must update `.github/workflows/cad-helper-shared-dotnet.yml`
so the dedicated Windows workflow triggers on changes under:

```text
clients/cad-desktop-helper/Lisp/**
clients/cad-desktop-helper/Lisp.Tests/**
clients/cad-desktop-helper/verify_lisp_shell_static.py
```

The workflow must run a Python static verifier (mirroring
`verify_bridge_static.py`) that checks:

- the canonical .lsp file exists at the documented path;
- exactly one `(defun c:yuantus_diff_preview ...)` definition;
- exactly one (or more, if explicitly justified) call to
  `(yuantus-helper-call "/diff/preview"`;
- exactly one call to `(yuantus-helper-call "/audit/apply-result"`;
- the literal string `"not-applied-display-only"` appears in the audit
  request construction;
- the forbidden mutation tokens in §3.D and §3.E are absent;
- parentheses and double quotes balance in the file (basic Lisp syntax
  smoke).

The workflow must **not** attempt to load the .lsp file inside a real
ZWCAD/GstarCAD process; per-S9 pattern that is operational signoff, not
CI coverage.

### 3.I Per-host divergence and PROGRAM sniffing

If S10-R1 needs to distinguish ZWCAD from GstarCAD (e.g., for different
command-line behavior or different `getvar` return shapes), it must do so
at runtime by sniffing `(getvar "PROGRAM")` (returns `"ZWCAD"` or
`"GstarCAD"` case-sensitive on respective hosts) or `(getvar
"_PKSER")` / `(getvar "ACADVER")` as backup signals.

R1 explicitly does **not** require divergent behavior: the canonical
display-only flow in §3.C works identically across ZWCAD and GstarCAD per
AutoLISP compatibility. The implementation PR must state explicitly
whether per-host divergence was introduced; if it was, each branch must
have its own operational signoff entry in §4.

### 3.J Deferred native-CAD operational evidence

Per the §3.K pattern established by S7 / S8 / S9, S10-R1 implementation
may merge with deferred operational signoff only if the PR body and
DEV/Verification MD state it plainly.

Deferred evidence must include, on **both** ZWCAD and GstarCAD where
applicable:

- the .lsp file `(load ...)`s without syntax errors;
- the `YUANTUS_DIFF_PREVIEW` command is available at the CAD command line
  after load;
- prompts for `item_id` (and optionally `profile_id`) accept user input;
- `(yuantus-helper-call "/diff/preview" ...)` starts or finds the helper
  and returns a JSON string;
- the displayed lines appear on the CAD command line without DWG mutation;
- `(yuantus-helper-call "/audit/apply-result" ...)` records a row in
  `audit.db` with `endpoint = "/audit/apply-result"`,
  `outcome = "not-applied-display-only"`, and the correct `pull_id`;
- no DWG entity was created, modified, or deleted during the command
  (verifiable by `(getvar "DBMOD")` returning 0 if zero before the
  command, or by Procmon evidence of no `.dwg` writes during the run);
- the `pull_id` from the `/diff/preview` audit row matches the `pull_id`
  in the `/audit/apply-result` audit row (correlation via the SQLite
  `idx_audit_pull` index — `HelperBusinessAuditService.DiffPreviewAsync`
  hoists the `PullCacheEntry` to outer scope and passes it into
  `WriteAuditAfterBusiness` at `HelperRuntime.cs:2588`, with the existing
  S6 contract test pinning
  `response.pull_id == audit.Events.Last().PullId` at
  `HelperBusinessAuditContractTests.cs:123`).

If this evidence is not collected in S10-R1, it remains a carried-forward
S11 or operational signoff obligation, recorded honestly as such in the
DEV/Verification MD §4.1 (mirroring the S7/S8/S9 pattern).

### 3.K No new error codes

S10-R1 introduces no new `ErrorCodes` constants. All failure surfaces
use existing codes already declared in
`clients/cad-desktop-helper/Shared/Transport/ErrorCodes.cs`:

- `HELPER_INPUT_VALIDATION_FAILED` (if the user cancels at a prompt, the
  command returns silently without calling the bridge);
- bridge / helper failure codes (returned by `(yuantus-helper-call ...)`,
  printed by the bridge — S10 does not re-emit them).

S10 does **not** add a `S10_LISP_*` error code family.

## 4. R1 Target Output

Implementation PR should contain:

- `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` — the AutoLISP
  source per §3.C, with `(defun c:yuantus_diff_preview ...)` + helper
  functions for JSON construction / extraction / `cad_system` sniffing;
- `clients/cad-desktop-helper/verify_lisp_shell_static.py` (or
  equivalent path) — the Python static verifier per §3.H;
- `.github/workflows/cad-helper-shared-dotnet.yml` updated with the new
  path filters and the static verifier step;
- `clients/cad-desktop-helper/Lisp.Tests/test_yuantus_cad_helper_static.py`
  (or the §3.H verifier covers both roles — the implementer chooses the
  cleanest split);
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md`;
- one `docs/DELIVERY_DOC_INDEX.md` line.

Implementation PR must **not** contain:

- new helper Kestrel routes;
- new Lisp commands beyond `C:YUANTUS_DIFF_PREVIEW`;
- per-host variant `.lsp` files unless §3.I justification is documented;
- modifications to `clients/cad-desktop-helper/Bridge/`;
- modifications to `Helper/`, `Shared/`, or `Detector/` runtime code;
- new `ErrorCodes` constants;
- changes to the S6 audit outcome enum;
- new pip / Python package dependencies for the verifier (use only
  standard library plus what `verify_bridge_static.py` already uses);
- AutoCAD `CADDedupPlugin` edits.

## 5. Mandatory Tests And Guards

The S10 implementation PR must add these exactly named static-verifier
checks (Python `assert`-based, runnable from the workflow as one
script):

1. `test_s10_lsp_file_exists_at_canonical_path`
2. `test_s10_defines_exactly_one_command_yuantus_diff_preview`
3. `test_s10_command_calls_yuantus_helper_call_for_diff_preview`
4. `test_s10_command_calls_yuantus_helper_call_for_audit_apply_result`
5. `test_s10_audit_apply_result_outcome_is_not_applied_display_only`
6. `test_s10_lsp_contains_no_dwg_mutation_or_entity_creation`
7. `test_s10_lsp_user_output_uses_princ_only_no_modal_dialogs`
8. `test_s10_lsp_handles_nil_from_helper_call_without_calling_audit_apply_result`
9. `test_s10_supports_zwcad_and_gstarcad_via_program_sniff_or_shared_source`
10. `test_s10_does_not_add_helper_server_routes_route_count_stays_ten`
11. `test_s10_does_not_add_s11_integration_or_other_lisp_commands`
12. `test_s10_workflow_runs_lisp_shell_static_verifier`
13. `test_s10_static_verifier_rejects_dwg_mutation_and_direct_http_intent`
14. `test_s10_dev_verification_records_deferred_native_cad_load_signoff`
15. `test_s10_lsp_balanced_parens_and_double_quotes`
16. `test_s10_lisp_function_call_arity_matches_s9_primitive`

Recommended additional source/drift guards (in the static verifier):

- the .lsp file contains exactly one `(defun c:` definition;
- the file does not contain `(arxload`, `(autoarxload`, `(startapp`, or
  `(command "_-SHELL"`;
- the file does not contain `(alert`, `(getfiled`, or `(initdia`;
- the file does not contain `(open ...` with `"w"` or `"a"` mode;
- helper Kestrel route count remains 10 (re-runs the equivalent check
  from `verify_bridge_static.py`);
- the S6 SQLite audit schema is not modified (no edits under
  `clients/cad-desktop-helper/Helper/HelperRuntime.cs`);
- the S9 bridge source is not modified (no edits under
  `clients/cad-desktop-helper/Bridge/`).

The Python static verifier should follow the same style as
`verify_bridge_static.py`: each check prints `ok` / `FAIL`, exit non-zero
on any failure, no external pip dependencies.

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

The implementation PR should additionally run:

```bash
python3 clients/cad-desktop-helper/verify_lisp_shell_static.py
```

and the existing:

```bash
python3 clients/cad-desktop-helper/verify_bridge_static.py
```

to confirm the S9 bridge guard set still passes (no S9 regression from
S10 changes).

This workstation does not have a real CAD (ZWCAD / GstarCAD) host. Do not
claim S10 operational success unless the .lsp file is actually loaded and
the `YUANTUS_DIFF_PREVIEW` command actually exercised in a real ZWCAD or
GstarCAD process — that is the deferred operational signoff per §3.J.

## 7. DEV / Verification MD Requirements

The later S10 implementation PR must add:

```text
docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S10_LISP_SHELL_R1_20260524.md
```

That MD must record:

- exact scope delivered (one .lsp file, one command, no other
  surfaces);
- §3.C command flow as implemented (any deviations explicitly justified);
- §3.D / §3.E mutation / display guard list and verifier results;
- helper route count after S10 (must remain ten);
- workflow run URL or run id for `cad-helper-shared-dotnet` on the
  implementation SHA;
- static verifier output (the §5 list of checks each printing `ok`);
- §3.I per-host divergence status (none, or explicit justification);
- §3.J deferred native-CAD operational signoff list, explicitly marked
  as deferred, not claimed as collected (mirroring the S7/S8/S9
  pattern);
- the `pull_id` cross-row correlation between `/diff/preview` and
  `/audit/apply-result` audit rows (which is wired in main per
  `HelperRuntime.cs:2588` and pinned by S6's existing contract test).

## 8. Explicit Non-Goals

S10-R1 does NOT:

- write DWG fields on ZWCAD/GstarCAD (R3 explicitly defers this per
  design `:724`);
- add `YUANTUS_SYNC_INBOUND`, `YUANTUS_SYNC_OUTBOUND`,
  `YUANTUS_AUDIT_APPLY`, `YUANTUS_RESET_TOKEN`, or any other Lisp
  command;
- add `/shell/notify`, `/dedup/check`, `/compose`, `/validate`,
  `/tasks`, or `/diagnostics/snapshot` helper routes;
- modify the S9 `Bridge/` source or the S9 NETLOAD adapter;
- modify the S6 audit substrate or audit schema;
- modify the S5 session state model or session config;
- modify the S4 security gate or origin allowlist;
- modify the S3 helper startup / mutex / session-file lifecycle;
- modify the S1 Shared transport or DPAPI token store;
- introduce new `ErrorCodes` constants;
- introduce a Lisp-side JSON parser library;
- introduce CORS or browser-accessible behavior;
- introduce Python FastAPI server-side routes;
- introduce schema or migration changes;
- introduce tenant baseline data changes;
- add per-host variant .lsp files without explicit §3.I justification.

## 9. Recommended Branch For Implementation

After this taskbook merges and only after a separate explicit opt-in, use:

```text
feat/cad-helper-bridge-s10-lisp-shell-r1-20260524
```

Do not start the S10 implementation from this taskbook PR.

## 10. Reviewer Focus

Please review these points before merge:

1. Confirm S10-R1 scope is exactly one `.lsp` file + one Lisp command
   (`YUANTUS_DIFF_PREVIEW`) targeting ZWCAD + GstarCAD via a single
   AutoLISP-compatible source.
2. Confirm §3.C command flow correctly reports
   `outcome = "not-applied-display-only"` to `/audit/apply-result` per
   R3.2 acceptance test 9 (`:822`).
3. Confirm §3.D / §3.E forbid all DWG mutation and modal UI surfaces;
   the static verifier list in §5 is sufficient.
4. Confirm §3.F restricts external interaction to the S9
   `(yuantus-helper-call ...)` primitive; no direct HTTP, no direct
   DPAPI access, no other native-CAD .NET DLL loads.
5. Confirm helper Kestrel route count remains exactly ten after S10.
6. Confirm §3.J native-CAD operational evidence is deferred to signoff
   per the S7/S8/S9 pattern, not claimed as CI-tested.
7. Confirm §3.K does not require any new `ErrorCodes` constant.
8. Confirm the static verifier list in §5 is testable in Python without
   real CAD assemblies, mirroring `verify_bridge_static.py` style.
9. Confirm the §2.7 minimal-JSON approach (string concat for build,
   narrow string extraction for parse) is acceptable for R1 given the
   absence of a Lisp JSON library.

## 11. Status

This taskbook is ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it;
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
