# DEV & Verification: OdooPLM 19 CAD-PDM WP1.2 Traversal (impl, PR 1/2)

Date: 2026-06-05

Implements the **traversal half** of WP1.2 from
`DEVELOPMENT_WP1_2_PDM_TRAVERSAL_AND_STALE_DRAWINGS_TASKBOOK_20260605.md` (#726,
D1–D7). This is PR 1 of 2; the `stale-drawings` thin slice is a separate PR (PR 2).
Read-only traversal only — no writes, no staleness-core changes.

## Scope (this PR)

Two read endpoints under the WP1.0-D4 `/pdm/items/...` prefix:
- `GET /pdm/items/{item_id}/relationships?kind=&direction=outgoing|incoming|both`
  — one-level relationships (any kind, incl `REFERENCE`).
- `GET /pdm/items/{item_id}/relationship-tree?kinds=ASSEMBLY&max_depth=10&projection=tree|flat`
  — recursive containment tree (ASSEMBLY only).

## As built (against the locked contract)

- **D1** — tree follows `ASSEMBLY` only; `REFERENCE`/unknown kind into the tree →
  **422** (never folded in). `REFERENCE` is reachable via the one-level
  `relationships` endpoint.
- **D2** — `projection=tree` keeps shared-part duplicates; `projection=flat`
  dedupes by item with `occurrence_count`/`min_depth`/`first_path`/
  `first_relationship_path`. **Root is included** in both.
- **D3** — `max_depth` default 10, hard cap **50** (`>50` or `<1` → 422); no
  unbounded. **Plus a total-node budget** (`MAX_TRAVERSAL_NODES=50_000`): the
  path-based cycle guard stops cycles but NOT shared-part (diamond) path
  explosion, and `max_depth` caps depth only, not breadth — so a part reachable
  via K ancestor paths expands K times and can blow up. Exceeding the node budget
  raises `TraversalBudgetError` → **422** (fail loud, not OOM). Applies to `flat`
  too (it builds the tree first, so it is NOT a safe escape). A bounded O(V+E)
  memoized flat is a tracked follow-up before pack-and-go scales.
- **D4** — **path-based** cycle guard (an ancestor reappearing → `cycle=true`,
  stop descending; a shared part in a different branch is kept, not a cycle).
- **D5** — `RelationshipService.get_item_relationships` /
  `get_relationship_tree` (`relationship/service.py`); router
  `web/pdm_relationship_router.py` (prefix `/pdm`); registered in `api/app.py`.
  No `/pdm/cad/` or `/documents/...`.
- **§3 contract** — rows/nodes explicitly distinguish `relationship_id` (the edge,
  itself an Item) from the counterpart `item_id`; `via_relationship` carries
  `quantity/uom/position/properties`; tree nodes carry `path`/`relationship_path`.
- Errors mirror existing routers: root missing → 404, non-`Part` → 400,
  permission via `MetaPermissionService` (`AMLAction.get`), `... from exc`.
- **D7** — +2 routes → **704**; all **4 route-count pins** bumped 702→704
  (metrics `EXPECTED_TOTAL_ROUTES`, phase4 authoritative pin, breakage-metrics
  secondary pin, portfolio meta-contract literal). New test in `ci.yml` contracts
  list (sorted) + `conftest.py` no-DB allowlist.

## Not in this PR (PR 2 / non-goals)

- `GET /cad/items/{root_id}/stale-drawings` (the stale-drawings thin slice;
  route-count 704→705) — separate PR, reuses `needs_update` read-only.
- No relationship **write** endpoints; no pack-and-go; no staleness-core change.

## Verification (Python 3.11 venv, requirements.lock)

- `pytest test_pdm_relationship_traversal.py` → **17 passed** (counterpart
  item_id≠relationship_id, incoming/kind filter, multi-level tree +
  via_relationship/path, ASSEMBLY-only, path-based cycle, diamond tree-dup +
  flat-dedup, max_depth truncation, **stacked-diamond node-budget abort (service
  tree + flat, router 422)**, router 404/400/403/422 + happy path).
  `kind=None` is constrained to PDM kinds (ASSEMBLY+REFERENCE), not every
  is_relationship type.
- `create_app()` → **704 routes**; all 4 route-count contracts pass.
- WP1.1 `test_pdm_relationship_types.py` (shares the extended service) + ci-list
  ordering → pass.
- Full CI contracts list (305+ files) run locally → green (see PR).
- `git diff --check` clean.
