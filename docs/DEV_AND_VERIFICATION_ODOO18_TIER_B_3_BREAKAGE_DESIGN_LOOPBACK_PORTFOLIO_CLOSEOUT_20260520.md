# Tier-B #3 Breakage Design-Loopback Portfolio Closeout — Development and Verification

Date: 2026-05-20

## 1. Purpose

Close out Tier-B #3 §3 — the **breakage design-loopback**
catalog — and machine-pin its surface against future
drift. The entire §3.1–§3.7 catalog merged 2026-05-19/2026-05-20
through 12 PRs; this document is the single canonical ledger
plus the contract surface a companion drift-guard test
(`test_tier_b_3_breakage_design_loopback_portfolio_contract.py`)
asserts. Pure additive — no runtime, no route, no schema, no
event subscriber.

## 2. Delivered (6 slices, 12 PRs)

Catalog in §-order; each slice = (taskbook PR if any) + impl PR,
both squash-merged into `origin/main`. The §3 remainder
taskbook (#601) is the umbrella spec; §3.1 has no separate
taskbook (its boundary was a single-route extension covered by
#601). §3.5 (RBAC capability) is **author-rejected** and
NOT delivered.

- **§3.1 route exposure** — impl PR #602 (`a02dbd0`). New
  `POST /breakages/{incident_id}/design-loopback/eco`; thin
  delegation; `ValueError` → 404 `breakage_not_found` / 409
  `breakage_not_eligible_for_loopback`; non-`ValueError` →
  rollback + re-raise verbatim. Phase-4 route-count pin bumped
  676 → **677**. DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_ROUTE_R1_20260519.md`.
- **§3.2 durable idempotency** — taskbook PR #603 (`3e5104f`) +
  impl PR #604 (`2609bba`). `BreakageIncident.eco_id` bare
  String soft-link (nullable, unique, indexed, **no FK**) +
  compare-and-swap UPDATE
  `WHERE id=:i AND (eco_id IS NULL OR eco_id NOT IN (SELECT id
  FROM meta_ecos))` as the concurrency sync point. Dangling
  link = degraded-to-no-link. Alembic head: `ab1c2d3e4f5a`.
  DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_DURABLE_IDEMPOTENCY_R1_20260519.md`.
- **§3.3 `update_status` auto-trigger** — taskbook PR #605
  (`fb9d0b5`) + impl PR #606 (`757c411`). Default-OFF
  `auto_loopback: bool = False` + `loopback_user_id:
  Optional[int]`. §3.C ratified single behavior: normal CAS
  race → service self-heal (re-read, re-apply target status)
  → ordinary success, no client retry; unrecoverable race →
  dedicated `BreakageDesignLoopbackLinkRace` → route 409
  `breakage_loopback_link_race`. DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_UPDATE_STATUS_AUTO_TRIGGER_R1_20260519.md`.
- **§3.4 helpdesk-sync auto-trigger** — taskbook PR #607
  (`cab1162`) + impl PR #608 (`fb3f099`). §3.E shared private
  helper `_auto_trigger_design_loopback` extracted from §3.3
  (behavior-preserving; §3.3 protected test stayed green
  zero-line diff). §3.C-β reorder in
  `apply_helpdesk_ticket_update`: status-only flush → helper
  → then the heavy helpdesk mutations on the returned
  incident + PK-refetched job — a CAS-loser rollback can only
  ever unwind the status-only flush; helpdesk state is never
  in the rolled-back window. §3.D double gate
  (`derived_sync_status == "completed"` AND status-eligible)
  closes the `canceled`→`closed`/`failed` vector and the
  explicit `incident_status` override + failed-sync vector.
  DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_R1_20260519.md`.
- **§3.6 event emission** — taskbook PR #609 (`61ce226`) +
  impl PR #610 (`b848d66`). `BreakageDesignLoopbackEcoEvent`
  reuses the existing transactional outbox (`enqueue_event` /
  `event_bus`). §3.A/§3.B emission is **inside
  `create_breakage_design_loopback_eco`** (reached by all three
  trigger sources: the §3.1 route directly, and §3.3
  `update_status` / §3.4 `apply_helpdesk_ticket_update` via
  the shared `_auto_trigger_design_loopback` helper).
  §3.B/§3.C exactly **two enqueue branches** — CAS-winner
  (after the post-CAS flush) + dedupe-early-return — and
  **NOT** the CAS-loser / unrecoverable / `allow_duplicate`
  branches → exactly one event per incident-link, zero
  double-emit (reuses the existing transactional-outbox
  rollback-drop, identical to how `EcoCreatedEvent` already
  dedups on the CAS-loser). §3.E S2 settings flag
  `BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED` (default
  **False**). §3.F additive kwargs: `trigger_source` (route /
  update_status / helpdesk_sync), `sync_status`,
  `provider_ticket_status` — defaulted; helpdesk source
  threads its `derived_sync_status` /
  `normalized_provider_status` through the helper. DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_EVENT_EMISSION_R1_20260519.md`.
- **§3.7 metrics** — taskbook PR #611 (`c2e404f`) + impl PR
  #612 (`c302089`). Three current-state gauges added to the
  **existing** `prometheus_metrics` text via a new
  `_breakage_design_loopback_metrics` helper called only
  there:
  `yuantus_parallel_breakage_design_loopback_links_total` (no
  labels), `…_by_status` (only `{status}`), `…_by_severity`
  (only `{severity}`). SQL `func.count` + `GROUP BY` with the
  §3.B live-link 口径 `eco_id IN (SELECT id FROM meta_ecos)`
  (dangling excluded). **No `common_labels`** —
  current-state values must not carry window/filter labels.
  `summary()` untouched. DEV MD:
  `DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_R1_20260519.md`.

The umbrella **§3 remainder taskbook** is PR #601 (`7fce255`)
— enumerated the 7-slice catalog (§3.5 explicit reject;
§3.1–§3.4 + §3.6/§3.7 delivered as above).

## 3. Cumulative design invariants (held across all 6 slices)

- **One substrate, six coherent slices.** §3.2's
  `BreakageIncident.eco_id` bare-String CAS link is the
  durable source-of-truth. §3.3 self-heal, §3.4 §3.C-β reorder,
  §3.6 transactional-outbox dedup-via-rollback, and §3.7
  live-link 口径 all read or compose with that single column —
  no parallel state, no event-derived counter, no schema fork.
- **Default-OFF per slice.** §3.3/§3.4 `auto_loopback: bool =
  False`; §3.6 settings flag default `False`; §3.7 gauges are
  read-only at scrape time. Every slice is byte-identical
  pre-§3.x when its switch is off.
- **Dangling `eco_id` = degraded-to-no-link.** §3.2's CAS
  predicate, §3.7's live-link 口径, and §3.6's `eco_id`
  required-`str` payload all agree: a hard-deleted ECO's
  dangling link is not a real loopback link.
- **Shared race handler, one place.** §3.4 extracted §3.3's
  inline block into `_auto_trigger_design_loopback`; the §3.C
  ratified self-heal / unrecoverable lives in ONE method;
  `update_status` and `apply_helpdesk_ticket_update` both
  delegate. §3.6 / §3.7 added only default-preserving kwargs
  (`trigger_source`, `sync_status`, `provider_ticket_status`)
  — the helper's race semantics are unchanged across §3.4 →
  §3.7.
- **No new route across the catalog.** `len(app.routes)` was
  bumped exactly once (§3.1, 676 → 677) and stayed at **677**
  through §3.2 / §3.3 / §3.4 / §3.6 / §3.7.
- **Protected behavior pins.**
  `test_breakage_update_status_auto_trigger.py` stayed green
  with **zero-line diffs** under the §3.4 helper extraction,
  the §3.6 event emission, and the §3.7 metrics —
  behavior-preservation proven, not argued.
- **Reviewer caught Medium per slice.** Concurrency-correctness
  (§3.2), choose-one wording (§3.3 / §3.4), surface
  containment + `common_labels` (§3.7), helpdesk context
  threading + `eco_id` Optional contradiction (§3.6). The
  converge-on-review pattern is now established discipline.

## 4. Recorded rejected / scoped-out (don't re-litigate)

- **§3.5 RBAC capability** — author-rejected as
  recommended-against; explicit RBAC capability gating for
  the design-loopback path was not added. Reopening requires a
  fresh RFC.
- **§3.4 §3.C option α** (trigger after all helpdesk
  mutations, accept the silent helpdesk-state loss on the
  CAS-loser race) — rejected. **Option γ** (replay-callback
  helper) — recorded rejected in favor of β
  (status-first reorder) which is smaller and keeps the
  shared helper pure.
- **§3.6 §3.E S1** (per-call `emit_loopback_event` param on
  3 request models) and **S3** (no switch, mirror
  `EcoCreatedEvent`) — rejected in favor of S2 (settings flag).
- **§3.6 `created_vs_reused` metric (§3.7 §3.C)** — explicitly
  out: no persisted signal; the event's `created` flag is
  transient; counting from `event_bus` is the restart /
  multi-process loss trap. An event-stream consumer is the
  proper future home for create-vs-reuse aggregation.
- **§3.7 windowed link-rate, additional label dimensions, SLO
  thresholds** — out of scope; this slice is three plain
  gauges only.
- **Durable-delivery / outbox / retry for §3.6** — inherited
  best-effort `after_commit` publish; a durable outbox is a
  separate future opt-in.
- **`execute_helpdesk_sync` / `record_helpdesk_sync_result`**
  remain non-trigger-points (they touch only `ConversionJob`,
  not `incident.status`); only `apply_helpdesk_ticket_update`
  is the helpdesk trigger surface.

## 5. Open follow-ups (none auto-entered; each its own opt-in)

- **Event consumer / materialized read-model** subscribing to
  `breakage.design_loopback_eco` — would enable
  `created_vs_reused` and other downstream signals as a
  stateful aggregation outside the metric-from-state surface.
- **Durable-delivery outbox** for §3.6 — replace the
  best-effort in-memory `event_bus` with a persisted-then-
  published outbox for at-least-once delivery.
- **Default-ON flips** for §3.3 / §3.4 `auto_loopback` and the
  §3.6 settings flag — each a separate explicit opt-in;
  unchanged byte-identical default is the current contract.
- **Windowed link-rate** metrics (links-per-window
  derivative) — a separate metric semantic from the
  current-state gauges.
- **Additional label dimensions** (`by_responsibility`,
  `by_product_item`, etc.) — additive to the three §3.7
  gauges; not in scope for the closeout.
- **Fail-open hardening / `_ALLOWED_TYPES` widening** (from
  the upstream automation engine work) — unrelated to
  Tier-B #3 but recorded in the broader Tier-B follow-ups
  ledger.

## 6. Portfolio drift guard

The companion test
`src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py`
machine-pins the catalog's contract surface:

1. **This closeout MD lists all 12 PRs** (#601 + #602 + the
   five taskbook+impl pairs for §3.2/§3.3/§3.4/§3.6/§3.7) and
   the six catalog DEV MDs.
2. **`BreakageIncidentService._auto_trigger_design_loopback`**
   exists with required keyword-only params
   `{target_status, loopback_user_id, trigger_source}` and
   optional `{sync_status, provider_ticket_status}` —
   subset checks (`<=`) so future additive kwargs remain
   allowed but removal fails loudly.
3. **`BreakageIncidentService.update_status`** exists with
   optional keyword-only params `{auto_loopback,
   loopback_user_id}`.
4. **`BreakageDesignLoopbackLinkRace`** is a subclass of
   `RuntimeError` (NOT `ValueError`, NOT `Exception` directly)
   — so the §3.3/§3.4 route ordering keeps catching it before
   the 404 / verbatim / 400 clauses.
5. **`BreakageDesignLoopbackEcoEvent`** has required fields
   `{incident_id, eco_id, created, trigger_source,
   incident_status}` and optional `{sync_status,
   provider_ticket_status}`; `event_type` is a **defaulted
   discriminator** with default `"breakage.design_loopback_eco"`
   (not a required Pydantic field — its default is what the
   guard pins).
6. **`Settings.BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED`** field
   default is `False` (S2 ratified; checked via
   `Settings.model_fields[...]`, NOT `get_settings()` runtime
   value).
7. **The three §3.7 Prometheus metric names** appear in
   `parallel_tasks_service.py` source (cheap source-scan
   instead of duplicating the §3.7 runtime harness).
8. **`summary()`'s implementation does NOT mention
   `breakage_design_loopback`** (source-scan) — pins the
   §3.A (b) surface containment.
9. **The phase-4 route-count assertion `len(app.routes) == 677`
   still lives in `test_phase4_search_closeout_contracts.py`**
   (source-scan cross-reference, not a re-assertion).
10. **The six catalog DEV MDs exist on disk**.
11. **The three canonical `trigger_source` values** are
    referenced in the service source (`"route"`,
    `"update_status"`, `"helpdesk_sync"`).

The guard is read-only / pure introspection — no DB, no
`event_bus.subscribe`, no fixture-laden setup — and runs
sub-second.

## 7. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_event_emission.py \
  src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
git diff --check
```

## 8. Non-Goals (hard boundaries for this PR)

- No runtime code change. No edit to any merged service /
  route / event / settings / schema.
- No new route; no request/response model change; no migration;
  no schema; no event subscriber.
- No reopening of §3.5, the rejected §3.4-α/γ, §3.6-S1/S3, or
  `created_vs_reused`.
- No CAD pool work — it remains deferred until its four entry
  conditions are satisfied.
- `.claude/` and `local-dev-env/` stay out of git.
