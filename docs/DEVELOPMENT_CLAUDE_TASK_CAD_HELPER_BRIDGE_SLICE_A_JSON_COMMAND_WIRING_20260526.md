# Claude Taskbook: CAD Helper Bridge — Slice A JSON Command Wiring (Implementation Taskbook)

Date: 2026-05-26

Type: **Doc-only implementation taskbook.** It pins the exact command names,
the three JSON route mappings, the command behavior, and the static-guard
changes for the future **Slice A** implementation (R1). It changes no runtime,
schema, workflow, or client/helper code itself. **Merging this taskbook does
NOT authorize the Slice A R1 implementation** — that requires its own explicit
opt-in. This is the planning artifact, not the change.

Parent scope-lock: `DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_COMMAND_WIRING_20260526.md`
(#653, `bda0992a`), which ratified D1 (three-slice phasing) and D2(a) — Slice A =
the cad-desktop-helper LISP/NETLOAD bridge channel, with **no** AutoCAD
material-sync plugin charter expansion.

## 1. Scope

Slice A wires **three in-CAD LISP commands** that drive the three already-merged
**JSON** helper routes (`/document/checkout`, `/document/undo-checkout`,
`/document/status`) through the existing S9 bridge primitive
`(yuantus-helper-call "ENDPOINT" json)`. JSON only — no file upload, no save, no
multipart (those are Slice B/C). CAD display-only per S10: the commands do not
write or mutate the drawing, while the checkout/undo-checkout helper routes
intentionally perform their server-side lock workflow.

## 2. Grounded contract — what the three routes already accept (no change)

All three helper routes are merged and take a JSON body with a single required
field, `item_id` (`HelperRuntime.cs` `DocumentCheckoutAsync` / `…UndoCheckoutAsync`
/ `DocumentStatusAsync`, `ProxyDocumentLockAsync` :2814):

| Helper route (LISP target) | Helper body | Backend call it proxies to |
|---|---|---|
| `POST /document/checkout` | `{"item_id":"…"}` | `POST /cad/{item_id}/checkout` (empty body) |
| `POST /document/undo-checkout` | `{"item_id":"…"}` | `POST /cad/{item_id}/undo-checkout` (empty body) |
| `POST /document/status` | `{"item_id":"…"}` | `GET /cad/{item_id}/checkin-status` |

Missing/blank `item_id` → the helper itself returns
`HELPER_INPUT_VALIDATION_FAILED`; the session gate (`TryReadSession`) returns the
not-logged-in error. The LISP command performs only the UX-level empty/cancel
short-circuit in §4. It does **not** duplicate helper auth/validation logic; for
actual helper responses the S9 bridge returns either `nil` on failure (after its
sanitized command-line error) or the helper `data` payload serialized as a JSON
string, **not** the full helper envelope.

## 3. Pinned command names + mapping (to ratify)

Proposed (mirroring the existing `c:yuantus_diff_preview` style — lowercase,
`yuantus_` prefix; typeable form upper-cased):

| LISP command | Typeable | Helper route |
|---|---|---|
| `c:yuantus_checkout` | `YUANTUS_CHECKOUT` | `/document/checkout` |
| `c:yuantus_undo_checkout` | `YUANTUS_UNDO_CHECKOUT` | `/document/undo-checkout` |
| `c:yuantus_status` | `YUANTUS_STATUS` | `/document/status` |

None collide with the S10 verifier's forbidden-command list (`sync_inbound`,
`sync_outbound`, `audit_apply`, `reset_token`, `dedup_check`, `shell_notify`).
After Slice A the `.lsp` defines **four** `(defun c:…)` total (the three above +
the existing `yuantus_diff_preview`).

## 4. Command behavior spec (R1 must implement exactly)

Each of the three commands:

1. Prompt for the required PLM `item_id`; on empty/cancel → one `(princ)` notice,
   **no** helper call.
2. Build the JSON body `{"item_id":"…"}` reusing the existing
   `yuantus--json-escape` / `yuantus--replace-all` helpers (no new escaping
   path; no direct `vl-string-subst`).
3. Call `(yuantus-helper-call "<route>" body)` — arity 2 exactly.
4. On `nil` (bridge already logged a sanitized error) → one `(princ)` notice;
   **stop** (no retry).
5. On a response → `(princ)` the bridge-returned helper `data` JSON string
   verbatim for display; do **not** extract or act on any returned business
   field in CAD, do **not** write the DWG, do **not** open a modal dialog. This
   intentionally follows `BridgeCallService.SerializeDataPayload(data)`: the
   LISP surface does not receive the full fixed-200 helper envelope.
6. These commands do **NOT** call `/audit/apply-result` (they are workflow
   lock/status ops, not the display-confirm-writeback flow that diff-preview
   uses). No new `outcome` string is introduced; the existing
   `not-applied-display-only` invariant is untouched.

No `(command …)` entity ops, no `(open … "w"/"a")`, no `(startapp …)`, no
modal dialogs — identical S10 posture to the existing command.

## 5. Files Slice A R1 touches (and what it must NOT touch)

Touches **only**:

- `clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` — add the three
  commands (same file; no new `.lsp`).
- `clients/cad-desktop-helper/verify_lisp_shell_static.py` — the guard updates
  in §6.
- a new `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_SLICE_A_JSON_COMMAND_WIRING_R1_20260526.md`
  + its sorted `DELIVERY_DOC_INDEX.md` line.

Must **NOT** touch: `HelperRuntime.cs` or any C# (the routes already exist); the
S9 bridge `.dll` (the `EndpointValidator` is a **structural** guard — rooted, no
control chars, no scheme — and already permits `/document/*`, so **no allowlist
change is needed**); any helper route (count stays **15**); the AutoCAD/
SolidWorks material-sync plugins; the multipart routes.

## 6. Static-guard changes (deliverables — every guard is a deliverable)

In `verify_lisp_shell_static.py`:

- **`:208` `check_defines_exactly_one_command_yuantus_diff_preview`** — currently
  asserts exactly **one** `(defun c:…)` and that it is `c:yuantus_diff_preview`.
  R1 must update it to assert the **four**-command set and an **allowed-names**
  set `{yuantus_diff_preview, yuantus_checkout, yuantus_undo_checkout,
  yuantus_status}` (rename the check accordingly). Keep it strict — an exact set,
  not "≥1".
- **`:353` `check_does_not_add_s11_integration_or_other_lisp_commands`** — keep
  the forbidden-name list and the **exactly-one-`.lsp`-file** assertion; the
  three new names are not on the forbidden list, so only the check's framing
  ("define only diff_preview") needs to widen to "define only the Slice-A
  allowed set." Do not weaken the exactly-one-file assertion.
- **NEW affirmative guards** R1 must add: for each of the three routes, assert
  `(yuantus-helper-call "/document/checkout"|"/document/undo-checkout"|
  "/document/status" …)` appears (endpoint-presence), and that each new command
  carries a `nil`-response `(princ)` guard. The arity-2 guard (`:421`) already
  covers the new calls.
- **`:302` `check_lsp_handles_nil_from_helper_call_without_calling_audit_apply_result`
  — ordering hazard, must be disposed in R1.** This guard is positional: it uses
  first-occurrence `source.find()` on the literal tokens `"/diff/preview"`,
  `(null response)`, `"/audit/apply-result"` and requires
  `diff_idx < nil_guard_idx < apply_idx`. The existing command uses `(null
  response)`, and §4 drives the new commands toward the same nil-guard idiom, so
  a new command that (a) reuses the var name `response` **and** (b) is placed
  **before** `c:yuantus_diff_preview` would make `(null response)` resolve first
  and break the ordering. **Disposition (R1 must follow):** place all three new
  commands **after** the existing `c:yuantus_diff_preview` definition in the
  `.lsp` (cheapest — keeps `:302` unchanged and green). As belt-and-suspenders,
  R1 may also give the new commands a distinct response var (e.g.
  `lock-response` / `status-response`). Do **not** rewrite `:302` to be
  function-scoped (needless guard churn).

Invariants to **re-assert unchanged** (command wiring adds no route):

- route count `== 15`: `verify_lisp_shell_static.py:348`,
  `verify_bridge_static.py:167` (`check_helper_route_count_after_g1b`),
  `verify_material_sync_static.py:234`, the 5 C# count tests, the G1-C DEV doc.
- the no-DWG-mutation guard (`:250`) and modal/`princ`-only guard (`:277`) stay
  green by construction.

## 7. Non-Goals

This taskbook does NOT: authorize the Slice A R1 implementation (separate
opt-in); touch any C#/bridge/helper code; add/remove a helper route; build the
multipart transport seam (Slice B) or multipart command wiring (Slice C); add
`/audit/apply-result` to the new commands; expand the material-sync plugin
charter; introduce a new `.lsp` file.

## 8. Acceptance / preconditions for the Slice A R1

1. The three commands exist in the single `.lsp`, each per §4, typeable as in §3.
2. `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` passes with
   the §6 guard updates (four-command allowed set; three endpoint-presence
   checks; nil guards) and route count still `15`.
3. No diff in `HelperRuntime.cs` / any `.cs` / the bridge `.dll`.
4. A DEV/verification doc records the run, the CAD display-only posture, and the
   deferred native-CAD operational signoff (no real ZWCAD/GstarCAD/AutoCAD host
   on CI), mirroring the S10 DEV doc. Note: the existing
   `check_dev_verification_records_deferred_native_cad_load_signoff` (`:408`)
   hard-codes the **S10** DEV doc path, so it stays green unchanged and does
   **not** cover the Slice A doc. **Decision recorded:** R1 adds a parallel
   Slice-A-scoped DEV-doc presence/signoff check (same deliverable-guard
   pattern) rather than relying solely on doc-index completeness — do not invent
   the check ad-hoc.
5. `git diff --check` clean; `DELIVERY_DOC_INDEX.md` updated + sorted.

(C# build/xUnit is not in scope here — Slice A adds no C#. If any future
revision touches C#, the standing rule applies: "C# build/xUnit deferred to
Windows CI" in the PR body.)

## 9. Reviewer Focus

1. Confirm §2: the three routes already accept `{item_id}`; R1 adds only the
   LISP callers, no helper change.
2. Ratify §3 command names (or amend) and the four-command post-state.
3. Confirm §5: R1 is `.lsp` + lisp verifier + DEV doc only; the
   `EndpointValidator` already permits `/document/*` (no bridge allowlist edit).
4. Confirm §6 enumerates the guard shifts by file:line and keeps route count
   `15`; the `:208` set-assertion stays strict (exact four), not loosened.
5. Confirm §4: new commands are CAD display-only and do **not** call
   `/audit/apply-result` (no new `outcome` string).

## 10. Status

Ready for review once: the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / sorting / Tier-B
drift checks pass; `git diff --check` clean. Merging sets the Slice A R1 plan;
**a separate explicit opt-in authorizes the R1 implementation.**
