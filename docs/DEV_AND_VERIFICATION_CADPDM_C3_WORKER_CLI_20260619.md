# DEV & VERIFICATION — CAD-PDM C3 date-obsolete worker CLI (operability slice)

Date: 2026-06-19 · Branch `claude/cadpdm-c3-worker-cli` · base `origin/main` (post-transfer
`adharamans/yuantus-plm`).

## 1. Summary

CAD-PDM C3 (date-BOM auto-obsolete) shipped feature-complete behind a default-off flag:
the mechanism (`DateEffectivityObsoleteService`, #797) and the gated worker class
(`DateObsoleteWorker`, #798) plus admin impact-ops routes. The worker was, however,
**library-only** — a deployment had no first-class way to *run* it; an operator had to
write their own process to call `run_once()` / `run_forever()`.

This slice adds the missing operability layer **and nothing else**: a CLI subcommand
`yuantus date-obsolete-worker` that runs the existing worker as a one-shot (`--once`) or a
daemon loop, plus this runbook. It does **not** change the obsolete semantics, does **not**
add or change any HTTP route, and does **not** touch BOM-line (`item_id`-scoped)
effectivity — those remain the documented C3 follow-ups.

Value: moves C3 from "feature-complete" to "production-operable".

## 2. What changed

- `src/yuantus/cli.py` — new `@app.command(name="date-obsolete-worker")`, mirroring the
  existing `ecm-publication-worker` / `publication-worker` commands (same typer app, same
  `--once` vs `run_forever` shape, same Ctrl+C-is-a-clean-stop handling).
- `src/yuantus/meta_engine/tests/test_date_obsolete_worker_cli.py` — DB-free CLI test
  (run_once/run_forever monkeypatched), mirroring `test_ecm_publication_worker_cli.py`,
  registered in both `.github/workflows/ci.yml` (contracts list, alphabetical) and
  `conftest.py` `_ALLOWLIST_NO_DB`.
- `docs/DEV_AND_VERIFICATION_CADPDM_C3_WORKER_CLI_20260619.md` — this document, indexed in
  `docs/DELIVERY_DOC_INDEX.md` under `## Development & Verification`.

No new model, migration, route, or setting. Route count stays **719**; Alembic head stays
`c3_date_obsolete_001`.

## 3. Design

### 3.1 Command surface

`yuantus date-obsolete-worker [OPTIONS]`

| Option | Meaning |
|---|---|
| `--once` | Run one sweep then exit (else: daemon loop until Ctrl+C). |
| `--worker-id TEXT` | Worker id for logs (default `cadpdm-date-obsolete-worker-1`). |
| `--poll-interval INT` | Daemon poll interval seconds (default from settings). |
| `--system-user-id INT` | User id recorded for the lifecycle promote (default from settings). |
| `--tenant TEXT` / `--org TEXT` | Request context — see §3.3 (tenant scope). |

The command constructs `DateObsoleteWorker(...)` and calls `run_once()` (then reports the
processed count) or `run_forever()`. It owns no business logic; the sweep, gating, and
idempotency all live in the worker/service already on `main`.

### 3.2 The two gates are preserved, not re-implemented

The worker is **default-OFF** and **double-gated**, and the CLI does not weaken that:

1. **Global kill-switch** `DATE_EFFECTIVITY_OBSOLETE_ENABLED` (restart-only — read once at
   startup). When off, `run_once()` / `run_forever()` are no-ops; the CLI surfaces a clear
   `Note:` to stderr so an operator who sees "Processed 0" understands it is *disabled*, not
   *idle*. Warn-and-proceed (parity with the worker's own no-op design).
2. **Per-tenant entitlement** `cadpdm_date_obsolete` (SKU `plm.cadpdm_date_obsolete`),
   checked on the session's tenant inside the worker. The CLI does not bypass it.

A second operator `Note:` fires when the effective system-user id is `0`: the lifecycle
promote will record `child_obsolete_failed` and only write parent flags, so the operator
knows to configure a real service user before expecting Items to actually obsolete.

### 3.3 Tenant scope (important — differs from the publication workers)

Unlike `ecm-publication-worker` (tenant-agnostic; drains a global outbox), the C3 sweep is
**per-tenant**: `get_db_session()` runs under the context tenant's scope (its schema in
`schema-per-tenant`, its database in `db-per-tenant`/`db-per-tenant-org`) and the
entitlement check is for that tenant. Therefore:

- In `TENANCY_MODE=single` (default), `--tenant` is not required.
- In any multi-tenant mode, `--tenant` is **load-bearing**: run one invocation per tenant.
  In `db-per-tenant-org` the session is scoped by tenant **and** org, so `--org` is required
  too. The CLI fails fast with a clear message and **exit code 2** when a required `--tenant`
  (any multi-tenant mode) or `--org` (`db-per-tenant-org`) is omitted, rather than leaking
  the opaque session-layer `RuntimeError` (which on the `--once` path surfaces as a raw
  traceback; `run_forever` would instead log-and-retry it each tick).

## 4. Operations runbook

### 4.1 Enable

1. Grant the tenant the SKU (offline license, no network):
   `yuantus license import /path/to/signed-license.json`
   (the license must carry app `plm.cadpdm_date_obsolete`, status Active, unexpired).
2. Set the global kill-switch and (recommended) a real service user, then restart the
   process (the flag is read once at startup):
   `DATE_EFFECTIVITY_OBSOLETE_ENABLED=true`
   `DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID=<service user id>`
   Optional: `DATE_EFFECTIVITY_OBSOLETE_POLL_INTERVAL_SECONDS` (default 300),
   `DATE_EFFECTIVITY_OBSOLETE_BATCH_SIZE` (default 100 — a *backlog warning* threshold, not
   a correctness cap; each tick drains the whole expired set idempotently).

### 4.2 Run

- One sweep (cron / manual / single-tenant): `yuantus date-obsolete-worker --once`
- Daemon: `yuantus date-obsolete-worker` (Ctrl+C stops cleanly)
- Multi-tenant: one invocation per tenant, e.g.
  `yuantus date-obsolete-worker --once --tenant <tenant-id>`
  (in `db-per-tenant-org` mode also pass `--org <org-id>`)

### 4.3 Stop

- Daemon: SIGINT (Ctrl+C) — the worker calls `stop()` and exits 0.
- Global disable: set `DATE_EFFECTIVITY_OBSOLETE_ENABLED=false` and restart; or revoke the
  tenant entitlement. Either gate alone makes the sweep a no-op.

### 4.4 System user

The lifecycle promote to `Obsolete` is recorded against `--system-user-id` (or
`DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID`). It must be a real (ideally service) user or the
promote records `child_obsolete_failed` and only the depth-1 parent flags are written (the
CLI warns when it is 0).

### 4.5 View impact (what the sweep flagged / obsoleted)

The existing admin-gated ops routes (unchanged by this slice) expose the impact ledger:

- List: `GET /api/v1/cadpdm/date-obsolete-impacts` (optional `?state=open|acknowledged`)
- Get one: `GET /api/v1/cadpdm/date-obsolete-impacts/{id}` (404 if absent)
- Acknowledge: `POST /api/v1/cadpdm/date-obsolete-impacts/{id}/acknowledge`

Each row records `child_item_id`, `parent_item_id`, `child_obsoleted`, and a `reason`
(`child_obsoleted` / `child_obsolete_failed` / `child_effectivity_expired`).

## 5. Verification

Harness: `.venv-wp13` (py3.11), `PYTHONPATH=<worktree>/src`, env DB vars unset.

- `test_date_obsolete_worker_cli.py` (10 tests, DB-free) — source contract (command
  registered + wired to `DateObsoleteWorker` + `--tenant`/`--org`/`--system-user-id`/once);
  `--once` reports the processed count; daemon mode uses `run_forever`; worker-id default +
  passthrough; `--tenant`/`--org` context is actually applied and `--system-user-id` /
  `--poll-interval` reach the worker; Ctrl+C is a clean stop; the disabled-kill-switch and
  system-user-0 hints fire; the multi-tenant guard exits 2 without `--tenant`, and
  `db-per-tenant-org` additionally exits 2 without `--org`.
- Regression (ran together, one process): `test_date_effectivity_obsolete_service.py`,
  `test_date_obsolete_wiring.py` (mechanism + gating + ops routes unchanged),
  `test_metrics_router_route_count_delta.py` (**719**, unchanged),
  `test_ci_contracts_ci_yml_test_list_order.py` + `test_ci_contracts_job_wiring.py`
  (registration valid), plus the DELIVERY_DOC_INDEX contracts (completeness + sorting +
  references).

Result at authoring: all green; route count 719; Alembic head `c3_date_obsolete_001`.

## 6. Out of scope (still C3 follow-ups)

- BOM-line (`item_id`-scoped) effectivities — the sweep remains version-scoped.
- The pre-existing `find_effective_version` NULL-start / no-effectivity narrowness (C3
  computes effectiveness locally to avoid it; the shared helper is a separate slice).
- No change to obsolete semantics, lifecycle map, or any HTTP route.
