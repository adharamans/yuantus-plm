# L2 — Forensic transition-history `?reason_code` filter (DEV & VERIFICATION)

Date: 2026-06-27
Line: L2 (lifecycle / audit / permissions). Completes the **deferred** part of the
L2-1 named first task: #879 shipped `?outcome`; the roadmap (#882) deferred
`?reason_code` for "cross-DB filter + limit-ordering complexity". This closes it.

## What

Add an optional `?reason_code` filter to the forensic (superuser) route
`GET /api/v1/transition-history/forensic/{item_id}`. It restricts to rows whose
recorded failure discriminator (`properties.reason_code`) equals the value, e.g.
`?reason_code=permission_denied`. AND-combines with `?outcome` and the `limit` cap.

## How (and why it's the right cross-DB / limit-ordering shape)

`reason_code` is stored in the JSON column `properties`
(`Column(JSON().with_variant(JSONB, "postgresql"))`), not a scalar column — which is
exactly the complexity that deferred it. The filter is:

```python
query.filter(LifecycleTransitionHistory.properties["reason_code"].as_string() == reason_code)
```

- **Cross-DB:** `.as_string()` JSON extraction is the portable pattern already used
  in `web/graphql/schema.py` (`Item.properties[key].as_string() == value`) —
  SQLAlchemy emits `json_extract` on SQLite and `->>` on Postgres/JSONB.
- **Limit-ordering correct:** applied at **SQL level, before `order_by`/`limit`**, so
  it composes with `?outcome` and the most-recent-N `limit` semantics. A Python
  post-filter after `limit` would be wrong (a newer non-matching row would displace a
  match); a dedicated test pins this.

## Design decisions

- **No router-side allowlist for `reason_code`** (unlike `?outcome`, which validates
  against a fixed 5-value vocabulary → 400). The reason_code vocabulary grows as new
  failure paths add codes; a duplicated router allowlist would drift and silently
  reject valid codes. An unknown code therefore matches nothing (empty 200), which is
  safe for a read-only forensic filter and avoids the silent-false-green trap.
- **Superuser gate unchanged** — the forensic route stays `require_superuser`.
- **No new route** — a query param on an existing route, so the app-route-count
  design-lock pins (`EXPECTED_TOTAL_ROUTES` etc.) are **unaffected** (722, no change).

## Verification

- `pytest .../test_lifecycle_transition_history_router.py` → **23 passed** (19
  existing from #879 + 4 new), no regression. New cases:
  `test_forensic_filters_by_reason_code`, `..._composes_with_outcome`,
  `..._unknown_reason_code_is_empty_200`, `..._composes_with_limit` (the
  filter-before-limit / ordering guard).
- The test file is already registered in `ci.yml`'s pytest list (#879); these
  assertions extend it, so they run in CI (no new-file silent-skip).
- Diff: 3 source files (service `+12`, router `+10/-1`, test `+50`).
