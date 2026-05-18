# Claude Taskbook: Odoo18 Automation Engine Substitution R1

Date: 2026-05-18

Type: **Doc-only taskbook.** Changes no runtime. Specifies the
implementation a later, separately opted-in implementation PR will
deliver. Merging this taskbook does NOT authorize that code.

## 1. Purpose

R2 closeout §4 **Tier-B** follow-up #2 (owner-ranked priority
2026-05-18). Substitute the embedded matcher
`WorkflowCustomActionService._rule_matches_runtime_scope` to
**delegate** to the merged pure predicate contract
(`automation_rule_predicate_contract`, PR #577 `36ad043`) without
changing observable behavior. The contract has been parallel and
behavior-preserving since #577 merged; this PR moves the runtime
to use it as the single source of truth, so future predicate
changes happen in one place rather than two.

**Risk profile:** higher than the pack-and-go plugin wiring
(#597) — this *replaces* an existing matcher rather than adding
a new gate. The 21-case parity matrix is the regression net; the
key design point in this taskbook (§5) is that the parity matrix
must be promoted to a **frozen golden snapshot** before the
substitution lands, because the existing matrix becomes circular
the moment the service delegates to the contract.

## 2. Current Reality (grounded — read before implementing)

All citations verified by direct file read (per
[[feedback-verify-grounding-facts]]).

### The matcher today

`src/yuantus/meta_engine/services/parallel_tasks_service.py`:

- **`WorkflowCustomActionService`** at line 2051. Class constants:
  `_ALLOWED_TYPES = {"emit_event", "create_job", "set_eco_priority"}`
  (2052), `_ALLOWED_MATCH_PREDICATES = {stage_id, eco_priority,
  actor_roles, product_id, eco_type}` (2055–2061),
  `_ALLOWED_ECO_PRIORITIES` (2062), `_ALLOWED_ECO_TYPES` (2063).
- **`_normalize_match_predicates(self, match_predicates)`** at
  2158–2213: strict normalizer; raises on non-dict, unsupported
  key, bad enum, non-array `actor_roles`. Returns a normalized
  dict with empties dropped.
- **`_rule_match_predicates(self, rule)`** at 2215–2221: pulls
  `rule.action_params["match_predicates"]`, calls the strict
  normalizer, **swallows `ValueError` → returns `{}`** (fail-open
  on illegal stored predicate). Only call site is
  `_rule_matches_runtime_scope` at 2251.
- **`_normalize_runtime_context(self, context)`** at 2223–2239:
  normalizes runtime context (trim+lower-cases the 5 scope keys
  + actor_roles); does NOT enforce enum domain (unlike the
  predicate normalizer).
- **`_rule_matches_runtime_scope(self, *, rule, context)`** at
  2241–2274: the matcher itself.
  - Step 1: `workflow_map_id` check (read directly from
    `rule.workflow_map_id` — NOT from match_predicates) at
    2247–2249.
  - Step 2: load predicates via `_rule_match_predicates` (fail-open).
  - Steps 3–6: equality checks on `stage_id`, `eco_priority`,
    `product_id`, `eco_type`.
  - Step 7: `actor_roles` set-intersection (rule role(s) ∩
    runtime role(s) must be non-empty).
  - Absent predicate value = wildcard (the `if value and …` guard
    is load-bearing).
- **Production call site** at `evaluate_transition` line
  2424–2442: queries enabled rules, normalizes runtime context
  ONCE via `_normalize_runtime_context` (2430), then per rule
  calls `_rule_matches_runtime_scope` (2437) and appends matched
  rules to `matched`.
- **External call site** at
  `src/yuantus/meta_engine/services/eco_service.py:243`:
  `WorkflowCustomActionService(self.session).evaluate_transition(
  object_id=eco.id, target_object="ECO", from_state=…,
  to_state=…, trigger_phase=…, context=runtime_context)`. This
  is the only production-facing entry point.

### The merged contract today

`src/yuantus/meta_engine/services/automation_rule_predicate_contract.py`
(PR #577 `36ad043`):

- `_normalize_match_predicates_strict(match_predicates)` at
  71–130: **pure mirror** of the service's
  `_normalize_match_predicates`. Same raises, same normalizations.
- `normalize_workflow_rule_predicate(workflow_map_id,
  match_predicates) -> WorkflowRulePredicate` at 273–301: pure
  mirror of `rule.workflow_map_id + _rule_match_predicates`. On
  predicate-normalization `ValueError`, returns the **empty
  predicate with `workflow_map_id` preserved** — bit-for-bit
  with `_rule_match_predicates` line 2169 fail-open behavior. The
  in-doc comment at line 281–285 of the contract explicitly pins
  why `workflow_map_id` is preserved (it's a rule-column scope
  filter, NOT a `match_predicates`-derived key).
- `WorkflowRulePredicate` at 138–213: frozen Pydantic v2,
  `extra="forbid"`. Field validators normalize on direct
  construction (the #577 Medium fix); `is_empty()` predicate for
  "matches everything".
- `WorkflowRuleFacts.from_context(context) -> WorkflowRuleFacts`
  at 216–270: pure mirror of `_normalize_runtime_context` —
  trim+lowercase, **no enum-domain enforcement** (runtime context
  is not enum-validated, only normalized).
- `evaluate_rule_predicate(predicate, facts) -> bool` at
  304–329: pure mirror of `_rule_matches_runtime_scope` steps
  1–7 in the same order, with the same "absent value = wildcard"
  semantics, same actor_roles set-intersection.

### The existing 21-case parity matrix

`src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py`
lines 229–301:

- `_PARITY_CASES` (235–280) — 21 distinct `(workflow_map_id,
  raw_match_predicates, runtime_context)` tuples covering:
  empty/wildcard, single-key match/miss, multi-key match/miss,
  actor_roles intersection cases incl. mixed-case lowercasing,
  fail-open cases (illegal `bogus` key, bad enum value,
  non-array `actor_roles`), workflow_map_id match/miss with and
  without predicates.
- `test_service_parity(wf, mp, ctx)` (284–301) — parametrized;
  each case **independently** computes:
  - `service_decision = svc._rule_matches_runtime_scope(rule=…,
    context=svc._normalize_runtime_context(ctx))`
  - `contract_decision = evaluate_rule_predicate(
    normalize_workflow_rule_predicate(wf, mp),
    WorkflowRuleFacts.from_context(ctx))`
  - asserts `service_decision == contract_decision`.

**The critical observation for substitution:** today the two
sides are independent implementations, so equality is a real
parity guarantee. **The moment the service delegates to the
contract, both sides become the same code path** — the assertion
holds vacuously and the regression net is destroyed. This must
be addressed by the impl PR (see §5).

### What is NOT being changed

The contract module itself, `_normalize_match_predicates` (the
strict normalizer), `_normalize_runtime_context`, the rule
column schema, the ECO model enums, the router, the eco_service
call site, and the contract's existing 46 tests are all
**unchanged**. The substitution is an internal swap inside one
service method.

## 3. Substitution Boundary

### What the impl PR replaces

The body of
`WorkflowCustomActionService._rule_matches_runtime_scope` is
replaced with a thin delegation to
`evaluate_rule_predicate(normalize_workflow_rule_predicate(...),
WorkflowRuleFacts.from_context(...))`. The method signature,
keyword-only args, and return type stay identical. No callers
need to change.

`_rule_match_predicates` becomes dead code after substitution
(its only caller was `_rule_matches_runtime_scope`). **R1 leaves
it in place** rather than deleting; a separate cleanup opt-in
can remove it later. Reason: deleting it here widens the diff and
risks unrelated regressions; the deletion is mechanical and can
land on its own.

`_normalize_match_predicates` and `_normalize_runtime_context`
**stay** in the service — they are used by *other* code paths
(`_normalize_action_params` at line 2120 calls
`_normalize_match_predicates` during rule CRUD validation;
`_normalize_runtime_context` is called by `evaluate_transition`
BEFORE the per-rule matcher loop). The contract has pure mirrors
of both, but the service keeps its versions; the parallel
codepaths are fine because (a) the contract's mirrors are
already verified bit-for-bit by the contract's own 46 tests, and
(b) substituting these is broader scope, separate opt-in.

### Hard boundaries (do NOT do in R1)

1. **No operator/key extension.** `_ALLOWED_MATCH_PREDICATES`
   stays the same 5 keys; no support for "in", "not", "ge", or
   new keys like `eco_state`. That's the Option C follow-up,
   blocked by this PR.
2. **No fail-open → fail-closed change.** Illegal stored
   `match_predicates` continues to degrade to the empty
   predicate with `workflow_map_id` preserved. Hardening to
   fail-closed is its own separate opt-in.
3. **No action-type widening.** `_ALLOWED_TYPES` stays exactly
   `{"emit_event", "create_job", "set_eco_priority"}`. Adding new
   action types is a separate opt-in.
4. **No router/schema/migration changes.** No edits to
   `parallel_tasks_workorder_docs_router` or any router; no
   alembic; no model change. The `WorkflowCustomActionRule`
   schema is untouched.
5. **No deletion of `_rule_match_predicates`** in this PR. R1 is
   substitution; cleanup deletion is its own future PR.
6. **No edit to the contract module** or its 46 tests. The
   contract is the source of truth; the impl PR only edits the
   service.
7. **No edit to `_normalize_match_predicates` /
   `_normalize_runtime_context`** in this PR (they have other
   callers).
8. **No edit to `eco_service.py`** — the call site stays at
   `evaluate_transition`.

## 4. R1 Target Output

Single file edit: `src/yuantus/meta_engine/services/parallel_tasks_service.py`.

Replace the body of `_rule_matches_runtime_scope` (lines
2241–2274 today) with:

```python
def _rule_matches_runtime_scope(
    self,
    *,
    rule: WorkflowCustomActionRule,
    context: Dict[str, Any],
) -> bool:
    # Delegate to the merged automation predicate contract
    # (`automation_rule_predicate_contract`, PR #577 `36ad043`).
    # Pinned bit-for-bit by the 21-case parity snapshot — see
    # `_AUTOMATION_PARITY_SNAPSHOT` in the contract's tests.
    from yuantus.meta_engine.services.automation_rule_predicate_contract import (
        WorkflowRuleFacts,
        evaluate_rule_predicate,
        normalize_workflow_rule_predicate,
    )
    params = rule.action_params if isinstance(rule.action_params, dict) else {}
    predicate = normalize_workflow_rule_predicate(
        rule.workflow_map_id,
        params.get("match_predicates"),
    )
    # `context` is already normalized by the caller
    # (`_normalize_runtime_context` at evaluate_transition line
    # 2430). `WorkflowRuleFacts.from_context` normalization is
    # idempotent on already-normalized input (lowercasing an
    # already-lowercase string is a no-op) — verified by the
    # parity snapshot covering pre-normalized inputs.
    facts = WorkflowRuleFacts.from_context(context)
    return evaluate_rule_predicate(predicate, facts)
```

The local import inside the method matches the existing pattern
in the file (avoids touching the module-level import block; some
contracts intentionally lazy-load to keep service module
startup minimal).

No other edits to `parallel_tasks_service.py`.

## 5. Tests Required (in the later impl PR)

This is the hardest part of R1 — the existing parity matrix
becomes circular the moment substitution lands.

### Promote the parity matrix to a spec-derived frozen snapshot

Why a "snapshot from pre-substitution main" wouldn't work: in a
single-PR review there's no way to verify the snapshot wasn't
computed *after* the substitution edit (which would be
tautological). The snapshot must be **independently derivable from
§3's documented matcher semantics** so a reviewer reading only this
taskbook + the PR diff can verify each entry without running any
code.

The impl PR must:

1. **Transcribe the spec-derived truth table below** into the
   contract's test file as `_AUTOMATION_PARITY_SNAPSHOT`. Each
   `expected` value is hand-derived from §3's `_rule_matches_runtime_scope`
   semantics (steps 1–7: workflow_map_id eq → stage_id eq →
   eco_priority eq → product_id eq → eco_type eq → actor_roles
   set-intersection; absent predicate value = wildcard;
   normalization fail-open keeps `workflow_map_id` but zeros
   `match_predicates`-derived keys). Every entry's derivation is
   reviewable directly from §3 — no test runs required.

   The 21 cases (matching today's `_PARITY_CASES` ordering at
   `test_automation_rule_predicate_contract.py:235–280`):

   | # | workflow_map_id | match_predicates | runtime_context | expected | derivation (1-line) |
   |---|---|---|---|---|---|
   |  1 | `None` | `None`         | `{}`                                                          | **True**  | empty predicate ⇒ wildcard |
   |  2 | `None` | `{}`           | `{"eco_type": "bom"}`                                         | **True**  | empty predicate ⇒ wildcard |
   |  3 | `None` | `{"eco_type": "bom"}` | `{"eco_type": "bom"}`                                  | **True**  | eco_type eq |
   |  4 | `None` | `{"eco_type": "bom"}` | `{"eco_type": "document"}`                             | **False** | eco_type "bom" ≠ "document" |
   |  5 | `None` | `{"eco_type": "bom"}` | `{}`                                                   | **False** | eco_type "bom" ≠ `None` |
   |  6 | `None` | `{"eco_priority": "High"}` | `{"eco_priority": "high"}`                         | **True**  | predicate lowercases to "high", facts already "high" |
   |  7 | `None` | `{"actor_roles": ["QA", "Eng"]}` | `{"actor_roles": ["eng", "pm"]}`             | **True**  | intersection ⊇ {"eng"} |
   |  8 | `None` | `{"actor_roles": ["qa"]}` | `{"actor_roles": ["pm"]}`                           | **False** | intersection = ∅ |
   |  9 | `None` | `{"stage_id": "s1", "product_id": "p1"}` | `{"stage_id": "s1", "product_id": "p1"}` | **True**  | both keys eq |
   | 10 | `None` | `{"stage_id": "s1", "product_id": "p1"}` | `{"stage_id": "s1", "product_id": "p9"}` | **False** | product_id "p1" ≠ "p9" |
   | 11 | `"wf1"` | `None`        | `{"workflow_map_id": "wf1"}`                                 | **True**  | workflow_map_id eq, empty predicate |
   | 12 | `"wf1"` | `None`        | `{"workflow_map_id": "wf2"}`                                 | **False** | workflow_map_id "wf1" ≠ "wf2" |
   | 13 | `"wf1"` | `{"eco_type": "bom"}` | `{"workflow_map_id": "wf1", "eco_type": "bom"}`      | **True**  | both keys eq |
   | 14 | `"wf1"` | `{"eco_type": "bom"}` | `{"workflow_map_id": "wf2", "eco_type": "bom"}`      | **False** | workflow_map_id "wf1" ≠ "wf2" (step 1 short-circuit) |
   | 15 | `None` | `{"actor_roles": ["QA", "Eng"]}` | `{"actor_roles": ["ENG", "Pm"]}`             | **True**  | both sides lowercase ⇒ intersection ⊇ {"eng"} |
   | 16 | `None` | `{"actor_roles": ["QA"]}` | `{"actor_roles": ["Pm"]}`                           | **False** | intersection = ∅ |
   | 17 | `None` | `{"bogus": 1}` | `{"eco_type": "bom"}`                                       | **True**  | unsupported key ⇒ fail-open ⇒ empty predicate ⇒ wildcard |
   | 18 | `"wfX"` | `{"bogus": 1}` | `{"eco_type": "bom"}`                                      | **False** | fail-open keeps workflow_map_id="wfX", facts.workflow_map_id is `None` |
   | 19 | `"wfX"` | `{"bogus": 1}` | `{"workflow_map_id": "wfX"}`                               | **True**  | fail-open keeps workflow_map_id="wfX", facts match |
   | 20 | `None` | `{"eco_priority": "nope"}` | `{"eco_priority": "high"}`                          | **True**  | bad enum ⇒ fail-open ⇒ empty predicate ⇒ wildcard |
   | 21 | `None` | `{"actor_roles": "qa"}` | `{"actor_roles": ["qa"]}`                              | **True**  | non-array actor_roles ⇒ fail-open ⇒ empty predicate ⇒ wildcard |

   Totals: 11 `True` + 10 `False` = 21 cases.

   Form for transcription (impl PR materialises exactly this list):

   ```python
   _AUTOMATION_PARITY_SNAPSHOT: Tuple[
       Tuple[Optional[str], Optional[Dict[str, Any]], Dict[str, Any], bool],
       ...,
   ] = (
       (None, None, {}, True),
       (None, {}, {"eco_type": "bom"}, True),
       # … (rows 3–21 per the table above)
   )
   ```

   Document inline that the snapshot is **spec-derived from §3**,
   not runtime-captured — the regression net is the spec, not a
   moment-in-time output.

2. **Add a new MANDATORY exactly-named test**
   `test_contract_matches_spec_derived_parity_snapshot` that
   parametrizes over `_AUTOMATION_PARITY_SNAPSHOT` and asserts
   `evaluate_rule_predicate(normalize_workflow_rule_predicate(wf,
   mp), WorkflowRuleFacts.from_context(ctx)) == expected`. This
   replaces the **regression-net role** of `test_service_parity`
   after substitution.

3. **Rename the now-tautological `test_service_parity` to
   `test_service_delegates_to_contract`** in the impl PR. Reason:
   keeping the old name would falsely advertise an
   independent-implementation parity check that no longer exists;
   the renamed test honestly describes its diminished role
   (verifies the service call still routes through the contract
   end-to-end). The DEV MD must document this rename, with the
   regression-net role explicitly transferred to
   `test_contract_matches_spec_derived_parity_snapshot`.

4. **AST pin** that `_rule_matches_runtime_scope`'s body delegates
   to the contract — module imports
   `automation_rule_predicate_contract`, calls
   `evaluate_rule_predicate`, calls
   `normalize_workflow_rule_predicate`, calls
   `WorkflowRuleFacts.from_context`. No new local lock-arithmetic
   helpers. Test name: `test_runtime_scope_delegates_to_contract`.

### Other tests required

- **No-new-public-surface AST test**: the impl PR adds NO new
  module-level function or class in `parallel_tasks_service.py`.
  Only `_rule_matches_runtime_scope`'s body changes.
- **No-dead-code-removal AST test**: `_rule_match_predicates`,
  `_normalize_match_predicates`, `_normalize_runtime_context` are
  ALL still defined in the service module (we don't delete them).
- **Drift guard preserved**: the contract's existing drift guard
  test (`_ALLOWED_MATCH_PREDICATES` equal on both sides) stays
  green. The R2 portfolio drift guard
  (`test_odoo18_r2_portfolio_contract.py`) stays green.
- **ECO hook smoke**: existing eco-service tests that exercise
  `evaluate_transition` continue to pass unchanged.

### What the snapshot does NOT cover

The 21-case matrix is the boundary case set the contract author
picked. New behavioral cases discovered post-R1 belong in a
separate test, not in the frozen snapshot (modifying the
snapshot retroactively defeats its purpose). If the impl PR
discovers a missing case, **add it to the snapshot before the
substitution edit**, capture its pre-substitution truth, then
proceed.

## 6. Verification Commands (for the impl PR)

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_automation_rule_predicate_contract.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_services.py \
  src/yuantus/meta_engine/tests/test_eco_service_workflow_hooks.py
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

(Note: `test_eco_service_workflow_hooks.py` is the historical
name — if it's been renamed, substitute the current ECO
workflow-hook test file from `git ls-files`.)

No alembic / tenant-baseline — the substitution adds no schema.

## 7. DEV/verification MD requirements (impl PR)

Add `docs/DEV_AND_VERIFICATION_ODOO18_AUTOMATION_ENGINE_SUBSTITUTION_R1_20260518.md`
+ index registration. Must document: (a) what was substituted
and what was deliberately left (the dead-code situation around
`_rule_match_predicates`); (b) the frozen parity snapshot — why
it exists and why `test_service_parity` is no longer the
regression net; (c) the hard boundaries (§3) that the impl PR
honored; (d) the local-import pattern used inside
`_rule_matches_runtime_scope`; (e) reuse-not-reimplementation of
the contract.

## 8. Non-Goals (hard boundaries for the impl PR — reaffirmed)

- No operator/key extension (Option C is a separate later opt-in).
- No fail-open → fail-closed hardening (separate later opt-in).
- No `_ALLOWED_TYPES` widening (separate later opt-in).
- No deletion of `_rule_match_predicates` (separate later opt-in).
- No edit to the contract or its 46 tests.
- No edit to `_normalize_match_predicates` /
  `_normalize_runtime_context` (other callers exist).
- No edit to `eco_service.py` or any router.
- No schema / migration / tenant-baseline / feature flag.
- No new public function in `parallel_tasks_service.py`.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude or the project owner
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/odoo18-automation-engine-substitution-r1-20260518`.

Follow-ups, each its own separate opt-in (explicitly NOT in R1):

- Deletion of `_rule_match_predicates` after substitution lands
  (mechanical cleanup).
- Option C: operator/key extension (`in`/`not`, etc.).
- Fail-open → fail-closed hardening (illegal predicate raises
  instead of degrading).
- `_ALLOWED_TYPES` action-set widening (new action types).
- Substituting `_normalize_match_predicates` and
  `_normalize_runtime_context` to delegate to the contract's
  pure mirrors (the contract has them but the service keeps its
  own versions because other callers depend on them).

## 10. Reviewer Focus

- Is the **spec-derived** frozen-snapshot in §5 the right way to
  preserve the regression net? The snapshot's 21 entries are
  hand-derived from §3's documented matcher semantics so each is
  independently verifiable by walking §3 rules — not "computed
  by running tests on pre-substitution main" (which would be
  unverifiable inside a single-PR review). Alternative considered
  and rejected: a "legacy" copy of the matcher kept in the
  service for parity comparison — doubles the surface, makes
  the deletion follow-up awkward. Snapshot is cleaner provided
  it's spec-derived. **Please walk a few rows (esp. #14, #17,
  #18, #19) against §3 to confirm the derivation is correct.**
- Confirm `_rule_match_predicates` being left as dead code is OK
  for R1 (cleanup is a separate opt-in). Reviewer can flag if
  they want the deletion bundled in.
- Confirm the local-import pattern inside
  `_rule_matches_runtime_scope` is acceptable — there's no
  hot-path concern (the import resolves once per Python process
  after first call) and it avoids a module-level cycle if any.
- Did anything add a new operator / change fail-open / widen
  action types / touch a router / change schema? It must not.
- Confirm the 4 hard boundaries (no operator extension / no
  fail-open→fail-closed / no action type widening / no router or
  schema change) are honored.
