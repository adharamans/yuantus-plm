# Odoo18 `_rule_match_predicates` Dead-Code Cleanup — Development and Verification

Date: 2026-05-18

## 1. Goal

Mechanical follow-up to PR #599 (`c769008`, the automation engine
substitution R1). #599 explicitly flagged in its §7 follow-ups that
`_rule_match_predicates` was dead code post-substitution and that
deletion would land as a separately opted-in mechanical PR. This is
that PR.

Scope is **strictly mechanical**: delete the helper, pin its
deletion with a drift test, and minimally update historical docstring
references in the contract test file that pointed at the now-removed
helper. NOT a new feature slice — no new operators, no fail-open
hardening, no action-type widening, no router/schema/migration.

## 2. Scope

### Modified

- `src/yuantus/meta_engine/services/parallel_tasks_service.py` —
  deleted the 7-line `WorkflowCustomActionService._rule_match_predicates`
  method. No other changes in this file.
- `src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py`
  — added one drift test (`test_rule_match_predicates_helper_is_deleted`);
  updated two historical docstring references (lines ~194 + ~211) that
  pointed at `_rule_match_predicates line 2169` so they describe the
  fail-open's current home (the contract's
  `normalize_workflow_rule_predicate`) rather than the now-removed
  service helper.
- `docs/DELIVERY_DOC_INDEX.md` — one index line for this DEV MD.

### Added

- `docs/DEV_AND_VERIFICATION_ODOO18_RULE_MATCH_PREDICATES_CLEANUP_20260518.md`

The contract runtime module, `_normalize_match_predicates`,
`_normalize_runtime_context`, `_rule_matches_runtime_scope`,
`eco_service.py`, every router, and every schema are **untouched**.

## 3. Pre-deletion verification

Direct `grep -rn '_rule_match_predicates\b' src/ plugins/` confirmed
zero production-code callers prior to deletion: the only definition
site was `parallel_tasks_service.py:2215`, and the only production
caller (the substituted `_rule_matches_runtime_scope` body) had
been rewritten by #599 to delegate to the contract via
`evaluate_rule_predicate(normalize_workflow_rule_predicate(...),
WorkflowRuleFacts.from_context(...))`. Pre-deletion test docstring
references in the contract test file (~194, ~211, plus the AST
negative check in `test_runtime_scope_delegates_to_contract`) refer
to the helper symbolically without calling it.

## 4. Drift guard

New test `test_rule_match_predicates_helper_is_deleted` asserts
`hasattr(WorkflowCustomActionService, "_rule_match_predicates")` is
`False`. A future re-introduction of the helper — for example, an
accidental revert of the substitution that brings back local matcher
arithmetic and needs this helper again — fails this test loudly
before landing.

Companion to `test_runtime_scope_delegates_to_contract` (#599):

- `test_runtime_scope_delegates_to_contract` pins **no call from
  inside the substituted method body** (AST `Call`-walk).
- `test_rule_match_predicates_helper_is_deleted` pins **the helper's
  complete removal from the class** (`hasattr` check).

Together they guard against both "the body re-introduces local
matcher arithmetic" and "the helper is re-added even if unused".

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

Observed as of 2026-05-18: contract test file 68/68 passed
(67 R1 baseline + 1 new drift test); 303 tests passed across all
matcher-touching files combined (R1 baseline 302 + 1 new drift
test); doc-index trio + R2 portfolio drift guard green;
`py_compile` clean; `git diff --check` clean.

## 6. Non-Goals

- No edit to `_normalize_match_predicates` /
  `_normalize_runtime_context` (other callers exist).
- No edit to the contract runtime module
  (`automation_rule_predicate_contract.py`) — same boundary as #599.
- No edit to `_rule_matches_runtime_scope` (that was #599's
  delivery; this PR only removes a dead helper and adds a drift
  guard).
- No DSL/operator extension, no fail-open → fail-closed change,
  no `_ALLOWED_TYPES` widening, no router/schema/migration/flag.
- No edit to `eco_service.py` or any router.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Relationship to #599

This PR delivers the mechanical-cleanup follow-up explicitly listed
in #599's DEV MD §7 (`Mechanical deletion of _rule_match_predicates
after this PR lands (now dead code).`). It does not extend, harden,
or revise any other §7 item. Each remaining §7 follow-up
(operator/key extension; fail-open hardening; `_ALLOWED_TYPES`
widening; substituting the strict normalizer / runtime-context
normalizer) is its own separate later opt-in.
