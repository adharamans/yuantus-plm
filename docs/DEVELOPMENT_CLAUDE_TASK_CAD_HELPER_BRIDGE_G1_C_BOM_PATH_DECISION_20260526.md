# Claude Taskbook: CAD Helper Bridge G1-C — BOM Path Decision Memo

Date: 2026-05-26

Type: **Doc-only decision memo (lightweight ADR).** Changes no runtime,
schema, workflow, or client/helper code. It locks the **BOM-from-assembly
path fork** so the later G1-C implementation taskbook can be written against a
ratified shape. Merging this memo does **NOT** authorize any implementation
and is **not** itself the G1-C taskbook.

## 1. Why decide before writing the G1-C taskbook

G1-A (lock routes, #646 `2c471aac`) and G1-B (checkin multipart, #648
`6ee4e66b`) are merged. The remaining last-mile piece is **BOM-from-assembly**:
getting the CAD assembly structure to the backend so it materializes Item
relationships via `CadBomImportService.import_bom`
(`src/yuantus/meta_engine/services/cad_bom_import_service.py:240`).

That work forks on **how the helper hands the BOM to the backend**, and the
fork changes the **helper seam, server API surface, quota/job semantics, test
layering, route-count, and acceptance boundary**. Per the project rule, that
cannot be decided mid-implementation — hence this memo.

## 2. Grounded current reality (origin/main = 6ee4e66b)

- Backend BOM import is **async only** today: `POST /cad/import`
  (`web/cad_import_router.py:99`, multipart `UploadFile`) with the
  `create_bom_job` form flag (`:125`) → `_plan_and_enqueue_jobs`
  (`services/cad_import_service.py:753`) enqueues a **`cad_bom` job** → the
  worker (`tasks/cad_pipeline_tasks.py:1428`) calls `import_bom`. There is **no
  direct synchronous HTTP route** that binds `import_bom` with a tree payload.
- `/cad/import` returns a `CadImportResponse` with `file_id` and `jobs:
  [{id, task_type, status}]` (`web/cad_import_router.py:38`, `:44`, `:222`),
  not a single top-level `job_id`. A `cad_bom_url` may remain absent in that
  initial response until the async worker persists BOM output.
- Helper today: 14 routes; `IPlmBusinessClient` has JSON `PostAsync`,
  `GetAsync`, and (G1-B) multipart `PostMultipartAsync`. Response contract is
  the fixed-200 envelope; quota rides inside it (G1-B).
- The **per-host assembly walker** (SolidWorks / Inventor / AutoCAD / ZWCAD /
  GstarCAD SDK traversal that *produces* the tree payload) does not exist in
  the helper/clients yet.

## 3. The fork

### Path A — helper reuses the existing async `/cad/import` pipeline

The helper adds a route (e.g. `POST /document/bom-import`) that forwards the
uploaded CAD file (multipart) to backend `POST /cad/import` with
`create_bom_job=true`; the backend returns `CadImportResponse.jobs`, including
the `cad_bom` job entry; the client polls that job's status. The BOM tree is
**extracted server-side** by the existing pipeline; the helper does not build or
send a tree payload.

### Path B — new server-side direct BOM route

A **new backend route** accepts a **tree payload** (the assembly structure,
built client-side) and calls `import_bom` synchronously. The helper adds a
route forwarding that JSON tree payload.

## 4. Impact comparison

| Dimension | Path A (reuse async `/cad/import`) | Path B (new direct BOM route) |
|---|---|---|
| Helper seam | reuse G1-B-style **multipart** `PostMultipartAsync` (file upload) | needs a **JSON tree** forward (existing `PostAsync` may suffice) |
| Backend API contract | **none new** — reuses `/cad/import` | **new route** = backend API surface → **separate server taskbook first** |
| BOM extraction | **server-side** (existing pipeline) | **client-side** assembly walker must build the tree |
| Quota | reuses existing `/cad/import` quota/worker semantics | new route must define its own quota handling |
| Job / status | **async**: returns `jobs[]`; client polls the `cad_bom` job | **sync**: deterministic single response |
| Load fit | better for **heavy assemblies/BOMs** (worker/queue) | better for **small/deterministic** uploads |
| Tests | helper-only contract + reuse pipeline coverage | helper + **new backend route** tests + contract |
| Route-count | 14 → 15 (one helper route) | 14 → 15 (one helper route) + backend route |
| CAD-host walker | **not required for R1** (server extracts) | **required** (client builds tree) — heavier |
| Risk | **lower** (helper-only, no new backend contract) | **higher** (new backend API + walker coupling) |

## 5. Recommendation

**Adopt Path A (reuse the async `/cad/import` pipeline) for the first BOM
slice.** Rationale:

- it already has **multipart upload, job planning, quota, and worker**
  semantics — closer to the real load profile of "assembly/BOM may be heavy";
- it adds **no new backend API contract** (lower risk; no server taskbook
  gate);
- BOM extraction stays **server-side**, so the per-host assembly walker is
  **not** entangled with the helper/server contract in this R1.

**Path B is reserved** for a later, explicitly-justified need: a **synchronous,
deterministic, lightweight** in-CAD BOM-upload closed loop. Because Path B adds
a backend API contract, it must be preceded by its **own server-side taskbook**
(per the S10/§6 precedent that backend changes get a server taskbook before the
client slice).

**Per-host assembly walker** is deferred to a **later CAD-host slice**. R1 must
not mix real CAD-SDK traversal with the helper/server contract.

## 6. Preconditions to enter the G1-C taskbook

Before the G1-C implementation taskbook is written, ratify:

1. **Path A** (or an explicit Path-B justification + a separate server taskbook);
2. the helper route shape (proposed `POST /document/bom-import`, multipart,
   forwards to `/cad/import` with `create_bom_job=true`);
3. the **async UX**: helper returns the backend `CadImportResponse.jobs` (or at
   minimum the selected `cad_bom` job entry `{id, task_type, status}` plus
   `file_id`); status polling uses an existing job/BOM status surface and the
   G1-C taskbook must pin the exact route. Current grounded candidates are
   generic `GET /api/v1/jobs/{job_id}` for the `cad_bom` job and
   `GET /api/v1/cad/files/{file_id}/bom` for BOM output/state; do **not** use
   `GET /api/v1/file/conversion/{job_id}` as the `cad_bom` status surface,
   because that route is limited to file-conversion task types and excludes
   `cad_bom` (`web/file_conversion_router.py:25`, `:295`);
4. that the **per-host assembly walker is out of G1-C R1 scope** (separate
   CAD-host slice);
5. the route-count move **14 → 15** and the full contract surface to update
   (the same set as G1-B: 3 Python verifiers + Helper.Tests
   [BusinessAudit/Session/Reset] + Bridge.Tests + material-sync client tests +
   DEV doc), per the hard rule that **every §6 static guard is a deliverable,
   not documentation**.

## 7. Non-Goals

This memo does NOT: write the G1-C taskbook; authorize any implementation;
change backend routes, helper code, or tests; design the assembly walker;
commit a slice number/branch.

## 8. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / R2 / Tier-B
drift checks pass; `git diff --check` is clean. Ratifying §5 + §6 unblocks the
G1-C implementation taskbook.
