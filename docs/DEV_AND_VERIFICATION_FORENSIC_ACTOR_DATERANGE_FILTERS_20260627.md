# Dev & Verification — L2 forensic `?actor` + `?date-range` filters

> Date 2026-06-27 · branch `claude/l2-forensic-actor-daterange` · **no route added** (extends the existing forensic route).

## What & why
Line 2 (lifecycle/audit/permissions) Fork-A continuation. The forensic route
`GET /api/v1/transition-history/forensic/{item_id}` already filters by `?outcome` (#879)
and `?reason_code` (#887). The #882 taskbook lists the full filter set as
`?outcome/?reason_code/?actor/?date-range`; this slice ships the remaining two so ops can
answer "what did user N attempt" and "what happened in this window" without a new surface.

- `?actor=<uid>` — repeatable; filter to one or more recorded actor user ids.
- `?created_after=<iso>` / `?created_before=<iso>` — inclusive time bounds on `created_at`
  (ISO-8601 date or datetime; invalid format → 400). A **date-only** `created_before`
  (e.g. `2026-06-05`) includes the **whole day** (end-of-day), so a daytime row on the
  boundary day is not silently dropped; pass a datetime for an exact instant. `created_after`
  date-only is start-of-day (already inclusive of the day).

All compose with each other and with the existing `?outcome`/`?reason_code`/`?limit`.

## Design decisions
- **Mirrors the shipped filter pattern.** `LifecycleService.get_transition_history`
  gained `actor_user_ids` / `created_after` / `created_before` params, filtered SQL-level
  (`actor_user_id.in_(...)`, `created_at >= / <=`) right beside the existing `outcomes` /
  `reason_codes` filters, so all compose with the `created_at desc, id desc` ordering + `limit`.
- **`actor` is FK-free** (a system/automated promote may record an id with no current user
  row); an unknown id simply matches nothing (no 400) — same robustness stance as `?reason_code`.
- **Date parsing in the router** (`datetime.fromisoformat`), 400 on malformed input — the
  service takes already-parsed `datetime` objects. `created_at` is indexed, so the bounds
  compose efficiently.
- **No new route** → app route-count unchanged (725); superuser gate unchanged.

## Verification
- Local: `PYTHONPATH=<wt>/src YUANTUS_PYTEST_DB=1 pytest` → **42 passed** =
  `test_lifecycle_transition_history_router.py` (existing + 13 new: actor single/multiple/
  unknown-empty/compose-with-outcome+limit; created_after/created_before inclusive; datetime
  component; range+other-filters; invalid created_after/created_before → 400; **date-only
  created_before includes whole day**; datetime created_before is an exact instant) +
  `test_phase4_search_closeout_contracts.py` (route-count pin still **725**).
- CI routing (empirically checked against the real `detect_changes` case): both
  `lifecycle_transition_history_router.py` and `lifecycle/service.py` set `run_contracts=true`;
  the test is already in the `ci.yml` contracts list (line 488). No ci.yml change needed.

## Files
- `src/yuantus/meta_engine/lifecycle/service.py` (get_transition_history: + actor/date params + filters)
- `src/yuantus/meta_engine/web/lifecycle_transition_history_router.py` (forensic route: + actor/date Query params + ISO parse → 400)
- `src/yuantus/meta_engine/tests/test_lifecycle_transition_history_router.py` (+11 tests)
- `docs/DELIVERY_DOC_INDEX.md` (this doc registered)
