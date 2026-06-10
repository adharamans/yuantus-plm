# DEV & Verification: OdooPLM 19 CAD-PDM B2b assembly promotion

Date: 2026-06-09

Implements the server-side assembly promotion slice locked by
`DEVELOPMENT_ODOOPLM_19_CADPDM_B2B_PROMOTE_ASSEMBLY_TASKBOOK_20260609.md`.

## Scope

- Adds `AssemblyPromotionService.promote_assembly(...)` as an orchestration layer
  over the existing `LifecycleService.promote` state transition path.
- Adds `POST /api/v1/pdm/items/{root_id}/promote-assembly`.
- Supports `target_state="Released"` only in v1.
- Supports dry-run by default and all-or-nothing apply mode.
- No ECO redesign, workflow redesign, background job, persisted batch object,
  migration, pack-and-go change, stale-drawings change, or A3 workstation checkout.

Route count moves from 707 to 708 for the single new PDM route.

## Semantics

- The planner builds an ASSEMBLY dependency graph from the root and keeps every
  parent-to-child edge for ordering, including shared-child shortcut diamonds.
- Apply order is reverse topological: every child is promoted before every
  reachable parent edge, with the root last.
- Already Released rows are skipped as `skip_already_released`.
- Unpromotable rows are classified as `blocked` and prevent apply.
- Dangling ASSEMBLY edges, cycles, and `max_depth` overflow fail closed before
  mutation.
- Apply mode runs in a nested transaction and rolls back any earlier in-session
  promotions if a later promotion fails.
- Per-item promote permission is checked for each row, not only the root.

## Verification

Focused B2b suite:

```bash
PYTHONPATH=src .venv-wp13/bin/pytest -q \
  src/yuantus/meta_engine/tests/test_assembly_promotion_service.py
# 10 passed
```

Coverage:

- shortcut diamond order (`R -> X` plus `R -> A -> B -> X`) keeps `X` before `B`,
  `A`, and `R`;
- apply promotes descendants before the parent so the B2 hard gate passes without
  force;
- already Released descendants are skipped;
- unpromotable children block parent promotion;
- midstream apply failure rolls back earlier promotions;
- midstream `LifecycleService.promote` exceptions roll back earlier promotions and
  return a failure plan instead of leaking a 500;
- dangling, cycle, and `max_depth_exceeded` cases fail closed;
- per-item permission denial blocks the plan;
- route dry-run/apply failure mapping is covered;
- non-Part roots and non-`Released` targets are rejected.

## CI Wiring

- `test_assembly_promotion_service.py` is registered in the contracts job and
  no-DB allowlist.
- The four route-count pins are moved together to 708.

## Non-Goals

- No broad bulk lifecycle transition API.
- No partial-success or resumable batch mode.
- No traversal-engine rewrite beyond the service-local planner required for this
  orchestration.
