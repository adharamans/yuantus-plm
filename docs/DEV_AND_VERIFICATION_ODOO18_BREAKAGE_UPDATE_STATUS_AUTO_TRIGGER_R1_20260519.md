# Odoo18 Breakage `update_status` Auto-Trigger R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement Tier-B #3 §3.3 per the ratified taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_UPDATE_STATUS_AUTO_TRIGGER_20260519.md`,
merged 2026-05-19 as `fb9d0b5` / PR #605). Add a **default-OFF**
opt-in so a `BreakageIncidentService.update_status(...)`
transition to a loopback-eligible status (`resolved` / `closed`)
automatically spawns (or reuses, via the §3.2 durable path) the
design-loopback ECO in the same transaction.

This is the first slice that makes a *state transition* trigger a
side effect. The central engineering problem — how the
auto-trigger composes with §3.2's CAS-loss `session.rollback()`
when both share the status-change transaction — is resolved by
the §3.C ratified single deterministic behavior (see §3.3 below).

## 2. Scope

### Modified

- `src/yuantus/meta_engine/services/parallel_tasks_service.py` —
  new module-level exception `BreakageDesignLoopbackLinkRace`
  (`RuntimeError`, NOT `ValueError`); `update_status` gains
  `auto_loopback: bool = False` + `loopback_user_id:
  Optional[int] = None`, the post-update eligibility gate, the
  create call, and the §3.C self-heal / unrecoverable branch.
- `src/yuantus/meta_engine/web/parallel_tasks_breakage_router.py`
  — imports `PLMException` + `BreakageDesignLoopbackLinkRace`;
  `BreakageStatusUpdateRequest` gains `auto_loopback: bool =
  False`; `update_breakage_status` forwards the kwargs and gains
  the 409 / verbatim-propagation exception clauses.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py`
  (7 MANDATORY + 3 route/defensive tests).
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_UPDATE_STATUS_AUTO_TRIGGER_R1_20260519.md`

### Unchanged by design

`create_breakage_design_loopback_eco`, §3.2's CAS / its
`session.rollback()`, all merged contracts, every other route
(no new route — `len(app.routes)` stays 677), no
schema/alembic/tenant-baseline, `update_status`'s return type,
`.claude/`, `local-dev-env/`.

## 3. Implementation

### 3.1 §3.A — trigger point (product policy A1, no delta gating)

`update_status` already reads the incident (`session.get`,
`parallel_tasks_service.py`) before mutating it, so the prior
status *is* available. A1 is a deliberate **product-policy**
choice to NOT gate on the old→new delta, not a technical
limitation. After the status flush, the loopback fires iff the
**new** status is eligible —
`is_breakage_eligible_for_design_loopback` evaluated on the
**post-update descriptor** built from the in-hand (post-flush)
incident via `_breakage_design_loopback_row`. §3.2's durable
idempotency makes a redundant fire safe (a `resolved → resolved`
no-op returns `created=False`, no duplicate).

### 3.2 §3.B — switch + signature (default-OFF byte-identical)

```python
def update_status(self, incident_id, *, status,
                   auto_loopback=False, loopback_user_id=None):
```

`auto_loopback=False` returns immediately after the original
two-line status flush — **no eligibility check, no descriptor
build, no create call, no extra query, no `eco_id` write**.
Byte-identical pre-§3.3 behavior, pinned by
`test_update_status_default_off_is_byte_identical` (spies on
`create_breakage_design_loopback_eco`,
`is_breakage_eligible_for_design_loopback`, and
`resolve_breakage_eco_closure_descriptor` all assert
not-called). The route maps `int(user.id) → loopback_user_id`;
`BreakageStatusUpdateRequest.auto_loopback` defaults `False`.

`loopback_user_id is None` while `auto_loopback=True` AND the
new status is eligible raises a `ValueError` (a direct-caller
contract guard; the route always supplies `int(user.id)` so it
is unreachable via HTTP). Placed AFTER the eligibility gate so
an ineligible `auto_loopback=True` call remains a valid no-op
(§3.E) regardless of `loopback_user_id`.

### 3.3 §3.C — the §3.2-rollback composition (RATIFIED, single deterministic behavior)

The route owns the single `db.commit()`. With
`auto_loopback=True`, `update_status` flushes the status change
and then calls `create_breakage_design_loopback_eco` **in the
same session/transaction**. §3.2's CAS-loser path calls a *full*
`self.session.rollback()` — which, composed here, also unwinds
the status flush. The ratified single behavior:

1. **`created=True` (CAS winner)** — no rollback occurred; the
   status flush is intact; return the incident.
2. **`created=False`** — either §3.2's durable-dedupe early
   return (no rollback; status flush intact) OR the CAS-loser
   path (full rollback; status flush unwound). The service
   **re-reads the incident and re-applies the target status +
   `updated_at`, flushes, and returns an ordinary success** —
   **no client-visible retry for normal concurrency**. This is
   idempotent in the no-rollback sub-case (harmless re-apply)
   and the real repair in the CAS-loser sub-case (the loser's
   own intended status is restored on top of the winner's
   committed link).
3. **Unrecoverable only** — if the post-rollback re-read finds
   **no incident** or **no winner ECO** (`creation.eco is
   None`), raise `BreakageDesignLoopbackLinkRace`. The route
   maps it to a retryable **409 `breakage_loopback_link_race`**.

The unified unconditional re-apply on `created=False` is the
simplest faithful reading of §3.C step 1 (which explicitly says
"re-applies the target status + `updated_at`") and is robust in
*all* sub-cases without a fragile "did a rollback happen?"
detection. Consequence: the no-rollback dedupe sub-case does one
extra cosmetic `UPDATE`/`updated_at` bump — accepted; the
`..._repeat_is_idempotent` test therefore pins only the contract
guarantees (exactly one ECO, same ECO, status stable), NOT
update-count or `updated_at` invariance.

**C2 (savepoint) remains BLOCKED** — §3.2's bare
`self.session.rollback()` unwinds any `begin_nested()` savepoint;
a true-savepoint C2 needs a merged-§3.2 refactor, a separate
opt-in (taskbook §8 Non-Goals). C3 (two-phase commit) rejected
(breaks the route's single-commit shape).

### 3.4 §3.D — repeat-trigger (via §3.2)

No new dedupe logic. A repeat
`update_status(resolved, auto_loopback=True)` on an
already-linked incident hits §3.2's durable `eco_id` lookup →
`created=False`, no duplicate. §3.3 is the *trigger*; §3.2 is
the *idempotent effect*.

### 3.5 §3.E — error handling + the route exception-ordering refinement

- **Incident not found** → existing `ValueError` → route 404
  `breakage_not_found` (unchanged).
- **Ineligible new status** + `auto_loopback=True` → gated
  BEFORE the create call; a valid **no-op** (status changes, no
  loopback). `create_…eco` is never called-and-swallowed.
- **Eligible + `create_…eco` raises** (ECO permission failure)
  → propagates; the route rolls back (status change too) and
  re-raises verbatim. **Intended atomic coupling**: opting into
  `auto_loopback` means an ECO-create failure blocks the status
  change.
- **CAS race** → §3.3 behavior above (normal → ordinary
  success; unrecoverable → 409).

Route clause order (specific → general), in
`update_breakage_status`:

```
except BreakageDesignLoopbackLinkRace → 409 breakage_loopback_link_race
except ValueError                     → 404 breakage_not_found      (unchanged)
except (HTTPException, PLMException)   → db.rollback(); raise        (verbatim)
except Exception                      → 400 breakage_status_invalid (unchanged)
```

`_raise_api_error` raises a fresh `HTTPException` from inside a
clause; that propagates OUT of the `try` (Python does not
re-evaluate sibling `except` clauses for an exception raised
inside one), so the 404/409/400 mappings are never re-caught by
the `(HTTPException, PLMException)` clause. `PLMException`
(handlers.py) is an `Exception`, NOT a `ValueError`;
`PermissionError` subclasses it, so catching the base is correct
and the `ValueError → 404` clause never swallows a permission
failure. `BreakageDesignLoopbackLinkRace` is a `RuntimeError`,
NOT a `ValueError`, so only its dedicated clause catches it.

**Verbatim propagation — explicit §3.1-mirroring callout (no
new gap).** There is **no app-wide `PLMException`→HTTP exception
handler anywhere in this codebase** (verified by a repo-wide
`add_exception_handler` / `@app.exception_handler` search —
zero hits outside tests). "Propagate verbatim" therefore means
exactly what §3.1's identical pattern means:

- carrier `HTTPException` → its status code surfaces verbatim
  (FastAPI's native handling);
- carrier raw `PLMException` / `PermissionError` → propagates
  with NO global handler → Starlette default (→ 500).

This is **intentional inheritance** of §3.1's behavior, not a
new defect introduced by §3.3. The taskbook (§8 Non-Goals)
explicitly forbids editing §3.1 / merged contracts, and the
§3.1 route test only ever exercised an `HTTPException` stand-in.
The §3.3 hard requirement (§10) is solely "**NOT collapsed into
the legacy 400 `breakage_status_invalid`**" + rollback — both
pinned. Widening to a global `PLMException`→403 handler is a
separate, app-wide opt-in, out of §3.3 scope.

### 3.6 §3.F — relationship to §3.2 `created=False`

`update_status`'s return type is unchanged (still the
`BreakageIncident`). The loopback result is a side effect;
surfacing it to callers is a §3.6 (event) / future-return-shape
concern, out of §3.3 scope.

## 4. Test Matrix

`test_breakage_update_status_auto_trigger.py` — 10 tests
(StaticPool shared in-memory engine, mirroring §3.2's CAS
harness):

- **`test_update_status_default_off_is_byte_identical`**
  (MANDATORY) — `auto_loopback` omitted AND explicit-False:
  create / eligibility / descriptor spies all assert
  not-called; `eco_id` stays NULL; no ECO.
- **`test_update_status_auto_loopback_on_eligible_status_spawns_link`**
  (MANDATORY) — `resolved` + `auto_loopback=True`: `eco_id`
  linked, one ECO, one commit.
- **`test_update_status_auto_loopback_skips_ineligible_status`**
  (MANDATORY) — `in_progress` + `auto_loopback=True`: status
  changes, `create_…eco` spy not called, no ECO.
- **`test_update_status_auto_loopback_repeat_is_idempotent`**
  (MANDATORY) — two sequential eligible auto-triggers: same
  ECO, exactly one ECO, status stable (asserts contract
  guarantees only, per §3.3).
- **`test_update_status_auto_loopback_cas_race_self_heals_status`**
  (MANDATORY) — §3.C normal arm. Winner sets `closed`+links
  ECO_W+commits; loser (dedupe forced to miss) sets `resolved`,
  loses the CAS → §3.2 rollback → §3.3 self-heal. Probe: status
  `resolved` (loser target re-applied, NOT winner's `closed`),
  `eco_id == ECO_W`, exactly one ECO, **no exception** (no
  try/except — propagation is the pin).
- **`test_update_status_auto_loopback_unrecoverable_race_maps_409`**
  (MANDATORY) — §3.C unrecoverable arm. Taskbook-sanctioned
  forcing: a side_effect that does the real `session.rollback()`
  then returns `created=False, eco=None`. Service raises
  `BreakageDesignLoopbackLinkRace`; route maps → 409
  `breakage_loopback_link_race` (rollback called, commit not).
- **`test_update_status_auto_loopback_eco_permission_failure_rolls_back_status`**
  (MANDATORY) — real-engine route; `create_…eco` denied with
  `PermissionError`. The status change rolls back end-to-end
  (probe: status still `open`, no ECO) and the error propagates
  (it RAISES — not collapsed into a 400 JSON response).
- **`test_route_status_update_auto_loopback_defaults_false_and_forwards`**
  — omitted body field → service receives `auto_loopback=False`;
  explicit `True` forwarded; `loopback_user_id == int(user.id)`.
- **`test_route_eco_permission_failure_propagates_verbatim_not_400`**
  — carrier 1 `HTTPException(403)` → 403 verbatim, code ≠
  `breakage_status_invalid`; carrier 2 real `PermissionError`
  → re-raised (the discriminator: a 400 collapse would return
  a JSON response, not raise), rollback called.
- **`test_route_default_off_flush_error_still_maps_legacy_400`**
  — defensive byte-identical guard: a generic error on the
  default-OFF path still maps to the pre-§3.3 `400
  breakage_status_invalid` (the new clause did not steal it).

Regression unchanged & green: §3.2 durable idempotency,
eco-creation-wiring, runtime-wiring, design-loopback route,
router contracts, phase-4 route-count pin (677, no new route),
doc-index trio, R2 portfolio.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
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

No alembic / tenant-baseline — §3.3 adds no schema (§3.2's
`eco_id` is the durable substrate; §3.3 only triggers it).

Observed 2026-05-19: §3.3 10/10; full combined suite green;
`py_compile` clean; `git diff --check` clean; `len(app.routes)`
unchanged at 677 (no new route).

## 6. Non-Goals (reaffirmed from taskbook §8)

- No edit to `create_breakage_design_loopback_eco`, §3.2's CAS
  or its `session.rollback()`, or any merged contract.
- No new route; no schema/alembic/tenant-baseline.
- No helpdesk-sync auto-trigger (§3.4 — reuses §3.3's path
  through a different entrypoint; separate opt-in).
- No event emission (§3.6) / metrics (§3.7).
- No change to `update_status`'s return type.
- No default-ON. `auto_loopback` defaults `False`; flipping the
  default is a separate explicit opt-in.
- No app-wide `PLMException`→HTTP handler (a separate app-wide
  opt-in; §3.3 intentionally inherits §3.1's verbatim behavior).
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Inter-slice status

- §3.1 route / §3.2 durable idempotency: merged (`a02dbd0` /
  `2609bba`); unchanged by §3.3.
- §3.3 `update_status` auto-trigger: delivered (this slice).
- §3.4 helpdesk-sync auto-trigger: **now unblocked** — it
  reuses §3.3's eligibility-gate + create + self-heal pattern
  through a different entrypoint; still its own separate opt-in.
- §3.6 event emission (could carry the loopback `created`
  result), §3.7 metrics, default-ON flip: each its own future
  opt-in.
