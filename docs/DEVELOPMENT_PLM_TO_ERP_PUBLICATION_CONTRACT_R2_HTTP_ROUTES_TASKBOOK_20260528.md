# Claude Taskbook: PLM→ERP Publication Contract — R2 HTTP Routes (Manual Outbox API)

Date: 2026-05-28

Type: **Doc-only taskbook (scope-lock for the routes slice).** It locks the
**thin** HTTP surface that exposes controlled entries to the merged R2 outbox
service (#668): five admin-gated manual routes, the manual *process-one*
semantics, the error→HTTP mapping, the concurrency handling, the route-count
delta, and the test acceptance items. It changes no code. **Merging this taskbook
does NOT authorize the routes implementation** — that requires its own explicit
opt-in.

Parents: `..._PLAN_20260527.md` (#663), `..._R1A_TASKBOOK_20260527.md` (#664),
the R1-B API (#665), `..._R2_ADAPTER_OUTBOX_TASKBOOK_20260528.md` (#666),
`..._R2_BUILD_TASKBOOK_20260528.md` (#667), and the merged R2 implementation
(#668, `392019ad`). Baseline `main = 392019ad`.

## 0. What this is (and is not)

R2's core service (`ErpPublicationOutboxService`) is on main but has **no API
surface** — nothing wires `enqueue`/`dry_run`/`process`/`replay`/status to HTTP.
This slice adds that surface and **only** that surface. It is deliberately
**thin**: routes expose controlled entries to the existing core service.

**Not in this slice (each a later, separately-opted slice):** no background
**worker daemon** (auto-poll-and-process), no real **ERP connector**, no
**`/publication/export`**. `process` here is **manual process-one** (an admin
sends/advances a single row on demand), not a daemon.

## 1. Grounding (against `main = 392019ad`)

The service behaviors the routes wrap + map (all in
`meta_engine/erp_publication/service.py`, as merged incl. the #668 review fixes):

- `enqueue(*, target_system, readiness, publication_kind, created_by_id)` →
  `Optional[ErpPublicationOutbox]`. Versionless verdict → returns **`None`** (no
  row, §6.4 of #667). Duplicate same-content → returns existing (idempotent).
  Duplicate changed-content vs a `sent` row → raises **`PublicationConflictError`**.
- `process(row, adapter, *, revalidate=None)` — **entry-state guard**: `state not
  in {pending, dry_run_ready}` raises **`PublicationReplayError`**
  ("cannot be processed directly"). `revalidate` (D-R2-1) flips ineligible →
  `skipped/not_eligible` (no send). `build_payload`/`validate_contract`/`send`
  exceptions are **folded** to `failed/adapter_error` and **returned, not raised**
  (`_fail_adapter_error`). `send` ok → `sent`.
- `replay(row, adapter, *, revalidate=None)` — `failed` retried **only** for
  `remote_error`/`adapter_error` (else `PublicationReplayError`); exhausted
  attempts → `PublicationReplayError`; `skipped` re-opened only when `revalidate`
  flips eligible; any other state → `PublicationReplayError`.
- `dry_run(row, adapter)` — `sent` → `PublicationReplayError`; `not_eligible`
  → no-op; never sends.
- Shared verdict builder: `web/plm_erp_publication_router.py::
  build_publication_readiness(db, item, item_id, *, ruleset_id, mbom_limit,
  routing_limit, baseline_limit)` — the routes reuse THIS for enqueue and for the
  `revalidate` callable (no re-derivation, #667 §8).
- R1-B auth pattern (`plm_erp_publication_router.py`): `user: CurrentUser =
  Depends(get_current_user)`, `db: Session = Depends(get_db)`, and
  `require_admin_permission(user)` as the first in-body statement. Router prefix
  `/plm-erp`, mounted with `prefix="/api/v1"` in `api/app.py`.
- Route-count pins currently `== 678`:
  `tests/test_phase4_search_closeout_contracts.py:155` and the cross-reference
  `tests/test_tier_b_3_breakage_design_loopback_portfolio_contract.py:240`.

## 2. Route surface (locked — exactly 5)

A new router (suggested `web/plm_erp_publication_outbox_router.py`, prefix
`/plm-erp`, mounted at `/api/v1`), all **admin-gated** (§4):

| # | method + path | calls | returns |
|---|---|---|---|
| 1 | `POST /plm-erp/items/{item_id}/publication-outbox/enqueue` | build readiness → `enqueue(...)` | the outbox row (or skipped-no-row, §6) |
| 2 | `POST /plm-erp/publication-outbox/{outbox_id}/dry-run` | `dry_run(row, NullAdapter)` | the row (→ `dry_run_ready`/`failed`) |
| 3 | `POST /plm-erp/publication-outbox/{outbox_id}/process` | `process(row, NullAdapter, revalidate=...)` | the row (→ `sent`/`skipped`/`failed`) |
| 4 | `POST /plm-erp/publication-outbox/{outbox_id}/replay` | `replay(row, NullAdapter, revalidate=...)` | the row |
| 5 | `GET /plm-erp/publication-outbox/{outbox_id}` | read | the row (status view) |

Enqueue (route 1) takes `target_system` (required), `publication_kind` (default
`"readiness"`), and the readiness params (`ruleset_id`, `mbom_limit`,
`routing_limit`, `baseline_limit`, R1-B defaults); it loads the `Item` (404 if
missing), builds the verdict via `build_publication_readiness`, then calls
`enqueue(..., created_by_id=user.id)`. A response model exposes the row
(`id`, identity tuple, `state`, `reason`, `attempt_count`, `dispatched_at`,
`payload_fingerprint`, and the `snapshot`); `GET` reuses it.

## 3. Manual process-one semantics (locked)

`process` (route 3) is a **manual, single-row** send: an admin advances one
`pending`/`dry_run_ready` row. This keeps the outbox from being a half-closed
loop (enqueue/dry-run only, never sends) **without** introducing a daemon. The
real background **worker** (auto-poll + batch process + retry/backoff scheduling)
is a **separate later slice**; nothing here loops or schedules.

In R2 the routes use the in-repo **`NullErpPublicationAdapter`** (no external
I/O); `sent` reached via a route is via the Null adapter only (#667 §4). A real
connector / adapter selection registry is a later slice.

## 4. Auth (locked)

Admin, **identical to R1-B**: `require_admin_permission(user)` as the first
in-body statement, with `user: CurrentUser = Depends(get_current_user)` and
`db: Session = Depends(get_db)`. These routes mutate the outbox, so they are at
least as privileged as the read-only R1-B readiness endpoint. (A dedicated
ERP-adapter principal is a later-connector concern, as #665 noted.)

## 5. revalidate reuses `build_publication_readiness` (locked)

Routes 3 (`process`) and 4 (`replay`) MUST construct the `revalidate` callable
from the shared `build_publication_readiness` (the #668 extraction), so the
`sent` transition re-validates against **current** PLM state using R1-B's
**exact** logic — never a copy (#667 §8). Concretely: load the backing `Item`,
read `ruleset_id`/`limits` from the row's snapshot, and pass
`revalidate=lambda: build_publication_readiness(db, item, item_id, ...)`. If the
backing item no longer exists, the route cannot safely revalidate → **409**
(see §6).

## 6. Error → HTTP mapping (locked)

| condition | HTTP |
|---|---|
| `PublicationConflictError` (enqueue: changed content vs a `sent` row) | **409** |
| `PublicationReplayError` (illegal state for process/dry-run/replay; non-retryable reason; attempts exhausted) | **409** |
| `outbox_id` not found (routes 2–5) | **404** |
| `item_id` not found (route 1; or backing item gone on process/replay revalidate) | **404** (enqueue) / **409** (process/replay revalidate — cannot send what cannot be revalidated) |
| request body / param invalid | **422** (FastAPI default) |
| readiness builder `ValueError` (e.g. unknown ruleset) on enqueue | **400** (chained, as R1-B) |
| **versionless** item on enqueue (service returns `None`) | **200** — body conveys `persisted: false`, `eligible: false`, `state: "skipped"`, `reason: "not_eligible"`; this is a valid "nothing to publish" outcome, **not** an error |
| any adapter exception during route 2/3/4 | **200** — the row is returned in `failed`/`adapter_error` (folded by the service); the route does **not** 500 |

## 7. Concurrency (locked)

The outbox now has a concurrent HTTP entry. Enqueue MUST handle the race where two
simultaneous first-enqueues of the same version-scoped key collide on the DB
`UNIQUE`: catch `IntegrityError` → **rollback → re-find the existing row → reuse
it** (the idempotent path), preserving "never conflict-fail" under concurrency
(the routeless service relied on serial calls; the route makes the race real).

## 8. Route-count (locked)

Five new routes → **`len(app.routes)` 678 → 683**. The implementation MUST:
1. run a **full-tree residual scan before moving any pin** —
   `grep -rn 'len(app.routes)'` across the whole tree and bump **every**
   occurrence pinned at 678, not only the two named below. Some pins are
   **DB-gated** (e.g. `test_breakage_design_loopback_metrics.py` ~`:344`, and the
   metrics-delta pin) and are **NOT collected by the local DB-off suite**, so a
   missed bump surfaces only under CI `regression` — grep the source, never rely
   on the local run to enumerate the pins;
2. bump the authoritative pin `test_phase4_search_closeout_contracts.py:155`
   `== 678` → `== 683` (ledger comment) and the cross-reference
   `test_tier_b_3_breakage_design_loopback_portfolio_contract.py` (the `_at_678`
   test + its literal) to 683 — **plus every other 678 pin the scan finds**;
3. register the new router in `api/app.py` (prefix `/api/v1`) and add the new
   test file to `conftest.py` `_ALLOWLIST_NO_DB` + the `ci.yml` contracts list.

(No route count is touched by this doc-only taskbook.)

## 9. Test acceptance items (locked — the two #668 guards continue at the route level)

The routes-impl test suite MUST include, at the **HTTP/route** level:

1. **Guard 1 — no illegal re-send:** `process` on a `skipped` or `sent` row →
   **409**, and the row is **not** mutated to `sent`. For `dry-run` the grounded
   service differs: `dry-run` on `sent` → **409**, but `dry-run` on a
   `skipped`/`not_eligible` row is a **200 no-op** (it returns the row unchanged
   — dry-run has no `send` to guard). Do **not** write the acceptance test as
   "dry-run on skipped → 409"; that contradicts the merged service.
2. **Guard 2 — adapter exceptions fold:** an adapter `build`/`validate`/`send`
   exception during a route call leaves the row `failed`/`adapter_error` and the
   route returns **200** with that row — never a 500 / never bubbling.

Plus: enqueue creates a `pending` row; enqueue idempotent reuse (one row);
enqueue changed-content vs `sent` → 409; `process` happy path → `sent` via the
Null adapter; `process` with revalidate-flips-ineligible → `skipped` (no send);
`replay` retries a `remote_error`/`adapter_error` failure → `sent`; `replay`
non-retryable → 409; versionless enqueue → 200 + `persisted:false`; `GET` status;
`revalidate` demonstrably reuses `build_publication_readiness`; and the
route-count is exactly **683**.

**Testing note (avoid the recurring traps):** route tests drive
`build_publication_readiness` → `ReleaseReadinessService` + the latest-released /
suspended guards + the auth middleware, so they need (a) R1-B's mocking pattern
for the service + guards, and (b) the `AUTH_MODE=optional` fixture — a surgical
`monkeypatch.setattr` of the middleware's `get_settings` (per the R1-B test) —
otherwise every request 401s (the auth-enforce trap that bit R1-B and the
`release_readiness` suite). Add the new test file to `conftest.py`
`_ALLOWLIST_NO_DB` so it is not silently un-collected when the DB is off.

## 10. Non-Goals

No worker daemon (auto-poll/schedule — separate later slice); no real ERP
connector (Null adapter only); no `/publication/export`; no adapter-selection
registry; no change to the R2 core service contract (#668) beyond the
concurrency catch in the route layer.

## 11. Preconditions to enter the routes IMPLEMENTATION

1. §2 route surface (the 5) ratified;
2. §3 manual process-one (no daemon) ratified;
3. §5 revalidate-reuses-builder ratified;
4. §6 error→HTTP mapping ratified (incl. versionless→200 and adapter-exception→200);
5. §7 concurrency (IntegrityError→rollback→re-find→reuse) ratified;
6. §8 route-count 678→683 + residual-scan discipline ratified;
7. §9 acceptance items (the two guards at route level) ratified.

A **separate explicit opt-in** then authorizes the implementation.

## 12. Reviewer Focus

1. §2/§3 — the 5 routes, and `process` is manual-one (no daemon)?
2. §5 — revalidate reuses `build_publication_readiness`, Null adapter only?
3. §6 — error map: Conflict/Replay→409, not-found→404, param→422, versionless→200,
   adapter-exception→200 (not 500)?
4. §7 — concurrent enqueue → reuse-row, never conflict-fail?
5. §8 — 678→683 with a full-tree residual scan first?
6. §9 — both #668 guards retested at the route level?
7. §10 — worker/connector/export stay out?

## 13. Status

Doc-only scope-lock. Ready for review once the doc exists at the canonical path;
`DELIVERY_DOC_INDEX.md` references it + its DEV/verification record (sorted under
`## Development & Verification`); doc-index / sorting / completeness checks pass;
`git diff --check` clean. Ratifying §2–§9 sets the routes implementation plan;
**a separate explicit opt-in authorizes the implementation.** The background
worker, the real ERP connector, and `/publication/export` remain later,
separately-opted slices.
