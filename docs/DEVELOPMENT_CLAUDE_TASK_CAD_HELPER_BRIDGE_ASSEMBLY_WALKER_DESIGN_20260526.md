# Claude Taskbook: CAD Helper Bridge — Per-Host Assembly Walker Design / Scope-Lock

Date: 2026-05-26

Type: **Doc-only taskbook (design / scope-lock).** Changes no runtime, schema,
workflow, or client/helper code. It scopes the per-host assembly walker and —
its primary job — **clarifies the walker's role relative to the merged G1-C
Path A**. Merging this taskbook does **NOT** authorize any implementation and
is **not** a walker implementation taskbook.

## 1. Purpose

The helper last-mile (G1-A lock, G1-B checkin, G1-C BOM import) is merged. The
remaining CAD-host pieces are: (a) a per-host **assembly walker**, (b) CAD-host
**command wiring**, (c) **productization**. This memo scopes (a) and resolves
the question the reviewer flagged: **does the walker have a role given G1-C
Path A, or is it deferred?**

## 2. The core clarification — walker vs G1-C Path A (CENTERPIECE)

**G1-C shipped Path A** (#651, `47068264`): the CAD-host saves the active file
and POSTs it to helper `POST /document/bom-import`, which forwards to backend
`POST /cad/import` with `create_bom_job=true`; the **backend** extracts the BOM
server-side (`cad_bom` job → `CadBomImportService.import_bom`). **No
client-built tree is sent.**

Consequence, stated plainly:

- **Under Path A, the CAD-host needs NO assembly walker for BOM.** The
  in-CAD flow is *save file → upload file → poll
  `GET /api/v1/cad/files/{file_id}/bom`*. BOM structure is produced on the
  server, from the file, by the existing connector pipeline.
- A **client-side assembly walker** (traversing the open assembly via the CAD
  SDK to build a BOM **tree** payload) is only needed to feed **Path B** — a
  server-side **direct** BOM route that accepts a tree. Path B is **reserved**
  in the BOM path decision memo (#649) and requires its **own server-side
  taskbook** before any client work.

**Therefore the per-host assembly walker is Path-B-conditional and should be
deferred.** It is **not** on the critical path for the CAD-host last-mile under
Path A. The immediate CAD-host need is **command wiring** (drive
checkout/checkin/bom-import by save+upload), which is a separate taskbook.

## 3. Decision To Ratify

- **Option A — defer the walker (recommended).** Keep the walker as a
  **Path-B-gated future track**. Make CAD-host **command wiring** the next
  implementation slice (it uses the file-upload flow G1-A/B/C already exposes;
  no tree, no walker). Open a walker implementation taskbook only after Path B
  is ratified and its server-side direct-BOM-route taskbook is merged.
- **Option B — build the walker now.** Only justified if the team commits to
  Path B imminently (synchronous/deterministic tree upload). This pulls in a
  backend API contract (the direct BOM route) **and** N per-SDK walker tracks
  at once — the heaviest, highest-risk path. Not recommended as the next slice.

Recommendation: **Option A.** This memo records the walker scope (for the
Path-B future) without putting it on the immediate path; the next CAD-host
taskbook should be **command wiring**, not the walker.

## 4. Grounded Reality

- **Path A flow** (no client tree): `/document/bom-import` (G1-C) →
  `/cad/import` `create_bom_job=true` → server extraction.
- **Walker output contract for the Path-B future** — `import_bom`
  (`services/cad_bom_import_service.py`) already accepts normalized BOM payloads
  via two shapes: explicit `nodes` + `edges` + `root`, or a nested root with
  `children`. The eventual walker taskbook must choose **one canonical shape**
  instead of relying on every synonym accepted by the normalizer. Conservative
  baseline: every node emits a stable `id` plus part attributes; edges/child
  occurrence fields use `quantity`, `uom`, `find_num`, and `refdes`. This is the
  **only** contract the walker has to hit — and it is consumed by Path B, not
  Path A.
- **Per-host client surfaces today**: AutoCAD has a C# client surface
  (`clients/autocad-material-sync`); SolidWorks has an SDK-free C# skeleton and
  gateway seams (`clients/solidworks-material-sync`) but no proven runtime COM /
  add-in adapter yet; ZWCAD/GstarCAD go through the helper LISP bridge
  (`clients/cad-desktop-helper`, display-only per S10); **Inventor / SolidEdge /
  FreeCAD have no client yet.**

## 5. IF/WHEN the walker is built (Path-B future) — per-SDK track scope

For the eventual walker implementation taskbook (not authorized here):

- **Host priority (proposal):** AutoCAD and SolidWorks **first** (AutoCAD has an
  active C# client surface; SolidWorks has SDK-free seams that can anchor a
  future COM/add-in adapter); ZWCAD/GstarCAD next (LISP/COM);
  **Inventor / SolidEdge / FreeCAD = placeholders** (no client surface yet).
- **One track per CAD SDK** — SolidWorks API, AutoCAD/ZWCAD/GstarCAD object
  model, etc. The traversal logic is **not shared** across SDKs; only the
  **output payload contract (§4)** is shared. Do not attempt one cross-SDK
  walker.
- **Input → output:** read the open assembly's component structure via the
  host SDK → emit the canonical `import_bom` payload (§4) → send to the Path-B
  direct route (not `/cad/import`).
- Each host track is its **own slice**; mixing SDKs in one R1 is forbidden.

## 6. S10 display-only boundary — walker is read-only traversal

The S10 prohibition is on **DWG entity mutation** (`(entmake`, `(entmod`,
`AddText`, etc.). An assembly walker **reads** the component structure; reading
is **not** a mutation. So a (future) walker does **not** relax or violate the
S10 boundary, provided it performs **no** entity creation/modification. The
walker implementation taskbook must restate this and keep the S10 guard intact.

## 7. Non-Goals

This memo does NOT: authorize a walker implementation; build Path B or the
server-side direct BOM route; write the CAD-host command-wiring taskbook (the
recommended next slice); do productization; commit per-SDK slice numbers.

## 8. Preconditions to enter a walker IMPLEMENTATION taskbook

1. Path B ratified (a synchronous/deterministic tree-upload need is justified);
2. the server-side **direct BOM route** taskbook merged first (it is a backend
   API change → server taskbook before any client walker);
3. a chosen first host track (SolidWorks or AutoCAD) with its SDK traversal
   approach;
4. the §4 canonical payload contract pinned as the walker's output;
5. the §6 read-only / no-DWG-mutation guard carried as a deliverable static
   check (every static guard is a deliverable, not documentation).

## 9. Reviewer Focus

1. Confirm §2: under Path A the CAD-host needs **no** walker for BOM; the
   walker feeds only Path B.
2. Ratify §3 Option A (defer the walker; make **command wiring** the next
   CAD-host slice) vs Option B (build now — only if committing to Path B).
3. Confirm §4 walker output = a future canonical `import_bom` payload contract
   (not every accepted synonym), consumed by Path B, not Path A.
4. Confirm §5 per-SDK tracks (host priority, one track per SDK, placeholders).
5. Confirm §6: a future walker is read-only traversal and does not touch the
   S10 entity-mutation guard.

## 10. Status

Ready for review once: the doc exists at the canonical path;
`docs/DELIVERY_DOC_INDEX.md` references it (sorted); doc-index / R2 / Tier-B
drift checks pass; `git diff --check` is clean. Ratifying §3 sets the next
CAD-host slice (command wiring, or a Path-B program if Option B).
