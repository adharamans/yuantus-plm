# DEV & Verification: OdooPLM 19 CAD-PDM WP1.2 stale-drawings (impl, PR 2/2)

Date: 2026-06-05

Implements the **stale-drawings thin slice** of WP1.2 from
`DEVELOPMENT_WP1_2_PDM_TRAVERSAL_AND_STALE_DRAWINGS_TASKBOOK_20260605.md` (#726,
D6), on top of the merged traversal PR (#728). **Read-only**: reuses WP1.3's
materialized `needs_update`, never recomputes provenance / touches staleness core.

## Scope (this PR)

- `GET /cad/items/{root_id}/stale-drawings?max_depth=10` — scan an assembly (root
  + ASSEMBLY descendants) for drawings whose `needs_update` is set.

## As built

- **Bounded reachable-set (the agreed prerequisite)** —
  `RelationshipService.get_reachable_items(root, kinds, max_depth)`: a visited-set
  BFS returning the **unique** reachable Part set with `min_depth`/`first_path`/
  `first_relationship_path`. O(V+E), so — unlike `get_relationship_tree` — it does
  **not** re-expand shared parts and cannot blow up on diamond-heavy assemblies.
  This is the safe whole-assembly scan basis (the "bounded O(V+E) flat" the review
  flagged), so PR 2 does not re-explode.
- **`CadStaleDrawingsService.scan`** — bounded reachable Part set × a single indexed
  **batch query per chunk** (`needs_update` is indexed; chunked under SQLite's
  param cap) → stale drawings with `part_id`/`path`/`drawing_file_id`/`file_role`/
  `staleness_reason`/`source_batch_id`/`import_batch_id`. No per-part N+1, no tree
  materialization, **no recompute**. Drawing selector matches WP1.3 (`document_type
  ="2d"`, `file_role in {drawing, native_cad}`).
- Root is scanned too (total-assembly drawings, incl. the top assembly's own).
- Endpoint added to the existing `cad_consistency_router` (`/cad` prefix, already
  registered): root missing → 404, non-`Part` → 400, `max_depth` 1..50 (→422),
  `MetaPermissionService` (`AMLAction.get`), `... from exc`.
- **Decision — permission scoping:** authorization is checked on the **root**; the
  scan then returns drawings of descendant parts. This matches `bom_tree_router`
  (assembly reads inherit root permission) — chosen deliberately, not missed. A
  per-part ACL filter is a follow-up if a tighter scope is ever required.
- **Depth × visited-set correctness:** the reachable-set is a shortest-first FIFO
  BFS, so each part is expanded at its **min** depth — a part reachable both
  shallow and deep is expanded via the shallow path, so its subtree (and any stale
  drawing there) is not dropped by the `max_depth` cap. Locked by an asymmetric
  test (`test_reachable_set_expands_at_min_depth_not_first_arrival` +
  `test_scan_finds_stale_under_min_depth_reachable_subassembly`).
- **D7** +1 route → **705**; all 4 route-count pins bumped 704→705 (metrics,
  phase4, breakage-metrics, portfolio meta-contract literal). New test in `ci.yml`
  contracts list (sorted) + `conftest.py` no-DB allowlist.

## Non-goals

- No staleness-core / provenance change; no recompute on scan (refresh via the
  existing single-item `POST /cad/items/{id}/staleness/recompute`).
- No `relationship-tree` flat change (its node-budget stays); no pack-and-go.
- The full `flat` projection with exact `occurrence_count` (depth-bounded
  path-count) remains a follow-up — not needed by stale-drawings.

## Verification (Python 3.11 venv, requirements.lock)

- `pytest test_cad_stale_drawings.py` → **12 passed** — bounded reachable-set on a
  stacked diamond (each shared part once, no explosion) + cycle-survival;
  **min-depth (shortest-first) expansion asymmetric case — stale drawing on a
  shallow-and-deep-reachable subassembly is not silently missed**; cross-assembly
  collection incl. root; **read-only (`staleness_checked_at` unchanged — recompute
  never ran)**; models/non-2d excluded; diamond shared-part listed once; router
  404/400/422 + happy path.
- `create_app()` → **705 routes**; all 4 route-count contracts pass.
- Full CI contracts list run locally → green (see PR).
- `git diff --check` clean.
