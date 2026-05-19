# Odoo18 Breakage Helpdesk-Sync Auto-Trigger R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement Tier-B #3 §3.4 per the ratified taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_20260519.md`,
merged 2026-05-19 as `cab1162` / PR #607, §3.C ratified as β).
Add a **default-OFF** opt-in so a helpdesk-sync ticket-update
that lands the incident on a loopback-eligible status
(`resolved` / `closed`) automatically spawns (or reuses, via the
§3.2 durable path) the design-loopback ECO — **reusing §3.3's
auto-trigger + self-heal + unrecoverable semantics through a
single shared helper, adding NO second race handler**.

## 2. Scope

### Modified

- `src/yuantus/meta_engine/services/parallel_tasks_service.py` —
  extracted §3.3's inline auto-trigger block into the shared
  private helper `_auto_trigger_design_loopback(self,
  incident_id, *, target_status, loopback_user_id) ->
  BreakageIncident` (behavior-preserving); `update_status` now
  delegates to it. `apply_helpdesk_ticket_update` gains
  `auto_loopback: bool = False` + `loopback_user_id:
  Optional[int] = None` and the §3.C-β reorder + §3.D double
  gate.
- `src/yuantus/meta_engine/web/parallel_tasks_breakage_router.py`
  — `BreakageHelpdeskTicketUpdateRequest` gains `auto_loopback:
  bool = False`; `apply_breakage_helpdesk_ticket_update`
  forwards `auto_loopback` + `loopback_user_id=int(user.id)` and
  gains the §3.I exception clauses.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py`
  (8 MANDATORY + 2 route-level tests).
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_R1_20260519.md`

### Unchanged by design

`create_breakage_design_loopback_eco`, §3.2's CAS / its
`session.rollback()`, all merged contracts,
`test_breakage_update_status_auto_trigger.py` (the §3.E
behavior-preservation proof — green UNCHANGED), every other
route (no new route — `len(app.routes)` stays 677), no
schema/alembic/tenant-baseline, the route/API response shape,
`.claude/`, `local-dev-env/`.

## 3. Implementation

### 3.1 §3.E — the shared helper extraction (behavior-preserving)

§3.3's post-flush block (eligibility gate → `loopback_user_id`
guard → `create_breakage_design_loopback_eco` → `created`
handling → §3.C self-heal → `BreakageDesignLoopbackLinkRace`)
is now `_auto_trigger_design_loopback`. `update_status` keeps
its get→raise→set-status→`updated_at`→flush→`if not
auto_loopback: return` prefix and then `return
self._auto_trigger_design_loopback(...)`. The helper does its
own identity-mapped `session.get` (no extra SQL — the caller
already loaded + flushed the row), so the post-update
descriptor is byte-identical to §3.3's inline version. **Proof:
`test_breakage_update_status_auto_trigger.py` stays green with
a zero-line diff** (10 passed, file unchanged) — that is the
behavior-preservation evidence required by the taskbook §3.E,
not an argument.

### 3.2 §3.B — switch + default-OFF byte-identical

`apply_helpdesk_ticket_update` gains `auto_loopback=False` +
`loopback_user_id=None`. `auto_loopback=False` runs the
**original single-flush flow on the originally-resolved job**
(no eligibility/sync gate, no descriptor build, no
`create_…eco`, no `eco_id` write). The only intra-transaction
change on the OFF path is that `incident.updated_at = now` is
set just before the shared `_apply_helpdesk_mutations` closure
(which sets `responsibility` then the job envelope) instead of
just after the responsibility line — a behaviorally-inert
reorder: same session, same single flush, **identical final
committed state** (pinned by
`test_helpdesk_ticket_update_default_off_is_byte_identical`,
which also spies the helper / `create_…eco` / eligibility and
asserts none called).

### 3.3 §3.C-β — the RATIFIED status-first reorder

For `auto_loopback=True`, after the event-replay short-circuit
(unchanged, still returns before any mutation):

1. set `incident.status = normalized_incident_status` +
   `incident.updated_at = now`; `session.add(incident)`;
   **flush ONLY that**;
2. §3.D **gate #1**: iff `derived_sync_status == "completed"`,
   call `self._auto_trigger_design_loopback(incident_id,
   target_status=normalized_incident_status,
   loopback_user_id=...)`. The helper applies **gate #2**
   (status eligibility), the `loopback_user_id` guard, the
   `create_…eco` call, and the §3.C self-heal / unrecoverable
   behavior — one race handler, reused;
3. re-fetch the job by PK (`session.get(ConversionJob,
   resolved_job_id)` — a §3.2 CAS-loser `session.rollback()`
   expires it; a PK get is deterministic and invariant under
   that ECO-only rollback) and apply the heavy helpdesk
   mutations (`responsibility` + the payload/status envelope)
   via the shared closure on the helper's returned incident.

The §3.2 CAS-loser `self.session.rollback()` can therefore
**only ever unwind the status-only flush** — the helper
self-heals that, and the responsibility / job-payload /
event-id-accumulator mutations land **after** the trigger
converges, never inside a rolled-back window. Grounded-safe:
`create_…eco`'s ECO draft (`_breakage_design_loopback_row`)
reads neither `responsibility` nor the job, so deferring them
past the trigger does not degrade the ECO. `updated_at` is the
status-only-flush value on the non-race / winner path, or the
helper's self-heal `_utcnow()` on the CAS-loser path;
`responsibility` does not re-bump it (matches pre-§3.4's single
bump).

### 3.4 §3.D — the non-redundant double gate

Gate #1 (`derived_sync_status == "completed"`) lives in
`apply_helpdesk_ticket_update`; gate #2 (status eligibility)
lives in the shared helper. Gate #1 is **not** redundant — two
grounded vectors where an *eligible* incident status co-occurs
with a *non-completed* sync, both MANDATORY-tested to NOT fire:

- **Vector A** — provider `canceled` (or alias `cancelled`) →
  incident `closed` (eligible) but
  `_HELPDESK_PROVIDER_TO_SYNC_STATUS` → `failed`
  (`test_helpdesk_ticket_update_auto_loopback_canceled_eligible_but_failed_sync_does_not_fire`).
- **Vector B** — explicit `incident_status` override forces an
  eligible status while the provider ticket derives
  `sync_status=failed`
  (`test_helpdesk_ticket_update_auto_loopback_incident_status_override_with_failed_sync_does_not_fire`).

### 3.5 §3.F / §3.G / §3.I

- **§3.F atomic coupling (single-incident, ratified):** an
  eligible+completed sync whose `create_…eco` raises propagates;
  the route rolls back the whole transaction (status +
  responsibility + job mutations) and surfaces the error
  verbatim. No batch entrypoint exists (the batch
  record-and-continue variant is scoped out as a future opt-in).
- **§3.G idempotent-replay:** the trigger is reached only after
  the status-only flush, which is after the event-id replay
  short-circuit, so a replayed provider event never fires the
  loopback (no new dedupe logic;
  `test_helpdesk_ticket_update_auto_loopback_idempotent_replay_does_not_fire`).
- **§3.I route exception order (specific→general):**
  `BreakageDesignLoopbackLinkRace` → 409
  `breakage_loopback_link_race` + rollback; `ValueError` →
  404/400 (unchanged); `(HTTPException, PLMException)` →
  rollback + re-raise **verbatim** (mirrors §3.3/§3.1 — no
  app-wide `PLMException`→HTTP handler exists, same intentional
  inheritance documented for §3.3, NOT re-litigated); a final
  `except Exception` → rollback + re-raise (this route never
  had a legacy `Exception → 400`, so the safe choice is an
  explicit rollback so a partially-flushed loopback/helpdesk
  state never leaks). Inert for default-OFF.

### 3.6 §3.J — response shape unchanged

`apply_helpdesk_ticket_update` still returns the
`get_helpdesk_sync_status` dict; the helper's returned
`BreakageIncident` is used **internally only** for the §3.C-β
continuation. Pinned by
`test_helpdesk_ticket_update_auto_loopback_completed_eligible_spawns_link`
(asserts no `eco_id` / `loopback` field added to the response).

## 4. Test Matrix

`test_breakage_helpdesk_sync_auto_trigger.py` — 10 tests
(StaticPool shared in-memory engine + `ConversionJob`):

- **`test_helpdesk_ticket_update_default_off_is_byte_identical`**
  (MANDATORY) — helper / `create_…eco` / eligibility spies all
  not-called; status + responsibility + job payload as pre-§3.4.
- **`test_helpdesk_ticket_update_auto_loopback_completed_eligible_spawns_link`**
  (MANDATORY) — provider `resolved`: `eco_id` linked, one ECO,
  status `resolved`, responsibility set, response shape
  unchanged.
- **`test_helpdesk_ticket_update_auto_loopback_canceled_eligible_but_failed_sync_does_not_fire`**
  (MANDATORY) — §3.D Vector A.
- **`test_helpdesk_ticket_update_auto_loopback_incident_status_override_with_failed_sync_does_not_fire`**
  (MANDATORY) — §3.D Vector B.
- **`test_helpdesk_ticket_update_auto_loopback_idempotent_replay_does_not_fire`**
  (MANDATORY) — replay short-circuit; helper not reached.
- **`test_helpdesk_ticket_update_auto_loopback_cas_race_preserves_status_and_helpdesk_mutations`**
  (MANDATORY) — the §3.C-β centerpiece. Winner links ECO_W +
  commits; loser's helpdesk auto-trigger loses the CAS → §3.2
  rollback → helper self-heal. Probe: status self-healed,
  `eco_id == ECO_W`, one ECO, **AND** `responsibility` + the
  job payload/status mutations present.
- **`test_helpdesk_ticket_update_auto_loopback_unrecoverable_race_maps_409`**
  (MANDATORY) — forced unrecoverable → service raises
  `BreakageDesignLoopbackLinkRace`; route → 409.
- **`test_helpdesk_ticket_update_auto_loopback_eco_permission_failure_rolls_back_all`**
  (MANDATORY) — real-engine route; ECO-create denied; probe:
  status `open`, responsibility `None`, no ECO, the helpdesk
  envelope this call would have written is absent (whole
  rollback); error RAISES (not collapsed into 400).
- **`test_route_helpdesk_ticket_update_auto_loopback_defaults_false_and_forwards`**
  — omitted → service receives `auto_loopback=False`; explicit
  True forwarded; `loopback_user_id == int(user.id)`.
- **`test_route_helpdesk_ticket_update_eco_permission_propagates_verbatim_not_400`**
  — carrier 1 `HTTPException(403)` → 403 verbatim, code ≠
  `breakage_helpdesk_sync_invalid`; carrier 2 real
  `PermissionError` → re-raised (a 400 collapse would return a
  JSON response, not raise), rollback called.

Regression unchanged & green:
`test_breakage_update_status_auto_trigger.py` (§3.3,
behavior-preservation proof, **zero-line diff**),
durable-idempotency, breakage tasks (helpdesk regression),
router contracts, phase-4 route-count pin (677, no new route),
doc-index trio, R2 portfolio.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
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
  src/yuantus/meta_engine/services/parallel_tasks_service.py \
  src/yuantus/meta_engine/web/parallel_tasks_breakage_router.py
git diff --check
```

Observed 2026-05-19: §3.4 10/10; §3.3 10/10 (file diff = 0);
full combined suite green; `py_compile` clean; `git diff
--check` clean; `len(app.routes)` unchanged at 677 (no new
route). (`test_parallel_breakage_helpdesk_traceability.py` from
the taskbook §6 indicative list does not exist;
`test_breakage_tasks.py` is the real helpdesk regression file
and is the one run.)

## 6. Non-Goals (reaffirmed from taskbook §8)

- No edit to `create_breakage_design_loopback_eco`, §3.2's CAS /
  `session.rollback()`, any merged contract, or
  `test_breakage_update_status_auto_trigger.py`.
- No new route; no schema/alembic/tenant-baseline.
- No batch helpdesk entrypoint / no record-and-continue coupling.
- `execute_helpdesk_sync` / `record_helpdesk_sync_result` are
  NOT trigger points and are unmodified.
- No route/API response extension.
- No event emission (§3.6) / metrics (§3.7).
- No default-ON. No new app-wide `PLMException`→HTTP handler.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Inter-slice status

- §3.1 route / §3.2 durable idempotency / §3.3 `update_status`
  auto-trigger: merged; unchanged by §3.4 (§3.3 reuses the
  same extracted helper — zero behavior change, proven by its
  unchanged test file).
- §3.4 helpdesk-sync auto-trigger: delivered (this slice).
- §3.6 event emission (could carry the loopback `created`
  result + the helpdesk context), §3.7 metrics, default-ON
  flip, a batch helpdesk auto-trigger (only if such an
  entrypoint is introduced): each its own future opt-in.
