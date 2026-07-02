# Date-obsolete revert — DP1 (i)/(ii) implementation + verification (2026-07-01)

**Status:** IMPLEMENTED — the ratified DP1 (i)/(ii) light path only.
**Ratifies/builds:** #932's DP1 two-tier default — the **(i)/(ii) reopen + un-acknowledge** review-flag revert, audited by an **append-only correction event (iv)**, `require_admin_permission` (DP2), idempotent/no-existence-leak (DP3). **DP1 (iii) undo child-obsolete promotion is DEFERRED** to a separate ratification, per owner instruction ("只先做 table-local correction，即 DP1 的轻路径 (i)/(ii)；不要碰 lifecycle undo").
**Grounding:** `origin/main` (with #931 ratification merged).

---

## 1. What shipped

New admin ops routes on the existing `date_obsolete_ops_router` (already mounted):
- `POST /api/v1/cadpdm/date-obsolete-impacts/{impact_id}/revert` — single.
- `POST /api/v1/cadpdm/date-obsolete-impacts/revert-batch` — batch.

Each reverts an impact's **review flag** `acknowledged → open` and clears `acknowledged_at`/`acknowledged_by_id`, **appending** a `DateObsoleteImpactCorrection` row that snapshots the prior review-axis state. Both are `require_admin_permission`-gated (DP2, mirrors acknowledge).

## 2. The load-bearing design decision — why a separate audit table (not `properties`)

The obvious "append-only" home would be `DateObsoleteImpact.properties` (JSON). **It is unsafe:** the worker's `_upsert_impact` executes `existing.properties = props` on *every* re-scan (`date_effectivity_obsolete_service.py:236`), so an in-row trail would be silently wiped by the poller. The correction event therefore lives in its **own table** `meta_date_obsolete_impact_corrections`, which the worker never touches.

Conversely, the **review axis is worker-stable**: `_upsert_impact` refreshes only `child_obsoleted`/`reason`/`properties` (`:234-236`) and never `state`/`acknowledged_*`. So a (i)/(ii) revert is stable against the poller — it will not be re-acknowledged, and (unlike the deferred (iii)) it has no re-promotion hazard.

## 3. (iii)-isolation — the never-cross guard

The revert touches **only** `state` + `acknowledged_*` and appends the correction. It never reads or writes `child_obsoleted`, the worker-derived `reason`, or the child Item's lifecycle state. This is locked by a golden test (`test_revert_does_not_touch_child_obsoleted_or_item_lifecycle`): an acknowledged impact with `child_obsoleted=True` and the child Item in the `Obsolete` lifecycle state, reverted → `child_obsoleted` stays `True`, `reason` unchanged, and the Item stays `Obsolete`. That is the structural proof the review-flag revert never crosses into deferred DP1 (iii).

## 4. Idempotency / audit (DP3) — mirrors batch-ack (#898)

Only `acknowledged` rows transition; already-`open` rows are a no-op; unknown ids are silently skipped (not 404, no existence leak); commit once; return only the rows transitioned this call. The correction event is **append-only** — never mutated/deleted; ack→revert→ack→revert appends two events, preserving full history (a revert can itself be reverted by re-acknowledging then reverting again).

## 5. Verification

- **56/56** pass in `test_date_obsolete_wiring.py` + `test_date_effectivity_obsolete_service.py` (10 new revert tests + all existing worker/ops/service tests — no regression), on the real SQLite DB tier (`YUANTUS_PYTEST_DB=1`, python 3.11).
- New revert coverage: single reopen+clear-ack+correction with prior snapshot; already-open no-op; 404; batch transitions-acknowledged-only; unknown-skipped-no-leak; dedup+idempotent; empty; **(iii)-isolation golden**; non-admin 403 (single + batch); correction survives a simulated worker `properties` overwrite; append-only history.
- **Migration** `date_obsolete_corr_001` (on the real head `bom_writeback_audit_002`): `alembic heads` shows it as the clean single head; full-chain `alembic upgrade head` on a fresh DB creates the table; `downgrade -1` drops it cleanly (both directions execute).

## 6. Scope / not done (deferred, still gated)

- **DP1 (iii) undo child-obsolete promotion** — reverses a real Item lifecycle transition; needs `LifecycleService.promote` reversibility + audit, the `child_obsoleted=True` ambiguity (`already_obsolete` vs promoted-this-run), and worker re-promotion stability resolved first. Its authz is superuser-only (DP2). Not built here; awaits separate ratification.
- No worker behavior change; no change to the acknowledge routes; the `reason` on a revert is caller-supplied and optional.
