# Development Taskbook: WP1.2 bounded flat projection

Date: 2026-06-09
Type: doc-only scope lock. This taskbook authorizes a later implementation PR,
not implementation in this PR.

## 1. Why This Slice Exists

WP1.2 shipped two related but different surfaces:

- `relationship-tree?projection=tree|flat` (`RelationshipService.get_relationship_tree`).
- `stale-drawings` (`CadStaleDrawingsService.scan`).

The stale-drawings path is already bounded: it uses
`RelationshipService.get_reachable_items`, a visited-set BFS that returns a unique
reachable Part set and avoids diamond re-expansion.

The public `relationship-tree?projection=flat` path is not yet bounded in the same
way. It currently builds the full duplicate-preserving tree first, then flattens it.
The node budget prevents OOM, but large shared-part assemblies can still fail with
`TraversalBudgetError` even when the caller only asked for `projection=flat`.

This slice closes that follow-up: make flat projection bounded without changing the
tree projection contract. "Bounded" here means no full duplicate-tree
materialization; exact path counts may still require depth-bounded contribution
counting rather than a simple unique visited-set.

## 2. Grounded Facts

- `relationship/service.py` defines `MAX_TRAVERSAL_NODES = 50_000` and explicitly
  notes that bounded flat projection is a tracked follow-up. The follow-up is
  "do not materialize the duplicate tree"; exact path-count semantics below are
  stricter than a plain O(V+E) unique-reachability scan.
- `get_relationship_tree(..., projection="flat")` currently calls `_build_node`
  first, then `_flatten_node`; therefore flat inherits tree materialization and the
  shared-part path-explosion budget.
- `_flatten_node` gives the flat contract: root included, item-level de-duplication,
  `occurrence_count`, `min_depth`, `first_path`, and `first_relationship_path`.
- `get_reachable_items` is already O(V+E) and powers stale-drawings, but it does not
  expose `occurrence_count`.
- `DEV_AND_VERIFICATION_ODOOPLM_19_CADPDM_WP1_2_STALE_DRAWINGS_IMPL_20260605.md`
  says stale-drawings intentionally did not change `relationship-tree` flat; exact
  `occurrence_count` remained a follow-up.

## 3. Locked Decisions

### D1 - Scope

Only change the flat projection implementation. No route, migration, schema,
stale-drawings, pack-and-go, lifecycle, or permission changes.

### D2 - Tree Contract Stays Duplicate-Preserving

`projection=tree` keeps the current path-based semantics:

- shared parts are represented under every parent path;
- only ancestor reappearance is a cycle;
- `MAX_TRAVERSAL_NODES` still guards materialized tree expansion.

This task must not globally memoize tree nodes or collapse shared subtrees.

### D3 - Flat Projection Becomes Direct And Bounded

`projection=flat` must not call `_build_node` or materialize the duplicate tree.
It should use a direct graph traversal/counting algorithm over ASSEMBLY edges and
return the same public flat row shape.

The implementation may reuse or extend `get_reachable_items`, but the public
`relationship-tree?projection=flat` response remains:

```json
{
  "root_id": "...",
  "max_depth": 10,
  "projection": "flat",
  "items": [
    {
      "item_id": "...",
      "item_type_id": "Part",
      "item_number": "...",
      "name": "...",
      "occurrence_count": 2,
      "min_depth": 2,
      "first_path": ["A", "B", "D"],
      "first_relationship_path": ["rel-ab", "rel-bd"]
    }
  ]
}
```

### D4 - Occurrence Count Is Still Path Count

`occurrence_count` remains the number of distinct bounded ASSEMBLY
relationship-edge paths reaching that item, after dropping any edge that would
re-enter an item already present in the current item path. The root contributes
exactly 1. A shared part reached through two different parents still reports
`occurrence_count=2`.

The implementation must compute this without building the full duplicate tree. A
plain unique visited-set is **not** enough, because it would under-count downstream
shared descendants in stacked diamonds. Do not de-duplicate contributions by child
item id or by `(source_id, related_id)`; parallel relationship rows are distinct
occurrences because tree projection materializes them as distinct child nodes.

Do not claim general O(V+E) complexity for exact path-count flat projection with
path-local cycle semantics. The requirement is no duplicate-tree materialization.
Use a depth-bounded contribution algorithm or equivalent memoized counting
approach that preserves enough depth/ancestor state to keep D6 correct. Item-only
or `(item, remaining_depth)` caches are valid only when the implementation has
proven the counted subgraph is acyclic for that contribution.

### D5 - First Path Is Shortest-First Stable

`first_path`, `first_relationship_path`, and `min_depth` use the same shortest-first
BFS rule as `get_reachable_items`: the first discovery at minimum depth wins.
Sibling ordering follows the existing relationship query order (`created_at ASC`)
unless the implementation already supplies a stricter stable ordering.

### D6 - Cycle Semantics Stay Path-Based

Cycles must not loop forever and must not inflate `occurrence_count` indefinitely.
If an outgoing edge's `related_id` is already present in the current item path,
that edge contributes `0` to `occurrence_count` and no descendants are traversed.
Existing counts for that item from other non-cycle relationship-edge paths remain
unchanged.

This preserves the existing `_flatten_node` behavior where cycle nodes are not
counted.

### D7 - Stale-Drawings Is Not Reworked

`CadStaleDrawingsService.scan` already uses bounded O(V+E) reachable-set traversal.
This slice may share helper code if that reduces duplication, but it must not
change the stale-drawings response contract or recompute staleness.

### D8 - No Route Count Change

No new endpoints. Route-count pins stay at the live baseline at implementation
time. The implementation PR must still run route-count contracts to prove no
accidental route movement.

## 4. Implementation Sketch

Add a bounded flat helper in `RelationshipService`, for example
`get_relationship_flat(root_id, kinds, max_depth)`, and have
`get_relationship_tree(..., projection="flat")` call it directly.

The helper should:

1. include the root with `occurrence_count=1`, `min_depth=0`, `first_path=[root]`;
2. traverse outgoing ASSEMBLY edges up to `max_depth` without constructing
   duplicate tree nodes;
3. preserve path-local cycle suppression semantics, either by carrying bounded
   path ancestry for contributions or by an equivalent guard that prevents cyclic
   self-contributions from inflating counts;
4. update `occurrence_count` for each non-cycle child path contribution;
5. keep first-path/min-depth selection shortest-first and stable;
6. preserve the same row fields and ordering contract as existing flat.

## 5. Required Tests

- Existing tree tests continue to pass unchanged, especially:
  `test_diamond_tree_keeps_duplicates_flat_dedupes`,
  `test_tree_cycle_is_path_based_and_stops`, and
  `test_tree_node_budget_aborts_on_shared_part_explosion`.
- Replace or update `test_flat_also_bounded_by_budget`: flat should no longer hit
  the tree budget on stacked diamonds. It should return the bounded flat set.
- Add a stacked-diamond flat test proving:
  - `projection=flat` does not raise with a tiny `max_nodes` value that makes tree
    projection raise;
  - shared item `occurrence_count` remains correct;
  - descendants under shared items keep exact path-count contributions, while each
    item appears once in `items`;
  - shortest first path is preserved.
- Add a cycle flat test proving a cyclic edge contributes `0`, does not descend,
  does not disturb counts from other non-cycle relationship-edge paths, and
  traversal terminates.
- Add a router test for `projection=flat` on the same stacked-diamond shape.
- Run the relationship traversal focused tests and route-count contracts.

## 6. Verification Plan

Implementation PR should run at least:

```bash
PYTHONPATH=src .venv-wp13/bin/pytest -q \
  src/yuantus/meta_engine/tests/test_pdm_relationship_traversal.py
```

```bash
PYTHONPATH=src .venv-wp13/bin/pytest -q \
  src/yuantus/meta_engine/tests/test_metrics_router_route_count_delta.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

Run the doc-index contracts if the implementation adds a DEV/V note.

## 7. Non-Goals

- No pack-and-go traversal consolidation.
- No stale-drawings behavior change.
- No new route, migration, or public response field.
- No global visited rewrite for tree projection.
- No B2b `promote_assembly`.
- No A3 workstation checkout.

## 8. Status

Drafted 2026-06-09 after #744 and before any implementation. Awaiting doc-only
review/merge before code changes.
