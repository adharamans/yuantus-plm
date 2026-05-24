# CAD Helper Bridge S10 ZWCAD/GstarCAD Lisp Shell R1 - Development And Verification

Date: 2026-05-24

## 1. Scope Delivered

This implementation delivers the S10 slice ratified in
`docs/DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_S10_LISP_SHELL_20260524.md`
(merged at `de365c01`).

Delivered scope:

- new `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp`: a single
  AutoLISP-compatible source file shared across ZWCAD and GstarCAD;
- exactly one Lisp command `(defun c:yuantus_diff_preview ...)` —
  typeable as `YUANTUS_DIFF_PREVIEW`;
- display-only flow per taskbook §3.C steps 1-10: prompt user for
  `item_id` (required) + `profile_id` (optional), read drawing context
  from CAD built-ins, sniff `cad_system` from `(getvar "PROGRAM")`,
  build JSON request bodies via hand string-concat with quote/backslash
  escaping, call helper `/diff/preview` via the S9 NETLOAD bridge
  primitive, parse only `pull_id` from the response (no other JSON
  parsing per §2.7), display the full helper data JSON verbatim via
  `(princ ...)`, then call helper `/audit/apply-result` with
  `outcome = "not-applied-display-only"`;
- `clients/cad-desktop-helper/verify_lisp_shell_static.py`: Python
  static verifier implementing the 16 mandatory §5 checks plus
  recommended drift guards, with Lisp-aware comment stripping +
  string-respecting parenthesis balancing + explicit
  `(yuantus-helper-call ...)` arity check;
- `.github/workflows/cad-helper-shared-dotnet.yml` updated with Lisp
  path filters and a `Verify CAD helper Lisp shell static contracts`
  step running the new verifier.

Not implemented:

- no other Lisp commands (no `YUANTUS_SYNC_INBOUND`,
  `YUANTUS_SYNC_OUTBOUND`, `YUANTUS_AUDIT_APPLY`, `YUANTUS_RESET_TOKEN`,
  `YUANTUS_DEDUP_CHECK`, `YUANTUS_SHELL_NOTIFY`) — explicit S10 non-goal;
- no DWG mutation in the .lsp (forbidden `(entmake`, `(entmod`,
  `(entdel`, `(vla-put-`, `(command "TEXT/LINE/INSERT/_ERASE/_-PURGE"`)
  — ZWCAD/GstarCAD R3 explicitly defers DWG writes per design `:724`;
- no modal dialogs (`(alert`, `(getfiled`, `(initdia` forbidden);
- no direct HTTP, no direct DPAPI, no shell-out (`(startapp`,
  `(command "_-SHELL"` forbidden);
- no new helper Kestrel routes — production helper route table still
  exactly 10;
- no edits to the S9 `Bridge/` source;
- no edits to the S6 audit substrate or audit schema;
- no new `ErrorCodes` constants;
- no per-host variant `.lsp` files (single shared source per §3.A);
- no S11 integration package;
- no CORS, no Python FastAPI changes, no schema/migration/tenant-baseline edits.

## 2. Runtime Design

### 2.1 Single AutoLISP source shared across ZWCAD and GstarCAD

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` is a single
file loaded via the standard `(load "...")` mechanism on either host.
ZWCAD and GstarCAD both expose AutoLISP-compatible syntax (`defun`,
`princ`, `getstring`, `getvar`, `vl-string-search`, `vl-string-subst`,
`substr`, `strcat`, `strcase`, `vl-load-com`) and both support the
AutoLISP convention `C:<name>` for typeable commands.

Per taskbook §3.A and §3.I, S10-R1 commits to a single shared source
file. Per-host divergence is implemented via runtime sniffing of
`(getvar "PROGRAM")` — `yuantus--cad-system` returns `"zwcad"`,
`"gstarcad"`, or `"unknown"`. The shared source carries identical
behavior on both hosts; the only conditional branch is the
`cad_system` value passed in the JSON request body for audit
correlation.

### 2.2 Command flow (per §3.C)

`(defun c:yuantus_diff_preview ...)` executes the strict 10-step
sequence ratified in taskbook §3.C:

1. prompt the user for `item_id` via `(getstring T "\nPLM item id: ")`;
   empty input cancels and writes one sanitized notice
   `[YUANTUS_DIFF_PREVIEW] cancelled (no item id).` before returning
   (intentional deviation from a fully-silent cancel: the brief notice
   confirms the command actually executed so the operator does not
   think the typed command was lost or misspelled);
2. optionally prompt for `profile_id` via `(getstring T "\nProfile id
   (optional, blank to skip): ")`; empty input is permitted;
3. read drawing context: `(getvar "DWGNAME")` + `(getvar "DWGPREFIX")`,
   `cad_system` via `yuantus--cad-system`;
4. build `/diff/preview` JSON request body via
   `yuantus--build-diff-request`;
5. call `(yuantus-helper-call "/diff/preview" <json>)`; on `nil`
   return, write one sanitized notice and exit WITHOUT calling
   `/audit/apply-result`;
6. parse only `pull_id` from the response via
   `yuantus--extract-pull-id` (no other JSON parsing per §2.7); on
   missing `pull_id`, write a notice and exit without
   `/audit/apply-result` (cannot correlate);
7. display via `(princ ...)`: header line `[YUANTUS_DIFF_PREVIEW]
   item=<item> pull_id=<pull_id>`, then the **full helper data JSON
   string verbatim** (which contains `pull_id` and `server_response`
   including `write_cad_fields` as nested fields per the post-review
   convergence), then footer line `[YUANTUS_DIFF_PREVIEW] display only;
   no DWG write.`;
8. build `/audit/apply-result` JSON with `outcome =
   "not-applied-display-only"` via
   `yuantus--build-apply-result-request`;
9. call `(yuantus-helper-call "/audit/apply-result" <json>)`; on `nil`
   return, write one sanitized notice; do **not** retry per §3.C step 9;
10. trailing `(princ)` so the AutoLISP REPL prints nothing extra after
    the explicit display lines.

### 2.3 Minimal JSON in AutoLISP (§2.7 + §3.C step 6)

AutoLISP has no native JSON parser. S10 implements the minimum needed:

- **Build**: `yuantus--json-escape` replaces `\` with `\\` then `"`
  with `\"` (order matters); `yuantus--build-diff-request` and
  `yuantus--build-apply-result-request` use `strcat` to construct the
  JSON object string with embedded escaped values. Drawing path
  backslashes are correctly double-escaped before JSON.
- **Parse**: `yuantus--extract-pull-id` does a narrow string search
  for the literal `"pull_id":"` marker and reads the next double-quoted
  value via `substr`. Returns `nil` if the marker is absent or the
  closing quote cannot be located.
- **Display**: the full helper data JSON string is printed verbatim
  through `(princ ...)`. No pretty-printing. No structural parsing.

Per the post-review convergence in §3.C step 6, S10-R1 does **not**
extract `server_response.write_cad_fields` or any other nested field —
the displayed string already contains them, satisfying the design's
`:822` acceptance test 9 spirit ("display `write_cad_fields` JSON")
without introducing a brace-balanced JSON extractor in Lisp.

### 2.4 Display-only contract (§3.D + §3.E)

The .lsp file contains **no** AutoLISP mutation functions. Verified by
static guard scan:

- forbidden: `(entmake`, `(entmakex`, `(entmod`, `(entupd`, `(entdel`,
  `(vla-put-`, `(vlax-put-property`, `(vlax-invoke`, and `(command
  "TEXT"` / `"LINE"` / `"INSERT"` / `"MTEXT"` / `"CIRCLE"` /
  `"_ERASE"` / `"_-PURGE"`;
- the only allowed display call is `(princ ...)` (also `(prompt`
  and `(write-line)` would be allowed by the taskbook, but only
  `(princ ...)` is used in the implementation);
- forbidden modal/external: `(alert`, `(getfiled`, `(initdia`,
  `(initget`, `(startapp`, `(arxload`, `(autoarxload`.

### 2.5 Transport surface (§3.F)

The .lsp file's only external-interaction surface is the S9 NETLOAD
bridge primitive `(yuantus-helper-call ...)`. Verified by static guard:

- forbidden: direct shell-out via `(startapp` or `(command "_-SHELL"`;
- forbidden: direct file-write via `(open ... "w")` or `(open ... "a")`;
- forbidden: any other native-CAD .NET DLL load via `(arxload` /
  `(autoarxload`;
- the bridge handles helper-process discovery, DPAPI token read, and
  HTTP transport — the .lsp file never touches those concerns directly.

Every `(yuantus-helper-call ...)` invocation is called with exactly 2
arguments per the S9 contract; the static verifier walks each call
site with string-respecting parenthesis tracking and asserts arity = 2.

### 2.6 Audit outcome pinned to "not-applied-display-only"

The `/audit/apply-result` JSON body emitted from
`yuantus--build-apply-result-request` always carries `"outcome":
"not-applied-display-only"` — the literal string from R3.2 design
`:822` and the merged S6 audit enum at `/audit/apply-result` per the
S6 ratified contract. Static guard rejects any other outcome
(`"ok"`, `"partial"`, `"failed"`, `"error"`) appearing in an
`"outcome":` field anywhere in the source.

## 3. Test Coverage

`clients/cad-desktop-helper/verify_lisp_shell_static.py` implements the
16 mandatory §5 checks plus the recommended drift guards (one extra
guard `check_json_escape_uses_loop_based_replace_all` was added during
the post-#634 external-review convergence to source-pin the
loop-based escape helper). All 20 checks pass locally:

1. lsp file exists at canonical path
2. defines exactly one command c:yuantus_diff_preview
3. (yuantus-helper-call "/diff/preview" ...) at least once
4. (yuantus-helper-call "/audit/apply-result" ...) at least once
5. /audit/apply-result outcome is "not-applied-display-only" only
6. no DWG mutation / entity creation in lsp
7. user output uses (princ) only; no modal dialogs
8. (null response) guards /audit/apply-result after /diff/preview
9. supports ZWCAD + GstarCAD via PROGRAM sniff in shared source
10. helper production routes still exactly 10
11. no S11 integration or other Lisp commands
12. workflow runs verify_lisp_shell_static.py
13. static verifier mentions DWG mutation + direct HTTP danger tokens
14. DEV/Verification MD records deferred native-CAD operational signoff
15. lsp parens and double quotes balance
16. every (yuantus-helper-call ...) has exactly 2 args

Plus recommended drift guards:

17. no (open ... "w") / "a" write-mode in lsp
18. no (startapp / (command shell-out in lsp
19. S9 bridge wiring files unchanged (SharedBridgeLocator / Transport)
20. json escape uses loop-based replace-all (not one-shot vl-string-subst)
    — added post-#634 to source-pin the `yuantus--replace-all` loop helper
    and reject bare `(vl-string-subst` inside `yuantus--json-escape`,
    after the external reviewer caught that one-shot `vl-string-subst`
    would mishandle multi-backslash Windows DWGPREFIX paths.

The verifier is Lisp-aware: it strips line comments respecting string
literals before counting structural tokens (so `(defun c:` in a
docstring header is not counted as a real definition), and it walks
`(yuantus-helper-call ...)` invocations character-by-character with
string-state tracking to count arguments inside the matching close
paren.

## 4. Verification

Local commands run on this workstation:

```bash
python3 clients/cad-desktop-helper/verify_lisp_shell_static.py
```

Result: `All 20 S10 Lisp shell static guards passed.`

```bash
git diff --check
```

Result: clean.

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

Result: `32 passed`.

`.NET build/test` is not relevant for S10-R1 because the .lsp file is
not a .NET assembly; the existing `cad-helper-shared-dotnet` workflow
continues to build and test Shared, Detector, Helper, and Bridge as
before. The S10 contribution to the workflow is the additional Python
static-verifier step that runs after the existing bridge verifier.

### 4.1 Deferred native-CAD operational signoff

Per taskbook §3.J and the deferred-signoff pattern established by S7
#628 §4.1, S8 #630 §3.J, and S9 #632 §4.1, the following native-CAD
operational evidence is **not** collected by this PR and is recorded
as deferred operational signoff (mirroring the S9 §4.1 posture):

- the .lsp file `(load ...)`s without syntax errors in a real ZWCAD
  process;
- the .lsp file `(load ...)`s without syntax errors in a real GstarCAD
  process;
- the `YUANTUS_DIFF_PREVIEW` command is available at the CAD command
  line after load on both hosts;
- prompts for `item_id` (and optionally `profile_id`) accept user
  input in both hosts;
- `(yuantus-helper-call "/diff/preview" ...)` starts or finds the
  helper through the S9 NETLOAD bridge and returns a JSON string;
- the displayed lines appear on the CAD command line via the real CAD
  command-line writer (not the SDK-free `(princ)` stub);
- no DWG entity is created, modified, or deleted during the command
  (verifiable by `(getvar "DBMOD")` returning 0 if zero before the
  command, or by Procmon evidence of no `.dwg` writes during the run);
- `(yuantus-helper-call "/audit/apply-result" ...)` records a row in
  `audit.db` with `endpoint = "/audit/apply-result"`,
  `outcome = "not-applied-display-only"`, and the correct `pull_id`;
- the `pull_id` cross-row correlation between `/diff/preview` and
  `/audit/apply-result` audit rows resolves correctly (wired in main
  per `HelperRuntime.cs:2588`, S6 contract test pin at
  `HelperBusinessAuditContractTests.cs:123`).

This is an explicit owner-accepted deviation from the §3.J manual
evidence list, not a substitute for the missing evidence. The PR body
records the same deferred-signoff posture so future readers do not
interpret the merge as native-CAD operational validation.

## 5. Explicit Non-Goals

- No `YUANTUS_SYNC_INBOUND` / `YUANTUS_SYNC_OUTBOUND` / `YUANTUS_AUDIT_APPLY`
  / `YUANTUS_RESET_TOKEN` / `YUANTUS_DEDUP_CHECK` / `YUANTUS_SHELL_NOTIFY`
  Lisp commands.
- No DWG mutation in the .lsp.
- No modal dialogs.
- No direct HTTP, no direct DPAPI, no shell-out.
- No new helper Kestrel routes (route count stays exactly 10).
- No edits to the S9 `Bridge/` source.
- No edits to the S6 audit substrate or audit schema.
- No new `ErrorCodes` constants.
- No per-host variant .lsp files.
- No S11 integration package.
- No CORS, no Python FastAPI changes, no schema/migration edits.

## 6. Next Slices

- **S11** integration + verification package — owns the deferred
  Windows + native-CAD operational evidence from S7 / S8 / S9 / S10
  per their respective §4.1 / §3.J / §4.1 / §4.1 sections;
- **CAD pool (R2)** — still deferred (4 entry conditions unchanged).

Each remains its own per-slice opt-in.
