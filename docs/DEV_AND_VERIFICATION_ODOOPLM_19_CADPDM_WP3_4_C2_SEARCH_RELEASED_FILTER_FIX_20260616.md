# DEV & Verification: WP3.4 C2 search filter — fail-open + is_current fix

Date: 2026-06-16

Follow-up correctness fix to the WP3.4 C2 opt-in `released_only` search filter (#759,
merged), addressing two review findings. **No route, no migration, no schema change.**

## P1 (correctness) — ES-failure fallback was fail-open

`SearchService.search` has two `_search_fallback_db` calls: the `if not self.client`
early return AND the `except Exception` block that catches an ES failure. Only the
first threaded `released_only`; the **exception fallback dropped it** (an earlier
`replace_all` matched only the shallower-indented call). Effect: with ES enabled, any
query error / timeout / index problem made `/search?released_only=true` **fail open** —
falling back to an unfiltered DB search that returns drafts/WIP, exactly the leak the
feature promises to prevent.

**Fix:** the `except` fallback now passes `released_only=released_only`.

## P2 (semantics) — "matches guard" was missing `version.is_current`

The DEV/V doc claimed the filter matches `LatestReleasedGuardService`. The guard's
`_assert_version` requires the version to be **both `is_current` and `is_released`**,
but the C2 filter checked only `is_released` (in `_item_is_latest_released` and the DB
subquery). A dirty/edge state where `Item.current_version_id` points at a released but
**non-current** version would have passed.

**Fix:** require `ItemVersion.is_current` too —
`_item_is_latest_released` now returns `version.is_released and version.is_current`, and
the DB subquery filters `ItemVersion.is_released AND ItemVersion.is_current`.

## Verification (Python 3.11, no-DB)

`test_search_released_only_filter.py` → **5 passed** (3 prior + 2 new regression tests):
- **P1** `…_survives_es_failure_fallback_no_failopen`: a `SimpleNamespace` ES client
  whose `.search()` raises → `released_only=true` still returns only the released item
  (the draft is **not** leaked). Non-vacuous: without the fix the fallback returns the
  draft and the `== {"A"}` assertion fails.
- **P2** `…_excludes_released_but_non_current_version`: an item whose
  `current_version_id` points at a released-but-non-current version is **excluded**, and
  its `_build_doc` `is_released` is `False`. Non-vacuous: without the fix it passes.

Blast radius — the fix is local to `search_service`; the **full CI contracts list** was
run locally (see PR for the green count). Route count unchanged **709**.

## Not in this PR

- No change to default search (`released_only=false` unaffected) or `GetOperation`.
- The ES-mode `is_released` doc field still needs a reindex for pre-existing indices
  (unchanged from #759); the DB fallback (incl. the failure path, now fixed) needs none.
