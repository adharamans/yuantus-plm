# ECM Publish — Worker E2E Evidence Smoke (Dev & Verification)

Date: 2026-06-17
Follows: `docs/DEV_AND_VERIFICATION_ECM_PUBLISH_P1E_LIVE_CLOSEOUT_AND_WORKER_E2E_PLAN_20260617.md`
(#774) — automates the **drain + verify** half of that doc's §5 worker-E2E operator
checklist.

## 1. What it delivers

`scripts/ecm_publish_phase0/worker_e2e_smoke.py` turns the last production-readiness
evidence item — `release() → outbox row → ecm-publication-worker → Athena Transfer Receiver
→ outbox SENT` — into a one-command, machine-checked gate for **one** disposable row.

Given the outbox id (`--outbox-id`) of an already-enqueued controlled file (the operator
prepares + releases the disposable version per the §5 checklist), it runs the real worker
until that row is terminal, then asserts:

- `state == 'sent'`, `reason is null`, `attempt_count >= 1`
- `properties.remote_id` present **and** `properties.athena_document_id` present (a Null run
  lacks `athena_document_id`, so a Null "sent" can never pass)
- `properties.athena_disposition ∈ {CREATED, RENAMED, OVERWRITTEN, UNCHANGED, SKIPPED}`
- **NOT** `properties.conflict_after_sent` (a stale already-sent row whose content drifted
  post-send is **failed**, not passed)

Output is a JSON evidence artifact. Exit: `0` pass, `1` fail, `2` blocked (preconditions),
`3` inconclusive-retrying. The transfer secret is never logged.

## 2. Safety (live blast radius is bounded)

- **Dry-run by default**: no worker run, no Athena I/O — reports the plan, the env-level
  missing inputs, the **settings-level preflight blockers**, and a read-only count of
  due-pending rows **across all target systems** (the real blast radius).
- **`--yes-live` requires `--outbox-id`.** A worker tick drains the whole *due batch*, not
  one row, so the script refuses to drain unless the named row is the **only** row a tick
  would claim. The check **mirrors the worker's `_claim_batch` selection across ALL target
  systems** (the worker does not filter by `target_system`), so an unrelated due-pending row
  of *any* target blocks the run — a backlog can never be published as a side effect of a
  smoke (status `blocked`).
- **Live preflight reads the SAME `get_settings()` the worker uses**, so passing the gate
  means the worker would really go live: it blocks if `ECM_PUBLISH_ENABLED` is off, if
  `PUBLICATION_ECM_TARGET_SYSTEM` ≠ the target, or if `resolve_adapter()` returns the **Null
  adapter** (a Null "sent" is no real publish). Env reads are `YUANTUS_`-prefixed only (a
  bare `ECM_PUBLISH_ENABLED` is ignored, matching pydantic Settings) so the script's view
  cannot diverge from the worker's.
- A retryable failure that backs off (`next_attempt_at` in the future) is reported as
  **`inconclusive_retrying`** (exit 3), distinct from a hard `failed` — a transient blip is
  not a live-ready denial.
- It only **drains + validates** — never enqueues or releases.

## 3. Usage

```bash
# operator prepares + releases a disposable version, then gets its outbox id (§5 SQL).
export YUANTUS_ECM_PUBLISH_ENABLED=true
export YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM=athena
export YUANTUS_PUBLICATION_ECM_BASE_URL='<athena-transfer-base-url>'
export YUANTUS_PUBLICATION_ECM_TRANSFER_USER='<transfer-user>'
export YUANTUS_PUBLICATION_ECM_TRANSFER_SECRET='<transfer-secret>'
export YUANTUS_PUBLICATION_ECM_ROOT_FOLDER_ID='<root folder UUID>'
export YUANTUS_PUBLICATION_ECM_SOURCE_REPOSITORY_ID=yuantus-plm   # PLM sender identity
python3 scripts/ecm_publish_phase0/worker_e2e_smoke.py --yes-live --outbox-id <outbox-uuid>
```

Run it inside a Yuantus deployment that can reach **both** the database and the live Athena
Transfer Receiver. `--max-ticks` caps the worker iterations (default 6).

## 4. Verification

Test env: `.venv-wp13` (python3.11); `unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB`.

`test_ecm_worker_e2e_smoke.py` (17, all pass) drives the **real worker** against an in-memory
DB with fake adapters (no live DB / Athena needed):

- pass (sent + athena props); hard fail (validation-terminal → `failed`).
- **blast radius**: `--outbox-id` required; an unrelated backlog row (same target) → `blocked`;
  an unrelated due-pending row of a **different** `target_system` → `blocked` and neither row
  is processed (the check mirrors the worker's target-agnostic claim set).
- **false-pass guards**: a skipped/`not_eligible` row → `failed` (not passed); an already-sent
  row **without** `athena_document_id` (Null-published) → `failed`; a `conflict_after_sent`
  stale row → `failed`; an already-sent row **with** props → `passed` with zero ticks.
- **retrying**: a `remote_error` with backoff → `inconclusive_retrying` (not a hard fail).
- **preflight**: blocks on kill-switch off / target mismatch / Null adapter; clean when the
  settings really go live; a bare (un-prefixed) env name is ignored.
- dry-run counts due-pending rows + surfaces preflight blockers **without draining**;
  `main --yes-live` blocks (exit 2) on missing inputs.

### 4.1 Adversarial verify

A 2-reviewer adversarial pass on the first cut returned **must_fix** and caught real
problems — all now fixed + regression-tested: (a) **live blast radius** — `--outbox-id`
scoped only validation while the worker drained the whole due batch (up to 120 rows) to live
Athena; now `--outbox-id` is required and the run blocks unless the named row is the only
due-pending target row; (b) **the gate did not verify the worker** — the env check diverged
from `get_settings()` (accepted bare names; defaulted the target), so it could "pass" while
the worker ran the Null adapter / kill-switch-off; now a `get_settings()`+`resolve_adapter`
preflight is authoritative; (c) **false-pass on `conflict_after_sent`**; (d) `dry_run_ready`
was treated as drainable though the worker only claims `pending`; (e) a backed-off retry
false-failed — now reported `inconclusive_retrying`.

A subsequent gate review caught one more gap: the blast-radius check counted only rows of the
*configured* `target_system`, but the worker's `_claim_batch` is **target-agnostic** — a
due-pending row of a *different* target was invisible to the precheck yet would be processed by
the tick. **Fixed**: the check now mirrors the worker's claim set across **all** targets; an
unrelated due row of any target blocks the run and neither row is touched (regression
`test_blast_radius_blocks_on_other_target_due_row`).

Registered in `ci.yml` (sorted) + `conftest.py` `_ALLOWLIST_NO_DB`. **No routes, tables, or
settings** → route-count pins and migration coverage are unaffected.

## 5. Boundary

This automates *evidence collection + assertion* for one disposable row; the operator still
prepares + releases the version and runs it in an environment that reaches both the DB and
Athena. Once it returns `status: passed`, the §5 evidence item is satisfied and ECM publish
can be marked live-ready for controlled rollout (with the restart-only kill-switch caveat).
