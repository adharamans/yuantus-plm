# ECM Publish — Worker CLI/Daemon Entrypoint (Dev & Verification)

Date: 2026-06-16
Branch: `feat/ecm-worker-cli-entrypoint` (off `main` after #766)
Follows: P1C #766 (worker + Null adapter + ops routes)

## 1. What this delivers

P1C shipped `EcmPublicationOutboxWorker` as a **library class** — code-complete and
testable, but nothing started it, so enqueued rows were never auto-dispatched. This
slice adds the **production drainer**: a `yuantus ecm-publication-worker` CLI command
(mirroring the erp `publication-worker` command) that runs the worker as a daemon
(`run_forever`) or one-shot (`--once`).

After this slice the Null pipeline actually *runs*: release enqueues → the worker
command drains the outbox → the Null adapter "publishes" (reaches `sent` with no
external write). It is **still not a real Athena publish** — that is P1D (the real
CMIS adapter, Phase-0-gated). This slice only wires the drainer.

Command (`src/yuantus/cli.py`):

```
yuantus ecm-publication-worker [--worker-id ID] [--poll-interval N] [--once] \
                               [--tenant T] [--org O]
```

- `--once`: drain one batch and exit (reports the row count) — for cron/manual ops.
- default (no `--once`): `run_forever` daemon loop; Ctrl+C → clean `stop()`.
- `--tenant`/`--org`: set the request context vars before draining.
- default `worker_id` = `ecm-publication-worker-1`.
- reuses the generic `PUBLICATION_OUTBOX_*` settings (batch/backoff/stale/poll).

## 2. Verification

Test env: `.venv-wp13` (python3.11); `unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB`.

`test_ecm_publication_worker_cli.py` (5, all pass) — uses Typer's `CliRunner` with
`run_once`/`run_forever` monkeypatched, so **no DB is touched**:

- source contract: the command is registered (`@app.command(name="ecm-publication-worker")`),
  constructs `EcmPublicationOutboxWorker`, and dispatches `run_once`/`run_forever`.
- `--once` drains one batch and reports the processed count (exit 0).
- without `--once`, `run_forever` is used and `run_once` is **not** called.
- `worker_id` default (`ecm-publication-worker-1`) and `--worker-id` passthrough.
- `KeyboardInterrupt` in the daemon loop is a clean stop (exit 0, `stop()` called),
  not a crash.

Registered in `ci.yml` (sorted, after `test_ecm_publication_worker.py`) + `conftest.py`
`_ALLOWLIST_NO_DB`. **No routes and no tables** are added — the route-count pins (712)
and the migration-table coverage contract are unaffected.

How to reproduce:

```bash
cd Yuantus && . .venv-wp13/bin/activate
unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB
python -m pytest src/yuantus/meta_engine/tests/test_ecm_publication_worker_cli.py -q
```

## 3. Boundary / deferred

- **Still Null-only**: the command drains via the Null adapter; nothing is written to a
  real Athena. **P1D** (the real Athena CMIS adapter, Phase-0-gated on U1–U5) is the
  step that turns this into a real publish.
- **`--tenant`/`--org` do not scope the drain**: they set the request context vars (for
  parity with the erp worker), but the claim query is tenant-agnostic — the worker
  drains all due rows across tenants. Nothing in the current ECM drain path
  (`_claim_batch` / `service.process` / `build_snapshot` / Null adapter) reads the
  context. Kept for parity; documented so an operator does not read `--tenant X` as a
  per-tenant scope.
- **Forward flag for P1D — kill-switch semantics at dispatch.** `ECM_PUBLISH_ENABLED`
  gates the *enqueue* hook (release path), NOT the worker. A running worker drains
  whatever is already in the outbox **unconditionally**. With the Null adapter this is
  harmless, but once P1D writes to a real Athena, flipping `ECM_PUBLISH_ENABLED` off
  will NOT stop a running worker from draining the backlog. This may be the intended
  outbox semantic ("committed once enqueued; halt by stopping the worker") — but the
  P1D taskbook should decide and document it explicitly (e.g. whether the worker should
  also check the kill-switch per tick) rather than have it surface in an incident.
- Process supervision (systemd/k8s/restart policy) and any metrics/health endpoint for
  the daemon are deployment concerns, out of scope here.
