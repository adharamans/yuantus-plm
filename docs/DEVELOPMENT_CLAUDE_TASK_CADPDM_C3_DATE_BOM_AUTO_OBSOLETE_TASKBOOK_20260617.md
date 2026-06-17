# Claude Taskbook: CAD-PDM **C3** — date-BOM auto-obsolete + upward where-used propagation

Date: 2026-06-17
Status: **DECISION — doc-only; OWNER-PRIORITIZATION-GATED**
Source: `DEV_AND_VERIFICATION_ODOOPLM_19_CADPDM_REMAINING_DEV_CLOSEOUT_20260616.md` §C3 — "deferred
per owner … P2, taskbook-first when prioritized." This taskbook satisfies the "taskbook-first"
gate so that the only remaining gate is the owner's **prioritization** + ratification.

## 0. Scope & the open priority question

A genuinely new feature: when a BOM line's **effectivity expires** (date-effective BOM), the
affected item/version is **auto-obsoleted**, and the change **propagates upward** (where-used) so
parents are flagged for review. Two pieces the closeout names: a **date trigger / scheduler** and
**upward propagation**. **This is owner-deferred (P2)** — do not build until the owner prioritizes.

## 1. Grounded baseline (what exists to reuse — file:line)

- **Obsolete/supersede**: `version/models.py:83` `is_superseded`; `version/service.py:584-586`
  sets `predecessor.is_superseded = True` (B1, merged `209820d8`). Reuse this transition (do NOT
  invent a new obsolete state).
- **Where-used (upward)**: `web/bom_where_used_router.py` + the where-used traversal service — the
  parent/used-in direction for propagation.
- **Effectivity dates**: `services/effectivity_service.py` (`EffectivityService`,
  `get_item_effectivities`); BOM is product-scoped + **date-effective** (`get_bom_structure(item_id)`
  + effectivity date — see C1). So "expired" = an effectivity whose `valid_to` is in the past.
- **No cron/scheduler infra**: the only background pattern is the **worker-poll** loop
  (`ecm_publication/worker.py`, `erp_publication/worker.py` — claim due → process → reschedule).
  C3's "scheduler" must be that pattern (a polling worker), NOT a new cron dependency.

## 2. Proposed locked decisions (ratify at prioritization)

- **D1 — Trigger** = a BOM-line effectivity with `valid_to` strictly in the past and not yet
  actioned. The worker scans for these (a `next_scan_at`/`actioned` marker prevents re-processing).
- **D2 — Scheduler = a polling worker** (`cadpdm_auto_obsolete_worker`), default-OFF
  (`CADPDM_AUTO_OBSOLETE_ENABLED`, restart-only), mirroring the ECM/erp worker (claim due batch →
  act → mark). No cron infra introduced.
- **D3 — Auto-obsolete** reuses the B1 `is_superseded`/obsolete transition on the affected
  version; **idempotent** (already-obsolete → skip). An audit row per auto-obsoletion.
- **D4 — Upward propagation = FLAG, not cascade-obsolete (load-bearing).** Traverse where-used
  (parents of the obsoleted item) and record a **"where-used impacted / needs-review"** signal on
  each parent (a flag/event), **NOT** an automatic parent-obsolete — cascading obsolete up a BOM
  is destructive and product-changing. Depth: **1 level by default** (configurable); a full
  transitive walk is an opt-in.
- **D5 — Idempotency + audit**: the worker re-run is safe (markers); every auto-obsolete + every
  propagation flag is audited. Ops visibility routes (list impacted / actioned) like the ECM ops.
- **D6 — Boundary**: default-off; no change to manual obsolete, release, or BOM-apply (C1 proved
  apply must not touch the live BOM). Tenant-baseline + route-count pins updated for any new
  table/route.

## 3. Open questions to ratify (with the owner, when prioritized)

- **OQ1 priority**: build now vs keep deferred (it's P2, owner-deferred). *Owner's call.*
- **OQ2 propagation**: **flag-for-review (D4)** vs cascade-obsolete (strongly NOT recommended).
- **OQ3 depth**: **1 level** vs transitive where-used.
- **OQ4 trigger granularity**: per-effectivity-expiry vs a daily sweep.

## 4. Verification plan (when built)

Worker scans → expired effectivity → version obsoleted (idempotent, audited); parents flagged
(not obsoleted); default-off = no behavior change; re-run safe; ops routes + pins + tenant
baseline; CI dual-registration.

## 5. Why this is doc-only now

C3 is **owner-deferred (P2)**. This taskbook prepares it (the "taskbook-first" gate); the
**implementation waits for the owner to prioritize** — building an owner-deferred, product-changing
feature (auto-obsoleting released versions) ahead of that prioritization would be exactly the kind
of unrequested change the deferral guards against.
