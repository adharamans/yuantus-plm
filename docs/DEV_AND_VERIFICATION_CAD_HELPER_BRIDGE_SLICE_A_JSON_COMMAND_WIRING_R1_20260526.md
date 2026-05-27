# DEV & Verification: CAD Helper Bridge — Slice A JSON Command Wiring (R1)

Date: 2026-05-26

Implements the Slice A R1 plan pinned in
`DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_A_JSON_COMMAND_WIRING_20260526.md`
(#654, `832f1231`): three in-CAD LISP workflow commands wired to the merged
G1-A **JSON** helper routes through the existing S9 bridge primitive. LISP +
static verifier + this doc only — no C#, no helper route, no multipart.

## 1. What changed

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` — three new
display-only commands, defined **after** `c:yuantus_diff_preview`:

| LISP command | Typeable | Helper route | Helper body |
|---|---|---|---|
| `c:yuantus_checkout` | `YUANTUS_CHECKOUT` | `/document/checkout` | `{"item_id":"…"}` |
| `c:yuantus_undo_checkout` | `YUANTUS_UNDO_CHECKOUT` | `/document/undo-checkout` | `{"item_id":"…"}` |
| `c:yuantus_status` | `YUANTUS_STATUS` | `/document/status` | `{"item_id":"…"}` |

Each command: prompts for a required PLM `item_id` (empty/cancel → one
`(princ)` notice, no helper call); builds `{"item_id":"…"}` via the shared
`yuantus--build-item-request` helper (reusing `yuantus--json-escape`, no direct
`vl-string-subst`); calls `(yuantus-helper-call "<route>" body)` (arity 2); on
`nil` → one notice and stop (no retry); on a response → `(princ)` the bridge
result verbatim. A non-c: helper `yuantus--build-item-request` was added (does
not count toward the four-command set).

## 2. Return shape (per the #654 review amend)

On success the bridge primitive returns
`BridgeCallService.SerializeDataPayload(data)` — the helper **data** payload as
a JSON **string**, NOT the full fixed-200 helper envelope; failure returns
`nil` after the bridge writes its own sanitized command-line error. The
commands therefore `(princ)` the data JSON string for display and do not parse
or act on any returned business field. They do **NOT** call
`/audit/apply-result` (workflow lock/status ops, not the display-confirm-
writeback flow diff-preview uses); no new `outcome` string is introduced.

## 3. Display-only posture (S10)

**display-only** here means the CAD / DWG is never written or modified — no
`(entmake`/`(entmod`/entity `(command …)`, no modal dialogs, only `(princ)`.
The server-side lock-state change performed by the `checkout` / `undo-checkout`
routes is the **intended** behavior of those routes and is **not** a DWG
mutation. The S10 entity-mutation guard is unchanged and stays green.

## 4. Static-guard changes (`verify_lisp_shell_static.py`)

- `check_defines_exactly_one_command_yuantus_diff_preview` →
  `check_defines_exactly_the_slice_a_command_set`: now asserts **exactly four**
  `(defun c:…)` and the exact name set
  `{yuantus_diff_preview, yuantus_checkout, yuantus_undo_checkout, yuantus_status}`.
- New `check_slice_a_commands_call_document_json_routes`: asserts each of
  `/document/checkout`, `/document/undo-checkout`, `/document/status` is reached.
- New `check_each_command_nil_guards_the_bridge_response`: asserts four
  `(if (null response) …)` guards (one per command). This also keeps the
  pre-existing `:302` first-occurrence ordering guard anchored in the
  file-first diff-preview block (new commands defined after it).
- New `check_slice_a_dev_verification_records_deferred_signoff`: asserts this
  doc exists and records the deferred operational signoff.
- `check_does_not_add_s11_integration_or_other_lisp_commands`: framing widened
  to the Slice A allowed set; forbidden-name list and exactly-one-`.lsp`-file
  assertion unchanged.
- Route count held at **15** (`check_helper_server_route_count_after_g1a`); no
  helper route added — the `EndpointValidator` is structural and already
  permits `/document/*`, so no bridge/`.dll` change was needed.

## 5. Verification

- `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` — all guards
  pass (23 guards), including parens/quote balance, arity-2, no-DWG-mutation,
  `(princ)`-only / no-modal, and route count `15`.
- `git diff --check` — clean.
- Doc-contract pytests — 24 pass (full delivery-doc-index sorting + references
  suite: all 9 `test_delivery_doc_index*` + runbook indexing + dev/verification
  completeness + doc-index sorting + claude-assist discipline + p6 plan gate).

## 6. Deferred native-CAD operational signoff

The `.lsp` cannot be loaded or executed on the GitHub runner — there is no real
**ZWCAD** / **GstarCAD** / AutoCAD host installed. End-to-end on-host exercise
of `YUANTUS_CHECKOUT` / `YUANTUS_UNDO_CHECKOUT` / `YUANTUS_STATUS` against a live
helper + PLM is therefore **Deferred** to the operational signoff track, as with
the S10 shell. This R1 closes the **static** contract only.
