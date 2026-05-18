# Odoo18 Breakage DB-Resolver Pure-Contract R1 — Development and Verification

Date: 2026-05-18

## 1. Goal

Implement R1 of the breakage DB-resolver taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DB_RESOLVER_CONTRACT_20260518.md`,
merged 2026-05-18 as `3b41702` / PR #592). R2 closeout §4
Tier-A follow-up #3c — closes the Tier-A pure-contract tier
(after this, remaining §4 work is Tier-B runtime wiring, each its
own opt-in).

R1 is **pure, contract-only**: a module + tests + this MD. The
caller fetches rows; the pure function maps them 1:1 to the
merged `BreakageEcoClosureDescriptor` (PR #579 `2775866`,
untouched). No DB / no `session` / no plugin wiring / no
closeout-contract enforcement / no edit to
`breakage_eco_closeout_contract`, `ecr_intake_contract`,
`BreakageIncidentService`, `parallel_tasks_breakage_router`, or
`breakage_tasks`.

## 2. Scope

### Added

- `src/yuantus/meta_engine/services/breakage_db_resolver_contract.py`
- `src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DB_RESOLVER_CONTRACT_R1_20260518.md`

### Modified

- `docs/DELIVERY_DOC_INDEX.md` (one index line)

The shipped `breakage_eco_closeout_contract` (incl.
`BreakageEcoClosureDescriptor`,
`is_breakage_eligible_for_design_loopback`,
`map_breakage_to_change_request_intake`,
`derive_breakage_change_reference`, `severity_priority`,
`eligible_statuses`, `severity_to_priority`), the merged
`ecr_intake_contract`, the `BreakageIncident` model, all
breakage services / routers / tasks, and the prior R2 contracts
are **unchanged** — proven by drift + AST guards.

## 3. Contract

The contract is pure: it does **not** query. The caller fetches
a `meta_breakage_incidents` row and passes a typed row view; one
descriptor is produced per row (1:1).

### Row DTO

`BreakageIncidentRow` — frozen Pydantic v2, `extra="forbid"`.
Field set and field names mirror `BreakageEcoClosureDescriptor`
exactly (Policy F): 7 fields.

- `description: str` (non-empty)
- `status: str` (non-empty before normalization; descriptor
  validator lowers + trims)
- `severity: str` (non-empty before normalization; descriptor
  validator lowers + trims)
- `incident_code: Optional[str] = None` (descriptor's
  `_blank_to_none` collapses empty/whitespace → None)
- `product_item_id: Optional[str] = None` (same)
- `bom_id: Optional[str] = None` (same)
- `version_id: Optional[str] = None` (same)

Drift guard pins `BreakageIncidentRow` fields ⊆
`BreakageIncident.__table__.columns` (subset of the 17 real
columns; the 10 unused columns — `mbom_id`, `routing_id`,
`batch_code`, `customer_name`, `responsibility`, etc. — are
deliberately absent), and `set(BreakageIncidentRow.model_fields)
== set(BreakageEcoClosureDescriptor.model_fields)` (Policy F).

### Resolver

`resolve_breakage_eco_closure_descriptor(row) ->
BreakageEcoClosureDescriptor` — **pure, 1:1**; always returns a
descriptor regardless of row status (Policy Z1). Eligibility is
the caller's job, composed via the merged predicate +
asserter. The merged
`BreakageEcoClosureDescriptor` constructor receives the row
values verbatim; the resolver does **not** re-implement
normalization (Policy P) — the merged descriptor's existing
validators do lower/trim on status/severity and blank→None on
the four Optional fields.

`resolve_breakage_eco_closure_descriptors(rows) ->
tuple[BreakageEcoClosureDescriptor, ...]` — batch; deterministic
(input order preserved); per Policy Z1, the output length
equals the input length (no eligibility filtering).

### Policies (taskbook §3 — three RATIFIED, one documented non-op)

1. **Policy Z — PRE-RATIFIED Z1 (pure 1:1 map).** Eligibility is
   the caller's job. Pinned in code by the test-side AST
   `no-evaluate` guard (the resolver module must not call
   `is_breakage_eligible_for_design_loopback`,
   `map_breakage_to_change_request_intake`,
   `derive_breakage_change_reference`, or `severity_priority`).
   Z2 (Optional-filter) and Z3 (raise-on-ineligible) were
   rejected in the taskbook audit trail (conflate mapping with
   eligibility / prevent valid read paths).
2. **Policy F — strict field-set parity with the merged
   descriptor.** `set(BreakageIncidentRow.model_fields) ==
   set(BreakageEcoClosureDescriptor.model_fields)`. Guards
   against a future descriptor field added without mirroring on
   the row DTO. Pydantic `extra="forbid"` handles the opposite
   direction (unknown columns).
3. **Policy P — pass-through normalization.** Resolver hands row
   values verbatim to the descriptor constructor; the merged
   descriptor's existing validators do all normalization. The
   resolver does NOT pre-normalize.
4. **Policy N — no id-mismatch (documented non-op).** Unlike
   3a/3b, the resolver takes a single row and produces a single
   descriptor — there is no paired input where an id mismatch
   could occur. The #588 case-(b) strict-reading carry-over has
   nothing to bite on here.

### Hard boundary (taskbook §8)

No DB read / no `session` / no plugin edit / no closeout-contract
enforcement / no edit to `breakage_eco_closeout_contract`,
`ecr_intake_contract`, `BreakageIncidentService`,
`parallel_tasks_breakage_router`, `breakage_tasks`,
`parallel_tasks.py` models, or any router. The merged §3.1
(`eligible_statuses`) and §3.2 (`severity_to_priority` +
unknown→`"normal"`) RATIFIED policies are unchanged. No
status-domain widening/narrowing. The only cross-contract
import is the merged `BreakageEcoClosureDescriptor`.

## 4. Test Matrix

`src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py`
— 14 tests (counts a point-in-time snapshot):

- Row DTO: frozen, `extra=forbid`, non-empty validators on
  `description`/`status`/`severity` (mirror of the
  `nullable=False` columns), Optional fields default to `None`
  and accept `None`/empty/whitespace/value.
- **`test_resolver_mirrors_breakage_incident_columns_one_to_one`
  (MANDATORY, exactly named)** — builds a row where every one of
  the 7 fields carries a **distinctive lowercase value**
  (``"row-<field>-distinct"``) and asserts each descriptor field
  equals the corresponding row field; a same-type swap (e.g.
  ``status`` ↔ ``severity``, both str-validated identically) is
  detectable because the markers are field-specific.
- **`test_resolver_pass_through_normalization_via_descriptor_validators`
  (MANDATORY, exactly named)** — adversarial fixture with
  uppercase status/severity + whitespace + blank Optionals → the
  descriptor's existing validators produce the expected
  normalized output (lower/trim on status/severity;
  blank/whitespace → `None` on Optionals).
- **`test_resolver_output_is_the_merged_breakage_descriptor`
  (MANDATORY, exactly named)** — compose proof at the
  resolver↔bridge seam: `type(descriptor) is
  BreakageEcoClosureDescriptor`, field-set parity, and the full
  path `row → resolver → is_breakage_eligible_for_design_loopback
  → map_breakage_to_change_request_intake` produces a valid
  `ChangeRequestIntake` for an eligible row (priority=urgent
  from §3.2 RATIFIED, change_type="product" from the bom⇒product
  invariant of `ChangeRequestIntake` since `bom_id is None`).
- Batch (Policy Z1): order preserved; ineligible rows still
  produce descriptors; the caller-composable predicate correctly
  classifies them as ineligible.
- Drift guards: `BreakageIncidentRow` fields ⊆ 17 real
  `BreakageIncident` columns; strict `==` parity with
  `BreakageEcoClosureDescriptor.model_fields` (Policy F); pin
  `nullable=False` on `description`/`status`/`severity`
  (`parallel_tasks.py:182/184/185`); type identity
  `type(d) is BreakageEcoClosureDescriptor`.
- **Purity guard (AST)**: module imports nothing matching
  `yuantus.database` / `sqlalchemy` / `parallel_tasks_service` /
  `_router` / `plugins` / `_service`; imports **only**
  `breakage_eco_closeout_contract`; contains no `session` name
  or attribute reference.
- **No-evaluate (AST) — pins Policy Z1**: module does NOT call
  `is_breakage_eligible_for_design_loopback`,
  `map_breakage_to_change_request_intake`,
  `derive_breakage_change_reference`, or `severity_priority`.
- **No-`assert_*` (AST)**: no `assert_*` callable defined in
  the module.

## 5. Verification Commands

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

Observed as of 2026-05-18: 20 resolver-contract tests passed;
merged closeout-contract regression passed (unchanged); merged
ECR-intake-contract regression passed (unchanged); doc-index
trio + R2 portfolio drift guard passed; `py_compile` clean;
`git diff --check` clean.

## 6. Non-Goals (reaffirmed from taskbook §8)

- No DB read / no `session` — caller-supplied rows only; the
  actual query is a separate later opt-in.
- No service/router/plugin wiring —
  `BreakageIncidentService`, `parallel_tasks_breakage_router`,
  `breakage_tasks` are not edited.
- No closeout-contract enforcement — the merged eligibility
  predicate / asserter / reference deriver / severity table are
  reused **unchanged**; the resolver only produces descriptors.
- No edit to `breakage_eco_closeout_contract`,
  `ecr_intake_contract`, the `BreakageIncident` model, or
  `parallel_tasks_service.py`.
- No change to the merged §3.1 / §3.2 RATIFIED policies
  (`eligible_statuses` / `severity_to_priority` +
  unknown→`"normal"`).
- No status-domain widening/narrowing.
- No schema / migration / tenant-baseline / feature flag /
  runtime wiring.
- No contact with other R2 contracts beyond importing the merged
  `BreakageEcoClosureDescriptor`.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Follow-ups (each its own separate opt-in)

- An actual DB resolver that **queries** `meta_breakage_incidents`
  and feeds these row DTOs (touches the DB — separate).
- Wiring the resolved descriptors into a breakage state-machine
  caller that triggers ECR creation on transition to
  `resolved`/`closed` (plugin + runtime — separate).
- ECR-creation wiring proper (touches `ECOService.create_eco`,
  permissions, state machine — the
  `breakage_eco_closeout_contract`'s documented follow-up).

## 8. Tier-A pure-contract tier — CLOSED

With this PR merging, the R2 closeout §4 Tier-A pure-contract
tier is complete: 3a pack-and-go DB-resolver (#588 `fdc1fd9`),
3b maintenance DB-resolver (#591 `3e22020`, owner-authored), 3c
breakage DB-resolver (this PR). All three follow the same row
→ merged-descriptor pattern with pure 1:1 (3c) or multi-row
selection (3a/3b) policy variants ratified per resolver.
Remaining R2-plan work is Tier-B runtime wiring (pack-and-go
plugin wiring already scoped doc-only by #590 `c5ec820` —
implementation still requires its own opt-in), each in its own
separate opt-in / session.
