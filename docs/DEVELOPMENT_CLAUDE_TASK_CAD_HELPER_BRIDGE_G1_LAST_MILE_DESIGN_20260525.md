# Claude Taskbook: CAD Helper Bridge G1 — Client "Last-Mile" Design / Scope-Lock

Date: 2026-05-25

Type: **Doc-only taskbook (program-level design / scope-lock).** Changes no
runtime, no schema, no workflow, and no helper / bridge / plugin / client
code. It maps the existing backend primitives to the client surface that a
later, separately opted-in slice program will deliver, and locks the
program scope and slice shape. Merging this taskbook does **NOT** authorize
any implementation. It also does **NOT** pre-write per-slice taskbooks —
each slice gets its own `DEVELOPMENT_CLAUDE_TASK_*` doc with its own tests,
JSON shapes, and command flow.

## 1. Purpose

"G1 — CAD 客户端最后一英里" is the #1 gap in
`docs/DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` §5: the **backend
PLM primitives for checkout / checkin / BOM-import already exist**; what is
missing is the **in-CAD command layer that drives them**, productized and
across CAD systems. odooplm's edge is the in-CAD one-click closed loop, not
"can the server record a checkin" — the server side is done.

This taskbook is the **program-level map and scope-lock** for closing that
gap. It:

- grounds the existing backend primitives the last-mile must reuse (§2.1);
- grounds the helper / LISP current surface and its boundaries (§2.2–2.3);
- shows the cross-CAD client matrix (§2.4);
- defines **the** action → CAD-command → helper-route → backend-primitive
  mapping (§3 — the centerpiece, the literal "把 … 映射清楚");
- names the cross-cutting design constraints to resolve **in slices**, not
  here (§4);
- proposes a slice **shape and dependency graph** without committing slice
  numbers (§5);
- locks program boundaries and non-goals (§6–7).

## 2. Grounded Current Reality

Grounded against `origin/main = 0b0fe8f5`.

### 2.1 Backend primitives that ALREADY exist (the "first 99 yards")

These are reused **as-is** by the last-mile. No backend changes are in G1
scope.

- **AMLEngine RPC** (`src/yuantus/meta_engine/services/engine.py`):
  `rpc_check_out` (`:499`), `rpc_check_in` (`:517`), `rpc_undo_check_out`
  (`:537`), plus `rpc_compare_bom` / `rpc_get_bom_structure` already present.
- **`cad_checkin_router`** (prefix `/cad`,
  `src/yuantus/meta_engine/web/cad_checkin_router.py`):
  - `POST /cad/{item_id}/checkout` (`:129`) → `CheckinManager.checkout` →
    locks item, returns `{status, message, locked_by_id}`;
  - `POST /cad/{item_id}/undo-checkout` (`:157`) → unlock;
  - `POST /cad/{item_id}/checkin` (`:169`) → **multipart `UploadFile`** →
    `CheckinManager.checkin(item_id, content, filename)` → new version +
    CAD conversion jobs; **auth-gated** (`get_current_user`) and
    **quota-enforced** (`QuotaService.evaluate(tenant_id, deltas={"files":1,
    "storage_bytes": len(content)})`); returns `CadCheckinResponse`
    (`version_id`, `generation`, `file_id`, `conversion_job_ids`,
    `status_url`, `file_status_url`);
  - `GET /cad/{item_id}/checkin-status` (`:233`) → `CadCheckinStatusResponse`.
- **`CadBomImportService.import_bom`**
  (`src/yuantus/meta_engine/services/cad_bom_import_service.py:235` /
  `:240`): accepts a **tree** BOM payload (`_walk` parent/child, refdes /
  位号, localized text, qty/uom normalization) and materializes Item
  relationships. **No HTTP route binds `import_bom` directly today** — its
  only caller is the async worker (`tasks/cad_pipeline_tasks.py:1428`). The
  current HTTP exposure is the **async CAD-import pipeline**: `POST /cad/import`
  (`web/cad_import_router.py:99`, multipart) with `create_bom_job` →
  `_plan_and_enqueue_jobs` (`services/cad_import_service.py:753`) enqueues a
  `cad_bom` job. So the G1 BOM slice must choose — **reuse the async
  pipeline**, or **first add a server-side direct tree-payload route** (which
  is its own server-side taskbook per §6, not a client-only slice).

### 2.2 Helper current surface (10 routes; no checkout/checkin)

Production helper Kestrel routes after S8+S9 are exactly ten (per S10
taskbook §2.4):

```text
GET  /healthz      GET  /version       GET  /session/status
POST /session/login  POST /session/logout
POST /cad/current-drawing  POST /diff/preview
POST /sync/inbound   POST /sync/outbound   POST /audit/apply-result
```

- The helper has **no** `/checkout`, `/undo-checkout`, `/checkin`, or
  BOM-upload route today.
- Dedup is **legacy-direct** (`DedupApiClient` → `/api/dedup/check`), not a
  helper route (tests guard "must not enter helper; route count == 10").
- S8 ("MaterialSync migration") already **migrated material-sync into the
  helper** — i.e. the helper bridge is the converging unified client, which
  is why G1 is framed as a helper-bridge program even though it spans CAD
  hosts.

### 2.3 LISP boundary (S10)

`clients/cad-desktop-helper/Lisp/yuantus_cad_helper.lsp` exposes exactly one
command, `YUANTUS_DIFF_PREVIEW`, and is **display-only**: forbidden
`(entmake`/`(entmod`/`(entdel`/…, `outcome = "not-applied-display-only"`.
The S9 NETLOAD bridge `(yuantus-helper-call "<endpoint>" "<json>")` is the
single transport egress for native-CAD LISP.

### 2.4 Cross-CAD client matrix (today)

| CAD host | Surface | auth | material write-back | checkout/checkin | BOM-from-assembly |
|---|---|---|---|---|---|
| ZWCAD / GstarCAD | helper bridge + LISP | ✅ `/session/login` | ❌ (R3 defers) | ❌ display-only | ❌ |
| AutoCAD | `clients/autocad-material-sync` (C#) + helper (S8) | ✅ | ✅ material fields | ❌ | ❌ |
| SolidWorks | `clients/solidworks-material-sync` (C#) | ✅ | ✅ material fields | ❌ | ❌ |
| Inventor / SolidEdge / ThinkDesign | — | ❌ | ❌ | ❌ | ❌ |

G1 reads across this matrix: the last-mile is **not** one LISP command — it
is per-host command surfaces over shared helper routes.

## 3. The Last-Mile Mapping (centerpiece)

Each row = one action. The **backend primitive already exists**; G1 adds the
**helper route** (cross-CAD) and the **per-host CAD command** that calls it.

| Last-mile action | Proposed CAD command (per host) | Helper route to add | Backend primitive (existing) | Today's boundary |
|---|---|---|---|---|
| Lock for edit | `YUANTUS_CHECKOUT` (LISP) / ribbon (C#) | `POST /document/checkout` | `POST /cad/{item_id}/checkout` `cad_checkin_router.py:129` | no helper route; LISP display-only |
| Release lock | `YUANTUS_UNDO_CHECKOUT` | `POST /document/undo-checkout` | `POST /cad/{item_id}/undo-checkout` `:157` | none |
| Check in saved file | `YUANTUS_CHECKIN` | `POST /document/checkin` (multipart proxy) | `POST /cad/{item_id}/checkin` `:169` (auth + quota) | none; auth/quota propagation unsolved (§4.1–4.2) |
| Status / lock owner | `YUANTUS_STATUS` | `GET /document/status` | `GET /cad/{item_id}/checkin-status` `:233` | none |
| BOM from assembly tree | `YUANTUS_BOM_UPLOAD` | `POST /bom/upload` | `CadBomImportService.import_bom` `cad_bom_import_service.py:240` (**service only**) | **no direct route today** — only async `/cad/import` pipeline (§2.1); payload also needs a **per-host assembly walker** (§4.3) |

Route names above are **proposals**; the owning slice ratifies the final
names. The point is the column-4 binding: G1 builds columns 2–3 onto the
already-present column 4.

## 4. Cross-Cutting Design Constraints (name here; resolve in slices)

1. **Auth / tenant / quota propagation.** Backend `/cad/.../checkin`
   requires `get_current_user` and runs `QuotaService.evaluate(tenant_id,
   …)`. The helper has `/session/login` (PLM JWT). A slice must define how
   the helper forwards the session JWT + tenant context to the upload proxy
   so the backend's auth + quota gate sees the real user. The helper must
   **not** re-implement quota.
2. **Multipart upload bridging.** Backend checkin is `UploadFile =
   File(...)`. The helper proxy must stream the **saved drawing file bytes**
   from disk to the backend as multipart; it must not pre-validate quota
   (the backend owns that, returning `429 QUOTA_EXCEEDED` /
   `X-Quota-Warning`).
3. **Checkin ≠ DWG mutation (does NOT relax the S10 guard).** Checkin =
   `QSAVE` (or host equivalent) then upload the saved file — **no entity
   creation/modification**. So the S10 `(entmake`/`(entmod` prohibition
   stays intact; a checkin LISP command is still display-/save-only at the
   DWG-entity level. By contrast, **BOM-from-assembly** requires reading the
   assembly structure via the **per-host CAD SDK** (SolidWorks API, Inventor
   API, AutoCAD/ZWCAD/GstarCAD object model) to build the tree payload — this
   is **N per-host tracks**, not one command, and is the heaviest part of G1.
4. **Cross-CAD convergence.** The helper bridge is the shared transport
   (S8 migrated material-sync in). Helper routes are cross-CAD; the **command
   surface and the assembly walker are per-host tracks**. No slice may imply
   "one command unlocks all CAD hosts".

## 5. Proposed Slice Shape & Dependency Graph (numbers TBD by team)

This taskbook does **not** commit slice numbers (S12+ vs a new sub-series
`G1-A/B/…`) — that naming is a team decision. It proposes the **shape**:

```text
[Slice: helper lock routes]  -> proxy /document/checkout + /undo-checkout + /document/status
        (cross-CAD; reuses cad_checkin_router; no CAD command yet)
                │
                ▼
[Slice: helper checkin proxy] -> /document/checkin multipart proxy
        (depends on §4.1 auth/tenant + §4.2 multipart; backend quota as-is)
                │
                ▼
[Slice: LISP checkout/checkin/status commands] -> YUANTUS_CHECKOUT/CHECKIN/STATUS past display-only
        (per ZWCAD/GstarCAD; depends on the two helper-route slices;
         relaxes S10 ONLY for QSAVE-then-upload, never entity mutation)
                │
                ├──► [Per-host track: AutoCAD command surface]   (C# / existing client)
                ├──► [Per-host track: SolidWorks command surface]
                └──► [Track: BOM-from-assembly walker, per host]  -> /bom/upload -> import_bom
                        (N parallel tracks; heaviest; one per CAD SDK)
                │
                ▼
[Slice: productization] -> public command-index page; unified installer already exists
        (per docs/...INSTALLER... + ...AUTO_UPDATE...; align to odooplm /client model)
```

Each box becomes its own `DEVELOPMENT_CLAUDE_TASK_*` taskbook + `R1`
implementation under the existing S-series ceremony.

## 6. Program-Level Boundaries (scope-lock)

Every G1 slice must hold these invariants (the per-slice taskbook restates
the slice-specific subset):

- **No backend primitive changes.** Reuse `cad_checkin_router`,
  `CadBomImportService.import_bom`, and the AMLEngine RPCs unchanged. If a
  backend change is needed, it gets its own server-side taskbook **before**
  the client slice (per S10 §3.G precedent).
- **Helper route additions are explicit and counted.** A slice states the
  exact new route count (`10 → 10+N`); dedup stays legacy-direct.
- **S10 display-only guard is not globally relaxed.** Only a slice that
  explicitly owns "QSAVE-then-upload" may add a non-display LISP command, and
  even then **no DWG entity mutation** — the `(entmake`/`(entmod` prohibition
  remains.
- **Per-host tracks are isolated.** No cross-host coupling; no "one command
  for all CAD".
- **No change to S1–S11 contracts** (session, audit, security gate, transport
  bridge) beyond additive routes a slice declares.

## 7. Explicit Non-Goals

This taskbook (and G1 as scoped) does NOT:

- specify any slice's internals — test names, JSON schemas, per-command LISP
  flow, error codes (those live in per-slice taskbooks);
- commit slice numbers or a branch-per-slice plan;
- touch G2 (PLM→ERP transaction surface), G3 (3D visual explode), or G5
  (spare parts) — those are separate program lines;
- authorize any implementation PR;
- change backend routes, schema, workflow, or tenant data.

## 8. Recommended Branch (FIRST slice only, after a separate opt-in)

Do **not** start any implementation from this taskbook PR. When the team
ratifies slice naming and opts in to the **first** slice, use a branch that
mirrors that slice's `DEVELOPMENT_CLAUDE_TASK_*` name, e.g.:

```text
feat/cad-helper-bridge-<ratified-slice-name>-r1-<date>
```

## 9. Reviewer Focus

1. Confirm the §3 mapping is accurate: every "backend primitive" column
   cites a real, current `file:line` and the last-mile only adds columns 2–3.
2. Confirm §4.3 correctly states checkin does **not** relax the S10
   entity-mutation guard, and that BOM-from-assembly is N per-host SDK
   tracks, not one command.
3. Confirm §4.1/§4.2 name the auth/tenant/quota propagation and multipart
   bridging as real, unsolved design points (deferred to slices, not
   hand-waved).
4. Confirm §5 proposes slice **shape** only and does not pre-commit slice
   numbers or per-slice internals (program scope-lock, not a slice taskbook).
5. Confirm §6 boundaries keep backend primitives and S1–S11 contracts
   unchanged, and helper route additions explicit/counted.
6. Confirm cross-CAD matrix (§2.4) is honest about what exists per host.

## 10. Status

This taskbook is ready for review once:

- the doc exists at the canonical path;
- `docs/DELIVERY_DOC_INDEX.md` references it (sorted position);
- doc-index / R2 / Tier-B drift checks pass;
- `git diff --check` is clean.
