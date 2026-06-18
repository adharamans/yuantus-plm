# Dev & Verification: CAD-PDM C3 date-BOM auto-obsolete — mechanism (Slice 1)

Date: 2026-06-18
Status: **IMPLEMENTED (mechanism-only, UNWIRED)** — pending review + merge.
Taskbook: `DEVELOPMENT_CLAUDE_TASK_CADPDM_C3_DATE_BOM_AUTO_OBSOLETE_TASKBOOK_20260617.md` (#792).

## 1. Owner-ratified decisions

- **Propagation**: FLAG only, never cascade. **Depth**: 1 (direct where-used parents).
- **Scheduler**: a polling worker, no cron. **Default**: off.
- **Obsolete target (Option 1)**: when a date effectivity expires, promote the affected Item to
  the lifecycle **Obsolete** state **only if it has no remaining currently-effective version**;
  otherwise just mark expired. Always flag the depth-1 where-used parents.

## 2. Live-code corrections to the taskbook (verified by a 4-lens orientation)

- Obsolescence is **Item-level** via the seeded lifecycle **Obsolete** end-state
  (`LifecycleService.promote(item, "Obsolete", user_id)`; `is_end_state` + `version_lock`) —
  **not** version `is_superseded` (orthogonal, invisible to `BOMObsoleteService`'s obsolete scan).
- Upward where-used = `BOMService.get_where_used(item_id, recursive=False)` (depth-1; relationships
  are Items via `related_id`→`source_id`; parent id = `entry["parent"]["id"]`).
- No expired-effectivity query existed; added `get_expired_date_effectivities` (`Date` type,
  `end_date` non-null and `< now`). It is the negation of the **end-date** half only — it
  deliberately does **not** mirror `VersionService.find_effective_version`, which is a shared,
  pre-existing helper that wrongly **excludes open-start (`NULL start_date`) and no-effectivity
  versions** on its start-side filter. The "no effective version remains" guard therefore does NOT
  call `find_effective_version`; it computes effectiveness directly (see §3) to avoid that
  narrowness without touching shared code.

## 3. What's in this slice (mechanism, unwired — zero runtime behavior change)

- `EffectivityService.get_expired_date_effectivities(now, version_scoped_only=True)` — `Date` type,
  `end_date` non-null and `< now` (open-ended never expires; naive-UTC).
- Model `DateObsoleteImpact` (table `meta_date_obsolete_impacts`) + migration `c3_date_obsolete_001`
  (single head): one row per depth-1 parent flagged, unique on `(effectivity_id, parent_item_id)`
  for idempotent re-scan.
- `DateEffectivityObsoleteService`:
  - `scan_expired` → expired date effectivities.
  - `_has_effective_version` → the Option-1 guard, computed **directly** under the canonical
    `_check_date` rule: a version with **no** Date effectivity is unbounded (always effective), else
    it is effective when in window (`NULL start_date` = -∞, `NULL end_date` = +∞). Over the Item's
    versions. (Replaces the narrow `find_effective_version` delegation — see §2.)
  - `process_expired(eff, user_id, now)` → resolve the version's Item; if it has **no** effective
    version (and isn't already Obsolete) → `promote` to Obsolete (failures recorded, never crash);
    else mark; then **flag depth-1 parents**. Distinct durable `reason`:
    `child_obsoleted` (promoted), `child_obsolete_failed` (no effective version but promote could
    not run — the `obsolete_error` is persisted in the flag's `properties`), or
    `child_effectivity_expired` (deliberate mark — an effective version still exists).
  - Idempotent (already-Obsolete skips promote; parent flags upsert on the unique key, refreshing
    `child_obsoleted` + `reason` + `properties` together so a re-scan never leaves a contradictory row).
  - **Unwired**: no worker, no route, no setting yet — nothing calls it at runtime.

## 4. Verification (`test_date_effectivity_obsolete_service.py`, 11 pass)

expired query excludes open-ended/future/non-Date; marks-not-obsolete when an effective version
remains (+ flags parent, Item untouched); obsoletes when none remains (+ flag reason
`child_obsoleted`); already-Obsolete skips promote; **depth-1 only — a depth-2 grandparent is NOT
flagged** (no cascade); idempotent re-run = one flag; non-version-scoped effectivity skipped.
Plus the adversarial-verify regressions: an **open-start (`NULL start_date`)** survivor and a
**no-Date-effectivity** survivor each keep the Item effective (not obsoleted); a **failed promote**
persists `reason="child_obsolete_failed"` + `obsolete_error` (distinct from a deliberate mark); a
**re-scan flip** (failed→success) refreshes the existing flag's `reason`/`child_obsoleted`.
Single Alembic head `c3_date_obsolete_001`; route count unchanged **716** (unwired); migration
coverage green; CI dual-registered. **Tenant baseline**: `meta_date_obsolete_impacts` is registered
in the generator supplement (`tenant_schema.register_tenant_model_metadata`) and added to the
committed baseline (regenerated) — because the model is imported by this slice's test, the
in-process baseline drift-guard would otherwise flag it; and it is a real per-tenant table that
belongs in the baseline regardless of wiring. Both baseline guards (in-process committed-matches +
the subprocess generator-vs-booted-app) pass in the CI shared-process order.

## 5. Boundary / next slice

Mechanism only. **Slice 2 (wiring)**: a polling worker mirroring `EcmPublicationOutboxWorker`
(default-off via a `C3_*`/`DATE_EFFECTIVITY_OBSOLETE_ENABLED` setting + `EntitlementService`), the
admin-gated impact ops routes (list / acknowledge), and the system user_id for the worker. The
table already ships in the tenant baseline (this slice); Slice 2 only adds it to the booted-app
route surface. BOM-line (`item_id`-scoped) effectivities remain out of scope.
