# Claude Taskbook: CAD Helper Bridge Auto-Update — Design / Scope Lock

Date: 2026-05-25

Type: **Doc-only taskbook — design / scope-lock only.** It locks the
boundaries an auto-update implementation must honor and **explicitly
defers** the decisions that genuinely depend on real-Windows + CAD
operational evidence from the installer R1 deferred packet. Merging this
taskbook does NOT authorize any implementation, and does NOT resolve the
evidence-gated open questions in §4 — those are settled in the impl PR
only after the installer R1 operational signoff stabilizes.

> **Why a taskbook now, but not the impl now.** Auto-update *design* needs
> no real machine and fakes no verification — it can lock the update
> source, signature policy, version scheme, rollback shape, relation to
> the Inno R1 installer, disable/manual path, and CI static-guard shape.
> But auto-update *implementation* depends on how the helper actually
> behaves on real hardware during install/repair (self-replace while
> running, token/DPAPI survival across an update) — exactly what the
> installer R1 §3.I deferred packet exists to verify. Locking those
> mechanics before that evidence exists would repeat the failure mode the
> installer R1 review caught (writing unverifiable per-host logic). So
> §3 locks the machine-independent boundaries and §4 lists the
> evidence-gated decisions that the impl resolves later.

## 1. Purpose And Program Framing

This is a **standalone follow-up** to the CAD Desktop Helper Bridge
installer slice (taskbook #638 `18b83d73`, implementation #639
`bbe7f8cc`). The installer R1 explicitly deferred auto-update to a
separate slice (installer taskbook §8: *"No auto-update. Version check /
download / replace / rollback is a separate follow-up slice with its own
opt-in"*). This taskbook is that slice's **design**, not its
implementation.

It is NOT a new R-numbered design cycle. The auto-update design is
constrained by the R3.2 design + the installer R1; this taskbook
self-contains it (no new design doc).

The auto-update feature, when later implemented, keeps the four R3.2
ship artifacts + the CADDedup bundle current on a user's machine without
the operator re-running the installer by hand.

It does **not** (now or in the eventual impl):

- require administrator privileges or write `HKEY_LOCAL_MACHINE`
  (per-user, no-admin — same model as the installer R1);
- change helper Kestrel routes, Lisp commands, or `ErrorCodes`;
- pre-seed or destroy any runtime-owned file (see §2.2);
- bypass Authenticode verification of downloaded artifacts;
- introduce a Tauri / Electron Companion shell (out of R3 scope).

Prerequisites already merged:

- #614 `fff93a2`: CAD helper bridge R3.2 design.
- S1–S11 (R3.2 program, closed).
- #635 `b2c18d07` + #637 `a8648875`: S11 closeout + runbooks.
- #638 `18b83d73` + #639 `bbe7f8cc`: installer taskbook + R1 impl.

## 2. Grounded Current Reality

Grounded against `origin/main = bbe7f8cc` after the installer R1 merged.

### 2.1 What the installer R1 established (the auto-update baseline)

- **Per-user, no-admin layout.** Binaries under
  `{userappdata}\YuantusPLM\helper\` (helper + detector `.exe`) and
  `{userappdata}\YuantusPLM\cad-bridge\` (bridge DLL + `.lsp`); the
  CADDedup bundle under
  `{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle\`.
- **First-party signing is owner-local.** `pack.ps1` Authenticode-signs
  the first-party binaries (helper/detector/Shared/Bridge/CADDedupPlugin)
  + the installer `.exe`; CI builds unsigned. Auto-update reuses this
  trust anchor (see §3.B).
- **Running-helper handling.** The installer reads `pid` + `image_path`
  from `helper-session-{sessionId}.json` and stops the managed helper
  with `taskkill /FI "PID eq <pid>" /FI "IMAGENAME eq yuantus-cad-helper.exe"`.
  Auto-update faces the same "replace a possibly-running, file-locked
  binary" problem (see §4).

### 2.2 Runtime-owned files at the `%APPDATA%\YuantusPLM\` root

The runtime writes **six** files at the root (verified:
`clients/cad-desktop-helper/Shared/Identity/Paths.cs` +
`clients/cad-desktop-helper/Helper/HelperRuntime.cs:2912-2913`):

| File | Owner | Auto-update MUST |
|---|---|---|
| `local-helper-token.dat` | S3 (DPAPI local token) | preserve across update |
| `plm-bearer-token.bin` | S5 (PLM bearer token) | preserve across update |
| `config.json` | helper session login config — `server_url`/`tenant_id`/`org_id`/`default_profile_id` + `server_allowlist`, written by `JsonHelperSessionConfigStore.SaveLogin`/`ClearLogin` (`HelperRuntime.cs:1518-1545`); holds login state, not a secret token | preserve across update |
| `audit.db` | S6 (SQLite audit) | preserve across update |
| `install-id.json` | per-user-per-machine id | preserve across update |
| `helper-session-{sessionId}.json` | S3 (per-session, S3-owned lifecycle) | never touch |

Only the **binaries** live in subdirs. Auto-update replaces binaries; it
must never delete or rewrite these root files (the same preserve-set
contract the installer R1 enforces via its allow-list).

### 2.3 Spawn-on-demand + idle-exit (the update-timing constraint)

The helper is started by a CAD-side caller, single-instance via an S3
mutex, and idle-exits after 30 minutes (R3.2 design acceptance test 7).
It is NOT a service. This shapes *when* an update can swap binaries
(see §3.E): the safest window is when the helper is NOT running.

### 2.4 Signature-validation forward reference

R3.2 design `:389` and `:479` both record *"进程映像签名校验留后续增强"*
(process-image signature validation is a later enhancement). The
installer R1 produces signed first-party binaries; auto-update's
signature verification of downloaded artifacts (§3.B) is the natural
consumer of that trust anchor and a step toward that design enhancement
— but implementing the helper-side image-signature gate remains out of
this slice.

## 3. Locked Boundaries (machine-independent — decide now)

These are settled by this taskbook; the impl must honor them. None
depends on real-machine evidence.

### 3.A Update model — per-user, no-admin, pull

- Auto-update is **per-user, no-admin** — it writes only under
  `{userappdata}\YuantusPLM\` + the CADDedup bundle path, never HKLM,
  never Program Files. (Same constraint as installer R1 §3.A.)
- It is a **pull** model: the client checks a remote **update manifest**
  over **HTTPS** and downloads artifacts; there is no server push and no
  inbound port. (The helper's loopback Kestrel binding is unchanged.)
- The updater **host** (helper self-update vs a separate
  `yuantus-cad-updater.exe`) is **evidence-gated** — it is decided by
  §4 #3, whose rule is bound to §4 #1 (whether a running helper can
  self-replace). This taskbook does NOT pick it here; §3.A only pins the
  invariant that **whichever** host is chosen runs **per-user** and
  reuses the §3.B trust anchor. (Do not read §3.A as a free impl choice —
  the decision rule lives in §4 #3.)

### 3.B Trust chain — signed manifest is the root; per-artifact policy

The trust chain is a single coherent order (no "either/or"):

1. **The manifest MUST be first-party signed.** An unsigned manifest is
   never trusted (HTTPS transport alone is NOT sufficient). Verifying the
   manifest signature authenticates its contents, including every
   per-artifact `sha256` and `kind` (first-party / third-party).
2. **Every downloaded artifact MUST match its `sha256` from the
   now-authenticated manifest** before it is staged. A hash mismatch
   rejects the artifact and aborts the update (no partial apply).
3. **First-party artifacts** (the helper/detector/Shared/Bridge/CADDedupPlugin
   binaries — `kind: first-party`) MUST **additionally** pass
   **Authenticode** verification against the same first-party signing
   identity the installer R1 `pack.ps1` uses.
4. **Third-party artifacts** (`Newtonsoft.Json.dll`, the .NET runtime
   DLLs in a self-contained publish — `kind: third-party`) are NOT
   re-signed and are NOT required to carry the first-party Authenticode
   identity; they are authenticated by the **signed-manifest hash**
   (step 2) and retain their upstream vendor signatures. This is the
   installer R1 owner policy, made consistent with the manifest root of
   trust.

So the contradiction "every artifact must be first-party signed" vs
"third-party DLLs are not first-party signed" is resolved: only
`kind: first-party` artifacts require first-party Authenticode;
third-party artifacts are covered by the signed manifest's hash. The
exact signing mechanism for the manifest is an impl detail; that the
manifest is signed is a locked invariant.

### 3.C Update source — HTTPS manifest, pinned origin

- The update endpoint is an **HTTPS** URL to a version **manifest**
  (e.g. JSON) listing the latest version + per-artifact download URLs +
  hashes. The origin host is pinned (an allowlist consistent with the
  R3.2 `server_allowlist` posture — design `:735`/`:740`).
- The manifest schema is locked here at the field level (version,
  `released_at` timestamp, `artifacts[name, url, sha256, kind]` where
  `kind` is `first-party` | `third-party` per §3.B,
  `minimum-supported-version`, and the manifest **signature**); the
  `released_at` timestamp + the pinned origin support replay-protection
  (reject a manifest older than the installed one's release time).
  Concrete URLs are configuration, not code.

### 3.D Version policy

- Semantic version compare; update only when manifest version > installed
  version. `helper_version` already exists in the session document
  (`HelperRuntime.cs` `HelperSessionDocument.HelperVersion`) — the
  installed-version source of truth is decided in impl (manifest-vs-file).
- A `minimum-supported-version` in the manifest may force-update older
  clients; below it the client refuses to run stale and prompts.
- No downgrade: never apply a manifest version older than installed.

### 3.E Rollback / failure strategy — SHAPE only

- Apply must be **atomic per artifact set**: download + verify ALL
  artifacts to a staging area first; only swap in after every artifact
  verifies. A mid-download failure leaves the installed version intact.
- On a failed swap, **roll back** to the prior binaries (keep the prior
  set until the new set is confirmed launchable).
- The update never proceeds while it cannot safely replace a running,
  file-locked helper — it defers to the next idle window (see §4 for the
  evidence-gated mechanics).
- **The shape is locked; the concrete swap/rollback mechanics are
  evidence-gated (§4).**

### 3.F Disable / manual-update path

- A per-user setting disables auto-update. To avoid contradicting the
  §2.2 / §3.G invariant that the updater never rewrites the six
  runtime-owned root files, the disable state lives in **updater-owned
  state** — a CLI flag `--no-auto-update` and/or an updater-owned
  settings file the updater manages itself (NOT `config.json`, which is
  helper-owned session-login config and is preserve-only to the updater).
  When disabled, the client never contacts the update origin.
- The installer R1 manual procedure
  (`docs/CAD_HELPER_BRIDGE_R3_INSTALL_RUNBOOK_20260524.md`) remains the
  always-available manual update path (re-run the installer).
- Auto-update is **opt-outable** and must fail safe: any update error
  leaves the working installed version running.

### 3.G Relation to the Inno R1 installer

- Auto-update updates the **payload** the installer R1 laid down; it does
  NOT replace the installer. First install / uninstall / repair stay with
  the Inno installer.
- Auto-update reuses the R1 install paths (§2.1) and preserve-set (§2.2)
  verbatim — it is bound by the same allow-list / never-touch-root rules.
- Whether the installer R1 ships the updater component (and how it's
  registered to run per-user) is an impl decision (§4), but the updater
  is delivered/updated through the same signed channel.

### 3.H No runtime / route / ErrorCode / Lisp / Bridge change

The eventual impl must not edit helper Kestrel routes (count stays 10),
Lisp commands (count stays 1), `ErrorCodes`, the S6 audit substrate, the
existing static verifiers, Python FastAPI source, or schema / migration /
tenant-baseline data. New surface is confined to the updater component +
its own static verifier + a CI path filter.

### 3.I Production-seam coverage — 5th application of the without-fakes rule

Building, signing, downloading, verifying, and swapping update artifacts
on a real Windows host is **environment-prohibited on CI** (Windows host,
owner-held signing cert, real network origin, real running-helper swap).
This is the **fifth application** of the
`feedback_production_seam_tests_without_fakes` rule (after S7, S9, S10,
and the installer R1): the seam cannot be exercised end-to-end on CI, so
the eventual impl's coverage shape is a **static verifier** (source-pins
the updater's invariants: HTTPS-only, Authenticode-verify-before-swap,
atomic staging, preserve-set untouched, disable path, no HKLM) **plus a
deferred operational signoff** packet. No fake stands in for the real
download/verify/swap.

## 4. Evidence-Gated Open Questions (do NOT lock now)

These depend on installer R1 §3.I real-machine evidence. The eventual
impl PR resolves them — and only after the installer R1 operational
signoff stabilizes. The taskbook records them as open, with the
constraint each answer must satisfy.

1. **Helper self-replace while running.** Can a net6.0 self-contained
   `yuantus-cad-helper.exe` be replaced on disk while a prior instance is
   running, or must the updater always wait for idle-exit / stop it via
   the R1 session-file pid mechanism? (Depends on R1 deferred item 6 —
   running-helper stop behavior on real hardware.) Constraint: must never
   corrupt a running helper or leave a half-swapped binary set.
2. **Token / DPAPI survival across update.** Does replacing the helper
   binary preserve the DPAPI `local-helper-token.dat` + `plm-bearer-token.bin`
   usability (DPAPI is user-scoped, not binary-scoped — likely yes, but
   unverified)? (Depends on R1 deferred item 7 — repair preserves the
   token set.) Constraint: a user logged in before the update stays
   logged in after, or is cleanly re-prompted — never silently broken.
3. **Updater host choice (self-update vs separate updater exe).** Decided
   only once #1 is known: if the helper cannot self-replace while running,
   a separate short-lived `yuantus-cad-updater.exe` that runs when the
   helper is down is the likely shape. Constraint: per-user, signed,
   reuses §3.B.
4. **Update trigger timing.** On helper idle-exit? On next CAD-side
   spawn? A scheduled per-user check? (Depends on #1 + the real
   idle/spawn timing from R1 evidence.) Constraint: never interrupts an
   in-flight CAD operation; never holds a CAD host waiting.
5. **CADDedup bundle hot-swap.** Can the bundle under
   `Autodesk\ApplicationPlugins\` be updated while AutoCAD is open, or
   only between sessions? (Depends on R1 deferred items 2/10 — bundle
   adopt/overwrite behavior on a real AutoCAD host.) Constraint: never
   corrupts a loaded AutoCAD session.

The impl PR's DEV/Verification MD must state, for each of #1–#5, the R1
evidence that settled it before the mechanic was implemented.

## 5. R1 (impl) Target Output — for the LATER, separately-opted-in impl

(Recorded so the taskbook is complete; NOT authorized by this merge.)

- an updater component under `clients/cad-desktop-helper/` (exact dir
  decided per §4 #3), with its static verifier;
- a `.github/workflows/cad-helper-shared-dotnet.yml` path-filter entry
  for the new dir (created same-PR, per the #636 lesson);
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_AUTO_UPDATE_R1_*.md` with
  the §4 evidence-resolution table + the deferred operational packet;
- a `docs/DELIVERY_DOC_INDEX.md` entry (lexically sorted).

The impl PR must NOT contain: runtime/route/ErrorCode/Lisp/Bridge edits;
a real signing cert in CI; schema / migration / tenant edits; resolution
of any §4 item without citing the R1 evidence that settled it.

## 6. Mandatory Static Guards (for the eventual impl)

The eventual `verify_auto_update_static.py` must source-pin, at minimum:

1. update transport is **HTTPS-only** — reject any `http://` update URL;
2. the **manifest signature is verified first** (an unsigned manifest is
   rejected); then **every artifact's `sha256` is checked against the
   signed manifest** before staging; then **`kind: first-party` artifacts
   additionally pass first-party Authenticode** — all verification
   precedes any file move into the install paths (§3.B trust chain);
3. apply is **atomic** — a staging area + an all-verified gate before any
   swap; no per-artifact swap interleaved with download;
4. the **preserve set** (§2.2 six root files) is never deleted/rewritten
   by the updater — including `config.json`; `helper-session-*.json` is
   never touched;
5. **no HKLM** / no Program Files / no service registration;
6. a **disable path** exists in **updater-owned state** (CLI flag +
   updater-owned settings file, NOT `config.json`); when off, no network
   call to the origin;
7. update **origin is pinned** (allowlist), not arbitrary;
8. **no downgrade** — version compare refuses older-than-installed
   (and, with `released_at`, refuses a manifest older than installed);
9. the **updater component itself is signed** with the same first-party
   Authenticode identity as the payload (§3.B trust anchor) — an unsigned
   updater is rejected.

Plus the doc-index drift suite + `test_workflow_trigger_glob_paths_match_repo_targets`
for the new path filter + the R2 portfolio / Tier-B drift contracts
unchanged.

## 7. Verification Plan (this taskbook PR)

```bash
python3 -m pytest -q \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_workflow_trigger_paths_contracts.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py

git diff --check
```

This taskbook is doc-only; it triggers `contracts`, not
`cad-helper-shared-dotnet`.

## 8. Explicit Non-Goals

- **No implementation.** This taskbook locks design + scope only.
- **No resolution of the §4 evidence-gated decisions** — those wait for
  installer R1 operational signoff and are settled in the impl PR.
- No admin / machine-wide / MSIX update mechanism.
- No server-push / inbound-port update channel.
- No unauthenticated update source or unsigned-artifact apply.
- No helper route / Lisp command / `ErrorCodes` additions.
- No Tauri / Electron Companion.
- No schema / migration / tenant-baseline edits.
- No CAD pool R2 work.
- The auto-update **implementation** remains gated on installer R1
  real-machine signoff stabilizing, AND requires its own per-slice
  explicit opt-in after this taskbook merges.

## 9. Recommended Branch For Implementation

After this taskbook merges AND installer R1 operational signoff
stabilizes AND a separate explicit opt-in:

```text
feat/cad-helper-bridge-auto-update-r1-<date>
```

(This taskbook is authored on `docs/cad-helper-bridge-auto-update-taskbook-20260525`.)

Do not start the auto-update implementation from this taskbook PR.

## 10. Reviewer Focus

1. Confirm the slice is correctly framed as **design / scope-lock only**,
   standalone follow-up to the closed R3.2 + installer R1, no new design
   doc, no implementation.
2. Confirm the **locked boundaries (§3)** are all machine-independent —
   nothing in §3 secretly depends on real-machine evidence.
3. Confirm the **evidence-gated open questions (§4)** are genuinely the
   decisions that need R1 real-machine behavior, and that the taskbook
   does NOT prematurely resolve them.
4. Confirm the **preserve set (§2.2)** matches the six real root files
   (`Paths.cs` + `HelperRuntime.cs:2912-2913`) the installer R1 enforces.
5. Confirm **signature verification (§3.B)** reuses the installer R1
   first-party trust anchor and that no unauthenticated manifest /
   unsigned artifact is ever trusted.
6. Confirm the **5th application** of the production-seam-without-fakes
   rule (§3.I) is correctly shaped (static verifier + deferred signoff).
7. Confirm the impl remains **doubly gated**: installer R1 signoff
   stabilized AND a separate opt-in.

## 11. Status

Ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it;
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
