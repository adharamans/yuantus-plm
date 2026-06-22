# Lifecycle transition — all-attempts (failure) logging taskbook (T1, design-only)

**Status:** taskbook / design-only — **T2 (implementation) is gated on owner approval of this T1.**
No code change here. Builds on the **reserved `outcome` column**: successful transitions already
write `outcome="success"` via `LifecycleService._record_transition_history` (#814); T1 designs
logging the **failed attempts** that today return `PromoteResult(success=False)` and record nothing.

Grounding: every line ref below is `meta_engine/lifecycle/service.py::promote()` as of `main`.

## 1. Which failures to record — the boundary

`promote()` has two classes of `success=False` return:

| # | path | line | record? | outcome |
|---|---|---|---|---|
| A1 | no lifecycle map | 110 | **no** | — |
| A2 | current state not found | 131 | **no** | — |
| A3 | target state not found | 145 | **no** | — |
| A4 | no valid transition | 161 | **no** | — |
| B1 | user not found | 183 | yes | `denied` |
| B2 | role permission denied | 188 | yes | `denied` |
| B3 | B2 assembly release gate | 213 | yes | `condition_failed` |
| B4 | before_transition hook abort | 222 | yes | `hook_aborted` |
| B5 | condition not met | 245 | yes | `condition_failed` |
| B6 | on_exit hook abort | 254 | yes | `hook_aborted` |
| B7 | on_enter hook abort (post-mutation rollback) | 288 | yes | `hook_aborted` |
| B8 | workflow start failure (post-mutation rollback) | 317 | yes | `workflow_failed` |
| B9 | version release failure (post-mutation rollback) | 338 | yes | `version_failed` |

**The boundary: record only failures at or after the transition object is resolved (B1–B9).** A1–A4
are *configuration/lookup* errors — there is no resolved `transition_obj` / `target_state_obj`, so
the row would be sparse (null to-state/transition) and they are not a user "attempt" against a real
transition. **Recommend: do not record A1–A4** as transition-history (they already `logger`/return a
clear error). This boundary also guarantees field availability (§3).

## 2. Best-effort + isolation — the load-bearing design point

Two requirements, both like the success write and `record_seat_cap_audit`:

- **Best-effort / non-blocking:** logging a failed attempt must **never** change the `PromoteResult`
  (it is already a failure) and must **never raise**. Wrap in its own try/except; a logging failure is
  swallowed + `logger`-ed, exactly as `_record_transition_history` already is for the success row.
- **Durability vs the caller's rollback — the subtle part.** The success row is written on the
  caller's session and persisted by the **caller's commit**. But on a *failed* promote the caller
  typically **rolls back** the transaction — which would discard a failure row written on that same
  session. So **failure rows must be written in a separate, self-committing best-effort transaction**
  (own session, like `record_seat_cap_audit` uses its own meta session + commit), so the attempt
  survives the caller rolling back the main attempt. Decision needed: separate session vs a nested
  `SAVEPOINT` that is released independently. **Recommend the separate session** (matches the seat-cap
  audit precedent; no coupling to the caller's rollback semantics).
- **No interference with rollback:** for B7–B9 the in-memory state was mutated then rolled back
  (lines 285–287 / 314–316 / 335–337) **before** the return; the attempt-logging only INSERTs a row,
  never re-touches `item.state` — so it cannot resurrect a rolled-back mutation.

## 3. Field availability on failure

At/after transition resolution (B1–B9) every audited field is available: `item_id`, `actor_user_id`
(the *attempted* `user_id` — FK-free, so it records even the B1 "user not found" id), `from_state`
(`current_state_obj`), `to_state` (`target_state_obj`), `transition_id`, `lifecycle_map_id`,
`from/to_permission_id`, `created_at`. The `§1` boundary is *why* these are non-null — A1–A4 are
excluded precisely because their to-state/transition are unresolved.

## 4. `outcome` enum

`success | denied | condition_failed | hook_aborted | workflow_failed | version_failed | error`

- `error` is the catch-all for an *unexpected* exception in the logging path itself, not a normal
  return. Mapping is the §1 table.
- **Open choices for the owner:** (a) B3 (B2 assembly gate) as `condition_failed` vs a dedicated
  `precondition_failed`; (b) keep `workflow_failed` distinct from `hook_aborted` (recommended — they
  are different failure modes and you listed them separately) vs merging. The enum is stored in the
  existing `outcome` column (string), so adding/splitting values is non-breaking.

## 5. Failure reason — controlled, never a raw exception (Q5)

- **Controlled paths (B1–B7):** `PromoteResult.error` is already a *user-facing, controlled* string
  ("Permission denied. Role requirement not met…", "Transition condition not met.", the joined B2
  child errors, hook `abort_reason`). Safe to record — in `comment` or `properties.reason`.
- **Exception paths (B8 workflow, B9 version):** the error embeds raw **`str(e)`** (lines 318/339),
  which can leak internal exception detail (DB messages, internal ids, stack fragments). **Do NOT put
  the raw exception in the audit row.** Record the **outcome** + a *generic* reason ("workflow start
  failed" / "version release failed"); the raw `str(e)` stays only in the existing `logger.error`
  (application log), not the durable audit. Optionally a stable `properties.reason_code`
  (`role_not_met`, `condition_unmet`, `children_unreleased`, `hook_abort`, `workflow_error`,
  `version_error`) — controlled codes, never free-text exception text.

## 6. Route — failed attempts belong on the forensic tier

The read surface is already two-tier: item-scoped per-item-ACL read (`/items/{id}/transition-history`,
#831) and the **forensic superuser route** (`/transition-history/forensic/{id}`, #827).

A failed attempt — especially a **denial** — reveals "who tried what and was blocked," a security
signal that should not be visible to everyone with read permission on the item. **Recommend: the
item-scoped read stays success-only by default; failed attempts surface only via the forensic
(superuser) route** (which already returns every row for an `item_id`, so it needs only to stop being
implicitly success-only — i.e., no filter). T2 then adds an `outcome != 'success'` exclusion to the
item-scoped read. **Open choice:** (b) forensic-only vs (c) an admin-gated `include_failures` filter
on the item read. **Recommend (b)** for v1 — simplest, and it matches the existing tiering.

## 7. T2 scope (only if this T1 is approved)

A `_record_attempt(outcome, *, reason_code, …)` helper writing on a **separate best-effort session**;
a call at each B1–B9 return (before the `return PromoteResult(...)`); the §4 enum + §5 sanitization;
the item-scoped read's success-only filter (§6); tests per failure path (each outcome recorded;
best-effort swallow; durability across a caller rollback; no raw `str(e)` in the row; denials absent
from the item read, present on forensic). No new migration (the `outcome`/`properties` columns exist).

## 8. Decisions to confirm before T2

1. Record only B1–B9, skip A1–A4 (config errors)? *(recommend yes)*
2. Separate self-committing session for durability across rollback? *(recommend yes)*
3. Enum: split `workflow_failed`/`version_failed`; B3 → `condition_failed` or `precondition_failed`?
4. Route: forensic-only for failures, item read success-only? *(recommend yes)*
5. Reason: controlled message + `reason_code` only; raw `str(e)` stays in the app log? *(recommend yes)*
