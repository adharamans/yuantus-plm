# Taskbook — Lifecycle transition-history persistence

Date: 2026-06-19 · Branch `claude/transition-history-taskbook` · base `origin/main`.
Status: **design / semantics locked, ready to implement.** This taskbook settles the audit-
table semantics; implementation follows in the slices below.

## 0. The gap

`LifecycleService.promote()` accepts `comment: str = ""` and threads it into the hook
context (`extra_data["comment"]`), but the history write is a **commented-out placeholder**:

```python
# 9. 记录历史 (Placeholder - actual history logging to a table or event stream)
# self._record_history(item, current_state_obj, target_state_obj, user_id, comment)
```

So the comment is dropped and there is **no durable record of who moved an item from which
state to which, when, and why**. The existing options don't fit:
- `VersionHistory` (`meta_version_history`) is **version-scoped** (create/checkout/checkin/
  revise on a version) — wrong grain for an item-level state transition.
- `AuditService` is a **log-only stub** ("In real implementation, this might write to a
  separate Audit Table") — no DB persistence.

## 1. Locked decisions (the two that change the table's meaning — owner-ratified)

**D1 — Write is BEST-EFFORT, never fatal to the transition.** The history write is wrapped in
`try/except`: a failure is logged and the transition still succeeds and commits. `actor_user_id` is
**nullable with no hard FK** (system/automated promotes — e.g. the C3 date-obsolete worker
with `system_user_id=0` — succeed *without* a validated user, because `promote()` only looks
up the user when a transition is role-gated; a hard FK would FK-violate and break the caller's
commit). **An audit row must never break the business operation it audits.**

**D2 — v1 records SUCCESSFUL transitions only.** The row is written at the line-338 placeholder
— **after** all three rollback returns (and **before** the `AFTER_TRANSITION` hooks, which
cannot abort the already-applied transition) — so only committed transitions get a row (a
rolled-back transition writes nothing). The schema includes an
`outcome` column (always `"success"` in v1) so *all-attempts* logging is a future non-breaking
schema extension. **All-attempts is explicitly NOT v1**: it requires writes on every failure/
return path (gate-denied, condition-failed, on_enter-abort, workflow-fail, version-fail) — a
different write pattern and a much larger surface, a separate slice if ever wanted.

## 2. One-line ratified defaults (uncontested)

- **New dedicated table** `meta_lifecycle_transition_history` — not `VersionHistory` (wrong
  grain), not `AuditService` (log-only stub).
- **Written directly in `promote()`** (not via an `AFTER_TRANSITION` hook) at the existing
  placeholder, so it is deterministic and co-located with the state mutation; the row is added
  to the session and the **caller commits it atomically** with the state change (so a later
  caller `rollback()` drops the history row too).
- **Per-tenant application table** → it must be in the ambient metadata the baseline generator
  sees. Placing the model where `import_all_models()` already imports it (e.g.
  `lifecycle/models.py`) makes that automatic — **no `tenant_schema` supplement needed**; a
  supplement is only required for a model NOT on an `import_all_models()` path (the C3
  `DateObsoleteImpact` case, which lives under `meta_engine/models`). Either way, **regenerate
  the committed baseline** and run the new-model test **in the same process** as the baseline
  drift-guard before pushing.
- **Default-on with a kill-switch** `LIFECYCLE_TRANSITION_HISTORY_ENABLED` (default `True`) that
  genuinely no-ops the write.
- **Read surface is a separate Slice 2** so Slice 1 stays **route-count-neutral**.
- No `tenant_id` column (lives in the per-tenant schema, consistent with `DateObsoleteImpact`).

## 3. Schema — `meta_lifecycle_transition_history`

| column | type | notes |
|---|---|---|
| `id` | String(64) PK | uuid |
| `item_id` | String, FK→`meta_items.id`, index, NOT NULL | the item that transitioned |
| `from_state_id` | String, nullable | |
| `from_state_name` | String | denormalized for readability |
| `to_state_id` | String | |
| `to_state_name` | String | |
| `from_permission_id` | String, nullable | item's permission **before** the transition (the `old_permission_id` #808 captures) |
| `to_permission_id` | String, nullable | state-driven permission **after** the transition |
| `transition_id` | String, nullable | the `LifecycleTransition` used |
| `lifecycle_map_id` | String, nullable | |
| `actor_user_id` | Integer, **nullable, NO FK** | actor; null/0 allowed (D1) |
| `comment` | Text, nullable | the now-persisted `promote(comment)` |
| `outcome` | String, NOT NULL, default `"success"` | reserved for future all-attempts (D2) |
| `properties` | JSON/JSONB, nullable | extensibility (e.g. selected `extra_data`) |
| `created_at` | DateTime, default utcnow, index | |

`from_permission_id`/`to_permission_id` capture the **state-driven permission change** the
transition causes (#808 already computes `old_permission_id` before the state change and sets
the new one from the target state) — so the audit row makes the permission move auditable
alongside the state move, at no extra cost.

Indexes: `item_id`, `created_at`, and composite `(item_id, created_at)` for the Slice-2 read.

## 4. Write semantics (Slice 1)

Replace the line-338 placeholder with a single call passing the `old_permission_id` that #808
already captures:

```python
def _record_transition_history(self, item, from_state, to_state, transition,
                               actor_user_id, comment, old_permission_id):
    if not getattr(get_settings(), "LIFECYCLE_TRANSITION_HISTORY_ENABLED", True):
        return
    try:
        self.session.add(LifecycleTransitionHistory(
            item_id=item.id,
            from_state_id=from_state.id, from_state_name=from_state.name,
            to_state_id=to_state.id, to_state_name=to_state.name,
            from_permission_id=old_permission_id, to_permission_id=item.permission_id,
            transition_id=transition.id, lifecycle_map_id=transition.lifecycle_map_id,
            actor_user_id=actor_user_id, comment=comment, outcome="success",
        ))
        self.session.flush()
    except Exception as exc:          # best-effort: never break the transition
        logger.warning("transition-history write failed for item %s: %s", item.id, exc)
```

There is exactly **one write site**. Placement guarantees only **successful** transitions are
recorded (it is after the three rollback returns). `flush()` (not `commit()`) keeps the row
pending for the caller's atomic commit; a failed *history* flush is swallowed + logged.

**Subtlety (must implement):** flush the already-applied *business* state **outside** this
best-effort guard first. `flush()` / `begin_nested()` flush the *whole* pending set, so a
business flush error done inside the `try` would be mislabeled "history write failed" and
swallowed — returning success on a rollback-pending session. Flush business state first (let
its error propagate), then `begin_nested()` scopes best-effort to the history row only.

## 5. Slices

- **Slice 1 (route-count-neutral):** model (in `lifecycle/models.py` — auto-registered via
  `import_all_models()`, no supplement) + Alembic migration (single head) + baseline regen + the
  `LIFECYCLE_TRANSITION_HISTORY_ENABLED` setting + the `_record_transition_history` write in
  `promote()` + tests. **Route count unchanged; only the Alembic head moves**, no route change.
- **Slice 2 (route +1):** `get_transition_history(item_id)` service + a read route
  (`GET /items/{item_id}/transition-history` or under the lifecycle router) + tests + bump the
  four route-count pin sites.

## 6. Risks / gotchas

- **Tenant-baseline drift-guard.** A new per-tenant model must be in the generator's ambient
  metadata: putting it on an `import_all_models()` path (`lifecycle/models.py`) needs no
  supplement; otherwise add it to the `tenant_schema` supplement. Either way regenerate the
  committed baseline + run the model test together with the in-process baseline guard before
  pushing (this exact ordering bit C3).
- **Default-on touches every `promote()` caller/test.** With a new model + a default-on write,
  existing promote() paths (`test_item_release_gate`, `test_assembly_promotion_service`, DB-
  gated lifecycle tests) now exercise the write and need the model in ambient metadata.
  Mitigated by **best-effort** (a write failure can't break them) + registering the model in
  `import_all_models()`. Slice 1 must re-run those suites green.
- **Route count:** Slice 1 unchanged; Slice 2 `+1` (bump the four pins).
- **Behavior change (intended):** `promote(comment=…)` now leaves a durable record.

## 7. Verification plan (Slice 1)

DB-free sqlite + the real `promote()` fixture (mirrors `test_item_release_gate`):
- successful transition → exactly one row with correct from/to/user/comment, `outcome="success"`;
- **best-effort** → a promote with a non-existent/`0` `user_id` still succeeds and commits (no
  FK to violate); a forced write failure (monkeypatched `session.add`/`flush` raising) is
  swallowed and the transition still succeeds;
- **disabled flag** → no row;
- **rolled-back transition** (on_enter abort) → **no** history row (written after the rollback
  returns);
- existing promote() suites stay green; the tenant-baseline drift-guard passes with the model
  registered.
