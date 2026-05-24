# CAD Helper Bridge R3.2 — Acceptance Evidence Runbook

Date: 2026-05-24

Consolidated 12-item operator acceptance-evidence runbook for the
**CAD Desktop Helper Bridge R3.2** program. Each row maps to one
acceptance test in the R3.2 design `:810-825` and exercises one or
more of the deferred operational signoff items from S7 (5) + S8 (5)
+ S9 (7) + S10 (8) = 25 consolidated items.

This runbook is **not a CI-runnable test list**. It is the operator's
ratified evidence-collection procedure for environment-prohibited
seams: real Windows + real CAD hosts (AutoCAD 2018 / 2024, ZWCAD
2025, GstarCAD 2025), real DPAPI, real NETLOAD into `acad.exe`, real
PowerShell / SSH / WinRM remote-shell detection.

## How to use this runbook

For each row:

1. Provision the **required environment**.
2. Complete the **setup steps**.
3. Execute the **execution steps** exactly as written.
4. Verify the **expected observable outcome** against the live host.
5. Capture the **evidence artifact** in the specified format and
   archive it at the specified path.
6. Fill in the **signoff slot** with operator name, date (ISO-8601),
   and archived artifact path.

Evidence archive root suggestion:

```text
%APPDATA%\YuantusPLM\acceptance-evidence\R3.2\
```

Conventions:

- `.pml` for Procmon recordings.
- `.png` for screenshots.
- `.txt` for command-line transcripts (UTF-8).
- `.sql.txt` for `audit.db` SQL excerpts (sqlite3 `.dump`-style with
  `SELECT` filters).
- Filenames prefixed with the row number, e.g.,
  `01_autocad2018_diff_preview.txt`.

A 13th "operator summary" row is included at the end so the
runbook execution itself can be signed off as complete.

---

## Row 1 — AutoCAD 2018: full `PLMMATPULL` path through helper

**Design citation:** `:811`. **Slice attribution:** S1 + S3 + S6 + S8.

**Required environment:**
- Windows 11 x64 with current security baseline.
- AutoCAD 2018 (baseline) installed.
- `CADDedupPlugin.bundle` registered via the existing AutoCAD bundle
  loader.

**Setup steps:**
1. Follow `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md`
   steps 1, 2, 4 to install helper, detector, and the AutoCAD
   plugin.
2. Confirm `%APPDATA%\YuantusPLM\helper\` is clean (no stale
   `helper-session-*.json`).
3. Log in to PLM from AutoCAD per the existing operator workflow so
   the helper has a valid session.

**Execution steps:**
1. In AutoCAD 2018: open any DWG containing a Yuantus-managed
   material entity.
2. At the AutoCAD command line: `PLMMATPULL`.
3. Observe: helper auto-spawn (if not already running), `/diff/preview`
   call to helper, then CAD field write back into the DWG.

**Expected observable outcome (verbatim from §2.3 row 1):**
Windows 11 + AutoCAD 2018 real host: `Yuantus.Cad.Shared (net46)`
integrated into `CADDedupPlugin`, `PLMMATPULL` → helper auto spawn →
`/diff/preview` returns `write_cad_fields` → `CadMaterialFieldService`
writes DWG → `/audit/apply-result` lands in `audit.db`.

**Evidence artifact:**
- `01a_plmmatpull_command_line_transcript.txt` — full AutoCAD command
  line transcript.
- `01b_audit_db_diff_preview_row.sql.txt` — sqlite3 SELECT excerpt
  from `audit.db` showing the `/diff/preview` row with non-null
  `pull_id`.
- `01c_audit_db_apply_result_row.sql.txt` — sqlite3 SELECT excerpt
  showing the matching `/audit/apply-result` row with the same
  `pull_id` (S6 correlation guarantee).
- `01d_dwg_after_screenshot.png` — DWG after the `CadMaterialFieldService`
  write.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 2 — AutoCAD 2018: `PLMMATPUSH` path through helper

**Design citation:** `:812`. **Slice attribution:** S6 + S8.

**Required environment:** Same as Row 1.

**Setup steps:** Same as Row 1 (helper + plugin already installed
and authenticated).

**Execution steps:**
1. In AutoCAD 2018: open any DWG containing a Yuantus-managed
   material entity edited locally.
2. At the AutoCAD command line: `PLMMATPUSH`.
3. Observe: helper forwards to PLM `/sync/inbound`, server returns
   an updated action.

**Expected observable outcome (verbatim from §2.3 row 2):**
AutoCAD 2018 real host: run `PLMMATPUSH` → helper `/sync/inbound`
forwarded to server → server returns updated action → audit row
written.

**Evidence artifact:**
- `02a_plmmatpush_command_line_transcript.txt`.
- `02b_audit_db_sync_inbound_row.sql.txt`.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 3 — Detector zero registry writes

**Design citation:** `:813`. **Slice attribution:** S2.

**Required environment:**
- Windows 10 or 11.
- Procmon (Sysinternals) installed.
- `yuantus-cad-detector.exe` installed per install runbook step 2.

**Setup steps:**
1. Start Procmon.
2. Set Procmon filter: Process Name is `yuantus-cad-detector.exe`,
   Operation begins with `RegSet` or `RegCreate` or `RegDelete`.
3. Clear current log.

**Execution steps:**
1. Run `yuantus-cad-detector.exe` once with its default scan.
2. Stop Procmon recording.
3. Save the recording as `.pml` (Procmon native format).

**Expected observable outcome (verbatim from §2.3 row 3):**
Procmon recording proves detector does zero registry writes (`.pml`
archived).

**Evidence artifact:**
- `03_detector_procmon_no_registry_writes.pml`.
- `03b_detector_procmon_summary.txt` — Procmon "Process Activity
  Summary" export confirming zero write/create/delete operations on
  `HKLM` or `HKCU` keys.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 4 — LAN access to `/healthz` is rejected

**Design citation:** `:814`. **Slice attribution:** S3.

**Required environment:**
- Two machines on the same LAN. Host A = workstation running the
  helper (e.g., `192.168.x.y`). Host B = any second machine with
  `curl.exe` or PowerShell available.

**Setup steps:**
1. On Host A: start the helper (any CAD-side command, or directly
   spawn via the install path).
2. From Host A's helper log or `helper-session-*.json`, note the
   loopback port (in 7959-7999 range), call it `<PORT>`.
3. Note Host A's LAN IP, call it `<HOST_LAN_IP>`.

**Execution steps:**
1. From Host B: run

   ```text
   curl -v http://<HOST_LAN_IP>:<PORT>/healthz
   ```

2. Observe: connection refused, connection reset, or TCP RST. The
   helper is loopback-only and the LAN bind is intentionally absent.
3. From Host A: run `curl http://127.0.0.1:<PORT>/healthz`. Confirm
   200 + `{"status":"ok"}` (positive control).

**Expected observable outcome (verbatim from §2.3 row 4):**
LAN: another machine accesses `http://<host-lan-ip>:7959/healthz` →
rejected (loopback-only binding).

**Evidence artifact:**
- `04a_lan_healthz_rejected_curl_transcript.txt` — full `curl -v`
  output from Host B.
- `04b_loopback_healthz_positive_control.txt` — `curl` output from
  Host A showing 200.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 5 — Non-allowlisted process is rejected (`curl.exe`)

**Design citation:** `:815`. **Slice attribution:** S4.

**Required environment:**
- Windows 10 or 11.
- `curl.exe` available at a path NOT on the S4 origin allowlist.

**Setup steps:**
1. Start the helper from a CAD-side caller (so the helper is
   running with the correct session).
2. From the helper log, note the loopback `<PORT>` and obtain a
   valid bootstrap token + DPAPI envelope as the AutoCAD plugin
   would.

**Execution steps:**
1. From an interactive `curl.exe` invocation on the same machine:

   ```text
   curl -v -X POST -H "Authorization: Bearer <token>" \
        http://127.0.0.1:<PORT>/session/status
   ```

2. Observe: helper returns `403` with `error.code =
   ORIGIN_PROCESS_NOT_ALLOWED`.

**Expected observable outcome (verbatim from §2.3 row 5):**
Simulate non-allowlisted process (use `curl.exe` to send the request
directly) → `403 ORIGIN_PROCESS_NOT_ALLOWED`.

**Evidence artifact:**
- `05_curl_rejected_origin_process_not_allowed.txt` — full `curl -v`
  output showing 403 + the structured error code.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 6 — Stale session file cleanup after `taskkill`

**Design citation:** `:816`. **Slice attribution:** S3.

**Required environment:**
- Windows 10 or 11; helper installed; AutoCAD or another CAD-side
  caller available to spawn the helper.

**Setup steps:**
1. Start the helper.
2. Note `helper-session-<sessionId>.json` under
   `%APPDATA%\YuantusPLM\helper\`.

**Execution steps:**
1. `taskkill /F /IM yuantus-cad-helper.exe` (or kill via Task
   Manager).
2. Confirm `helper-session-<sessionId>.json` remains on disk
   (stale).
3. From the CAD-side caller: re-issue a command that spawns the
   helper (`PLMMATPULL` or `YUANTUS_DIFF_PREVIEW`).
4. Observe: helper start path follows R3.2 §5.1 step 5/6 cleanup,
   removes the stale session file, publishes a fresh one, and the
   call succeeds.

**Expected observable outcome (verbatim from §2.3 row 6):**
helper process `taskkill` → `helper-session-{sessionId}.json` left
behind → next startup follows R3.2 §5.1 steps 5/6 deletion and
operates normally.

**Evidence artifact:**
- `06a_pre_kill_session_file.txt` — `dir` listing showing stale
  session file.
- `06b_post_kill_cleanup_log.txt` — helper startup log showing
  cleanup of the stale file.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 7 — 30-minute idle auto-exit clears session file

**Design citation:** `:817`. **Slice attribution:** S3.

**Required environment:**
- Windows 10 or 11; helper installed.

**Setup steps:**
1. Start the helper via any CAD-side caller (one-shot is fine).
2. Confirm `helper-session-<sessionId>.json` exists.

**Execution steps:**
1. Leave the helper idle (no calls) for **30 minutes**.
2. Observe: helper exits cleanly.
3. Confirm `helper-session-<sessionId>.json` is removed.

**Expected observable outcome (verbatim from §2.3 row 7):**
30-minute idle auto-exit, current session's
`helper-session-{sessionId}.json` is cleaned up.

**Evidence artifact:**
- `07a_pre_idle_session_file.txt`.
- `07b_post_idle_no_helper_process.txt` — `tasklist` showing no
  `yuantus-cad-helper.exe`.
- `07c_post_idle_no_session_file.txt` — `dir` listing showing the
  session file is gone.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 8 — 30-minute coexistence with `CADDedupPlugin`, no leaks

**Design citation:** `:818`. **Slice attribution:** S6 + integration.

**Required environment:**
- AutoCAD 2018 baseline; helper installed; `CADDedupPlugin`
  registered.

**Setup steps:**
1. Start AutoCAD; spawn the helper via `PLMMATPULL` to establish
   the session.
2. Note the helper PID; note RSS via Task Manager or `tasklist
   /fi "imagename eq yuantus-cad-helper.exe"`.

**Execution steps:**
1. Continue normal AutoCAD activity (drawing edits, periodic
   `PLMMATPULL` / `PLMMATPUSH` calls) for **30 minutes**.
2. Periodically (every 5 minutes) record helper RSS and Kestrel
   port.
3. Confirm no port conflict, no mutex conflict, no memory leak.

**Expected observable outcome (verbatim from §2.3 row 8):**
helper and existing `CADDedupPlugin` coexisting for 30 minutes — no
port / mutex conflict, no memory leak.

**Evidence artifact:**
- `08a_coexistence_helper_rss_timeline.txt` — 5-minute RSS samples.
- `08b_coexistence_no_conflict_log.txt` — confirming Kestrel port
  unchanged and helper single-instance mutex held without contention.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 9 — ZWCAD: `YUANTUS_DIFF_PREVIEW` display-only

**Design citation:** `:819`. **Slice attribution:** S9 + S10.

**Required environment:**
- Windows 10 or 11.
- ZWCAD 2025 installed (or GstarCAD 2025 — repeat this row for
  each host that ships).
- `YuantusCadHelperBridge.dll` (.NET Framework v4.6) + Lisp shell
  installed per install runbook step 3.
- Helper running (or available to auto-spawn).

**Setup steps:**
1. Open ZWCAD.
2. NETLOAD `YuantusCadHelperBridge.dll`.
3. `(load "yuantus_cad_helper.lsp")` (or your configured load
   path).

**Execution steps:**
1. At the ZWCAD command line: `YUANTUS_DIFF_PREVIEW`.
2. Respond to prompts (item id, profile id) per the Lisp shell
   prompts.
3. Observe: helper `/diff/preview` returns JSON; the Lisp shell
   prints the response's `data` (containing `write_cad_fields`) via
   the production CAD command-line writer; **no DWG is written**;
   helper writes one `/audit/apply-result` row with outcome
   `not-applied-display-only` and the same `pull_id`.

**Expected observable outcome (verbatim from §2.3 row 9):**
ZWCAD real host: install LISP shell + `YuantusCadHelperBridge.dll`
(.NET Framework v4.6), run `YUANTUS_DIFF_PREVIEW` → command line
displays `write_cad_fields` JSON, no automatic DWG write,
`/audit/apply-result` records `not-applied-display-only`.

**Evidence artifact:**
- `09a_zwcad_command_line_transcript.txt` — full ZWCAD command line.
- `09b_dwg_unchanged_before_after.png` — screenshots before / after,
  confirming no DWG mutation.
- `09c_audit_db_display_only_row.sql.txt` — `audit.db` row showing
  `outcome = 'not-applied-display-only'` and the matching `pull_id`
  from the `/diff/preview` row.

Repeat with `09g_gstarcad_*` for GstarCAD if GstarCAD is in scope.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 10 — `--reset-local-token` from interactive PowerShell

**Design citation:** `:820`. **Slice attribution:** S7.

**Required environment:**
- Windows 10 or 11; helper installed; AutoCAD 2018 baseline (or
  any CAD-side caller).
- Interactive PowerShell session on the workstation's desktop
  (not RDP, not SSH, not WinRM).

**Setup steps:**
1. Confirm helper not running (`taskkill /F /IM
   yuantus-cad-helper.exe` if needed; row 6 covers cleanup path).
2. Note the DPAPI envelope file's `LastWriteTime` under
   `%APPDATA%\YuantusPLM\helper\`.

**Execution steps:**
1. In an interactive PowerShell prompt:

   ```text
   %APPDATA%\YuantusPLM\helper\yuantus-cad-helper.exe --reset-local-token
   ```

2. At the `y/n` prompt: input `y`.
3. Observe: command exits 0; DPAPI envelope file's `LastWriteTime`
   has advanced.
4. In AutoCAD: run `PLMMATPULL`; the existing session
   transparently picks up the new token.

**Expected observable outcome (verbatim from §2.3 row 10):**
`--reset-local-token` in PowerShell real-host execution → prompt
confirms → user inputs `y` → DPAPI token replaced → existing
AutoCAD session's next `PLMMATPULL` picks up the new token
automatically.

**Evidence artifact:**
- `10a_powershell_reset_transcript.txt`.
- `10b_dpapi_envelope_mtime_before_after.txt`.
- `10c_subsequent_plmmatpull_success.txt`.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 11 — `--reset-local-token` from SSH / WinRM is rejected

**Design citation:** `:821`. **Slice attribution:** S7.

**Required environment:**
- Windows 10 or 11 with OpenSSH server enabled (for SSH leg).
- Optional: WinRM enabled (for WinRM leg). Both legs should be
  exercised if both transports are in operator scope.

**Setup steps:**
1. Confirm SSH and/or WinRM is configured to reach the workstation
   from a second machine.

**Execution steps:**
1. From the second machine, via SSH:

   ```text
   ssh user@workstation "%APPDATA%\\YuantusPLM\\helper\\yuantus-cad-helper.exe --reset-local-token"
   ```

2. Observe: exit code is 1; the helper prints the structured error
   indicating non-interactive remote-shell rejection per S7
   parent-ancestry walk (`HELPER_RESET_REQUIRES_INTERACTIVE`).
3. Repeat with WinRM (`Invoke-Command -ComputerName workstation
   -ScriptBlock { ...exe --reset-local-token }`).

**Expected observable outcome (verbatim from §2.3 row 11):**
`--reset-local-token` triggered remotely from SSH / WinRM → rejected
with exit code 1.

**Evidence artifact:**
- `11a_ssh_reset_rejected_transcript.txt`.
- `11b_winrm_reset_rejected_transcript.txt` (if WinRM leg
  exercised).

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 12 — Shared net46 and net6.0 coexist in process

**Design citation:** `:822`. **Slice attribution:** S1.

**Required environment:**
- Windows 11 + AutoCAD 2018 + helper.
- Process-monitor tool that can enumerate loaded modules
  (Process Explorer or `Get-Process | Select-Object -ExpandProperty
  Modules`).

**Setup steps:**
1. Open AutoCAD 2018; ensure `CADDedupPlugin.bundle` is loaded so
   `Yuantus.Cad.Shared` (net46) is in `acad.exe`'s module list.
2. From AutoCAD, run `PLMMATPULL` to spawn the helper so
   `Yuantus.Cad.Shared` (net6.0-windows) is also loaded inside
   `yuantus-cad-helper.exe`.

**Execution steps:**
1. With both processes running, enumerate modules of `acad.exe` —
   confirm `Yuantus.Cad.Shared.dll` is present and is the **net46**
   build.
2. Enumerate modules of `yuantus-cad-helper.exe` — confirm
   `Yuantus.Cad.Shared.dll` is present and is the **net6.0-windows**
   build (different assembly load path).
3. Continue normal AutoCAD activity for ≥ 5 minutes; no runtime
   conflict, no `BadImageFormatException`, no `FileLoadException`
   surfaces.

**Expected observable outcome (verbatim from §2.3 row 12):**
AutoCAD 2018 real host: `Yuantus.Cad.Shared` loaded simultaneously as
net46 (inside `acad.exe`) and as net6.0 (inside `helper.exe`) without
runtime conflict.

**Evidence artifact:**
- `12a_acad_modules_shared_net46.txt`.
- `12b_helper_modules_shared_net60_windows.txt`.
- `12c_no_exceptions_during_coexistence.txt`.

**Signoff:** operator: _____  date: _____  archive path: _____

---

## Row 13 — Runbook execution summary (operator closeout)

After Rows 1-12 are all signed off:

| Row | Date executed | Operator | Archive path |
|---|---|---|---|
| 1 | _____ | _____ | _____ |
| 2 | _____ | _____ | _____ |
| 3 | _____ | _____ | _____ |
| 4 | _____ | _____ | _____ |
| 5 | _____ | _____ | _____ |
| 6 | _____ | _____ | _____ |
| 7 | _____ | _____ | _____ |
| 8 | _____ | _____ | _____ |
| 9 (ZWCAD) | _____ | _____ | _____ |
| 9 (GstarCAD) | _____ | _____ | _____ |
| 10 | _____ | _____ | _____ |
| 11 | _____ | _____ | _____ |
| 12 | _____ | _____ | _____ |

When the table is complete, the R3.2 cycle moves from "closed
pending acceptance-evidence runbook execution" to "fully closed".
Notify the project owner and update
`docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` Section 11
with the closeout date.

**Operator closeout signoff:** _____  date: _____.
