# DEV & Verification: ECM Publish P1A — release hook-point hardening (impl)

Date: 2026-06-16

First implementation slice of the PLM→ECM publish goal, per taskbook
`DEVELOPMENT_ECM_PUBLISH_P0_REFRESH_TASKBOOK_20260616.md` (D7). **Pure release
hardening — no ECM code yet, no route, no migration, no schema change** (the columns
already exist). Prepares the seam the P1B enqueue hook will read.

## As built (D7)

- **`VersionService.release(item_id, user_id)`** now stamps release provenance on the
  version being released: `current_ver.released_at = datetime.utcnow()` and
  `current_ver.released_by_id = user_id`, placed immediately after `is_released = True`
  (the columns `ItemVersion.released_at` / `released_by_id` existed but were never set).
- **Only the just-released version is stamped.** The B1 supersede hook below touches the
  predecessor's `is_superseded`/`state` but **not** its provenance — so a superseded
  predecessor keeps its own original `released_at`/`released_by_id`.
- **No public `release_version(version_id)` entry added** (D7) — the entry stays
  `release(item_id)`, the hook lives inside it; ECO / arbitrary-version-id release is
  *not* widened. `ChangeService._release_version` is **left fenced** (deprecated legacy
  surface; the B1 static guard `test_no_runtime_use_of_deprecated_changeservice_release_path`
  continues to prevent runtime reuse).
- **No behavior change** to `is_released`/`state`/supersede/lock-release/history — only
  the two provenance fields are newly set.

## Verification (Python 3.11, no-DB)

`test_release_hook_point_hardening.py` → **3 passed** (B1 SQLite harness):
- `release()` stamps `released_at` (a timestamp ≥ call start) + `released_by_id == user_id`;
  `is_released`/`state` as before.
- **No mis-stamp:** releasing vN+1 supersedes vN (`is_superseded`/`state`) but leaves vN's
  `released_at`/`released_by_id` at its original values.
- **Idempotent:** an already-released version early-returns without re-stamping provenance.

Behavior-unchanged confidence: `test_version_supersede_b1` + `test_version_service` →
green (supersede hook, lock-release, guard all unchanged). The canonical-writer fence
holds (B1 static guard). **Full CI contracts list run locally** (release() is in the hot
promote path) — see PR for the green count. Test **dual-registered** (ci.yml + conftest
allowlist). `create_app()` route count unchanged **709**.

## Not in this PR

- No ECM outbox / enqueue / entitlement gate yet (that is **P1B**).
- No `release_version(version_id)` public entry; no ECO-apply publish (v1 scope, D1).
- `ChangeService._release_version` untouched (kept fenced; sunset later).
