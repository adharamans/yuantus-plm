# DEV & VERIFICATION — CAD-PDM C3 BOM-line (item_id-scoped) effectivities

Date: 2026-06-19 · Branch `claude/c3-bom-line-effectivities` · base `origin/main`
(`adharamans/yuantus-plm`).

## 1. Summary

CAD-PDM C3 (date-BOM auto-obsolete) handled only **version-scoped** date effectivities; an
expiring **BOM-line** (`item_id`-scoped) effectivity was skipped as `not_version_scoped`.
But BOM lines created with dates are real: `bom_service.add_bom_line(..., effectivity_from,
effectivity_to)` stores `Effectivity(item_id=<relationship Item id>, type="Date")` on a
`Item(item_type_id="Part BOM", source_id=parent, related_id=child)` (S3.2, enabling
`/bom/{id}/effective`). So an ended parent→child usage produced no C3 signal — a real gap.

This slice handles it, **flag-only** (owner-ratified): when a BOM-line date effectivity
expires, record a single `DateObsoleteImpact` for that parent→child line and promote /
cascade nothing. A line's date window closing means *that usage ended* — not that the child
part (which may be used elsewhere) or the parent assembly is obsolete.

## 2. What changed

One service file + its existing test file. **No new route, model, migration, or setting.**
Route count stays **719**; Alembic head unchanged; the `DateObsoleteImpact` schema is reused
(the BOM-line summary lives in the existing `properties` JSON column).

- `services/date_effectivity_obsolete_service.py`:
  - `scan_expired` now passes `version_scoped_only=False`, so the sweep also sees BOM-line
    effectivities.
  - `process_expired` is now a **dispatcher**: `version_id` → the unchanged version path
    (extracted verbatim to `_process_version_expired`); `item_id` → the new
    `_process_bom_line_expired`; neither → `skipped/not_scoped`.
  - `_upsert_impact` extracted from `_flag_parents` (shared insert-or-refresh on the unique
    `(effectivity_id, parent_item_id)` key) and reused by the BOM-line path.
- `tests/test_date_effectivity_obsolete_service.py`: the old `test_skips_non_version_scoped`
  (which pinned the now-removed skip) is replaced by the BOM-line suite (below). The file is
  already registered in `ci.yml` + conftest, so no registration change.

## 3. Design & guards

A BOM-line effectivity expiry produces exactly one impact:
`parent_item_id = rel.source_id` (assembly), `child_item_id = rel.related_id` (part),
`reason = "bom_line_effectivity_expired"`, `child_obsoleted = false`, with
`properties = {bom_line_id, scope:"bom_line", uom?, quantity?, find_num?, refdes?}` for ops
visibility. The four guards (owner-specified):

1. **Type guard** — `item_id` must resolve to an Item with `item_type_id == "Part BOM"` and
   both `source_id`/`related_id` present, else `skipped`
   (`not_a_bom_line` / `bom_line_not_found`). A generic item-scoped effectivity is never
   mistaken for a BOM line.
2. **No fan-out** — the line's own `source_id`/`related_id` are used **directly**, never
   `get_where_used(child)`, so "this line expired" cannot spread to every parent of the
   child.
3. **Unique key suffices** — `(effectivity_id, parent_item_id)` is enough for this semantics;
   the line id + summary go in `properties`.
4. **Multi-UOM independence** — two BOM lines between the same parent and child (e.g.
   different UOM) are distinct relationship Items with distinct effectivities, so each
   expired line produces its own impact (distinct `effectivity_id`); neither swallows the
   other.
5. **Current-only** — a superseded line (`is_current == False`) is skipped
   (`bom_line_not_current`). `bom_obsolete` supersedes BOM lines *in place* (flips the old
   line to `is_current=False` and copies its date effectivity to the new current line), so
   without this guard the `version_scoped_only=False` scan would flag stale, no-longer-live
   usages; the copy on the current line carries the live effectivity and is flagged via its
   own current relationship Item. (Found by adversarial verify.)

Gating is unchanged: still behind the global `DATE_EFFECTIVITY_OBSOLETE_ENABLED` kill-switch
+ per-tenant `cadpdm_date_obsolete` entitlement; the worker and ops routes gain no new
privilege.

## 4. Decision (owner-ratified)

**Flag only.** A BOM-line expiry records the line and promotes/cascades nothing. The
alternative (also obsolete an orphaned child with no effective usage anywhere) was declined
as too aggressive / false-obsolete-prone, and is a separate future decision.

## 5. Verification

DB-free sqlite, harness `.venv-wp13` + `PYTHONPATH=<worktree>/src`, env DB vars unset.

- `test_date_effectivity_obsolete_service.py` BOM-line cases: flag-only (parent=source,
  child=related, reason, child_obsoleted=false, properties carry bom_line_id+uom, neither
  part nor assembly promoted); idempotent rerun → one row; **multi-UOM two lines → two
  distinct impacts** (guard 4); a plain part item-scoped effectivity AND a non-"Part BOM"
  relationship *with* both endpoints → `not_a_bom_line` (guard 1, type discriminator);
  **superseded (`is_current=False`) line → `bom_line_not_current`, 0 impacts** (guard 5);
  missing item → `bom_line_not_found`; unscoped (no version/item) → `not_scoped`;
  `scan_expired` returns both version- and BOM-line-scoped expired effectivities.
- Regression (one process): the full version-scoped C3 suite + `test_date_obsolete_wiring`
  (worker gating/drain + ops routes) + `test_date_obsolete_worker_cli` stay green
  (37 passed); `test_metrics_router_route_count_delta` = **719**.

## 6. Out of scope

- Obsoleting an orphaned child (the declined aggressive option).
- Any change to the version-scoped path (extracted verbatim), routes, models, or migrations.
- Lot/Serial/Unit BOM-line effectivities (only `Date` is handled, matching the version path).
