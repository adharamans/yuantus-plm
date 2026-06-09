# Development Taskbook: OdooPLM 19 CAD-PDM B2b promote_assembly

Date: 2026-06-09
Type: doc-only scope lock. This taskbook authorizes a later implementation PR,
not implementation in this PR.

## 1. Context

B2 shipped the hard gate and readiness surface:

- `LifecycleService.promote(..., target_state_name="Released")` now blocks a parent
  whose direct ASSEMBLY child is unreleased or dangling.
- `release_readiness` surfaces the same `bom.children_all_released` diagnostics
  advisorily through `item_release`.

That closes the safety half. The remaining OdooPLM-inspired B2b half is a controlled
one-click assembly promotion workflow: promote the assembly's eligible subtree in
bottom-up order so the hard gate can pass naturally.

## 2. Grounded Facts

- `LifecycleService.promote` is the central state transition method. It performs
  lifecycle transition validation, role checks, hooks, workflow start, version
  release integration, and returns `PromoteResult`. It does **not** commit.
- B2 hard gate runs inside `LifecycleService.promote` before state mutation when
  entering `Released`.
- `ItemReleaseService.assert_children_released` checks **direct** ASSEMBLY children
  only; recursive safety is achieved by promoting descendants first.
- WP1.2 provides PDM ASSEMBLY traversal and stale-drawings read surfaces. Current
  `relationship-tree` tree projection preserves duplicate paths; `get_reachable_items`
  returns a unique reachable set for scans.
- The OdooPLM gap analysis described `promote_assembly(root, target_state)` as an
  orchestration layer, not as a replacement for lifecycle state transitions.

## 3. Locked Decisions

### D1 - New Orchestration Service, Existing Promote Semantics

Add a service-level orchestration, for example
`AssemblyPromotionService.promote_assembly(root_id, target_state="Released", ...)`.
V1 supports only `target_state="Released"`; this is a release orchestration, not a
generic bulk state-change API. It must call `LifecycleService.promote` for each
item. It must not directly mutate `Item.state`, `current_state`, versions,
permissions, hooks, or workflows.

### D2 - Bottom-Up Order

For `target_state="Released"`, process descendants before parents. This is required
because B2's hard gate intentionally rejects a parent while any direct child is not
released.

Ordering rule:

- build the reachable ASSEMBLY dependency graph from the root, retaining every
  parent -> child edge even when a child is de-duplicated across multiple paths;
- fail closed on cycles, dangling edges, or traversal-limit overflow before apply;
- order the unique item plan with a reverse topological sort: for every reachable
  ASSEMBLY edge `parent -> child`, `child` must appear before `parent`;
- a `max_path_depth DESC` sort is acceptable only if `max_path_depth` is the
  maximum path length from the root across all paths, not BFS `min_depth` / first
  discovery depth;
- first-path order is diagnostic/tie-break data only for nodes with no dependency
  path between them;
- root is last.

If a child appears through multiple paths, it is promoted once, but all parent
dependencies remain part of ordering and diagnostics.

### D3 - Eligible Items Only, Idempotent Released Skip

Already released items are skipped as `already_released`, not treated as errors.
Items that have no valid transition to `target_state` are reported as failures and
must prevent parent promotion in apply mode.

The service should expose plan rows with:

- `item_id`, `item_number`, `state`, `target_state`;
- `depth`, `path`, `relationship_path`;
- `action`: `promote`, `skip_already_released`, or `blocked`;
- `blocking_reason` when applicable.

### D4 - Dry-Run First

The public API must support `dry_run=true` and should default to dry-run unless the
implementation's surrounding API convention strongly argues otherwise.

Dry-run returns the ordered plan and diagnostics; it performs no state mutation, no
hooks, no workflow start, no version release.

### D5 - Transaction Semantics

Apply mode should be all-or-nothing for v1:

- run promotions in bottom-up order in one caller-managed transaction;
- if any promotion fails, roll back the transaction and return the failure plan;
- do not leave a partially promoted assembly tree.

This is stricter than a partial-success batch and matches the hard-gate safety
posture. Partial success / resume can be a later orchestration feature.

### D6 - Cycle And Dangling Handling

Cycles and dangling ASSEMBLY references must fail closed before apply.

The implementation may reuse WP1.2 traversal helpers, but the plan must surface:

- cycle evidence (path / item id) as `cycle_detected`;
- dangling relationship evidence as `child_missing`;
- no silent omission of a broken edge.

### D7 - Permission Model

The API must require promote permission on each item to be promoted, not just the
root. A user who can promote the root but not a child should receive a dry-run/apply
failure row for that child.

If the implementation first lands service-only with no route, tests must still
prove the eventual route can enforce per-item promote checks without bypassing
`LifecycleService.promote`.

### D8 - Public Surface

Preferred v1 route:

`POST /pdm/items/{root_id}/promote-assembly`

Rationale: this is CAD product-structure behavior, not a generic lifecycle route.
It consumes ASSEMBLY structure and is part of the CAD-PDM borrow line. The route
should accept:

```json
{
  "target_state": "Released",
  "dry_run": true,
  "max_depth": 10
}
```

`target_state` must be `"Released"` in v1; other values return 422. This keeps the
slice tied to the B2 release hard gate and avoids accidentally creating a broad
bulk lifecycle transition surface.

`max_depth` is a traversal safety limit, not a silent truncation request. If the
actual ASSEMBLY graph exceeds the requested depth, dry-run/apply must return a
`max_depth_exceeded` failure and perform no mutation.

Response:

```json
{
  "root_id": "...",
  "target_state": "Released",
  "dry_run": true,
  "ok": false,
  "summary": {
    "promote": 2,
    "skip_already_released": 1,
    "blocked": 1
  },
  "plan": [
    {
      "item_id": "...",
      "depth": 2,
      "action": "promote",
      "state": "Draft",
      "target_state": "Released",
      "path": ["root", "sub", "leaf"],
      "relationship_path": ["rel-root-sub", "rel-sub-leaf"]
    }
  ],
  "errors": []
}
```

Route count increases by +1. Implementation must live-recheck the baseline and
move all route-count pins together.

### D9 - No ECO Or Workflow Redesign

B2b is a lifecycle orchestration thin slice. It does not introduce ECO approval,
batch release objects, resume tokens, background jobs, or custom workflow maps.
Those can wrap this service later if product needs a richer release process.

### D10 - No A3 Workstation Checkout

This slice is server-side release orchestration only. It does not introduce CAD
desktop checkout, file locks, or native-signoff evidence.

## 4. Implementation Sketch

1. Build a unique ASSEMBLY dependency graph with path metadata and dangling/cycle
   detection. If WP1.2 bounded-flat is merged first, prefer its helper. Otherwise,
   write a small service-local planner with the same path/dangling discipline.
2. Sort plan rows by reverse topological order so every child appears before every
   reachable parent edge. Stable first-path order is only a tie-breaker between
   independent nodes.
3. In dry-run, classify each row without mutating.
4. In apply mode:
   - check per-item promote permission;
   - call `LifecycleService.promote` for each `action=promote` row;
   - flush after each successful promote so downstream hard gates see updated child
     state;
   - rollback on first failure and return the failure plan.
5. Add a route in the PDM area and route-count pin updates.

## 5. Required Tests

- Dry-run on a two-level assembly returns children before parent, with root last.
- Shared-child / diamond ordering proves a child reachable through multiple paths
  is promoted before every reachable parent edge, including a shortcut case such
  as `R -> X` and `R -> A -> B -> X`.
- Apply promotes leaves first, then parent; B2 hard gate passes without force.
- Already released descendants are skipped, not errors.
- Unpromotable child blocks parent and rolls back all prior in-session promotions.
- Dangling ASSEMBLY edge fails closed before apply.
- Cycle fails closed before apply.
- If actual ASSEMBLY depth exceeds request `max_depth`, dry-run/apply returns a
  `max_depth_exceeded` failure and performs no mutation.
- Per-item permission denial on a child blocks apply.
- Route test for happy dry-run and apply failure mapping.
- Route test rejects non-`Released` `target_state`.
- Route-count pins move by +1 from the implementation-time live baseline.

## 6. Verification Plan

Implementation PR should run at least:

```bash
PYTHONPATH=src .venv-wp13/bin/pytest -q \
  src/yuantus/meta_engine/tests/test_item_release_gate.py \
  src/yuantus/meta_engine/tests/test_assembly_promotion_service.py
```

Route-count contracts:

```bash
PYTHONPATH=src .venv-wp13/bin/pytest -q \
  src/yuantus/meta_engine/tests/test_metrics_router_route_count_delta.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py
```

Run doc-index contracts if the implementation adds a DEV/V note.

## 7. Non-Goals

- No direct state mutation bypassing `LifecycleService.promote`.
- No partial-success apply mode.
- No ECO redesign, workflow redesign, background worker, or persisted batch object.
- No pack-and-go or stale-drawings changes.
- No A3 workstation checkout.

## 8. Status

Drafted 2026-06-09 after B2 hard gate/readiness and before implementation. Awaiting
doc-only review/merge before code changes.
