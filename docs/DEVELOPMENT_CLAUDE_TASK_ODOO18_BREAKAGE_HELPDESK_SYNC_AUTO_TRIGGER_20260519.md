# Claude Taskbook: Odoo18 Breakage Helpdesk-Sync Auto-Trigger

Date: 2026-05-19

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

Tier-B #3 §3.4 (per the remainder catalog ratified at PR #601
`7fce255`). Add a **default-OFF** opt-in so a helpdesk-sync
transition that lands the incident on a loopback-eligible status
(`resolved` / `closed`) automatically spawns (or reuses, via the
§3.2 durable path) the design-loopback ECO — **reusing §3.3's
auto-trigger + self-heal + unrecoverable semantics, NOT
duplicating a second race handler**.

§3.4's entrypoint differs from §3.3's and so do its risk points.
This taskbook must pin: the trigger point, the switch, the
**double gate** (sync outcome AND status eligibility), the
**composition problem** (§3.4's entrypoint mutates far more than
§3.3's — the central question), the helper-extraction approach,
the atomic-coupling / batch adjudication, idempotent-replay
interaction, and the route refinement.

Prerequisites (merged): §3.2 durable idempotency (`2609bba` /
PR #604) and §3.3 `update_status` auto-trigger (`757c411` /
PR #606). §3.4 reuses §3.3's just-shipped logic.

## 2. Current Reality (grounded — direct file reads)

All citations verified by direct read (per
[[feedback-verify-grounding-facts]]). Line numbers are against
`main` @ `757c411`.

### Which helpdesk method transitions `incident.status`

`src/yuantus/meta_engine/services/parallel_tasks_service.py`:

- **`apply_helpdesk_ticket_update` (line 6268) is the ONLY
  helpdesk method that mutates `incident.status`** — line 6380
  `incident.status = normalized_incident_status`.
- `execute_helpdesk_sync` (6042) and
  `record_helpdesk_sync_result` (6163) mutate **only the
  `ConversionJob`** (payload / status / `last_error`); they do
  NOT touch `incident.status`. They are **NOT** §3.4 trigger
  points.
- A repo-wide non-test caller search confirms
  `apply_helpdesk_ticket_update` has exactly **one** caller:
  the route `apply_breakage_helpdesk_ticket_update`
  (`web/parallel_tasks_breakage_router.py:1163`). It is
  **single-incident, route-driven** — there is **no batch
  entrypoint** that mutates `incident.status` via helpdesk
  (the only `incident.status =` sites in the service are
  `update_status` 4228/4288 and `apply_helpdesk_ticket_update`
  6380).

### `apply_helpdesk_ticket_update` shape

- Signature: `(self, incident_id, *, provider_ticket_status,
  job_id=None, external_ticket_id=None, provider=None,
  provider_updated_at=None, provider_assignee=None,
  provider_payload=None, event_id=None, incident_status=None,
  incident_responsibility=None, user_id=None)`.
- `incident = self.session.get(BreakageIncident, incident_id)`;
  `ValueError` if missing (6284-6286).
- **Idempotent-replay short-circuit (6358-6362):** if the
  provider `event_id` was already seen, returns early
  **before** the line-6380 status mutation (no incident
  mutation on a replay).
- Final incident status =
  `normalized_incident_status` (6292-6295) = the explicit
  `incident_status` kwarg if given, else
  `_map_helpdesk_provider_ticket_to_incident_status(...)`.
- **Mutations performed before any would-be trigger point**
  (this is the §3.C composition problem):
  - `incident.status` (6380),
  - `incident.responsibility` (6381-6384),
  - `incident.updated_at` (6385),
  - the entire `target_job.payload` envelope (6387-6428),
  - `target_job.status` / `completed_at` / `last_error` /
    possibly `started_at` (6432-6448),
  - the `provider_event_ids` dedupe accumulator (6404/6430).
- `self.session.flush()` (6451) — **no commit; caller owns the
  boundary** (route's single `db.commit()` at 1177).

### The two provider mappings (the §3.D trap)

Class constants (2960-2992):

```
_HELPDESK_PROVIDER_STATUS_ALIASES: "cancelled" -> "canceled", ...
_HELPDESK_PROVIDER_TO_INCIDENT_STATUS:
   resolved->resolved  closed->closed  canceled->closed  failed->open  ...
_HELPDESK_PROVIDER_TO_SYNC_STATUS:
   resolved->completed closed->completed canceled->FAILED  failed->failed ...
```

`derived_sync_status =
_map_helpdesk_provider_ticket_to_sync_status(provider_ticket_status,
fallback=...)` (6375). **Critical:** a provider ticket status of
`canceled` (or alias `cancelled`) maps the **incident** status to
`closed` — which **IS** in `eligible_statuses =
frozenset({"resolved","closed"})` — while mapping the **sync**
status to `failed`. So an eligible-looking incident status can
co-occur with a failed sync.

### The route

`web/parallel_tasks_breakage_router.py`
`apply_breakage_helpdesk_ticket_update` (1150):

- calls `service.apply_helpdesk_ticket_update(..., user_id=int(user.id))`
  then a single `db.commit()` (1177).
- `BreakageHelpdeskTicketUpdateRequest` (line 143) — request
  model; **no `auto_loopback` field today**.
- `except ValueError` → 404 `breakage_not_found` (msg-prefix)
  else 400 `breakage_helpdesk_sync_invalid` (1178-1196). There
  is **no `except Exception`** — a non-`ValueError` propagates
  **without an explicit `db.rollback()`** (relies on
  `get_db()`'s `finally: close()` to discard the transaction).
- Returns a **`Dict`** (the sync-status view), NOT a
  `BreakageIncident` (1197-1198).

### §3.3 reusable logic (merged `757c411`)

`update_status` (4216-4291) contains the post-flush
auto-trigger **inline** (NOT yet a helper): eligibility gate →
`loopback_user_id` guard → `create_breakage_design_loopback_eco`
→ `created=True` return / `created=False` re-read + re-apply
target status + flush / unrecoverable →
`BreakageDesignLoopbackLinkRace` → route 409
`breakage_loopback_link_race`. The §3.3 self-heal **re-applies
only `incident.status` + `updated_at`**. `create_…eco`'s ECO
draft is built by `_breakage_design_loopback_row` (4293+) from
`description / status / severity / incident_code /
product_item_id / bom_id / version_id` — it does **NOT** read
`responsibility` or any `ConversionJob` field.

## 3. Design decisions

### 3.A Trigger point — PRE-RATIFIED

`apply_helpdesk_ticket_update` only (the sole helpdesk
incident-status mutation). `execute_helpdesk_sync` /
`record_helpdesk_sync_result` are explicitly NOT trigger points
(they never reach an eligible incident status). Fire on the
**post-update** incident status (mirrors §3.3 §3.A product
policy A1 — no old→new delta gating), AND only after the
real status mutation (so the idempotent-replay short-circuit at
6358-6362, which returns earlier, naturally never triggers).

### 3.B Switch name + signature — PRE-RATIFIED (consistent with §3.3)

- `apply_helpdesk_ticket_update` gains
  `auto_loopback: bool = False` and
  `loopback_user_id: Optional[int] = None`.
- `BreakageHelpdeskTicketUpdateRequest` gains
  `auto_loopback: bool = False`.
- Route maps `loopback_user_id=int(user.id)` (the
  ticket-update route always has an authenticated `user`;
  identical actor-source convention to §3.3).
- `auto_loopback=False` ⇒ **byte-identical** pre-§3.4 behavior
  (no eligibility check, no sync-outcome read for loopback, no
  create call, no `eco_id` write). Pinned by a MANDATORY test.

### 3.C THE COMPOSITION PROBLEM — *the central question* (RATIFY ONE)

§3.3's `update_status` mutates exactly `status` + `updated_at`
before the trigger, so §3.3's self-heal (re-read, re-apply
`status` + `updated_at`) fully restores it on a §3.2 CAS-loser
`self.session.rollback()`.

**`apply_helpdesk_ticket_update` mutates much more** (see §2:
`status`, `responsibility`, `updated_at`, the whole
`target_job.payload`, `target_job.status/completed_at/
last_error/started_at`, the `provider_event_ids` accumulator).
If the auto-trigger fires *after* those mutations + the 6451
flush and then loses the §3.2 CAS, the **full**
`self.session.rollback()` unwinds **all of them** — but §3.3's
self-heal re-applies only `status`. The responsibility update,
the sync-result envelope, the job-status transition, and the
event-id dedupe accumulator would be **silently lost on the
rare race**. That is a correctness gap, not cosmetic, and it is
exactly the rare race this taskbook must make deterministic.

Three options — the impl PR cannot ship until ONE is ratified:

- **(α) Trigger AFTER all helpdesk mutations; accept the gap.**
  Simplest, but a CAS-loser race silently drops helpdesk-sync
  state. **Author-rejected** (silent state loss on the very
  race we are hardening).
- **(β) Reorder: trigger on status FIRST, helpdesk mutations
  AFTER.** After the replay short-circuit: set
  `incident.status = normalized_incident_status` +
  `incident.updated_at` and flush *only that*; run the reused
  §3.3 helper (eligibility/sync gate → create → self-heal /
  unrecoverable); then on the helper's returned (re-read on the
  CAS-loser path) incident apply `responsibility` + the
  `target_job` payload/status mutations and flush again; route
  commits all. The §3.2 CAS-loser rollback can only ever unwind
  the *status-only* flush — the helper self-heals that, and the
  heavy helpdesk mutations are applied **after** the trigger has
  converged, so they are **never inside a rolled-back window**.
  The §3.3 helper stays **pure and unchanged**. Grounded safe:
  `create_…eco`'s ECO draft does not read `responsibility` or
  the job (see §2), so doing them after the trigger does not
  degrade the ECO. **Author-ratified.** Cost: a deliberate
  re-ordering of `apply_helpdesk_ticket_update` and a second
  flush.
- **(γ) Enhance the helper with a caller-mutation replay
  callback.** `_auto_trigger_design_loopback(self, incident, *,
  target_status, loopback_user_id, replay)`: after the
  post-rollback re-read the helper invokes
  `replay(refreshed_incident)` so the caller re-applies its
  FULL mutation set (incl. re-resolving + re-writing the
  `ConversionJob`) atop the self-healed status. Genuinely
  one race handler, no flow reorder, but materially more
  intricate (the replay must re-resolve the rolled-back job).
  **Author-viable-alternative**; ratify only if the §3.C-β
  reorder is rejected.

Reviewer must confirm **β** (or pick **γ**); **α** is recorded
rejected. This subsection is the gate — everything else is
satellite.

### 3.D The double gate — non-redundant (RATIFIED)

§3.4 fires the loopback **iff BOTH**:

1. `derived_sync_status == "completed"`, AND
2. `is_breakage_eligible_for_design_loopback(post-update
   descriptor)` is True (status ∈ {`resolved`,`closed`}).

Gate #1 is **NOT** redundant with gate #2. Two concrete vectors
where an *eligible* incident status co-occurs with a *non-
completed* sync (a failed/aborted provider outcome that must
NOT spawn an ECO — requirement: "provider sync 失败不触发
loopback"):

- **Vector A — the `canceled` mapping.** Provider ticket
  `canceled` (or alias `cancelled`) →
  `_HELPDESK_PROVIDER_TO_INCIDENT_STATUS` → incident `closed`
  (**eligible**) while `_HELPDESK_PROVIDER_TO_SYNC_STATUS` →
  `failed`. Gate #2 alone would wrongly fire.
- **Vector B — the explicit `incident_status` override.** The
  `incident_status` kwarg (6280, applied at 6292-6295) lets a
  caller force the incident to `resolved`/`closed` while the
  provider ticket still derives `sync_status=failed`. Gate #2
  alone would wrongly fire.

Both are closed by gate #1. The taskbook surfaces both so the
reviewer does not read gate #1 as belt-and-braces.

### 3.E Helper reuse / extraction — RATIFIED (requirement: no duplicate race handling)

Extract §3.3's inline post-flush block (eligibility →
`loopback_user_id` guard → `create_breakage_design_loopback_eco`
→ `created` handling → self-heal → `BreakageDesignLoopbackLinkRace`)
into a **private shared helper** on `BreakageIncidentService`
(e.g. `_auto_trigger_design_loopback(self, incident_id, *,
target_status, loopback_user_id) -> BreakageIncident`).
`update_status` and `apply_helpdesk_ticket_update` both call it.

- This is a **behavior-preserving refactor** of merged §3.3
  code. `update_status` is plain service code (no separate
  contract module), so the extract is allowed under §3.3's §8
  Non-Goals; it does **not** touch §3.2's CAS / `session.rollback()`
  or any merged *contract*.
- **§3.3's 10 MANDATORY/route tests are the regression guard
  and must stay green UNCHANGED** (no edits to
  `test_breakage_update_status_auto_trigger.py`) — that is the
  proof the extract preserved §3.3 behavior.
- The helper carries the §3.C-§3.2-rollback semantics in ONE
  place — §3.4 adds **no** second race handler (requirement
  satisfied).

### 3.F Atomic coupling + the batch question — EXPLICITLY ADJUDICATED (requirement #5)

Requirement #5 asks whether a loopback failure should roll back
the incident's sync status, OR record-and-continue a batch.
**Adjudication, not a dodge:**

- **Grounded reality:** `apply_helpdesk_ticket_update` is
  single-incident and route-owned-single-commit (§2, verified
  caller search). There is **no batch entrypoint** that mutates
  `incident.status` via helpdesk. The "single failure aborts
  the whole batch vs. record-and-continue" choice is therefore
  **vacuous for the code that exists**.
- **RATIFIED (single-incident path):** atomic coupling mirrors
  §3.3 exactly — an eligible+completed sync whose loopback
  `create_…eco` raises propagates; the route rolls back the
  **whole** transaction (the helpdesk status/responsibility/job
  mutations included) and surfaces the error verbatim. Opting
  into `auto_loopback` means an ECO-create failure blocks the
  helpdesk status transition. This is the intended,
  single-incident coupling.
- **SCOPED OUT (future opt-in):** if a batch helpdesk
  entrypoint that mutates `incident.status` is ever added, its
  per-item "abort vs. record-and-continue" atomic-coupling
  policy is a **separate taskbook + opt-in**, decided then
  against that entrypoint's real transaction shape. §3.4 does
  not pre-decide it and adds nothing batch-shaped.

### 3.G Idempotent-replay interaction — PRE-RATIFIED

The trigger is placed *after* the real status mutation (§3.C-β:
after the status flush), which is *after* the 6358-6362
event-id replay short-circuit. A replayed provider event
returns before the status mutation, so it **never** reaches the
trigger. §3.4 adds **no** new replay/dedupe logic; idempotency
of the ECO itself is §3.2's durable CAS (a non-replay re-trigger
on an already-linked incident returns `created=False`, no
duplicate).

### 3.H Provider-sync-failure — PRE-RATIFIED

Covered by §3.D gate #1 (`derived_sync_status == "completed"`).
A `failed` / `canceled` / queued / in-progress derived sync
never fires the loopback even if the incident status mapping is
eligible.

### 3.I Route exception refinement — RATIFIED

`apply_breakage_helpdesk_ticket_update` today: `except
ValueError` → 404/400; non-`ValueError` propagates with no
explicit `db.rollback()`. The §3.4 refinement (clause order
specific→general):

- `except BreakageDesignLoopbackLinkRace` → **409
  `breakage_loopback_link_race`** + `db.rollback()` (the §3.C
  unrecoverable arm; normal race is self-healed in the service
  and never reaches here).
- `except ValueError` → 404 `breakage_not_found` / 400
  `breakage_helpdesk_sync_invalid` (**unchanged**).
- `except (HTTPException, PLMException)` → `db.rollback();
  raise` (verbatim ECO-permission propagation, mirroring §3.3 /
  §3.1; no app-wide `PLMException`→HTTP handler exists — same
  intentional inheritance documented for §3.3, NOT re-litigated
  here).
- `except Exception` → `db.rollback(); raise`. Unlike §3.3's
  status route there is **no legacy `Exception → 400` behavior
  to preserve** here (this route only ever had `except
  ValueError`), so the safe choice is an explicit rollback +
  verbatim re-raise (a partially-flushed loopback/helpdesk
  state must not leak). Reviewer may instead elect to keep the
  pre-§3.4 implicit-propagation (no explicit rollback) for
  strict byte-identity — pin one; author recommends the
  explicit `db.rollback(); raise`.

Default-OFF is byte-identical regardless: with
`auto_loopback=False` none of `BreakageDesignLoopbackLinkRace`
/ ECO-permission / loopback paths can arise, so only the
unchanged `ValueError` clause is reachable.

### 3.J Return shape — PRE-RATIFIED (requirement #6)

`apply_helpdesk_ticket_update` returns its existing
sync-status `Dict`. §3.4 does **NOT** extend the route/API
response. The reused helper returns a `BreakageIncident` used
**internally only** (for the §3.C-β post-self-heal
continuation); it is not added to the response dict. Surfacing
the loopback outcome (the §3.2 `created` boolean) is a §3.6
(event) concern, explicitly out of §3.4 scope unless this
taskbook is separately amended to ratify it.

## 4. R1 Target Output (for the impl PR)

- `parallel_tasks_service.py`: extract §3.3's inline block to
  the shared private helper (§3.E, behavior-preserving);
  `update_status` calls it (no behavior change — §3.3 tests
  stay green unchanged). `apply_helpdesk_ticket_update` gains
  `auto_loopback` + `loopback_user_id`; implement the §3.C-β
  reorder (status-only flush → helper → then
  responsibility/job mutations + flush); fire the helper iff
  the §3.D double gate holds.
- `parallel_tasks_breakage_router.py`:
  `BreakageHelpdeskTicketUpdateRequest` gains `auto_loopback:
  bool = False`; route passes `auto_loopback=payload.auto_loopback,
  loopback_user_id=int(user.id)`; the §3.I exception clauses.
- No new route; `len(app.routes)` stays 677.
- No edit to `create_breakage_design_loopback_eco`, §3.2's CAS /
  `session.rollback()`, the merged contracts, or
  `test_breakage_update_status_auto_trigger.py`.

## 5. Tests Required (in the impl PR)

MANDATORY exactly-named (new file
`test_breakage_helpdesk_sync_auto_trigger.py`):

- **`test_helpdesk_ticket_update_default_off_is_byte_identical`**
  — `auto_loopback` absent/False: no eligibility/sync-gate
  check, no `create_…eco` (spy), no `eco_id`; incident status
  / responsibility / job payload behave exactly as pre-§3.4.
- **`test_helpdesk_ticket_update_auto_loopback_completed_eligible_spawns_link`**
  — provider `resolved` (→ incident `resolved`, sync
  `completed`) + `auto_loopback=True`: `eco_id` linked, one
  ECO, one commit, helpdesk job/payload intact.
- **`test_helpdesk_ticket_update_auto_loopback_canceled_eligible_but_failed_sync_does_not_fire`**
  — **the §3.D Vector A trap.** Provider `canceled` → incident
  `closed` (eligible) but `derived_sync_status == "failed"`:
  status/responsibility/job update normally, `create_…eco`
  spy **not called**, no ECO, no `eco_id`.
- **`test_helpdesk_ticket_update_auto_loopback_incident_status_override_with_failed_sync_does_not_fire`**
  — the §3.D Vector B trap: explicit `incident_status="closed"`
  + a provider ticket deriving `sync_status=failed`:
  `create_…eco` spy not called.
- **`test_helpdesk_ticket_update_auto_loopback_idempotent_replay_does_not_fire`**
  — a replayed `event_id` (6358-6362 short-circuit): returns
  early, no status mutation, `create_…eco` spy not called.
- **`test_helpdesk_ticket_update_auto_loopback_cas_race_preserves_status_and_helpdesk_mutations`**
  — **the §3.C-β centerpiece.** Two sessions on a shared
  engine (mirroring §3.2/§3.3 CAS harness). Loser's
  auto-trigger does §3.2's internal rollback; after self-heal +
  the §3.C-β continuation the committed row has: incident
  status == target (self-healed), `eco_id` == winner's ECO, no
  duplicate ECO, **AND `incident.responsibility` + the
  `target_job` payload/status mutations present** (proves the
  reorder kept helpdesk state out of the rolled-back window).
- **`test_helpdesk_ticket_update_auto_loopback_unrecoverable_race_maps_409`**
  — forced unrecoverable (taskbook-sanctioned side_effect:
  real `session.rollback()` then `created=False, eco=None`):
  service raises `BreakageDesignLoopbackLinkRace`; route → 409
  `breakage_loopback_link_race` (rollback called, commit not).
- **`test_helpdesk_ticket_update_auto_loopback_eco_permission_failure_rolls_back_all`**
  — eligible+completed + `ECOService.create_eco` permission
  error: the whole transaction rolls back (incident status,
  responsibility, AND job mutations all reverted); error
  propagates verbatim (NOT collapsed into 400
  `breakage_helpdesk_sync_invalid`).

Plus: route-level tests (auto_loopback default False & forwarded;
409 mapping; ECO-permission verbatim); and **`test_breakage_update_status_auto_trigger.py`
must stay green UNCHANGED** (the §3.E behavior-preservation
proof), alongside the breakage/route/phase-4(677)/doc-index/
R2-portfolio regression.

## 6. Verification Commands (impl PR)

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_tasks.py \
  src/yuantus/meta_engine/tests/test_parallel_breakage_helpdesk_traceability.py \
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

The impl PR must enumerate the actual helpdesk regression test
file names it ran (the names above are indicative — the impl PR
verifies and lists the exact set). No alembic / tenant-baseline
— §3.4 adds no schema.

## 7. DEV/verification MD requirements (impl PR)

`docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_R1_20260519.md`
+ index line. Must document: the ratified §3.C composition
choice (β vs γ) **as implemented**, with the §3.2-rollback
mutation-scope analysis that forces it; the §3.D double-gate
with BOTH trap vectors (canceled-mapping; incident_status
override); the §3.E helper-extraction behavior-preservation
proof (§3.3's tests green unchanged); the §3.F single-incident
atomic-coupling ratification + batch scope-out; the default-OFF
byte-identical proof; the idempotent-replay non-interaction; the
§3.I route clause order + the §3.3/§3.1 verbatim-propagation
inheritance (cross-reference, do not re-litigate); inter-slice
status (§3.6 event / §3.7 metrics unchanged).

## 8. Non-Goals (hard boundaries for the impl PR)

- No edit to `create_breakage_design_loopback_eco`, §3.2's CAS /
  `session.rollback()`, any merged contract, or
  `test_breakage_update_status_auto_trigger.py`.
- No new route; no schema/alembic/tenant-baseline.
- No batch helpdesk entrypoint and no batch
  "record-and-continue" coupling (§3.F — separate future
  opt-in).
- `execute_helpdesk_sync` / `record_helpdesk_sync_result` are
  NOT trigger points and are not modified.
- No route/API response extension (§3.J).
- No event emission (§3.6) / metrics (§3.7).
- No default-ON. `auto_loopback` defaults False; flipping the
  default is a separate explicit opt-in.
- No new app-wide `PLMException`→HTTP handler (inherited §3.3
  behavior; separate app-wide opt-in).
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude or the project owner
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/odoo18-breakage-helpdesk-sync-auto-trigger-r1-20260519`.

Follow-ups (each its own opt-in): §3.6 event emission; §3.7
metrics; default-ON flip (if ever); a batch helpdesk
auto-trigger (only if such an entrypoint is ever introduced).

## 10. Reviewer Focus

- **§3.C is the central question — ratify ONE.** Confirm **β**
  (status-first reorder; §3.3 helper stays pure; helpdesk
  mutations applied after the trigger converges, never in a
  rolled-back window) vs. **γ** (replay-callback helper). **α**
  is recorded rejected (silent helpdesk-state loss on the
  CAS-loser race). Push back if β's reorder of
  `apply_helpdesk_ticket_update` is unacceptable, or if γ's
  single-handler purity is judged worth the extra intricacy.
- **§3.D double gate — both vectors.** Confirm gate #1
  (`derived_sync_status == "completed"`) is NOT redundant:
  Vector A (`canceled`/`cancelled` → incident `closed`/sync
  `failed`) and Vector B (explicit `incident_status` override +
  failed-sync ticket). Both must be MANDATORY-tested to not
  fire.
- **§3.E helper extraction.** Confirm it is a behavior-
  preserving refactor and that §3.3's 10 tests staying green
  **unchanged** is the accepted proof; confirm it touches no
  merged contract / §3.2 CAS.
- **§3.F adjudication.** Confirm the single-incident
  atomic-coupling ratification and that batch
  record-and-continue is correctly scoped out as vacuous for
  the grounded code (no batch entrypoint) rather than
  unaddressed.
- **§3.I route clause order** + default-OFF byte-identity;
  confirm the explicit `except Exception: rollback; raise`
  choice (vs. preserving pre-§3.4 implicit propagation).
- Did anything pre-decide a §3.6/§3.7 slice, add a batch
  entrypoint, extend the response, or touch merged §3.2/§3.3
  contracts/tests? It must not.
