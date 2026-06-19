# Design note — `find_effective_version` open-start (NULL `start_date`) fix

Date: 2026-06-19 · Branch `claude/find-effective-version-nullstart` · base `origin/main`.

## The bug

`VersionService.find_effective_version(item_id, target_date)`
(`src/yuantus/meta_engine/version/service.py`) filtered date effectivities with a bare
`Effectivity.start_date <= target_date`. SQL `NULL <= x` is `NULL` (not true), so a version
whose effectivity has an **open start** (`start_date IS NULL`, meaning "effective from the
beginning") was wrongly excluded — the read route `GET /versions/items/{id}/effective`
returned **404 for a version that is in fact effective**.

This was surfaced during CAD-PDM C3: the C3 services deliberately do **not** call this helper
and compute effectiveness locally precisely to avoid this narrowness
(`effectivity_service.py` and `date_effectivity_obsolete_service.py` carry that note). C3 is
therefore unaffected by this change — see "Blast radius".

## The fix (one line)

Mirror the canonical `EffectivityService._check_date`, where a null bound is open
(`-inf` / `+inf`):

```python
.filter(or_(Effectivity.start_date == None, Effectivity.start_date <= target_date))
```

The end-side already used this idiom (`end_date IS NULL OR end_date >= target_date`); the
start side now matches. No normalization logic is pulled into the query — `_check_date`
normalizes tz, but the helper does a raw column comparison and keeping it raw avoids an
unrelated behavior change; that divergence is pre-existing and out of scope.

## Deliberately NOT changed (decision to ratify)

`find_effective_version` still uses an **inner join** on `Effectivity`, so a version with
**no Date effectivity at all** is not returned (the route 404s). This is intentional: the
helper answers *"which version's Date window covers this date"*, which requires a Date row —
distinct from `EffectivityService.check_effectivity`, where an item with no effectivity is
treated as *always effective*.

Making the helper return non-date-scoped versions (e.g. the latest version when none has a
Date effectivity) would be a **larger semantic change** with collateral on the route's 404
contract, so it is left as a separate, ratifiable decision rather than folded into this fix.
If the desired behavior is to mirror `check_effectivity`'s always-effective semantics, that
is a deliberate follow-up (inner join → outer join + a fallback), not a bug fix.

## Blast radius

- One production caller: `web/version_effectivity_router.py` (read-only `GET .../effective`).
  The only behavior change is **correct**: an open-start version that covers the date is now
  returned instead of 404.
- **Cannot regress C3**: the C3 date-obsolete path does not call `find_effective_version`
  (it computes effectiveness locally; confirmed by the in-code notes), so the obsolete
  decision is untouched.
- No route/model/migration change → route count and Alembic head unchanged.

## Tests

`test_find_effective_version.py` (first direct tests of the helper's query logic, DB-free
sqlite): open-start effective ✓, fully-open window ✓, bounded window ✓, open-end ✓, future
start → none, expired → none, **no-Date-effectivity version → none (pinned)**, non-Date
(Lot) effectivity ignored, and a two-matching-versions `created_at desc` tie-break (the fix
widens the candidate set, so the tie-break is pinned). Registered in `ci.yml` contracts list
+ conftest `_ALLOWLIST_NO_DB`.
