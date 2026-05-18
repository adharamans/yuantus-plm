# Odoo18 Automation Engine Substitution R1 — Development and Verification

Date: 2026-05-18

## 1. Goal

Implement R1 of the automation engine substitution taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_AUTOMATION_ENGINE_SUBSTITUTION_20260518.md`,
merged 2026-05-18 as `397422e` / PR #598). R2 closeout §4 Tier-B
follow-up #2. Substitute
`WorkflowCustomActionService._rule_matches_runtime_scope` to
**delegate** to the merged automation predicate contract
(`automation_rule_predicate_contract`, PR #577 `36ad043`) without
changing observable behavior.

R1 is **behavior-preserving runtime substitution**: a single
service-method body edit + test additions to preserve the
regression net. No DSL extension / no fail-open change / no
action-type widening / no router-schema-migration / no
`eco_service.py` edit / no deletion of `_rule_match_predicates`.

## 2. Scope

### Modified

- `src/yuantus/meta_engine/services/parallel_tasks_service.py` —
  body of `_rule_matches_runtime_scope` (lines 2241–2274 pre-
  substitution) replaced with a thin delegation to the contract.
  Method signature unchanged.
- `src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py`
  — scoped exclusively to the §5 additions per the taskbook's
  ratified boundary:
  - Renamed `test_service_parity` → `test_service_delegates_to_contract`.
  - Added `_AUTOMATION_PARITY_SNAPSHOT` constant (21 spec-derived rows)
    and **removed the redundant `_PARITY_CASES` list** — both
    parametrized tests now consume the same single source of
    truth (the renamed delegation test ignores the `expected`
    column).
  - Added MANDATORY `test_contract_matches_spec_derived_parity_snapshot`.
  - Added MANDATORY `test_runtime_scope_delegates_to_contract`.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `docs/DEV_AND_VERIFICATION_ODOO18_AUTOMATION_ENGINE_SUBSTITUTION_R1_20260518.md`

The contract runtime module
(`automation_rule_predicate_contract.py`) and 45 of the 46 prior
contract tests are **verbatim**. The
`_normalize_match_predicates`, `_rule_match_predicates`,
`_normalize_runtime_context` service helpers are **unchanged** —
they have other callers (`_normalize_action_params` at line 2120
calls `_normalize_match_predicates` for rule CRUD validation;
`_normalize_runtime_context` is called by `evaluate_transition`
before the per-rule matcher loop). `_rule_match_predicates`
becomes **dead code** post-substitution (its only caller was
`_rule_matches_runtime_scope`); deletion is a separate later
opt-in per the ratified boundary. `eco_service.py:243` is
unchanged.

## 3. Contract

### What the substitution replaces

The body of `WorkflowCustomActionService._rule_matches_runtime_scope`
now delegates to:

```python
predicate = normalize_workflow_rule_predicate(
    rule.workflow_map_id,
    rule.action_params.get("match_predicates"),  # via dict guard
)
facts = WorkflowRuleFacts.from_context(context)
return evaluate_rule_predicate(predicate, facts)
```

The merged contract handles all matcher logic: fail-open on
illegal stored `match_predicates` (preserving `workflow_map_id`),
idempotent normalization, the 7-step matcher (workflow_map_id eq
→ stage_id eq → eco_priority eq → product_id eq → eco_type eq →
actor_roles set-intersection), wildcard semantics on absent
values.

The local-import pattern (importing the three contract surfaces
inside the method body) matches the existing pattern in the file
— other service methods do the same to keep module-level imports
minimal and avoid potential circular dependencies. The import
resolves once per Python process after first call; no hot-path
concern.

### Regression net — spec-derived frozen snapshot

The pre-substitution 21-case parity matrix (`test_service_parity`)
worked because the service and contract were independent
implementations: the assertion `service_decision ==
contract_decision` was a real parity guarantee. After substitution
both sides compute via the same code path, and the assertion
holds **tautologically by construction**.

To preserve the regression net, the taskbook (PR #598 `397422e`,
§5) ratified a **spec-derived** approach: a 21-row
`_AUTOMATION_PARITY_SNAPSHOT` constant whose `expected` values
are **hand-derived from the documented matcher semantics**, not
runtime-captured. Each row's `expected` is independently
verifiable by walking the §3 matcher steps. The new MANDATORY
`test_contract_matches_spec_derived_parity_snapshot` parametrizes
over this snapshot and asserts the contract's outputs match the
spec-derived expectations. A semantic drift on any one of the 21
cases fails loudly here, independently of the service code path.

The original `test_service_parity` is **renamed**
→ `test_service_delegates_to_contract` so its diminished
post-substitution role is honest: it verifies the delegation is
wired end-to-end (rule + context flow through the contract's API
and back), not an independent-implementation parity check.

**Single source of truth**: the redundant `_PARITY_CASES` list
that previously fed `test_service_parity` is removed.
`_AUTOMATION_PARITY_SNAPSHOT` is the only parametrize source for
both tests; the delegation test simply ignores the `expected`
column (binds it to `_expected`). A future case addition lands
in exactly one place — no separate alignment-check test needed.

### Hard boundary (ratified — see taskbook §3 + §8)

- No operator/key extension (Option C is a separate later opt-in).
- No fail-open → fail-closed hardening (separate later opt-in).
- No `_ALLOWED_TYPES` action-set widening (separate later opt-in).
- No deletion of `_rule_match_predicates` (mechanical cleanup,
  separate later opt-in).
- No edit to the contract **runtime** module
  (`automation_rule_predicate_contract.py`); test file edits
  scoped exclusively to the §5 additions.
- No edit to `_normalize_match_predicates` /
  `_normalize_runtime_context` (other callers exist).
- No edit to `eco_service.py` or any router.
- No schema / migration / tenant-baseline / feature flag.
- No new public function in `parallel_tasks_service.py`.

## 4. Test Matrix

`src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py`
— 67 tests post-substitution (counts a point-in-time snapshot;
pre-substitution count was 46):

- **Existing 45 tests verbatim** — DTO validation, evaluator
  match matrix, workflow_map_id independence, fail-open pin,
  AST purity guard, drift guard vs the service constants.
- **`test_service_delegates_to_contract` (renamed from
  `test_service_parity`)** — 21 parametrized cases verifying the
  delegation is wired end-to-end. Tautological by construction
  post-substitution; documented as a wiring smoke test, NOT a
  parity check.
- **`test_contract_matches_spec_derived_parity_snapshot`
  (MANDATORY, exactly named)** — 21 parametrized cases over
  `_AUTOMATION_PARITY_SNAPSHOT`; asserts the contract's
  `evaluate_rule_predicate(normalize_workflow_rule_predicate(wf,
  mp), WorkflowRuleFacts.from_context(ctx)) == expected`. **This
  is the regression net** — it pins the contract's outputs
  bit-for-bit to spec-derived truth.
- *(Removed the previously-planned `test_snapshot_size_matches_parity_cases`
  defensive test — consolidating both parametrized tests over a
  single source of truth obviates it; no two lists can drift
  because there is only one list.)*
- **`test_runtime_scope_delegates_to_contract` (MANDATORY,
  exactly named)** — AST pin: (positive) the service module's
  `_rule_matches_runtime_scope` source must reference the four
  contract surfaces (`automation_rule_predicate_contract` import,
  `normalize_workflow_rule_predicate(`, `WorkflowRuleFacts.from_context(`,
  `evaluate_rule_predicate(`); (negative) the method body must NOT
  call `self._rule_match_predicates` — that helper was the
  pre-substitution matcher's predicate accessor, its only caller
  was `_rule_matches_runtime_scope`, and a future refactor that
  re-introduces local matcher arithmetic via it would have to call
  it again. The negative check walks the method's AST for `Call`
  nodes (advisor-recommended over a brittle string match like
  `"predicates.get(" not in src`, which would miss a renamed
  variable).

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) stays green.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_services.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_router.py \
  src/yuantus/meta_engine/tests/test_eco_parallel_flow_hooks.py
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
  src/yuantus/meta_engine/services/parallel_tasks_service.py
git diff --check
```

No alembic / tenant-baseline — the substitution adds no schema.

Observed as of 2026-05-18: contract test file 67/67 passed;
`parallel_tasks_services` regression unchanged (broader matcher
& runtime tests); `parallel_tasks_router` + `eco_parallel_flow_hooks`
regression unchanged (288 tests passed across all matcher-touching
files combined); doc-index trio + R2 portfolio drift guard green;
`py_compile` clean; `git diff --check` clean.

## 6. Non-Goals (reaffirmed from taskbook §8)

- No DSL extension — `_ALLOWED_MATCH_PREDICATES` stays the same 5
  keys; no new operators (`in`/`not`/`ge`).
- No fail-open → fail-closed change — illegal stored
  `match_predicates` continues to degrade to the empty predicate
  with `workflow_map_id` preserved.
- No `_ALLOWED_TYPES` action-set widening — stays
  `{"emit_event", "create_job", "set_eco_priority"}`.
- No deletion of `_rule_match_predicates` (now dead code; deletion
  is a separate mechanical opt-in).
- No edit to the contract runtime module or to any of its 45
  remaining tests (other 45 stay verbatim).
- No edit to `_normalize_match_predicates` /
  `_normalize_runtime_context` (other callers exist).
- No edit to `eco_service.py` / any router / any schema /
  migration / tenant-baseline / feature flag.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Follow-ups (each its own separate opt-in)

- Mechanical deletion of `_rule_match_predicates` after this PR
  lands (now dead code).
- Option C — operator/key extension (`in`/`not`, new keys).
- Fail-open → fail-closed hardening.
- `_ALLOWED_TYPES` action-set widening.
- Substituting `_normalize_match_predicates` and
  `_normalize_runtime_context` to delegate to the contract's
  pure mirrors (the contract has them but the service keeps its
  own versions because other callers depend on them; substitution
  is broader scope).

## 8. Tier-B sequencing

Per the owner's 2026-05-18 serialization rule (only ONE Tier-B
runtime follow-up at a time), this PR is the in-flight item.
After merge, next candidates from the priority queue:
(3) breakage state-machine integration (partially shipped by
owner via #595 + #596; remainder needs finer taskbook split),
(4) DB-querying resolvers for 3a/3b/3c — each its own session
and opt-in.
