# CAD Helper Bridge R3 — Evidence Closeout Coordination

Date: 2026-05-25

This is a **coordination document**, not a runbook. It does NOT restate
how to run any acceptance test — it **references** the existing runbooks
and DEV/Verification MDs that already specify execution. Its job is to:

1. **consolidate** the deferred operational-signoff obligations from the
   R3.2 program (25 items) and the installer R1 slice (10 items) into a
   single ordered worklist (§3);
2. define the **review verdict schema** + gap-table structure the
   reviewer uses on collected evidence (§4);
3. define the **evidence storage + redaction policy** — raw evidence
   never enters git (§5);
4. define the **exit criterion (rollup gate)** that flips R3.2 +
   installer R1 from *"closed pending"* to *"fully closed"* (§6).

> **Living document.** This PR creates the worklist skeleton + the
> schema + the policy + the exit gate. Verdicts are appended over time
> as the operator collects evidence on real Windows + CAD hosts and the
> reviewer adjudicates it (§7). This document collects **no evidence
> itself** — same posture as the S11 acceptance-evidence runbook
> (operator executes offline).

## 1. Why this exists

R3.2 is **"closed pending the acceptance-evidence runbook execution"**
(`docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` §11). The
installer R1 added its own 10-item deferred operational packet
(`docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_INSTALLER_R1_20260524.md`
§4). Those obligations are spread across several documents. Before any
further development on this line (auto-update implementation, installer
R2), the program is **evidence-driven**: collect the real-host evidence,
adjudicate it, and let the gaps decide what gets built next. This
document is the single place that tracks that.

It changes **no runtime, no routes, no ErrorCodes, no verifiers, no
workflow**. It is a `docs/` coordination artifact only.

## 2. Sources of truth (referenced, never copied)

| Source | What it specifies |
|---|---|
| `docs/CAD_HELPER_BRIDGE_R3_ACCEPTANCE_EVIDENCE_RUNBOOK_20260524.md` | The 12 R3.2 acceptance rows — env / setup / execution / expected outcome / evidence-artifact format / archive path / signoff slot. **Execution detail lives here.** |
| `docs/CAD_HELPER_BRIDGE_R3_CLOSEOUT_REPORT_20260524.md` §7 | The consolidated 25 R3.2 deferred-signoff items (S7=5, S8=5, S9=7, S10=8). |
| `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_INSTALLER_R1_20260524.md` §4 | The 10 installer R1 deferred operational checks. |
| `docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md` | The manual install procedure the installer automates (and the fallback). |
| Per-slice `DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S7…S10…` §4.1 | The authoritative per-slice item enumeration behind the closeout §7 summary. |

For any item below, the **execution steps + expected outcome are read
from the source**, not from this document.

## 3. Consolidated worklist (35 deferred obligations)

Each row has a stable ID, a one-line pointer (NOT a re-spec), its source
reference, and the tracking fields filled during review: **Verdict**
(§4), **Archive** (the out-of-git path per §5), **Notes/gap**.

Initial Verdict for every row is `pending`.

### Part A — R3.2 deferred items (25) — `docs/...CLOSEOUT_REPORT...md` §7

Evidence for these is produced by the 12 acceptance rows in the
acceptance-evidence runbook; the **Produced-by** column maps each
obligation to the runbook row(s) that exercise it.

| ID | Obligation (pointer — see source for detail) | Slice / source | Produced-by (acceptance row) | Verdict | Archive | Notes |
|---|---|---|---|---|---|---|
| R32-S7-1 | `--reset-local-token` PowerShell **y** confirm → token replaced | S7 §4.1 | Row 10 | pending | — | |
| R32-S7-2 | `--reset-local-token` PowerShell **n** cancel → token untouched | S7 §4.1 | Row 10 | pending | — | |
| R32-S7-3 | reset refused while a helper is **running** | S7 §4.1 | Row 10/6 | pending | — | |
| R32-S7-4 | reset refused from **SSH / WinRM / RDP** (exit 1) | S7 §4.1 | Row 11 | pending | — | |
| R32-S7-5 | post-reset CAD session **re-auths** on next `PLMMATPULL` | S7 §4.1 | Row 10 | pending | — | |
| R32-S8-1 | AutoCAD 2018 **build** of `CADDedupPlugin.csproj` | S8 §3.J | Row 1 | pending | — | |
| R32-S8-2 | AutoCAD **load** of `CADDedup.bundle` | S8 §3.J | Row 1 | pending | — | |
| R32-S8-3 | `PLMMATPUSH` → helper `/sync/inbound` | S8 §3.J | Row 2 | pending | — | |
| R32-S8-4 | `PLMMATPULL` → `/diff/preview` + CAD field write + `/audit/apply-result` | S8 §3.J | Row 1 | pending | — | |
| R32-S8-5 | helper **audit DB** rows present | S8 §3.J | Row 1/2 | pending | — | |
| R32-S9-1 | Windows + AutoCAD/ZWCAD/GstarCAD **NETLOAD** | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-2 | bridge DLL loads **without missing deps** | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-3 | `(yuantus-helper-call …)` **starts/finds** helper | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-4 | success returns **JSON** | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-5 | failure returns **nil + sanitized line** | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-6 | **no token** in CAD command-line output | S9 §4.1 | Row 9 | pending | — | |
| R32-S9-7 | display-only `/audit/apply-result not-applied-display-only` row | S9 §4.1 | Row 9 | pending | — | |
| R32-S10-1 | real **ZWCAD + GstarCAD** load of the `.lsp` | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-2 | `YUANTUS_DIFF_PREVIEW` **available** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-3 | prompts **accept input** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-4 | `(yuantus-helper-call "/diff/preview" …)` returns **JSON** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-5 | displayed lines via **production CAD command-line writer** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-6 | **no DWG mutation** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-7 | `audit.db` row with correct **outcome + pull_id** | S10 §4.1 | Row 9 | pending | — | |
| R32-S10-8 | **pull_id cross-row correlation** (`/diff/preview` ↔ `/audit/apply-result`) | S10 §4.1 | Row 9 | pending | — | |

R3.2 acceptance rows **not** mapped above (they exercise S1–S6, not the
deferred S7–S10 packet) are still part of full closeout and are tracked
in Part A-bis:

| ID | Acceptance row (pointer) | Source row | Verdict | Archive | Notes |
|---|---|---|---|---|---|
| R32-ROW-3 | Procmon: detector zero registry writes | Runbook Row 3 | pending | — | |
| R32-ROW-4 | LAN `/healthz` rejected (loopback-only) | Runbook Row 4 | pending | — | |
| R32-ROW-5 | `curl.exe` → `403 ORIGIN_PROCESS_NOT_ALLOWED` | Runbook Row 5 | pending | — | **redact token** per §5 |
| R32-ROW-6 | `taskkill` stale session-file cleanup | Runbook Row 6 | pending | — | |
| R32-ROW-7 | idle 30-min auto-exit | Runbook Row 7 | pending | — | |
| R32-ROW-8 | helper + `CADDedupPlugin` coexist 30 min | Runbook Row 8 | pending | — | |
| R32-ROW-12 | Shared net46 + net6 coexist in `acad.exe` + helper | Runbook Row 12 | pending | — | |

### Part B — installer R1 deferred checks (10) — `DEV_AND_VERIFICATION…INSTALLER_R1…md` §4

| ID | Check (pointer — see source §4 for detail) | Verdict | Archive | Notes |
|---|---|---|---|---|
| INST-1 | per-user install on clean Win11 non-admin, no elevation | pending | — | |
| INST-2 | files land at exact `%APPDATA%` paths | pending | — | |
| INST-3 | first `PLMMATPULL`/`YUANTUS_DIFF_PREVIEW` triggers S3 DPAPI bootstrap (not installer) | pending | — | |
| INST-4 | startup stub resolves once cad-bridge on Support path; `YUANTUS_DIFF_PREVIEW` available; + manual-config-required fallback | pending | — | |
| INST-5 | `--skip-cad-config` leaves CAD startup untouched | pending | — | |
| INST-6 | install over running helper prompts + stops via session-file pid | pending | — | |
| INST-7 | repair preserves token / `audit.db` / `install-id.json` / `plm-bearer-token.bin` / `config.json` | pending | — | |
| INST-8 | uninstall preserves user-data set; full-purge removes it (incl. bearer) | pending | — | |
| INST-9 | signed release binaries + installer pass Authenticode verify | pending | — | |
| INST-10 | already-installed `CADDedup.bundle` adopted/overwritten in place, not duplicated | pending | — | |

**Tracked total: 25 + 10 = 35 obligations** (R32-ROW-* are the
S1–S6 acceptance rows that complete full R3.2 closeout but are outside
the 25-item deferred packet; they are tracked for completeness and do
not change the deferred count).

## 4. Review verdict schema

The reviewer adjudicates each row's collected evidence into exactly one:

| Verdict | Meaning | Effect on the exit gate (§6) |
|---|---|---|
| `pass` | Evidence present and the source's expected outcome is met. | Closes the obligation. |
| `gap` | Evidence missing or incomplete; not a defect, just not yet collected/sufficient. | Keeps the gate open; needs more evidence. |
| `blocker` | Evidence shows the expected outcome is **not** met — a real defect. | Keeps the gate open; routes to development (§6). |
| `accepted-deferred` | Owner explicitly accepts the obligation as a documented deferred risk for this cycle (named owner + reason). | Closes the obligation **for gate purposes** with a recorded acceptance. |

### Gap-table structure

Open `gap` / `blocker` rows are mirrored into a running gap table so the
next-development decision (§6) reads one place:

| Gap ID | Worklist ID | gap \| blocker | One-line finding | Suspected area (installer R1 / R2 Support-path / runtime) | Owner decision |
|---|---|---|---|---|---|

## 5. Evidence storage + redaction policy (MANDATORY)

- **Raw evidence never enters git.** Logs, screenshots, `.pml` traces,
  `audit.db` dumps, signtool output, etc. are archived by the operator
  in a secure out-of-repo location. The **Archive** column records the
  path/locator only.
- **In a PR that updates this document, the only evidence that may
  appear is: (a) the archive path/locator, (b) a short *redacted*
  excerpt, (c) the verdict + notes.** No raw artifact files are
  committed (avoids repo bloat **and** secret leakage).
- **Redaction is mandatory.** Any of the following must be `<REDACTED>`
  before an excerpt is committed:
  - the `X-Yuantus-Local-Token` header value (curl `-v` echoes it — this
    is the S11 Row 5 secret-handling rule, see the acceptance-evidence
    runbook Row 5 "Secret-handling");
  - the PLM bearer token (`plm-bearer-token.bin` contents / any
    `Authorization` value);
  - the DPAPI `local-helper-token.dat` payload;
  - any other token / credential / `Authorization`-class header.
- If a token may have been written to a shared location during capture,
  rotate it afterward via the S7 `--reset-local-token` path.
- `helper-session-*.json` excerpts may include `port`/`pid`/`image_path`
  (not secret) but must NOT include any token.

## 6. Exit criterion (rollup gate)

R3.2 + installer R1 flip from **"closed pending"** to **"fully closed"**
when **all** of:

1. every Part A (25), Part A-bis (7), and Part B (10) row has Verdict
   `pass` **or** `accepted-deferred`;
2. **zero** rows are `gap`;
3. **zero** rows are `blocker`;
4. every `accepted-deferred` row records a named owner + reason.

Until then the program stays "closed pending". The gap table (§4) drives
the **next development decision** (consistent with the agreed
evidence-driven route):

- a `blocker` (or cluster) in **install / repair / signing** → open an
  **installer R1 hardening** slice (taskbook → impl, each its own
  opt-in);
- a `gap`/pain concentrated on **Support-path manual configuration**
  (INST-4) → open the **installer R2** taskbook (per-host Support-path
  auto-registration), grounded in the real per-host key shapes the
  evidence reveals;
- all `pass`/`accepted-deferred` and the gate closes → **auto-update
  implementation** becomes unblocked (its §4 evidence-gated decisions can
  now be resolved against real-host evidence) — still its own opt-in;
- **no Windows/CAD evidence available** → this line **pauses**; pivot to
  other verifiable work. Auto-update / installer R2 are NOT started on
  prediction.

## 7. How verdicts are appended (the review loop)

1. Operator runs the referenced runbook(s) on real Windows + CAD hosts,
   archives raw evidence out-of-repo, and notes archive paths.
2. Operator (or reviewer) opens a PR updating this document: fills
   **Verdict / Archive / Notes** per row, with redacted excerpts only.
3. Reviewer adjudicates each updated row into the §4 schema, mirrors any
   `gap`/`blocker` into the gap table, and states the §6 next-step
   implication.
4. Repeat until the §6 gate closes or the line pauses.

## 8. Non-goals

- **No runtime / route / ErrorCode / Lisp / Bridge / verifier / workflow
  change.** `docs/` only.
- **No re-statement of execution steps** — those stay in the referenced
  runbooks / DEV MDs.
- **No raw evidence in git** (§5).
- **No starting auto-update implementation or installer R2** from this
  document — those are gated on §6 outcomes and each needs its own
  explicit opt-in.
- This document does not itself collect or run anything; it coordinates.
