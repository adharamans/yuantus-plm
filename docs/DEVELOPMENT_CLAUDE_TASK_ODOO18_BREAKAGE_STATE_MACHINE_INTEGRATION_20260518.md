# Claude Taskbook: Odoo18 Breakage State-Machine Integration — Remainder Catalog

Date: 2026-05-18

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Catalogs the remaining executable slices in the
breakage→ECO design-loopback integration after the owner-shipped
preparation (#595) and ECO-creation (#596) wirings. Merging this
taskbook does NOT authorize any of the listed slices —
**each enumerated slice in §3 is its own separate later opt-in**.

## 1. Purpose

R2 closeout §4 **Tier-B follow-up #3** (owner-ranked priority
2026-05-18). The owner shipped two service-level integration
slices ahead of Claude session work:

- **#595 `063e3de`** — `BreakageIncidentService.resolve_breakage_design_loopback_descriptor(...)`
  + `prepare_breakage_design_loopback_intake(...)`: read-only
  preparation that maps a persisted `BreakageIncident` row →
  descriptor → ECR intake → `EcoDraftInputs`. No side effects.
- **#596 `6e4ce54`** — `BreakageIncidentService.create_breakage_design_loopback_eco(...)`:
  the first side-effecting step. Calls `ECOService.create_eco(...)`
  via the prepared intake, with best-effort query-before-create
  dedupe via the closeout-reference envelope in
  `ECO.description`.

Both DEV MDs (`docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_RUNTIME_WIRING_R1_20260518.md`
and `..._ECO_CREATION_R1_20260518.md`) explicitly list follow-ups
they did NOT do. This taskbook **catalogs** those follow-ups —
clarifying boundaries, prerequisites, dependencies between
slices, and the order recommended for landing — so the project
owner can opt into one slice at a time without re-deriving the
shape each round.

**The goal of this taskbook is enumeration + scoping**, not
preferred-shape pre-ratification. Most slices below are flagged
**OPEN for reviewer ratification** per the established pattern
(3b §3 Policy A) — author proposes a shape and lists trade-offs;
reviewer chooses.

## 2. Current Reality (grounded — direct file reads)

Verified by direct reads, no Explore-agent summaries (per
[[feedback-verify-grounding-facts]]).

### Owner-shipped service methods

`src/yuantus/meta_engine/services/parallel_tasks_service.py`,
inside `BreakageIncidentService`:

- **`_breakage_design_loopback_row(self, incident)`** at
  line 4202 — pure helper, builds a `BreakageIncidentRow` from
  the persisted incident's 7 descriptor-relevant fields.
- **`resolve_breakage_design_loopback_descriptor(self, incident_id)`**
  at line 4216 — loads `BreakageIncident`, raises
  `ValueError("Breakage incident not found: <id>")` on missing,
  returns a `BreakageEcoClosureDescriptor` via the merged 3c
  resolver.
- **`prepare_breakage_design_loopback_intake(self, incident_id)`**
  at line 4227 — calls the descriptor resolver, evaluates
  eligibility via the merged
  `is_breakage_eligible_for_design_loopback`, and returns a
  frozen `BreakageDesignLoopbackPreparation` dataclass. Eligible
  incidents carry `intake` (a `ChangeRequestIntake`) and
  `eco_draft_inputs` (an `EcoDraftInputs`); ineligible carry a
  human-readable `ineligible_reason`. **Read-only — no DB write.**
- **`_find_breakage_design_loopback_eco_by_reference(self, reference)`**
  at line 4254 — queries `ECO` rows whose `description` contains
  the substring `reference=<hash>` AND `breakage-eco-closeout`,
  ordered by `created_at ASC, id ASC`. Returns the earliest
  matching ECO or `None`. **No locking, no `select_for_update`.**
- **`create_breakage_design_loopback_eco(self, incident_id, *, user_id, allow_duplicate=False)`**
  at line 4275 — calls `prepare_breakage_design_loopback_intake`,
  raises `ValueError(preparation.ineligible_reason or "...")` if
  ineligible, otherwise:
  - If `allow_duplicate=False` (default): query-before-create via
    `_find_breakage_design_loopback_eco_by_reference`; if an
    existing ECO is found, return
    `BreakageDesignLoopbackEcoCreation(created=False, eco=existing,
    ...)`.
  - Else (or no existing): build kwargs from
    `preparation.eco_draft_inputs.as_kwargs()`, override
    `user_id`, call `ECOService(self.session).create_eco(**kwargs)`,
    return `BreakageDesignLoopbackEcoCreation(created=True, ...)`.
  - **Caller owns the transaction boundary.** The method does
    NOT commit; it flushes only through `ECOService.create_eco`'s
    existing behavior.

### Breakage state-machine surface today

- **`BreakageIncidentService.update_status(self, incident_id, *, status)`**
  at line 4193 — simple status setter: load incident, normalize
  status string (`strip().lower()`), set `incident.status`,
  set `incident.updated_at`, `session.flush()`. **No loopback
  hook.** Returns the mutated incident.
- **`POST /breakages/{incident_id}/status` route**
  (`parallel_tasks_breakage_router.py:823`) — calls
  `update_status`, commits or rolls back on error, returns
  `{id, status, updated_at, operator_id}`. **No loopback hook.**
- **Helpdesk-sync routes** (`parallel_tasks_breakage_router.py:861,
  905, 925, 968, 1011`) — 5 endpoints (`/helpdesk-sync` queue +
  status + execute + result + ticket-update) that mutate
  incident status via the
  `_HELPDESK_PROVIDER_TO_INCIDENT_STATUS` mapping
  (`parallel_tasks_service.py:2914–2923`, the
  `{open,in_progress,resolved,closed}` canonical 4-value
  domain). **No loopback hook.**

### ECO model surface (relevant to slices below)

- `ECO.description` is the carrier for both reserved envelopes
  (`breakage-eco-closeout` + `ecr-intake`). The closeout-reference
  hash lives inside the description as `reference=<sha256>`.
- **No `BreakageIncident.eco_id` FK column** today; no back-
  reference from breakage to ECO. (A schema slice would add one
  if desired.)
- **No unique constraint** on `ECO.description` or any portion
  of it; the current best-effort dedupe is purely application-
  level substring matching.

### What's NOT yet wired (per #595 and #596 DEV MD §7 / §8)

- No API route exposing `create_breakage_design_loopback_eco`.
- No durable idempotency (current dedupe is race-unsafe).
- No automatic call from `update_status(...)`.
- No automatic call from any helpdesk-sync handler.
- No dedicated permission/RBAC capability for "design-loopback
  spawn" (today it inherits ECO create permission).
- No domain event for "breakage → ECO loopback created".
- No UI affordance.
- No observability instrumentation (metrics/logs/traces).

These are the candidate slices §3 catalogs.

## 3. Remaining Executable Slices

**Reading guide:** each slice below has its own §3.N entry with
(a) brief scope, (b) prerequisites (which other slices must land
first, if any), (c) recommended R1 shape, (d) hard non-goals.
Each is its own **separate later opt-in** — merging this
taskbook does NOT authorize any slice. Reviewers should ratify
the recommended shapes (or push back) so subsequent implementation
PRs have a clear target.

The slices group into 4 independent tracks. Suggested landing
order is listed at the end of §3.

---

### §3.1 — Route exposure (independent, low risk)

**Scope.** Add one HTTP endpoint that exposes
`BreakageIncidentService.create_breakage_design_loopback_eco` to
authenticated callers. The service method already exists; this
slice is purely a router seam + permission + response-shape
decision.

**Prerequisites.** None. This slice can land before §3.2 (durable
idempotency) — the route just inherits the service's best-effort
dedupe semantics.

**Recommended R1 shape (OPEN for reviewer ratification):**

- **Endpoint:** `POST /breakages/{incident_id}/design-loopback/eco`.
  Mirrors the existing `/breakages/{incident_id}/status` shape.
- **Request body:** `{"allow_duplicate": bool = False}`. Minimal —
  no descriptor override (the service path always uses the
  persisted incident).
- **Response on 200 + new ECO:** `{"created": true, "eco_id":
  str, "reference": str, "incident_id": str, "operator_id": int}`.
- **Response on 200 + dedupe hit (`created=False`):** same shape
  with `"created": false`. **Reviewer call:** alternative is HTTP
  409 + `{"existing_eco_id": str}`. Author recommends 200 +
  `created: false` because: (a) the dedupe is best-effort by
  design, (b) the caller's intent ("get me an ECO for this
  breakage") is satisfied either way, (c) consistent with idempotent-
  POST semantics.
- **Error mapping:** mirror the existing breakage router pattern
  (`_raise_api_error`):
  - `ValueError("Breakage incident not found: <id>")` → 404
    `breakage_not_found`.
  - `ValueError("breakage status ... is not eligible ...")` →
    409 `breakage_not_eligible_for_loopback`.
  - `ECOService.create_eco` permission failure → propagate (the
    existing FastAPI exception handler will map it).
- **Permission:** require the same capability the operator needs
  to create an ECO via the existing ECO routes. Author recommends
  no new dedicated capability in R1; if a dedicated "design
  loopback spawn" capability is wanted, that's §3.5 below
  (separate later opt-in).
- **Audit:** log at INFO with `incident_id`, `operator_id`,
  `eco_id`, `created`, `reference`. No event-bus emission in R1
  (that's §3.6).

**Hard non-goals for R1:**

- No UI (separate later opt-in).
- No new permission capability (separate slice §3.5).
- No event-bus emission (separate slice §3.6).
- No durable idempotency (separate slice §3.2 — this route
  inherits service's best-effort dedupe).
- No edit to `BreakageIncidentService.create_breakage_design_loopback_eco`'s
  signature or body.

---

### §3.2 — Durable idempotency (independent, medium risk; schema)

**Scope.** Replace the current best-effort substring-scan dedupe
with a race-safe persistence guarantee, so concurrent calls to
`create_breakage_design_loopback_eco` for the same incident
return the same ECO (or fail explicitly) rather than producing
duplicates.

**Prerequisites.** None directly. Lands cleanly without §3.1, but
§3.3/§3.4 (auto-triggers) should **not** land before this slice
because race-unsafe auto-fire is dangerous: a status transition
fired twice (e.g., user double-clicks + helpdesk sync arrives
simultaneously) would create two ECOs.

**Recommended R1 shape (OPEN for reviewer ratification — three
alternatives, author recommends 2a):**

- **2a (recommended): `BreakageIncident.eco_id` FK + UNIQUE.**
  Add a nullable `eco_id: Optional[str]` column to
  `meta_breakage_incidents` with FK to `meta_ecos.id` and a
  UNIQUE INDEX. `create_breakage_design_loopback_eco` writes
  `incident.eco_id = eco.id` after `ECOService.create_eco`
  returns (inside the same session.flush). Race-safe: the UNIQUE
  index forces serialization. Pro: cleanest semantic — one
  incident → at most one loopback ECO; cheap to query. Con:
  schema change (alembic + tenant baseline); `allow_duplicate=True`
  has to bypass the constraint (separate column? bypass flag in
  service?).
- **2b: `breakage_eco_creations` audit/lock table.** New table
  with `(incident_id, reference) UNIQUE`. Insert-or-fail before
  calling `create_eco`; on conflict, look up existing row. Pro:
  doesn't touch `BreakageIncident`. Con: extra table; harder
  to query "which ECO is THIS breakage's loopback?".
- **2c: Application-level `select_for_update`.** No schema
  change; serialize via `SELECT ... FOR UPDATE` on the
  incident row before the find-or-create. Pro: no migration.
  Con: row lock lives only for the transaction (no persistent
  uniqueness guarantee — a *committed* duplicate could still
  appear from another transaction that didn't take the lock,
  e.g., a script with stale code); portability concerns
  between SQLite test env and Postgres prod.

  Author recommendation rationale: **2a** is the cleanest
  long-term answer because it surfaces the breakage↔ECO
  relationship as a first-class FK that the UI/reports/audits
  can navigate. Adding the back-reference column is also useful
  beyond idempotency. **2b** is a reasonable middle-ground if
  the owner prefers not to touch `BreakageIncident`. **2c** is
  not recommended — it adds runtime complexity without persistent
  audit value.

**Hard non-goals for R1 (whichever alternative is chosen):**

- No new ECO column / no change to ECO schema.
- No edit to merged contract modules.
- No change to existing best-effort substring scan in
  `_find_breakage_design_loopback_eco_by_reference` — either
  keep it as belt-and-suspenders or remove it after the durable
  check is in place; reviewer's call.

---

### §3.3 — `update_status` auto-trigger (depends on §3.2; default OFF — author pre-ratified)

**Scope.** When `BreakageIncidentService.update_status(...)`
transitions an incident to `resolved` or `closed`,
**optionally** auto-fire `create_breakage_design_loopback_eco`.

**Prerequisites.** §3.2 (durable idempotency) **must** land
first. Race-unsafe auto-fire is dangerous: a UI button + a
helpdesk webhook arriving close in time would currently produce
2 ECOs.

**Recommended R1 shape — default OFF (author pre-ratified per
#595/#596 DEV MD framing):**

- Signature: `update_status(self, incident_id, *, status, auto_loopback: bool = False, loopback_user_id: Optional[int] = None)`.
- Behavior when `auto_loopback=True` AND the transition is to
  `resolved`/`closed` AND the incident is eligible per
  `is_breakage_eligible_for_design_loopback`:
  - Call `create_breakage_design_loopback_eco(incident_id,
    user_id=loopback_user_id, allow_duplicate=False)` AFTER the
    status flush.
  - On `ValueError` (ineligible / not found): re-raise so the
    transaction rolls back at the caller boundary.
  - On `ECOService.create_eco` permission failure: re-raise.
- The route `POST /breakages/{incident_id}/status` gains
  `auto_loopback` in its request body; the route maps
  `user.id → loopback_user_id`.
- **Default OFF preserves byte-identical pre-R1 behavior.**

**Rationale for pre-ratifying OFF:** (a) #595 + #596 DEV MDs
explicitly say "intentionally keeps the behavior manual and
no-op by default"; (b) ON-by-default with the durable-idempotency
guard from §3.2 is *safe* but is a product behavior change. If
the reviewer prefers ON-by-default after §3.2 lands, flag it
during this taskbook's review; otherwise the §3.3 impl PR
materialises `auto_loopback: bool = False`.

**Hard non-goals for R1:**

- No edit to `create_breakage_design_loopback_eco`.
- No retry-on-failure logic (a transient ECO-create failure
  bubbles up; caller retries via the status endpoint).
- No background-task offload (sync only).

---

### §3.4 — Helpdesk-sync auto-trigger (depends on §3.2 + §3.3; inherits §3.3 default)

**Scope.** When a helpdesk-sync handler transitions an incident
status to `resolved` or `closed` via the
`_HELPDESK_PROVIDER_TO_INCIDENT_STATUS` mapping, optionally
auto-fire the design loopback (analogous to §3.3 but on the
helpdesk-driven path).

**Prerequisites.** §3.2 (idempotency) AND §3.3 (so the trigger
gets routed through the same `update_status(auto_loopback=...)`
path rather than building a second auto-fire site). Strong
preference for "one auto-trigger site" — keeps semantics
consistent across UI-driven and helpdesk-driven status changes.

**Recommended R1 shape — same default as §3.3 (OFF):**

- Each helpdesk-sync handler that flips status to
  `resolved`/`closed` routes through `update_status(...,
  auto_loopback=True, loopback_user_id=...)` only when the
  helpdesk-sync payload explicitly opts in (e.g., a flag in
  the sync request body).
- `loopback_user_id` for helpdesk-driven calls is either a
  service-account id (e.g., `0` for "system") or — author
  recommends — the original ticket owner / agent if the helpdesk
  payload carries one.
- **Inherits the §3.3 default**; if §3.3 ratifies ON, §3.4
  inherits ON. No separate ratification needed.

**Hard non-goals for R1:**

- No new helpdesk-sync handler (use existing routes).
- No edit to `_HELPDESK_PROVIDER_TO_INCIDENT_STATUS`.
- No retry/queueing of failed loopback creations.

---

### §3.5 — Dedicated RBAC capability (independent, low risk)

**Scope.** Decide whether spawning a design loopback should
require a dedicated permission separate from "ECO create". Add
the capability (or not) and wire it through §3.1's route +
§3.3/§3.4's auto-triggers.

**Prerequisites.** None directly; §3.1 should land first if a
dedicated capability is going to be enforced (so the route is
the natural enforcement seam).

**Recommended R1 shape (OPEN for reviewer ratification):**

- **Recommendation: NO new capability.** Today,
  `BreakageIncidentService.create_breakage_design_loopback_eco`
  delegates create permission to `ECOService.create_eco`. R1
  inherits that. A user who can create an ECO via the existing
  ECO routes can also spawn a design loopback. Minimal surface,
  no new schema/migration, no new policy table.
- **Alternative:** add a `breakage:design_loopback:spawn`
  capability that gates the route in §3.1 and the auto-trigger
  in §3.3/§3.4. Pro: separates "can edit ECOs" from "can fire
  business workflows from breakages". Con: another permission
  to manage; another seed/baseline change.

The author recommends rejecting this slice unless an operator-
permission audit specifically requires the separation.

**Hard non-goals:**

- No edit to existing ECO create permission.
- No edit to RBAC infra.

---

### Suggested landing order

The dependency graph (`A → B` means "A should land before B"):

1. **§3.1 (route)** — independent, immediately useful, lowest
   risk.
2. **§3.2 (durable idempotency)** — independent of §3.1 but
   prerequisite for §3.3/§3.4.
3. **§3.3 (`update_status` auto-trigger)** — depends on §3.2.
4. **§3.4 (helpdesk-sync auto-trigger)** — depends on §3.2 + §3.3.
5. **§3.5 (dedicated RBAC)** — depends on §3.1 if enforced;
   recommended rejected.

Per the owner's serialization rule (2026-05-18), only **ONE**
slice should be in flight at a time.

### §3.6 — Out-of-this-taskbook findings (not slices)

Two areas surfaced during scoping that **cannot** be made into
this taskbook's slices without out-of-scope infrastructure
decisions. They are recorded here as findings rather than
slices, so the catalog stays honest about what it covers:

- **No service-level event bus exists.** The only in-tree event
  surface is `WorkflowCustomActionService`'s `emit_event` action
  type (`parallel_tasks_service.py:2560`), which is a workflow-
  rule **action**, not a general-purpose bus that services emit
  to. There is no `event_bus` / `publish_event` / `EventBus`
  client wired into the meta_engine services today. A "loopback
  ECO created" domain event would therefore require **first**
  adding a project-wide event bus — a much larger epic than a
  breakage-integration slice. Out of scope for this taskbook;
  flagged for owner direction.
- **No structured observability surface exists.**
  `parallel_tasks_service.py` has no `logging.getLogger`
  usage; `src/yuantus/meta_engine/` has no `prometheus_client`,
  no `opentelemetry`, no metrics client. A slice that added
  observability around `create_breakage_design_loopback_eco`
  would have to **bring its own infrastructure**, which is a
  cross-cutting concern broader than this taskbook. Out of
  scope; flagged for owner direction.

Both are reachable — the owner may want to start a separate
epic to add an event bus / observability stack, after which a
follow-up taskbook can scope event-emission and instrumentation
slices into the breakage loopback. They are not blocked by this
taskbook; they block on a project-wide infra decision.

## 4. R1 Target Output (per slice)

Each implementation PR adds **one** of the §3 slices. Common
shape: 1 modified service method (or 1 route handler, or 1
schema migration), focused tests, a DEV MD per slice + index
line.

This taskbook intentionally does NOT include skeleton code per
slice — too much surface to pre-author. The implementation PRs
materialize the recommended shapes (or the ratified alternative)
after this taskbook merges + the chosen slice gets its own
explicit opt-in.

## 5. Tests Required (per slice — common shape)

Each implementation PR includes:

- **MANDATORY exactly-named tests pinning slice semantics.**
  Per the established session pattern (#587 / #588 / #592 /
  #594 / #597 / #599), each slice contributes 2–4 MANDATORY
  test functions whose names encode the ratified policy.
- **Drift guards** appropriate to the slice (e.g., for §3.2 2a:
  pin the `BreakageIncident.eco_id` column's FK target +
  UNIQUE; for §3.3: pin that `update_status` with
  `auto_loopback=False` is byte-identical to the pre-R1 method).
- **AST purity / no-call guards** where applicable (e.g.,
  §3.1's route handler must NOT call
  `ECOService.create_eco` directly; only through the service
  method).
- The slice's existing regression net (closeout contract, ECR
  intake, runtime wiring tests, etc.) stays green verbatim.

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) must stay green across
every slice.

## 6. Verification Commands (per slice)

Each implementation PR uses a focused command set scoped to
the touched files:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/<the_slice's_new_test_file>.py
```

Plus the standard doc-index trio + R2 portfolio + `py_compile`
+ `git diff --check`.

Schema slices (§3.2 alt 2a / 2b) additionally need alembic
upgrade head + tenant-baseline validation in the impl PR.

## 7. DEV/verification MD requirements (per slice)

Each implementation PR adds a slice-specific DEV MD:
`DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_<SLICE_NAME>_R1_20260518.md`
(or appropriate date). Must document: (a) what the slice changed
and what it left alone; (b) ratified shape vs. options considered
+ rationale; (c) hard boundaries honored; (d) regression / drift
guards added; (e) inter-slice dependency status (which §3
prereqs are satisfied, which remain open).

## 8. Non-Goals (cross-slice hard boundaries)

The following are NOT in scope for ANY of the §3 slices unless a
separate later opt-in explicitly authorizes:

- No edit to the merged pure contracts
  (`breakage_db_resolver_contract`,
  `breakage_eco_closeout_contract`,
  `ecr_intake_contract`).
- No edit to `ECOService.create_eco` (the
  `BreakageIncidentService.create_breakage_design_loopback_eco`
  method continues to delegate to it unchanged).
- No edit to `_HELPDESK_PROVIDER_TO_INCIDENT_STATUS` value
  domain (the 4-value canonical
  `{open,in_progress,resolved,closed}` set is pinned by
  taskbook #592 §2 and stays binding).
- No tenant-baseline reorganization beyond the additive column
  needed by §3.2 2a (if ratified).
- No edit to the existing R2 portfolio drift guard.
- No new feature flags / runtime toggles unrelated to a
  ratified slice.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. **Merging this taskbook does NOT authorize any §3
slice's implementation.** Each slice needs its own separate
explicit opt-in. The implementation PR for slice §3.N goes on
branch `feat/odoo18-breakage-design-loopback-<slice-suffix>-r1-<date>`.

Slices not listed above are out of scope for this catalog and
need a fresh taskbook of their own.

## 10. Reviewer Focus

This taskbook covers the remainder as 5 enumerable slices
(§3.1–§3.5) plus 2 honest "out-of-this-taskbook" findings
(§3.6) that block on project-wide infra decisions (event bus,
observability stack). Reviewer focus:

- **Slice scoping**: does §3.1–§3.5 correctly capture the
  enumerable remainder? Notable adjacent items the author left
  out intentionally — push back if you want any of these
  in-scope: backfill ECOs for breakages closed before #596;
  UI/frontend affordance for "spawn loopback" (separate frontend
  session); event-bus addition + observability stack (the
  §3.6 findings — these are higher-level infra epics, not
  slices of this taskbook).
- **Dependency arrows**: confirm §3.3/§3.4 require §3.2 (race
  safety). Push back if you want them parallelizable somehow.
- **§3.1 response shape**: 200 + `created:false` vs. 409.
  Reviewer call.
- **§3.2 alternative choice**: 2a (schema column) vs. 2b
  (audit table) vs. 2c (advisory lock). Author recommends 2a;
  reviewer ratifies.
- **§3.3 default**: author **pre-ratified OFF** per #595/#596
  framing. Push back if you want default ON after §3.2 lands.
  §3.4 inherits the §3.3 default — no separate ratification
  needed.
- **§3.5 (dedicated RBAC)**: keep recommended-reject or
  ratify the capability addition.
- **§3.6 findings ratification**: confirm both findings are
  correct — there really is no service-level event bus and no
  metrics surface in `src/yuantus/meta_engine/` today — and
  agree that both are out-of-scope for this taskbook (not
  blocked, just at a higher infra level).
- **Out-of-scope check**: did anything in this catalog claim
  authorization for a slice that hasn't been ratified? It must
  not — the goal is enumeration, not pre-decision.
