# DEV & Verification: CAD Helper Bridge — Slice C Multipart Command Wiring (R1)

Date: 2026-05-27

Implements the Slice C R1 plan pinned in
`DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_SLICE_C_MULTIPART_COMMAND_WIRING_20260527.md`
(#659, `758ecf15`): two in-CAD LISP commands that drive the merged Slice B
`yuantus-helper-upload` primitive. LISP + static verifier + this doc only — no
C#/bridge/helper change, no material-sync change. Helper route count stays `15`;
the bridge two-primitive set is unchanged.

## 1. What changed

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` — two new display-only
commands, defined **after** the existing four:

| LISP command | Typeable | Bridge call | Helper route | item_id |
|---|---|---|---|---|
| `c:yuantus_checkin` | `YUANTUS_CHECKIN` | `(yuantus-helper-upload "/document/checkin" item-id filepath)` | `/document/checkin` | required |
| `c:yuantus_bom_import` | `YUANTUS_BOM_IMPORT` | `(yuantus-helper-upload "/document/bom-import" item-id filepath)` | `/document/bom-import` | optional (blank → auto-create root) |

The `.lsp` header and the load-time confirmation `(princ …)` were synced from
four to **six** commands.

## 2. Command behavior

Each command: prompts for `item_id` (`YUANTUS_CHECKIN` cancels on empty;
`YUANTUS_BOM_IMPORT` passes blank through so the bridge omits the part and the
helper auto-creates the root); applies the §3 save model; derives the file path;
calls `(yuantus-helper-upload "<route>" item-id filepath)` (arity 3);
nil-guards the bridge return; on success `(princ)` the bridge data JSON string
verbatim. They do **not** call `/audit/apply-result`, write the DWG, or open a
modal. **display-only** posture identical to the Slice A commands.

## 3. Save model (fail-closed)

Upload proceeds **only** when `(getvar "DBMOD")` returns numeric `0` (no unsaved
changes). `nil` (modified-state unavailable) or non-zero → one `(princ)` notice
and abort, so stale on-disk bytes are never uploaded. No save, no DWG mutation —
`DBMOD`/`DWGPREFIX`/`DWGNAME` are read-only getvars. The local variable is named
`dirty-flag` (never the bare `dbmod` symbol) so the existing no-DWG-mutation
guard token stays untripped.

## 4. File-source policy (the #657 §3 P1 deliverable)

The upload `filepath` is always `(strcat (getvar "DWGPREFIX") (getvar
"DWGNAME"))` — the active document path, never a prompted or file-picker path. A
new static guard
(`check_upload_filepath_is_active_document_path`) pins the canonical derivation,
requires every `(yuantus-helper-upload …)` third argument to be the `filepath`
symbol, and forbids `(getfiled …)`. Combined with the bridge `IBridgeFileSource`
validation and the helper-side no-local-read guard, the file source is bounded
at all three layers.

## 5. Static-guard changes (`verify_lisp_shell_static.py`)

- `SLICE_A_COMMAND_SET` / `check_defines_exactly_the_slice_a_command_set` →
  `LISP_COMMAND_SET` / `check_defines_exactly_the_lisp_command_set`: now exactly
  **six** commands (adds `yuantus_checkin`, `yuantus_bom_import`).
- `check_each_command_nil_guards_the_bridge_response`: per-command `(null
  response)` coverage now spans all six.
- New `check_upload_call_arity_is_three`: every `(yuantus-helper-upload …)` is
  arity 3 (parallel to the arity-2 guard for `yuantus-helper-call`).
- New `check_upload_commands_call_document_multipart_routes`: both
  `/document/checkin` and `/document/bom-import` are reached via the upload
  primitive.
- New `check_upload_filepath_is_active_document_path` (§4).
- New `check_header_and_load_line_list_all_commands`: the header and load-time
  line both name all six commands (guards the four→six caption drift).
- New `check_slice_c_dev_verification_records_deferred_signoff`: this doc exists
  and records the deferred operational signoff.
- Unchanged invariants: route count `15`; bridge two-primitive set and
  `check_bridge_sources_unchanged_assumption`; no-DWG-mutation, `(princ)`-only /
  no-modal, no-`(open … "w")`, no-shell-out; the arity-2 guard for
  `yuantus-helper-call`.

## 6. Verification

- `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py` — all guards
  pass (28 guards), including the six-command set, upload arity-3, upload
  endpoint presence, the filepath policy, header/load-line sync, parens/quote
  balance, and route count `15`.
- `python3 clients/cad-desktop-helper/verify_bridge_static.py` → 13 pass;
  `python3 clients/autocad-material-sync/verify_material_sync_static.py` → pass.
- doc-contract pytests → pass; `git diff --check` → clean.

## 7. Deferred native-CAD operational signoff

The `.lsp` cannot be loaded or executed on the GitHub runner — there is no real
**ZWCAD** / **GstarCAD** / AutoCAD host. End-to-end on-host exercise of
`YUANTUS_CHECKIN` / `YUANTUS_BOM_IMPORT` (save → upload → helper → PLM) against a
live helper is therefore **Deferred** to the operational signoff track, as with
the Slice A commands. This R1 closes the **static** contract only. With Slice C
merged, the helper last-mile (G1-A lock/status, G1-B/C upload) is fully
command-wired end to end.
