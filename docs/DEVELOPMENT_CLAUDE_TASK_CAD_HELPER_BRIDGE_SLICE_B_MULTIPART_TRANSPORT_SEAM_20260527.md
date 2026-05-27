# Claude Taskbook: CAD Helper Bridge — Slice B Client Multipart Transport Seam (Design + Envelope Pin)

Date: 2026-05-27

Type: **Doc-only taskbook (design / scope-lock + envelope pin).** Its primary
job is to **pin the canonical client→helper multipart envelope** and scope the
transport seam that would produce it. It changes no runtime, schema, workflow,
or client/helper code. **Merging this taskbook does NOT authorize the Slice B
implementation** — that requires its own explicit opt-in.

Parent scope-lock: `DEVELOPMENT_CLAUDE_TASK_CAD_HELPER_BRIDGE_COMMAND_WIRING_20260526.md`
(#653), which split the work into Slice A (JSON command wiring — merged, #655),
**Slice B (this — client multipart transport seam)**, and Slice C (multipart
command wiring, gated on B). The COMMAND_WIRING memo (§4 / D4) required that
"whatever transports B picks, the B taskbook **must select one canonical
multipart envelope** shared across them — no per-transport synonym divergence."
This taskbook does that.

## 1. Why Slice B exists

The two multipart helper routes are merged — `POST /document/checkin` (G1-B) and
`POST /document/bom-import` (G1-C) — but **no client transport can call them**:

- the LISP bridge primitive `(yuantus-helper-call endpoint json)` sends a JSON
  string body only (registered once in `Bridge/Adapters/AutoCadHostAdapter.cs:47`);
- the C# `IMaterialSyncHelperTransport` exposes only `PostJsonAsync<T>`.

Slice B adds the **client-side multipart transport seam** so a future Slice C
command can upload a saved file. It does **not** wire any in-CAD command (that is
Slice C) and adds **no** helper route.

## 2. Canonical client→helper multipart envelope (CENTERPIECE — PINNED)

Grounded against the merged route handlers (`HelperRuntime.cs` `/document/checkin`
and `/document/bom-import`): both read the same client-facing field vocabulary —
`form["item_id"]` and `form.Files.GetFile("file")`. The pinned envelope is
therefore the same vocabulary for both routes, with route-specific `item_id`
requiredness:

| Field | Form name | Kind | Value | Required |
|---|---|---|---|---|
| Item id | `item_id` | text part | the PLM item id (UTF-8) | checkin: **yes**; bom-import: **no** (omit → server auto-creates) |
| File | `file` | file part | the saved CAD file's raw bytes | **yes** |

Pinned rules (the build R1 must hit exactly this — no synonyms):

1. Transport: `multipart/form-data` (RFC 7578). Exactly **one** file part, named
   `file`. (The helper falls back to "first file" but the canonical name is
   `file` — do not rely on the fallback.)
2. The file part's `filename` = the document's **base filename** (e.g.
   `pump.dwg`), **not** the full local path. The file part `Content-Type` =
   `application/octet-stream`.
3. The text part name is exactly `item_id` (UTF-8 value). For
   `/document/checkin`, `item_id` is required and a blank value is a client-side
   validation error before upload. For `/document/bom-import`, a blank value
   means **omit** the `item_id` part so the helper applies its auto-create root
   policy; do not send a synonym or an empty path-like field. No other client
   text parts.
4. **The client envelope carries NO workflow flags.** `create_bom_job`,
   `auto_create_part`, and any other backend form fields are added **server-side
   by the helper** (`DocumentBomImportAsync`), never by the client. The client
   sends only the route-appropriate optional/required `item_id` plus `file` for
   both routes.
5. One canonical envelope shared across **every** transport Slice B builds (LISP
   bridge and/or C#) — no per-transport field-name or part-name divergence.

Open sub-decision (D-B3 below): non-ASCII `filename` encoding.

## 3. Transport-approach decisions to ratify

- **D-B1 — which transport(s) get the multipart seam.**
  - **(a) the cad-desktop-helper LISP/NETLOAD bridge** — add a new multipart
    primitive in the bridge `.dll` alongside `yuantus-helper-call`.
    **Recommended**: it matches the Slice A channel (D2(a), #655) and feeds the
    LISP Slice C command wiring.
  - **(b) the C# `IMaterialSyncHelperTransport`** — add a multipart method. This
    interface currently lives in the AutoCAD **material-sync plugin**; SolidWorks
    has a separate JSON diff-preview client seam, not this interface. A C#
    multipart track therefore needs its own per-plugin charter/taskbook and
    **expands the material-sync charter** — the same expansion Slice A's D2(a)
    declined. Defer to its own track unless that charter decision is revisited.
  - **(c) both.** Heaviest; only if both channels are committed at once.
  - Recommendation: **(a) only** for Slice B; treat (b) as a separate future
    track tied to the material-sync charter question.
- **D-B2 — bridge multipart primitive shape (if D-B1 = a).** Propose a new
  `(yuantus-helper-upload "ENDPOINT" item-id filepath)` registered as a second
  `[LispFunction]`. The bridge (in-CAD-process, client side) reads the bytes of
  the file at `filepath` — which the Slice C command supplies from the user's
  **own open document** (`DWGNAME`/`DWGPREFIX`) — and POSTs the §2 envelope. This
  is the **caller holding its own bytes**; it is distinct from the **helper**
  reading an arbitrary local path, which G1-B/G1-C forbid. **The helper still
  receives bytes over multipart and never reads a path — its no-local-read
  guards stay intact.** The `item-id` argument is route-sensitive: nonblank is
  required for `/document/checkin`; empty is allowed for `/document/bom-import`
  and must omit the `item_id` part. Exact arity/validation pinned by the build
  taskbook.
- **D-B3 — non-ASCII filename.** For a first cut, restrict the file part
  `filename` to an ASCII-safe base name (transliterate/replace otherwise) and
  record the limitation, vs. RFC 5987 `filename*` encoding. Decide in the build
  taskbook; recorded here so it is not rediscovered late.

## 4. S10 / security framing

- The bridge reading a local file **path** is a **new client-side capability**
  for the bridge `.dll` and a new attack surface. The build R1 must validate the
  path is an existing regular file and emit sanitized errors. Because the raw
  primitive takes a `filepath`, the build taskbook must also pin a testable
  **file-source policy** before implementation: either validate/derive the path
  against the active CAD document path in the host adapter, or explicitly defer
  that host-level enforcement and add a Slice-C static guard that repo-owned LISP
  callers pass only the active document path (`DWGPREFIX` + `DWGNAME`). Do not
  describe the primitive as accepting arbitrary user-entered paths.
- S10 entity-mutation boundary is untouched: Slice B is transport plumbing, not
  a CAD command; it creates/modifies no DWG entities and (in Slice B) adds no
  `.lsp` command at all.
- The helper-side no-local-read guards (`/document/checkin` and `/document/bom-
  import` read multipart bytes, never a path) remain unchanged and must stay
  green.
- **No helper route** is added: route count stays **15**.

## 5. Static-guard surfaces Slice B would shift / add (deliverables)

Per the standing rule (every static guard is a deliverable), the build R1 must
treat these by file:line:

- **`verify_bridge_static.py:73-80` `check_single_lisp_function`** — currently
  asserts **exactly one** `[LispFunction(` **and** that it is
  `yuantus-helper-call`. Adding `yuantus-helper-upload` (D-B2) breaks both
  halves. R1 must update it to the **exact two-primitive set**
  `{yuantus-helper-call, yuantus-helper-upload}` (keep it strict — an exact set,
  not "≥1"), and add a registration/arity guard for the new primitive.
- **Route-count invariant (`== 15`, re-assert unchanged):**
  `verify_bridge_static.py:167`, `verify_lisp_shell_static.py:348`,
  `verify_material_sync_static.py:234`, the 5 C# count tests, the G1-C DEV doc.
  Slice B adds no helper route.
- **Bridge contract tests** (`Bridge.Tests/BridgeContractTests.cs`) — any
  assertion on the single-primitive surface must move to the two-primitive set.
- The new multipart primitive is **C#** in the bridge `.dll`; it cannot be built
  on this machine. The build R1 PR body must carry the literal phrase **"C#
  build/xUnit deferred to Windows CI"** and is gated on Windows CI green.

## 6. Non-Goals

This taskbook does NOT: authorize the Slice B implementation (separate opt-in);
wire any in-CAD command (that is Slice C, gated on B); add/remove a helper route;
expand the material-sync plugin charter (D-B1(b) is explicitly deferred);
implement the bridge primitive or the C# transport; decide the non-ASCII
filename encoding (D-B3, build taskbook).

## 7. Preconditions to enter the Slice B IMPLEMENTATION taskbook

1. D-B1 ratified (which transport — recommended (a) bridge only);
2. D-B2 ratified (bridge primitive shape + the caller-holds-own-bytes framing);
3. the §2 canonical envelope pinned as the build's exact target;
4. the file-source policy in §4 chosen and turned into concrete tests/guards;
5. the §5 guard shifts enumerated as concrete verifier edits (every shifted
   static guard is a deliverable), with route count held at `15`;
6. acknowledgement that the build is C# (Windows CI gate + the literal deferral
   phrase in the PR body).

## 8. Reviewer Focus

1. Confirm §1: both client transports are JSON-only, so the multipart routes
   have no caller until Slice B.
2. Ratify §2 — the canonical envelope (route-appropriate optional/required
   `item_id` text + exactly one `file` part, **no** client-side workflow flags).
   This is the must-pin.
3. Ratify §3 D-B1 (recommended (a) bridge only; (b) C# transport deferred as a
   material-sync charter question) and D-B2 (bridge primitive shape + helper
   no-local-read guards stay intact).
4. Confirm §4: the bridge reading the user's own document is caller-holds-bytes,
   distinct from helper local-read; the build taskbook must choose a testable
   file-source policy; route count stays `15`.
5. Confirm §5 enumerates the guard shifts by file:line — especially the
   `check_single_lisp_function` one→two primitive-set change kept strict.

## 9. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / sorting / Tier-B
drift checks pass; `git diff --check` is clean. Ratifying §2 + §3 sets the Slice
B build plan; **a separate explicit opt-in authorizes the implementation**, and
Slice C (multipart command wiring) remains gated on Slice B merging.
