# Taskbook: Lifecycle Transition History All-Attempts Logging

Status: design lock / docs-first / no runtime code in this slice.

Created: 2026-06-22

Owner gate: implementation requires an explicit follow-up opt-in.

## 1. Current State

Lifecycle transition history is implemented through the success-only line:

- Slice 1 writes `LifecycleTransitionHistory` rows for successful transition
  results.
- Slice 2 exposes item-scoped reads and the superuser forensic route.
- The table already contains an `outcome` column, currently written as
  `"success"`.
- The table has a JSON `properties` column for additive detail.
- The existing write is best-effort and savepoint-isolated. It must not break
  `promote()`.
- `item_id` is intentionally FK-free so history can survive hard item deletes.

The original taskbook explicitly deferred all-attempts logging. This taskbook
locks the follow-up shape before any code change.

## 2. Goal

Record selected failed, denied, blocked, or aborted `LifecycleService.promote()`
attempts in the same transition-history table, without changing promote
business behavior.

The feature should answer:

- Who attempted a transition?
- From which lifecycle state and permission?
- To which target state?
- Why did it not commit?
- When did the attempt happen?

It must not turn an audit failure into a lifecycle failure.

## 3. Non-Goals

- No runtime code in this taskbook PR.
- No schema migration expected for v1 all-attempts. Use the existing `outcome`
  and `properties` fields unless implementation proves a new column is needed.
- No route change expected for v1 all-attempts. Existing read surfaces should be
  sufficient once rows are written.
- No logging of secrets, raw stack traces, full hook payloads, or unbounded
  exception text.
- No all-attempts persistence that can turn an audit write failure into a
  lifecycle failure.
- No conversion of audit logging into a hard requirement for `promote()`.

## 4. Core Semantics

### 4.1 Write Guarantee

All-attempt rows are best-effort, like success rows.

If the attempt-history insert fails:

- log the audit failure;
- preserve the original `PromoteResult`;
- do not raise a new exception from the audit path;
- do not alter item state, permission, or version release behavior.

### 4.2 Transaction Boundary

Use two different durability modes:

- Successful transition rows stay same-session and caller-transactional. This is
  the existing Slice 1 behavior.
- Failed, denied, blocked, or aborted attempt rows use a separate best-effort
  audit write that can survive the caller transaction rolling back.

This split is required because `src/yuantus/meta_engine/operations/promote_op.py`
raises `ValidationError` when `promote()` returns a failed `PromoteResult`. A
raised AML operation is not committed. If the failure row is written only in the
same caller transaction, the main AML apply path records nothing where
all-attempts logging matters most.

The failure-attempt write must still be best-effort:

- never make the original promote failure worse;
- never mask or replace the original `PromoteResult`;
- never reuse a rollback-pending business session;
- never write raw exception details, secrets, or unbounded payloads.

Acceptable implementation shapes include a separate audit session or a small
append-only audit writer. The implementation PR must document which one it uses.

### 4.3 Outcome Shape

Keep `outcome` low-cardinality and stable:

- `success` - existing successful transition rows.
- `denied` - actor/auth/transition policy refused the transition.
- `blocked` - business precondition blocked the transition.
- `aborted` - hook or condition logic intentionally aborted the transition.
- `failed` - internal integration/workflow/version-release path failed after
  the transition attempt started.

Put the exact machine reason in `properties.reason_code`.

Recommended reason codes for v1:

- `target_state_not_found`
- `transition_missing`
- `actor_missing`
- `permission_denied`
- `assembly_release_blocked`
- `before_transition_aborted`
- `condition_failed`
- `on_exit_aborted`
- `on_enter_aborted`
- `workflow_start_failed`
- `version_release_failed`

Do not use free-form user or exception text as the primary reason. If a human
message is useful, store a sanitized, bounded `properties.public_message`.

### 4.4 Read Surface

Use the two-tier read model already shipped:

- item-scoped transition-history route: success rows only;
- forensic superuser route: success and non-success rows, including rows for
  deleted item IDs.

This starts tight and remains reversible: a future slice can add selected
non-denial outcomes to the item-scoped read route if product needs ordinary item
readers to see "why did this release not finish?" process failures. Denials stay
forensic-only unless explicitly re-ratified. Sanitization remains mandatory even
on the forensic route.

## 5. Grounded Promote() Write Sites

The current `LifecycleService.promote()` has several return paths. All-attempts
implementation must handle only the paths where a row is meaningful and safe to
write.

| Promote path | Recommended v1 history behavior | Notes |
| --- | --- | --- |
| No lifecycle map for the item type | Do not record | This is configuration/integrity state, not a transition attempt with a lifecycle state. |
| Current state cannot be found | Do not record | From-state is unknown; avoid misleading audit rows. |
| Target state cannot be found | Record `denied` / `target_state_not_found` | Actor requested a target; item and current state are known. |
| Transition does not exist | Record `denied` / `transition_missing` | The transition graph refused it. |
| Role-gated transition has missing actor | Record `denied` / `actor_missing` | `actor_user_id` may be null or the raw requested id. |
| Actor lacks required role | Record `denied` / `permission_denied` | Keep role detail bounded and non-secret. |
| Assembly release child gate blocks release | Record `blocked` / `assembly_release_blocked` | Store counts or child IDs only if already safe for current callers. |
| BEFORE_TRANSITION hook aborts | Record `aborted` / `before_transition_aborted` | No item mutation has committed. |
| Condition evaluates false | Record `aborted` / `condition_failed` | The existing ON_PROMOTE_FAIL hook remains business behavior. |
| ON_EXIT_STATE hook aborts | Record `aborted` / `on_exit_aborted` | No item mutation has committed. |
| ON_ENTER_STATE hook aborts after mutation attempt | Record after state/permission rollback | Row must describe attempted target and preserved old state. |
| Workflow start fails after mutation attempt | Record after state/permission rollback | Store sanitized error class/message only. |
| Version release fails after mutation attempt | Record after state/permission rollback | Store sanitized error class/message only. |
| Business flush fails | Do not swallow; do not record in v1 | A rollback-pending session is not a safe audit write site. |
| AFTER_TRANSITION hook runs | No failure row | These hooks run after success history and cannot abort the transition. |

## 6. Implementation Plan for the Follow-Up Slice

### 6.1 Helper

Add a helper such as `_record_transition_attempt(...)` that writes the failed
attempt through the chosen independent best-effort audit path and accepts:

- `outcome`
- `reason_code`
- `target_state`
- `from_state`
- `from_permission`
- `to_permission` when known
- `actor_user_id`
- bounded `properties`

The helper must:

- respect `LIFECYCLE_TRANSITION_HISTORY_ENABLED`;
- isolate audit insert failures from lifecycle business state;
- not flush or commit the caller's business session;
- never change the `PromoteResult`;
- commit only its own independent audit write, if that write path uses a
  separate session;
- tolerate missing/rolled-back business transaction state by taking primitive
  IDs and sanitized values, not live ORM objects that need lazy loading later.

### 6.2 Post-Rollback Write Sites

For ON_ENTER, workflow, and version-release failures, the code must first restore
the previous item state and permission in memory, then record the failed attempt.

The history row should describe:

- `from_state_id` and `from_permission_id` as the committed/pre-attempt values;
- `to_state_id` as the attempted target;
- `to_permission_id` if the attempted permission is known;
- `properties.rolled_back = true`.

### 6.3 Sanitization

Properties should be structured and bounded:

```json
{
  "reason_code": "workflow_start_failed",
  "error_class": "WorkflowStartError",
  "public_message": "workflow start failed",
  "rolled_back": true
}
```

Avoid:

- stack traces;
- raw hook payloads;
- secrets or headers;
- unbounded exception strings;
- full child BOM payloads.

## 7. Verification Plan

The implementation PR must include focused tests for:

- target state not found records `denied`;
- missing transition records `denied`;
- role/user denied records `denied`;
- assembly release gate records `blocked`;
- BEFORE_TRANSITION abort records `aborted`;
- condition failure records `aborted`;
- ON_EXIT abort records `aborted`;
- ON_ENTER abort records `aborted` after rollback;
- workflow-start failure records `failed` after rollback;
- version-release failure records `failed` after rollback;
- audit insert failure is best-effort and preserves the original
  `PromoteResult`;
- failed-attempt rows survive the AML apply failure/rollback path;
- business flush failure is not swallowed by the audit guard;
- failure-attempt writes do not flush or commit the caller's business session;
- success rows are unchanged (`outcome == "success"`);
- item-scoped read route remains success-only;
- forensic read route returns non-success rows with sanitized properties.

Mutation checks should prove at least one failure-path write test fails if the
new helper call is removed.

Expected route count for the implementation slice: unchanged, unless the owner
chooses a new read/filter endpoint.

Expected Alembic head for the implementation slice: unchanged, unless the owner
chooses a schema change.

## 8. Owner Decisions to Ratify Before Runtime

1. Should configuration/integrity failures (`no lifecycle map`, current state
   missing) stay unrecorded in v1?
   - Recommendation: yes, do not record them.

2. Should failed attempt rows survive caller rollback?
   - Recommendation: yes for v1. Success rows stay caller-transactional, but
     failed/denied/blocked/aborted rows need an independent best-effort audit
     write; otherwise the main AML apply path logs nothing.

3. Should normal item readers see failed attempts?
   - Recommendation: no for v1. Keep item-scoped reads success-only and expose
     failed, denied, blocked, and aborted attempts only through the superuser
     forensic route. This is reversible: adding selected non-denial outcomes to
     item-scoped reads later is additive; removing already-shipped failure rows
     from item-scoped reads would be a regression.

4. Should `outcome` remain low-cardinality with `properties.reason_code`, or
   become one granular outcome per failure?
   - Recommendation: keep `outcome` low-cardinality and put the exact reason in
     `properties.reason_code`.

## 9. Exit Criteria for Runtime Slice

- All selected failure paths write a best-effort history row.
- No selected failure path changes its existing `PromoteResult`.
- No audit-path exception can make a lifecycle transition fail.
- No business flush or lifecycle rollback error is swallowed by the audit guard.
- The implementation requires no new route or migration unless an owner decision
  explicitly changes the scope.
- Contracts and regression CI are green.
