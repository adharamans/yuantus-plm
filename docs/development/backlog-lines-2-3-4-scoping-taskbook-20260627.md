# Backlog scoping taskbook — lines 2 / 3 / 4

> Status (2026-06-27): each line's first slice is **MERGED** — L2-1 #879, L3-0 #878, L4-1 #881 (Line 1 CAD: A1a #125 + flip-to-blocking #126, in VemCAD). This doc is the trusted entry for the **next** slices: L2 `?reason_code` filter · **L3-1 effectivity-date PATCH (past `end_date` → 409)** · L4 Fork B cap-change audit UI / Fork C append-only revoke · (CAD A1a-2 curated readiness corpus, in VemCAD). Doc-only · branch `claude/backlog-taskbooks-l2l3l4`.
> Companion to the owner's 4-line priority list; grounds the three Yuantus lines so each next slice takes a per-phase opt-in (own worktree + branch + `DEV_AND_VERIFICATION_*` impl doc).
> Every "done"/file:line claim below is from a fresh `origin/main` read (2026-06-27); re-verify before editing.

---

## Line 2 — Lifecycle / audit / permissions
**Owner intent:** transition history is complete; next = productized query/filter/export, or an all-attempts UI/ops surface.

**Done (foundation):**
- `LifecycleTransitionHistory` (`meta_engine/lifecycle/models.py:145-188`) — immutable, **FK-free** (recorded IDs survive item deletion), `outcome ∈ success|denied|blocked|aborted|failed`, `properties.reason_code` (low-cardinality: permission_denied / condition_failed / assembly_release_blocked / *_aborted / …).
- `promote()` (`lifecycle/service.py:84-578`) records **both** success (`_record_transition_history`, same-session SAVEPOINT) and **all-attempts** (`_record_transition_attempt`, independent `get_db_session()` that commits across caller rollback; best-effort, never raises).
- Reads: `GET /items/{id}/transition-history` (per-item ACL `check_permission(...get)`, `success_only=True` — failures never leak to end-users); `GET /transition-history/forensic/{id}` (superuser, **all** outcomes, no existence gate). Wired `app.py:427`.
- Prior taskbooks already exist: `lifecycle-transition-history-taskbook-20260619.md`, `…-all-attempts-taskbook-20260622.md`. CI: `test_lifecycle_*` are all in `ci.yml` (no false-green).

**Forks / gaps:** (A) query/filter/export — no `?outcome`/`?reason_code`/`?actor`/`?date-range` filter, only `?limit` (no offset/cursor), no export, no aggregates. (B) all-attempts ops surface — no cross-item search, no dashboard (top reasons / most-failed items/actors), no drill-down.

**Recommended first task — L2-1 (Fork A seed):** add `?outcome` + `?reason_code` + `?limit` filters to the **forensic** route (service `get_transition_history` already gates `success_only`; generalise to a filter set; keep superuser gate). Additive, low-risk, unblocks ops investigation without a new surface. Verify: extend `test_lifecycle_transition_history_router.py` (already in CI).
> ✅ **MERGED — #879.** Shipped the **`?outcome`** filter only (repeatable, SQL-level `outcome.in_()`, invalid→400, composes with `limit`); **no new route** (untouched app-route-count contract), no app.py change. `?reason_code` deferred (it lives in `properties` JSON → cross-DB filter + limit-ordering complexity; do as a follow-up). Local: router test **19 passed** (14 existing + 5 new) + service test 7 passed.
**Audit relation (don't conflate):** `AuditLog` (HTTP request trace) and `ApprovalRequestEvent` (approval domain) are orthogonal to transition history — three distinct trails; do not merge.

---

## Line 3 — CAD-PDM C3 / effectivity ops
**Owner intent:** mechanism + worker done; next = BOM-line effectivity dedicated ops/CRUD, or a fuller date-obsolete-impacts operational view.

**Done (foundation):**
- Model `meta_effectivities` (`models/effectivity.py:8`) — date(MVP)/lot/serial/unit; attach by `version_id` or `item_id` (BOM line = "Part BOM" Item). Service (`services/effectivity_service.py`): create/get/delete, `check_effectivity`, `get_expired_date_effectivities`, `filter_bom_by_effectivity`.
- Worker `DateObsoleteWorker` (`services/date_obsolete_worker.py`) — polling (`DATE_EFFECTIVITY_OBSOLETE_POLL_INTERVAL_SECONDS=300`), **double-gated** (env kill-switch `DATE_EFFECTIVITY_OBSOLETE_ENABLED` default OFF **and** `cadpdm_date_obsolete` entitlement), idempotent; version-scoped → promote Item to Obsolete (if no effective version) + flag depth-1 parents; BOM-line-scoped → flag-only (never cascades). Output `DateObsoleteImpact` (`models/date_obsolete.py`, state open|acknowledged).
- Ops endpoints (admin): `GET /cadpdm/date-obsolete-impacts` (+`?state`), `GET …/{id}`, `POST …/{id}/acknowledge`. Routers wired `app.py:346/408/426`.

**⚠ CI false-green:** `test_effectivity.py` (EffectivityService `_check_date/lot/serial/unit` core logic) is **NOT** in `ci.yml`'s explicit pytest list → core effectivity bugs hide. (Other effectivity tests ARE listed.)

**Forks / gaps:** (A) BOM-line effectivity CRUD — CREATE (`bom add_child(effectivity_from/to)`) + READ (`/effectivities/items/{id}`) exist; **UPDATE & DELETE missing** (today you must recreate the line). (B) date-obsolete impacts fuller view — no summary/aggregate, no batch-acknowledge, no revert, no export.

**Recommended first task — L3-0 (no-brainer CI fix):** add `test_effectivity.py` to `ci.yml`'s pytest list (+ `conftest._ALLOWLIST_NO_DB` — required: the no-DB collector ignores non-allowlisted files even when CI passes them explicitly) — closes the false-green per `feedback_yuantus_silent_failure_traps`; locally verifiable (`pytest …/test_effectivity.py`). **✅ MERGED — #878.**

Then **L3-1 (Fork A):** `PATCH /effectivities/{id}` to edit `start_date`/`end_date` in place (guard: item must be a current "Part BOM" Item for BOM-line edits).
> ⚠️ **OWNER DECISION — L3-1 collides with `DateObsoleteWorker`.** Editing an `end_date` can *un-expire* an effectivity the worker already swept: it may have written `DateObsoleteImpact` rows and/or promoted the Item to **Obsolete**. So PATCH cannot be a naive field write. Two scopes to choose from: (a) **reconcile** — on extend, re-open/retract the related `DateObsoleteImpact` (and consider un-obsoleting the Item) within the same transaction; or (b) **scope out** — forbid editing an `end_date` that is already in the past (return 409), leaving expired-then-swept lines immutable. Pick before L3-1 impl; (b) is the smaller, safer first cut.

---

## Line 4 — V2 seats / licensing productization
**Owner intent:** seats projection + cap clearing have foundations; next = license status page, cap-change audit UI, or import/rollback ops.

**Done (foundation):**
- `seat_projection.project_license_seats()` (`security/auth/seat_projection.py:53-115`) — signed-payload `seats` → `TenantQuota.max_users`: `noop` (absent) / `set N` / `clear` (explicit `seats:null` = unlimited; keys on `"seats" in payload`). Same cap `QuotaService` enforces at `POST /admin/users` (`QUOTA_MODE` default disabled). Design: `docs/development/plm-collab-v2-seats-cap-clearing-design-20260621.md`.
- License lifecycle: import (`license_import_service.py`, Ed25519 verify, idempotent upsert, audit) → seat-cap projection (fail-open, audited `path=cli:license/seat-cap?max_users=…`) → status (`license_status.collect_license_status`, **CLI only**) → entitlement (`is_entitled`). Cap-change audit trail exists (`AuditLog method=LICENSE`), queryable only via `/admin/audit/logs?path=cli:license/seat-cap` (superuser). `GET /features/{key}` returns entitled+upgrade. Tests all in `ci.yml`.

**Forks / gaps:** (A) license **status HTTP endpoint** — only CLI today; `collect_license_status` ready to reuse. (B) cap-change **audit endpoint/UI** — trail exists but buried in generic audit logs. (C) **revoke/rollback ops** — none; needs new service + an owner decision on rollback semantics (restore prior cap ⇒ needs `previous_max_users` or audit replay).

**Recommended first task — L4-1 (Fork A):** `GET /api/v1/admin/license-status?tenant-id=…` reusing `collect_license_status` (whitelist fields, never expose `license_data`). **No schema change**, fastest + safest. Auth pattern (per `feedback_plm_collab_entitlement_invariant`): admin/superuser gate **before** any is_entitled; READ projection pins `auth → is_entitled → query`, no existence leak; response `Cache-Control: no-store` + `Vary`.
> ✅ **MERGED — #881.** Adding the route bumped the app-route count **721→722 in lockstep across all four design-lock pins** (`test_breakage_design_loopback_metrics.py`, `test_phase4_search_closeout_contracts.py` [authoritative], `test_metrics_router_route_count_delta.py` `EXPECTED_TOTAL_ROUTES`, `test_tier_b_3_breakage_design_loopback_portfolio_contract.py` literal string-pin). Test mirrors `test_feature_router` (in-memory SQLite, overridden get_db+require_superuser); registered in ci.yml sorted contracts list + detect_changes entitlement case (anti-false-green — the test actually ran). Verified: local 44 passed; CI `contracts` **1874 passed**. (Route-count design-lock = the blast radius that made this the last of the four first-cuts.)

---

## Cross-line notes
- **All three first tasks are additive read/query endpoints or a CI fix** — low-risk, in-repo, **no operator assets** needed (unlike CAD A1a-2's curated corpus, or the license default-flip go/no-go which stays owner/ops-gated).
- **New-test CI registration is the recurring trap** (`feedback_yuantus_silent_failure_traps`): any new `test_*.py` must go in `ci.yml`'s explicit pytest list (+ `conftest` allowlist if no-DB) or it is CI false-green. L3-0 fixes one existing instance.
- **Per-phase opt-in:** picking a line below authorizes ONLY its named first task; impl lands in a fresh per-line worktree+branch with its own `DEV_AND_VERIFICATION_*`.
