# CAD Helper Bridge — Native-CAD Operational Signoff Runbook

Date: 2026-05-27

Type: **Operational signoff runbook (doc-only).** This is an operator
evidence checklist to be executed by hand on real **AutoCAD / ZWCAD /
GstarCAD** hosts. It is **not** a CI test and it does **not** itself collect or
assert any evidence. Creating this document does **not** mean native-CAD signoff
is complete — that requires a later evidence PR after the §6 summary table is
filled and artifacts are archived.

## 1. Purpose

Convert the deferred *native-CAD operational signoff* — the only remaining open
item of the CAD helper bridge last-mile — into an executable, evidence-producing
checklist. It binds to the last-mile already merged to `main` (baseline
`befd519d`):

- **Backend / helper routes**: G1-A (`/document/checkout`,
  `/document/undo-checkout`, `/document/status`), G1-B (`/document/checkin`),
  G1-C (`/document/bom-import`).
- **In-CAD commands**: Slice A JSON commands (`YUANTUS_CHECKOUT`,
  `YUANTUS_UNDO_CHECKOUT`, `YUANTUS_STATUS`), Slice B bridge multipart primitive
  `yuantus-helper-upload`, Slice C upload commands (`YUANTUS_CHECKIN`,
  `YUANTUS_BOM_IMPORT`), plus the original S10 `YUANTUS_DIFF_PREVIEW`.

The static contracts (verifiers, xUnit, doc-contract) are already green in CI;
what CI **cannot** cover is loading the bridge `.dll` / `.lsp` inside a real CAD
host and running the commands against a live helper + PLM. That is what this
runbook captures.

## 2. Evidence archive convention

Archive all artifacts under a per-run root:

```
%APPDATA%\YuantusPLM\acceptance-evidence\native-cad-last-mile\20260527\<host>\
```

where `<host>` is one of `autocad2018`, `autocad2024`, `zwcad2025`,
`gstarcad2025`. Artifact types:

- `*.txt` — CAD command-line transcript (copy/paste of the command output).
- `*.png` — screenshots (command line + any PLM-side state change).
- `*.log.txt` — helper / server log excerpts for the call window.
- `*.sql.txt` — audit / job table excerpts **where DB access is available**
  (optional; absence is not a blocker for the workflow commands — see §4).

Name files `row<N>-<command>-<host>.<ext>` so the §6 summary can reference them.

## 3. Preflight (record once per host)

Capture into `preflight-<host>.txt`:

1. Windows version; CAD host name + version; PLM tenant + server URL.
2. Helper build / hash; `YuantusCadHelperBridge.dll` path; `yuantus_cad_helper.lsp` path.
3. Helper is running and a **PLM session is valid** (helper `/session/status`).
4. `NETLOAD` `YuantusCadHelperBridge.dll` — no load error.
5. `(load "yuantus_cad_helper.lsp")` — loads cleanly.
6. The load-time confirmation line lists **all six** commands:
   `YUANTUS_DIFF_PREVIEW, YUANTUS_CHECKOUT, YUANTUS_UNDO_CHECKOUT,
   YUANTUS_STATUS, YUANTUS_CHECKIN, YUANTUS_BOM_IMPORT`.
7. Use a saved test drawing with an ASCII-safe basename (for example
   `native_signoff.dwg`, characters limited to `[A-Za-z0-9._-]`) so upload
   filename evidence is not obscured by the bridge filename sanitizer. Record
   its full active path (`DWGPREFIX + DWGNAME`) and file size before upload.

If any preflight step fails, stop and record it as a §5 blocker.

## 4. Command rows (execute per host; capture transcript + screenshot each)

**Per-command evidence boundary.** Only `YUANTUS_DIFF_PREVIEW` is expected to
produce a helper `/audit/apply-result` row. The workflow/upload commands
(`CHECKOUT` / `UNDO_CHECKOUT` / `STATUS` / `CHECKIN` / `BOM_IMPORT`) do **not**
call `/audit/apply-result` — do **not** look for an apply-result audit row for
them; their evidence is the **PLM/backend effect** (lock state, `cad_bom` job),
the **helper transcript**, and helper/server logs.

| Row | Command | Expected outcome (evidence) |
|---|---|---|
| 1 | `YUANTUS_DIFF_PREVIEW` | Prompts item_id; command line shows the helper `data` JSON verbatim; **no DWG write**; a helper `/audit/apply-result` row exists with `outcome = "not-applied-display-only"`. |
| 2 | `YUANTUS_CHECKOUT` | Calls `/document/checkout`; command line shows response `data`; the PLM item/document enters the **locked** state (verify PLM-side). |
| 3 | `YUANTUS_STATUS` | Calls `/document/status`; command line shows the lock/status info for the item (reflecting Row 2's lock). |
| 4 | `YUANTUS_UNDO_CHECKOUT` | Calls `/document/undo-checkout`; the lock is **released**; a follow-up `YUANTUS_STATUS` shows the changed state. |
| 5 | `YUANTUS_CHECKIN` (positive) | With a non-empty item_id and a **saved** drawing (`DBMOD = 0`): uploads the active DWG to `/document/checkin`; command line shows response `data`; **the server-side file metadata matches the currently open DWG evidence** (at minimum filename; file size/checksum where exposed by file metadata or DB access); **no DWG write**. |
| 6 | `YUANTUS_CHECKIN` (negative) | (a) **empty item_id** → only a cancel notice, **no** helper upload. (b) **dirty/unsaved DWG** (`DBMOD ≠ 0`) → a "save first" notice, **no** helper upload. |
| 7 | `YUANTUS_BOM_IMPORT` (positive) | (a) **with item_id** → uploads the active DWG to `/document/bom-import`; response returns `file_id` + a `cad_bom` job. (b) **blank item_id** → upload is allowed; the helper applies auto-create-root. Poll BOM status via backend `GET /api/v1/cad/files/{file_id}/bom`. Confirm server-side file metadata matches the currently open DWG evidence (filename; file size/checksum where exposed). |
| 8 | `YUANTUS_BOM_IMPORT` (negative) | **dirty/unsaved DWG** → a "save first" notice, **no** helper upload. |

For Rows 5 / 7, the active-document upload check is a **confirmation** item
(the LISP binds the path to `DWGPREFIX + DWGNAME` and a static guard enforces
it). Capture the current DWG basename + size before upload, then compare against
the returned `file_id`'s server-side metadata or DB row. `BOM_IMPORT` may expose
checksum via the CAD import/file metadata path; `CHECKIN` at minimum exposes the
stored filename through checkin status / file metadata, and DB access can also
confirm file size. Do not rely on a helper `/audit/apply-result` row for these
upload commands.

## 5. Failure classification

**Blocker** (native-CAD signoff cannot be claimed):

- `YuantusCadHelperBridge.dll` cannot `NETLOAD`.
- `yuantus_cad_helper.lsp` fails to load, or the six commands are not visible.
- A helper PLM session is valid but commands cannot reach the helper at all.
- `CHECKIN` / `BOM_IMPORT` upload **despite** a dirty/unsaved DWG (`DBMOD ≠ 0`).
- The server-side upload metadata contradicts the currently open DWG evidence
  (wrong filename, or wrong size/checksum where those fields are available).

**Expected negative behavior** (not a blocker — record as pass):

- `CHECKIN` with empty item_id → cancel notice, no upload.
- `CHECKIN` / `BOM_IMPORT` with a dirty DWG → "save first" notice, no upload.
- Any command with no valid PLM session → a helper auth failure (`nil` at the
  Lisp surface + sanitized error line).

## 6. Operator summary

Fill one row per host; signoff is claimable only when this table is complete and
artifacts are archived under §2.

| Host | Version | Six-command result | Upload result (checkin / bom-import) | Artifact root | Operator | Date |
|---|---|---|---|---|---|---|
| AutoCAD | 2018 | | | | | |
| AutoCAD | 2024 | | | | | |
| ZWCAD | 2025 | | | | | |
| GstarCAD | 2025 | | | | | |

**Native-CAD operational signoff is "completed" only after** this table is
filled (all hosts pass, blockers resolved) and the artifacts are archived — to
be asserted in a **separate** evidence/signoff PR, not by the existence of this
runbook.
