# Claude Taskbook: Odoo18 Breakage DB-Resolver Pure-Contract R1

Date: 2026-05-18

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

R2 closeout §4 **Tier-A** follow-up #3c — companion to the merged
pack-and-go DB-resolver R1 (#588 `fdc1fd9`) and maintenance
DB-resolver R1 (#591 `3e22020`). The merged breakage→ECO closeout
contract (`breakage_eco_closeout_contract.py`, PR #579 `2775866`)
consumes `BreakageEcoClosureDescriptor`s the **caller** supplies,
but there is no typed, pure mapping from the persisted
`BreakageIncident` rows to those descriptors. This R1 supplies
that pure mapping — **the contract still does not read the DB**;
the caller fetches rows, the pure function maps them. Closes the
Tier-A pure-contract tier (after this, the only outstanding work
under the §4 plan is Tier-B runtime wiring, each its own opt-in).

## 2. Current Reality (grounded — read before implementing)

All citations below are verified by direct file reads (per
[[feedback-verify-grounding-facts]] — do not lean on Explore-agent
summaries for policy-critical facts).

- Merged `src/yuantus/meta_engine/services/breakage_eco_closeout_contract.py`
  (PR #579, `2775866`):
  - `BreakageEcoClosureDescriptor` (frozen Pydantic v2,
    `extra="forbid"`) — **7 fields**:
    - `description: str` (non-empty)
    - `status: str` (lower/trim normalized by validator)
    - `severity: str = "medium"` (lower/trim normalized by
      validator)
    - `incident_code: Optional[str] = None` (blank→None)
    - `product_item_id: Optional[str] = None` (blank→None)
    - `bom_id: Optional[str] = None` (blank→None)
    - `version_id: Optional[str] = None` (blank→None)
  - **RATIFIED policies (binding, untouched here):**
    - §3.1: `eligible_statuses = frozenset({"resolved", "closed"})`
      — open/in_progress/unknown are NOT eligible.
    - §3.2: `severity_to_priority = {critical→urgent, high→high,
      medium→normal, low→low}` + **unknown severity → "normal"**
      (`_UNKNOWN_SEVERITY_PRIORITY`) as an explicit conservative
      *downgrade* policy.
  - Public functions (out of scope here, reused unchanged):
    `is_breakage_eligible_for_design_loopback`,
    `severity_priority`, `derive_breakage_change_reference`,
    `map_breakage_to_change_request_intake`.
- Persisted source row
  (`src/yuantus/meta_engine/models/parallel_tasks.py:168–192`):
  `BreakageIncident` — table `meta_breakage_incidents`. Columns:
  - `id: String, primary_key, default=_uuid` (line 171)
  - `incident_code: String(40), nullable=True, unique=True, index=True` (172)
  - `product_item_id: String, nullable=True, index=True` (173)
  - `bom_id: String, nullable=True, index=True` (174)
  - `bom_line_item_id: String, nullable=True, index=True` (175)
  - `production_order_id: String(120), nullable=True, index=True` (176)
  - `version_id: String, nullable=True, index=True` (177)
  - `mbom_id: String, nullable=True, index=True` (178)
  - `routing_id: String(120), nullable=True, index=True` (179)
  - `batch_code: String(120), nullable=True, index=True` (180)
  - `customer_name: String(200), nullable=True, index=True` (181)
  - **`description: Text, nullable=False`** (182)
  - `responsibility: String(120), nullable=True` (183)
  - **`status: String(30), nullable=False, default="open", index=True`** (184)
  - **`severity: String(30), nullable=False, default="medium", index=True`** (185)
  - `created_by_id` / `created_at` / `updated_at` (186–192)
- **Canonical `status` value-domain produced by in-tree code**
  (`src/yuantus/meta_engine/services/parallel_tasks_service.py:2914–2923`)
  — `BreakageIncidentService._HELPDESK_PROVIDER_TO_INCIDENT_STATUS`
  maps every helpdesk-side state to one of
  `{"open", "in_progress", "resolved", "closed"}`. Notable
  collapse: `canceled → closed` (memory-recorded; verified). The
  column is a bare `String(30)` (no enum), so callers can in
  principle put any string, but in-tree writers stay within those
  four canonical values. The descriptor validator lowers+trims;
  the merged §3.1 eligibility predicate selects the **2-of-4**
  canonical `{resolved, closed}` set.
- **No existing producer.** `BreakageEcoClosureDescriptor` is
  input-only in the merged contract; no service method today
  builds one from `BreakageIncident` rows. The closest existing
  surface is `BreakageIncidentService._incident_*` helpers
  (`parallel_tasks_service.py:3039+`) that read individual columns
  for routing/reporting purposes — they do NOT produce a
  descriptor.
- **Why 3c is structurally simpler than 3a/3b.** 3a (pack-and-go)
  reproduces `serialize_link`'s three branches over a (link, version)
  *pair*. 3b (maintenance) picks the first non-terminal request out
  of N for one equipment (multi-row → single descriptor field). 3c
  is a **1:1 row → descriptor map**: one `BreakageIncident` row
  produces one `BreakageEcoClosureDescriptor`, no multi-row
  selection logic, no paired-id check. The work here is to pin the
  row DTO shape, prove field-set equivalence with the descriptor,
  and prove the resolver's output composes cleanly with the merged
  closeout contract's existing functions.

## 3. Row → Descriptor Boundary (the core of this taskbook)

The contract is **pure**: it does **not** query. The caller
fetches a `BreakageIncident` row and passes a typed row view; the
pure function maps the 7 descriptor-relevant columns to a
`BreakageEcoClosureDescriptor`.

| Source row column | Descriptor field | Notes |
|---|---|---|
| `description` (`Text`, non-null) | `description: str` | descriptor validator strips + non-empty |
| `status` (`String(30)`, non-null, default `"open"`) | `status: str` | descriptor validator lowers + trims |
| `severity` (`String(30)`, non-null, default `"medium"`) | `severity: str` | descriptor validator lowers + trims |
| `incident_code` (`String(40)`, nullable) | `incident_code: Optional[str] = None` | descriptor `_blank_to_none` collapses `""`/whitespace → `None` |
| `product_item_id` (`String`, nullable) | `product_item_id: Optional[str] = None` | same |
| `bom_id` (`String`, nullable) | `bom_id: Optional[str] = None` | same |
| `version_id` (`String`, nullable) | `version_id: Optional[str] = None` | same |

### Policies

**Policy Z — Resolver scope: PRE-RATIFIED Z1 (pure 1:1; eligibility
is the caller's job).** Unlike 3b §3 Policy A, this has no
competing in-tree precedents that would justify opening it for
reviewer ratification — the merged
`breakage_eco_closeout_contract` already has the eligibility
predicate (`is_breakage_eligible_for_design_loopback`) and the
asserter (`map_breakage_to_change_request_intake` raises on
ineligible). The resolver's job is mapping; eligibility is the
composition seam, mirroring 3a/3b's "resolver produces, bridge
decides" pattern. The §5 AST `no-evaluate` guard pins this scope
in code: the resolver module CANNOT call the eligibility
predicate, the asserter, or the reference deriver. If the
reviewer prefers Z2 (Optional-returning) or Z3
(raise-on-ineligible), flag it — but the default is Z1 and the
AST guard is built for Z1. Options considered and rejected:

- **Z2 — Resolver filters by eligibility (returns Optional).**
  Rejected: would force the resolver to call
  `is_breakage_eligible_for_design_loopback`, conflating mapping
  with eligibility (two different policies in one function), and
  would change the batch API to `tuple[Optional[...], ...]` or
  filter.
- **Z3 — Resolver raises on ineligible.** Rejected: a triage /
  reporting caller needs to *read* ineligible breakages (e.g.,
  list open incidents) — raising prevents that read path.

**Policy N — No id mismatch carry-over (documented, NOT a
RATIFIED policy here).** Unlike 3a/3b, this resolver takes a
single row and produces a single descriptor — there is no paired
input where an id-mismatch could occur. The #588 case-(b)
strict-reading carry-over has nothing to bite on here; nothing to
ratify.

**Policy F — Field-set parity with the merged descriptor
(documented).** The row DTO has **exactly the 7 descriptor
fields** — no more, no fewer. The drift guard pins
`set(BreakageIncidentRow.model_fields) ==
set(BreakageEcoClosureDescriptor.model_fields)`. The risk this
guards against is **a future descriptor field added without
mirroring it on the row DTO**: if the descriptor grows an 8th
field, callers using the resolver could not supply that value
through the row DTO and would silently rely on the descriptor's
default (or hit a validator failure if the new field is required
without a default). Strict `==` parity makes that drift fail the
test loudly rather than discover it in production. Pydantic's
`extra="forbid"` on the row DTO already prevents the *opposite*
direction (caller passing unknown columns) — that direction does
not need a separate policy.

**Policy P — Pass-through normalization (documented).** The
resolver does **not** pre-normalize values; it constructs the
descriptor directly from the row DTO values and lets the
descriptor's existing validators do the lower/trim/blank→None
work. Why this is right: re-implementing normalization in two
places creates exactly the kind of drift the closeout contract's
merged validators were written to prevent.

## 4. R1 Target Output (for the later, separately opted-in impl PR)

New pure module
`src/yuantus/meta_engine/services/breakage_db_resolver_contract.py`:

- `BreakageIncidentRow` — frozen Pydantic v2, `extra="forbid"`.
  Subset of `meta_breakage_incidents` columns the mapping needs —
  exactly the 7 descriptor-relevant fields (Policy F):
  - `description: str` (non-empty)
  - `status: str` (non-empty before normalization; descriptor
    validator handles lower/trim)
  - `severity: str` (non-empty; descriptor validator handles
    lower/trim) — keep mandatory in the row DTO since the real
    column is `nullable=False`
  - `incident_code: Optional[str] = None`
  - `product_item_id: Optional[str] = None`
  - `bom_id: Optional[str] = None`
  - `version_id: Optional[str] = None`

  Field names mirror the column names (drift-guarded).
- `resolve_breakage_eco_closure_descriptor(row) ->
  BreakageEcoClosureDescriptor` — **pure**; 1:1 mapping per §3
  Policy Z (Z1: pure 1:1, eligibility is the caller's job). Always
  returns a descriptor, regardless of row status.
- `resolve_breakage_eco_closure_descriptors(rows) ->
  tuple[...]` — batch over a
  `Sequence[BreakageIncidentRow]`; deterministic (input order
  preserved).

No DB read, no `session`, no `eval`, no plugin edit, no
enforcement. Imports **only** the merged
`breakage_eco_closeout_contract.BreakageEcoClosureDescriptor` (the
merged closeout contract type — not its predicate/asserter
functions; resolving and enforcing stay in separate functions).

## 5. Tests Required (in the later impl PR)

New `test_breakage_db_resolver_contract.py`:

- Row DTO: frozen, `extra=forbid`, non-empty `description` /
  `status` / `severity`; the 4 Optional fields default to `None`
  and accept `None`/empty/whitespace/string.
- **`test_resolver_mirrors_breakage_incident_columns_one_to_one`
  (MANDATORY, exactly named)** — every descriptor field is
  populated from the row column of the same name; no
  transformation beyond what the descriptor validator already
  does. Parametrized to cover each of the 7 fields independently
  (so a future drift on any single mapping fails loudly).
- **`test_resolver_pass_through_normalization_via_descriptor_validators`
  (MANDATORY, exactly named)** — pins Policy P: row DTO with
  `status="  RESOLVED  "` → descriptor `status="resolved"` (lower
  + trim done by the descriptor, not the resolver); row DTO with
  `incident_code=""` → descriptor `incident_code=None`
  (blank→None done by the descriptor). The resolver does **not**
  pre-normalize. Asserted via a small adversarial fixture.
- **`test_resolver_output_is_the_merged_breakage_descriptor`
  (MANDATORY, exactly named)** — compose proof:
  `type(resolver_out) is
  breakage_eco_closeout_contract.BreakageEcoClosureDescriptor`,
  and for an eligible row the full path
  `row → resolver → is_breakage_eligible_for_design_loopback →
   map_breakage_to_change_request_intake` produces a valid
  `ChangeRequestIntake` unchanged (no DB).
- Batch: order preserved across mixed status / severity rows;
  per §3 Policy Z (Z1) the batch test also asserts that
  ineligible rows still produce descriptors (the resolver does
  not filter).
- **Drift guards**: `BreakageIncidentRow` fields ⊆
  `BreakageIncident.__table__.columns` (subset of the 17 real
  columns); **strict parity** `set(BreakageIncidentRow.model_fields)
  == set(BreakageEcoClosureDescriptor.model_fields)` (Policy F —
  enforces no quiet drift wider than the descriptor); the produced
  descriptor's field set equals
  `BreakageEcoClosureDescriptor.model_fields` (reuse, not
  reimplement);
  `BreakageIncident.__table__.columns["description"].nullable is False`
  + `…["status"].nullable is False` + `…["severity"].nullable is False`
  (pin the assumption Policy F's "required" typing rests on).
- **Purity guard** (AST): module imports nothing matching
  `yuantus.database` / `sqlalchemy` / `parallel_tasks_service` /
  `_router` / `plugins` / `_service`; imports **only** the
  merged `breakage_eco_closeout_contract` module (and via that,
  transitively, `ecr_intake_contract`); contains no `session`/DB
  call.
- **No-evaluate / no-`assert_*` (AST)**: module does NOT call
  `is_breakage_eligible_for_design_loopback`,
  `map_breakage_to_change_request_intake`, or
  `derive_breakage_change_reference` (whichever scope policy Z is
  ratified, these stay the caller's composition surface); no
  `assert_*` callable is defined.

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) must stay green.

## 6. Verification Commands (for the impl PR)

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_eco_closeout_contract.py \
  src/yuantus/meta_engine/tests/test_ecr_intake_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/breakage_db_resolver_contract.py
git diff --check
```

No alembic / tenant-baseline — the contract adds no schema.

## 7. DEV/verification MD requirements (impl PR)

Add `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DB_RESOLVER_CONTRACT_R1_20260518.md`
+ index registration. Must document: (a) the pure 1:1
row→descriptor boundary (caller fetches; contract never queries);
(b) §3 Policy Z (Z1 — pure 1:1, pinned by the AST `no-evaluate`
guard) and the audit-trail rejection of Z2/Z3; (c) Policy F
strict field-set parity with the merged descriptor and the
"future descriptor field added without row-DTO mirroring" risk
it guards against; (d) Policy P pass-through normalization and
why the resolver does NOT re-implement the descriptor's
validators; (e) the merged `BreakageEcoClosureDescriptor` reused
unchanged (no shadow type, drift-guarded).

## 8. Non-Goals (hard boundaries for the impl PR)

- **No DB read / no `session`** — caller-supplied rows only; the
  actual query is a separate later opt-in.
- **No service/router/plugin wiring** —
  `BreakageIncidentService`, `parallel_tasks_breakage_router`,
  and `breakage_tasks` are not edited.
- **No closeout-contract enforcement** —
  `is_breakage_eligible_for_design_loopback`,
  `map_breakage_to_change_request_intake`,
  `derive_breakage_change_reference`, `severity_priority` are
  reused **unchanged**; the resolver only produces descriptors.
- **No edit to `breakage_eco_closeout_contract`,
  `ecr_intake_contract`, `parallel_tasks/models.py`, or
  `parallel_tasks_service.py`**.
- **No change to the merged §3.1 / §3.2 RATIFIED policies** —
  `eligible_statuses` and `severity_to_priority` are pinned in
  #579's tests and stay binding.
- **No status-domain widening / narrowing** — the resolver does
  not validate `status` against the 4-value canonical set; that
  remains a value-domain question for the merged contract and
  any future schema work (out of scope).
- No schema / migration / tenant-baseline / feature flag /
  runtime wiring.
- No contact with other R2 contracts beyond importing the merged
  `BreakageEcoClosureDescriptor`.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by the project (Claude or owner)
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/odoo18-breakage-db-resolver-contract-r1-20260518`.

Follow-ups, each its own separate opt-in (explicitly NOT in R1):

- An actual DB resolver that **queries** `meta_breakage_incidents`
  and feeds these row DTOs (touches the DB — separate).
- Wiring the resolved descriptors into a breakage state-machine
  caller that triggers ECR creation on transition to
  `resolved`/`closed` (plugin + runtime — separate).
- ECR-creation wiring proper (touches `ECOService.create_eco`,
  permissions, state machine — the
  `breakage_eco_closeout_contract`'s documented follow-up; was
  already noted as a separate opt-in when #579 merged).

## 10. Reviewer Focus

- §3 Policy Z is **PRE-RATIFIED Z1** (pure 1:1 map; eligibility
  is the caller's job; pinned by §5 AST `no-evaluate` guard).
  Unlike 3b §3 Policy A, this is NOT opened for ratification
  because there are no competing in-tree precedents — the merged
  closeout contract already has the eligibility predicate, and
  Z1 matches the 3a/3b "resolver produces, bridge decides"
  pattern. If you prefer Z2 (Optional-filter) or Z3
  (raise-on-ineligible), please flag in this PR review and the
  AST guard + §4 signature will be relaxed in the impl PR
  accordingly.
- Confirm §3 Policy F (strict field-set parity with the merged
  descriptor) — this prevents quiet drift where the row DTO
  silently accepts columns the closeout flow doesn't consume.
- Confirm §3 Policy P (resolver does NOT re-implement
  normalization; relies on the merged descriptor's validators).
- Is the contract pure (no DB/session/service/router import;
  allows only the merged closeout contract) and does it reuse the
  merged `BreakageEcoClosureDescriptor` unchanged (drift-guarded)?
- Is the row DTO field set a proper subset of
  `BreakageIncident.__table__.columns`?
- Did anything add a DB read, edit the closeout contract / ECR
  intake contract / `BreakageIncidentService` /
  `parallel_tasks_breakage_router` / `breakage_tasks`, change the
  §3.1/§3.2 RATIFIED policies, or add enforcement? It must not.
