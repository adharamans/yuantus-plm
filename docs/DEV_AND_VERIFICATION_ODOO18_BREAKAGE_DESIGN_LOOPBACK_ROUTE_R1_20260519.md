# Odoo18 Breakage Design-Loopback ECO Route R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement §3.1 of the breakage state-machine integration remainder
taskbook (`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_STATE_MACHINE_INTEGRATION_20260518.md`,
merged 2026-05-19 as `7fce255` / PR #601). Adds a single HTTP
endpoint that exposes the merged
`BreakageIncidentService.create_breakage_design_loopback_eco`
service method (shipped by #596 `6e4ce54`) to authenticated
callers.

R1 is the thinnest viable HTTP seam over a fully-tested service
method. No new service logic; no schema; no durable idempotency
(that's §3.2). The route inherits the service method's
best-effort query-before-create dedupe, so a 200 +
`created=False` response from a duplicate call reflects an
existing-ECO match against the closeout reference envelope.

## 2. Scope

### Modified

- `src/yuantus/meta_engine/web/parallel_tasks_breakage_router.py`
  — added `BreakageDesignLoopbackEcoCreateRequest` model + new
  `POST /breakages/{incident_id}/design-loopback/eco` route handler
  (`create_breakage_design_loopback_eco`).
- `src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py`
  — extended `_BREAKAGE_ROUTE_KEYS` with the new endpoint so
  the route-registration contract test pins it.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py`
  — 9 behavioral tests via FastAPI `TestClient`, mocking the
  service method directly.
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_ROUTE_R1_20260519.md`

### Unchanged by design

- `BreakageIncidentService` (the service method shipped in #596
  is reused verbatim).
- `ECOService.create_eco` (the route never calls it directly;
  AST-pinned).
- `BreakageIncident` model, schema, migrations, tenant baselines.
- All other breakage routes and helpdesk-sync endpoints.
- `.claude/` and `local-dev-env/` stay out of git.

## 3. Contract

### Endpoint

`POST /breakages/{incident_id}/design-loopback/eco`

### Request

```python
class BreakageDesignLoopbackEcoCreateRequest(BaseModel):
    allow_duplicate: bool = Field(default=False)
```

Empty body `{}` is accepted (`allow_duplicate` defaults to
`False`). A non-bool value yields the standard FastAPI 422.

### Response — 200 success

```json
{
  "incident_id": "<str>",
  "eco_id":      "<str>",
  "reference":   "<str — closeout reference hash>",
  "created":     true | false,
  "operator_id": <int — auth'd user.id>
}
```

`created=False` indicates a dedupe hit: the merged service
method's `_find_breakage_design_loopback_eco_by_reference` found
an existing ECO whose `description` carries the same
`reference=<hash>` envelope and the route returned that ECO
without calling `ECOService.create_eco`. **The HTTP status is
still 200** — see §3 Reviewer call below.

### Error mapping

- **`ValueError("Breakage incident not found: <id>")`** (the
  service method's not-found shape, raised at
  `parallel_tasks_service.py:4220–4222`):
  - **404**, code `breakage_not_found`, context `{"incident_id":
    <id>}`.
- **`ValueError("breakage status <s> is not eligible ...")`**
  (the service method's eligibility shape, raised when
  `prepare_breakage_design_loopback_intake` returns
  `eligible=False`):
  - **409**, code `breakage_not_eligible_for_loopback`, context
    `{"incident_id": <id>}`.
- **`ECOService.create_eco` permission failure** — propagated
  without translation; FastAPI's existing exception handler
  surfaces the same response shape the ECO routes use.
  Rationale: inventing a breakage-route-local mapping would
  diverge from ECO routes and create inconsistent permission
  error semantics for the same underlying operation.

### Transaction boundary

The route owns the commit. Two exception paths handle rollback:

- **`except ValueError`** → `db.rollback()`, then `_raise_api_error`
  translates to the 404 / 409 codes above.
- **`except Exception`** → `db.rollback()`, then `raise` re-raises
  the original exception so FastAPI surfaces it verbatim (e.g.,
  `HTTPException(status_code=403)` from
  `ECOService.create_eco`'s permission check stays a 403; the
  route does NOT translate it into a breakage-local 4xx code,
  which would diverge from how ECO routes surface the same
  permission failures).

Defense-in-depth note: today `ECOService.create_eco` performs
the permission check at `eco_service.py:520` **before** any
`session.add` / `session.flush` (`eco_service.py:534–535`), so
a permission failure leaves the session unchanged and the
rollback is structurally redundant for the current implementation.
The unconditional rollback on the non-`ValueError` path is
defensive against a hypothetical future reorder (permission-check-
after-flush) that would otherwise leak partial state through
this route. A pinned test
(`test_db_rollback_on_non_value_error_propagated`) verifies the
behavior.

### Permission

R1 inherits whatever capability `ECOService.create_eco`
requires today — no dedicated `breakage:design_loopback:spawn`
capability is added. Per taskbook §3.5 (author-recommended
reject), a separate capability would be its own future opt-in.

### Audit

R1 does **not** add structured logs. The existing
`parallel_tasks_breakage_router.py` has no `logging.getLogger`
usage anywhere; introducing a logger for one new handler would
diverge from the file's style. The audit-trail substrate is
already in place via:

- The `ECO.description` carrier with the
  `breakage-eco-closeout` envelope + `reference=<hash>`
  (queryable post-merge).
- The merged service method's
  `BreakageDesignLoopbackEcoCreation.created` boolean (echoed
  in the route response).

Structured logging + metrics are §3.7 (observability slice;
separate opt-in).

### Reviewer call: dedupe-hit response status

Taskbook §3.1 ratified **200 + `created=false`** as the dedupe-
hit response (author recommendation). Alternative considered:
409 + `{"existing_eco_id": ...}`. Rationale for 200:

- The caller's intent ("get me the ECO for this breakage") is
  satisfied either way.
- The route is idempotent by design — a second call with the
  same effective input should not change the result OR the
  status code.
- 409 is reserved for genuine semantic errors (the
  `breakage_not_eligible_for_loopback` case above) so the
  caller can distinguish "your request is malformed/forbidden"
  from "your request succeeded, here's the ECO you asked for".

If the reviewer prefers 409 for dedupe hits, the change is
local to the route handler — service method semantics stay
unchanged.

## 4. Test Matrix

`src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py`
— 9 tests via FastAPI `TestClient` with the service method
mocked (the service method itself is covered by
`test_breakage_design_loopback_eco_creation_wiring.py`):

- **200 + `created=True`** for a new ECO: response shape
  correct, service called with `incident_id` positional and
  `user_id=42, allow_duplicate=False` kwargs.
- **200 + `created=False`** for a dedupe hit.
- **404** when the service raises `ValueError("Breakage
  incident not found: ...")`; response detail has code
  `breakage_not_found` and `context.incident_id`.
- **409** when the service raises `ValueError("breakage status
  ... is not eligible ...")`; response detail has code
  `breakage_not_eligible_for_loopback`.
- **`allow_duplicate=True` forwards verbatim** to the service
  method.
- **`allow_duplicate` defaults to False** when the request body
  is empty `{}` (not 422).
- **`db.commit()` on success / `db.rollback()` on `ValueError`** —
  pinned via mock spy.
- **AST guard**: route handler's source contains neither
  `ECOService(...)` instantiation nor `*.create_eco(...)` call;
  delegation goes through
  `BreakageIncidentService.create_breakage_design_loopback_eco`
  exclusively. AST `Call`-walk catches a swapped variable name
  that a brittle string check would miss.

`src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py`
extension:

- `_BREAKAGE_ROUTE_KEYS` now contains
  `("POST", "/breakages/{incident_id}/design-loopback/eco")` so
  the existing
  `test_breakage_routes_owned_by_split_router` and
  `test_create_app_registers_breakage_routes_once` cover the
  new endpoint without code duplication.

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) stays green.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
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
  src/yuantus/meta_engine/web/parallel_tasks_breakage_router.py
git diff --check
```

No alembic / tenant-baseline — the route adds no schema.

Observed as of 2026-05-19: 95 tests passed across all
breakage-touching + doc-index/portfolio files; new route file
9/9 passed; `py_compile` clean; `git diff --check` clean.

## 6. Non-Goals (reaffirmed from taskbook #601 §3.1)

- No durable idempotency — the route inherits the merged
  service method's best-effort substring scan. §3.2 is the
  follow-up.
- No new permission capability (§3.5; recommended-reject).
- No event emission (§3.6).
- No metrics/logging (§3.7); existing breakage router has no
  logger; this PR matches the file style.
- No UI affordance (out of contracts scope).
- No edit to `BreakageIncidentService.create_breakage_design_loopback_eco`.
- No edit to `ECOService.create_eco` or any router beyond the
  one new handler in `parallel_tasks_breakage_router.py`.

## 7. Follow-ups (each its own separate opt-in — per taskbook #601)

- **§3.2 durable idempotency** — `BreakageIncident.eco_id` FK +
  UNIQUE constraint (author-recommended 2a) or alternative.
  Race-safe; unlocks §3.3/§3.4 auto-triggers.
- **§3.3 `update_status` auto-trigger** — depends on §3.2.
- **§3.4 helpdesk-sync auto-trigger** — depends on §3.2 + §3.3.
- **§3.5 dedicated RBAC capability** — recommended-reject;
  open for ratification.
- **§3.6 domain event emission** — reuses existing
  `enqueue_event` infra.
- **§3.7 observability** — reuses existing
  `ParallelOpsService.prometheus_metrics` infra; source-data
  choice depends on §3.2 alt ratified.

## 8. Tier-B sequencing

Per the owner's 2026-05-18 serialization rule (only ONE Tier-B
runtime follow-up in flight at a time), this PR is the in-flight
item. After merge, the next candidate from the taskbook §3
landing order is §3.2 (durable idempotency) — schema-level slice
that unlocks §3.3/§3.4 race-safe auto-triggers.
