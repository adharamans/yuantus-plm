# Odoo18 Breakage Design-Loopback Event Emission R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement Tier-B #3 §3.6 per the ratified taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DESIGN_LOOPBACK_EVENT_EMISSION_20260519.md`,
merged 2026-05-19 as `61ce226` / PR #609; §3.E ratified **S2 —
settings flag**). Emit a `breakage.design_loopback_eco` domain
event when a design-loopback ECO result converges, **reusing
the existing transactional `enqueue_event` / `event_bus` / ECO
event pattern — no new event infrastructure, no subscriber**.
Emission-only.

## 2. Scope

### Modified

- `src/yuantus/meta_engine/events/domain_events.py` — add
  `BreakageDesignLoopbackEcoEvent(DomainEvent)` (§3.A; required
  `eco_id: str`).
- `src/yuantus/config/settings.py` — add the S2 flag
  `BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED: bool` (default
  `False`).
- `src/yuantus/meta_engine/services/parallel_tasks_service.py`
  — import `enqueue_event` + the event; add
  `_enqueue_breakage_design_loopback_event` (no-op unless the
  flag is on); enqueue at the two §3.B branches of
  `create_breakage_design_loopback_eco`; add the §3.F additive
  kwargs (`trigger_source` default `"route"` / required on the
  helper; `sync_status` / `provider_ticket_status` defaulted
  `None`) to `create_breakage_design_loopback_eco` and
  `_auto_trigger_design_loopback`; `update_status` /
  `apply_helpdesk_ticket_update` thread their source.
- `src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py`
  and
  `src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py`
  — **only** the `_unrecoverable_creation` autospec side_effect
  helper signatures widened with `**_kw` (see §4). No
  assertion / behavior change; both stay 10/10.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_event_emission.py`
  (9 MANDATORY + 1 defensive test).
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_EVENT_EMISSION_R1_20260519.md`

### Unchanged by design

§3.2's CAS / `session.rollback()`, the merged contracts, every
route (no route/request-model change — S2 has zero API surface;
`len(app.routes)` stays 677), no schema/alembic, no subscriber,
no metrics (§3.7 untouched), `.claude/`, `local-dev-env/`.

## 3. Implementation

### 3.1 §3.A event + §3.E S2 flag

`BreakageDesignLoopbackEcoEvent` mirrors `EcoCreatedEvent`:
`event_type="breakage.design_loopback_eco"`, **required
`eco_id: str`**, `incident_id`, `created`, `trigger_source`,
`incident_status`, `sync_status=None`,
`provider_ticket_status=None`, `actor_id` (base). The S2
settings flag `BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED` (default
`False`, `YUANTUS_`-prefixed like its siblings) is the **single
gate**, checked once inside
`_enqueue_breakage_design_loopback_event` — so both §3.B call
sites stay trivial and default-OFF is byte-identical.

### 3.2 §3.B emission point — inside `create_breakage_design_loopback_eco`

`_enqueue_breakage_design_loopback_event` mirrors
`ECOService._enqueue_eco_created` (one transactional
`enqueue_event(self.session, …)`). Enqueued at exactly two
branches, before their returns:

- **dedupe early-return** (`existing is not None`) →
  `created=False`, `eco_id=existing.id`;
- **CAS winner** (`result.rowcount == 1`, **after** the
  post-CAS `self.session.flush()`) → `created=True`,
  `eco_id=eco.id` — the same transactional position as
  `_enqueue_eco_created` (verified `eco_service.py:550`,
  post-`flush`).

**NOT** enqueued on: the `rowcount == 0` CAS-loser branch; the
unrecoverable arm (`eco=None`); the `allow_duplicate=True`
branch. `incident_status` is sourced from
`preparation.descriptor.status` (the post-flush eligible
status), commented at both sites.

### 3.3 §3.C two-stage timing + CAS-loser zero-emit

`enqueue` happens at the loopback-result-determination point;
**`publish` happens at `after_commit`** (the route's single
commit), by which time the whole request has converged
(status/ECO/§3.4 helpdesk mutations applied) — "after
convergence" holds at publish time. Dedup is automatic and
reuses, not reinvents, the existing rollback-drop: the §3.2
CAS-loser's `self.session.rollback()` fires
`after_soft_rollback` which drops the pending queue (the
loser's `EcoCreatedEvent` and any queued loopback event), and
§3.B does not enqueue on that branch anyway. Net: **exactly one
`BreakageDesignLoopbackEcoEvent` per incident-link, zero
double-emit on the race** — pinned by the centerpiece test.

### 3.4 §3.F additive-signature threading + the protected-test consequence

`create_breakage_design_loopback_eco` gains `trigger_source:
str = "route"` (default keeps the §3.1 route caller unchanged)
and `sync_status` / `provider_ticket_status` (defaulted
`None`); `_auto_trigger_design_loopback` gains
`trigger_source: str` (required) + the same two defaulted.
`update_status` passes `trigger_source="update_status"`;
`apply_helpdesk_ticket_update` passes
`trigger_source="helpdesk_sync"` + its `derived_sync_status` /
`normalized_provider_status` (the values the emit point inside
`create_…eco` otherwise cannot see — the Medium finding).

**Protected-test consequence (disclosed).** The §3.3/§3.4
unrecoverable tests patch `create_breakage_design_loopback_eco`
with `autospec=True`; autospec forwards the **real** signature's
new kwargs to the test's `_unrecoverable_creation` side_effect,
which had a fixed signature → `TypeError`. This is the §3.4
precedent (a test pinning an old call shape legitimately
updated for a taskbook-ratified **additive** contract change,
cf. `test_parallel_tasks_router.py` in #608). The fix is the
**minimal, assertion-preserving** widening of those two
`_unrecoverable_creation` helpers with `**_kw` — zero
assertion/behavior change; both files stay 10/10. The §3.6
taskbook §3.F explicitly authorized the additive kwargs on
`create_…eco` **and** the helper; the alternative (hidden
instance-state to dodge autospec) would violate that
ratification and be worse engineering. This is the only edit to
those two files and it does not weaken them as the §3.4 §3.E
behavior-preservation proof (the loopback behavior they pin is
unchanged; with the flag OFF the emit helper is a no-op).

### 3.5 §3.D failure semantics (inherited, not re-litigated)

`enqueue_event` cannot fail (list append); `event_bus.publish`
runs post-commit and swallows handler exceptions. Emission is
best-effort, at-most-once, after-commit — identical to every
existing ECO/item event, the same intentional inheritance
documented for §3.3/§3.1. A publish/handler failure cannot roll
back the loopback (commit already happened). Durable delivery /
retry is scoped out.

## 4. Test Matrix

`test_breakage_design_loopback_event_emission.py` — 10 tests.
All subscribe via the `captured_loopback_events` fixture, which
**removes the handler on teardown** (event_bus is a
process-global singleton — mandatory isolation; the centerpiece
subscribes before the race begins). S2 flag flipped per test
via `_enable_events(monkeypatch)` on the real settings object
(all other settings stay real; restored on teardown).

- **`test_cas_winner_emits_one_event_with_created_true`**
  (MANDATORY) — one event; full payload incl.
  `trigger_source="route"`, `actor_id`.
- **`test_dedupe_reuse_emits_one_event_with_created_false`**
  (MANDATORY) — second call → `created=False`, same `eco_id`.
- **`test_cas_loser_race_emits_zero_events_winner_emits_one`**
  (MANDATORY) — centerpiece; two-session shared-engine race;
  exactly one event, the winner's `created=True`.
- **`test_unrecoverable_race_emits_zero_events`** (MANDATORY).
- **`test_eco_permission_failure_emits_zero_events`**
  (MANDATORY) — `ECOService.create_eco` denies before either
  enqueue branch.
- **`test_idempotent_replay_emits_zero_events`** (MANDATORY) —
  §3.4 replay short-circuit precedes `create_…eco`.
- **`test_default_off_emits_zero_events`** (MANDATORY) — asserts
  the S2 flag defaults `False`; loopback still works, zero
  events.
- **`test_trigger_source_threaded_route_update_status_helpdesk`**
  (MANDATORY) — all three sources tag `trigger_source`.
- **`test_helpdesk_source_event_carries_sync_context`**
  (MANDATORY, Medium finding) — helpdesk event carries
  `sync_status="completed"` / `provider_ticket_status="resolved"`;
  a route event leaves both `None`.
- **`test_allow_duplicate_true_emits_zero_events`** (defensive,
  advisor) — pins the §3.B reading that `allow_duplicate=True`
  is outside the loopback-link lifecycle and emits nothing.

Regression unchanged & green:
`test_breakage_update_status_auto_trigger.py` (10/10) and
`test_breakage_helpdesk_sync_auto_trigger.py` (10/10) with only
the §3.4 disclosed `**_kw` helper-signature widening;
durable-idempotency, breakage tasks, router contracts,
phase-4 route-count pin (677), doc-index trio, R2 portfolio.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_event_emission.py \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_breakage_tasks.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/events/domain_events.py \
  src/yuantus/config/settings.py \
  src/yuantus/meta_engine/services/parallel_tasks_service.py
git diff --check
```

Observed 2026-05-19: §3.6 10/10; §3.3 10/10 + §3.4 10/10 (only
the disclosed `**_kw` widening); full combined suite green;
`py_compile` clean; `git diff --check` clean; `len(app.routes)`
unchanged at 677 (no route/request-model change — S2).

## 6. Non-Goals (reaffirmed from taskbook §8)

- No new event infrastructure — `enqueue_event` / `event_bus` /
  the `_enqueue_eco_created` pattern reused.
- No subscriber / handler (consumers = separate future
  opt-ins).
- No edit to §3.2's CAS / `session.rollback()`, any merged
  contract, or the merged behavior of
  `create_breakage_design_loopback_eco` (additive,
  default-preserving kwargs only).
- No new route; no request-model / response change (S2); no
  schema / alembic / tenant-baseline; no metrics (§3.7
  untouched).
- No delivery guarantee / durable outbox / retry.
- No default-ON — the S2 flag defaults `False`.
- `execute_helpdesk_sync` / `record_helpdesk_sync_result` are
  NOT trigger points and unmodified.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Inter-slice status

- §3.1 / §3.2 / §3.3 / §3.4: merged; unchanged by §3.6 (with
  the S2 flag OFF the emit helper is a no-op → byte-identical;
  §3.3/§3.4 test files stay 10/10).
- §3.6 event emission: delivered (this slice). The emitted
  `breakage.design_loopback_eco` event has **no subscriber by
  default**; wiring a consumer is a separate future opt-in.
- §3.7 metrics: **untouched** — its own taskbook-then-impl
  opt-ins. A durable-delivery outbox / default-ON flip: each a
  separate future opt-in.
