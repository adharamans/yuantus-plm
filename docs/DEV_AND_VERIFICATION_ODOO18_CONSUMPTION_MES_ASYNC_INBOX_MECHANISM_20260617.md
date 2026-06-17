# Dev & Verification: MES async inbox **mechanism** (Consumption R2.5a)

Date: 2026-06-17
Status: **IMPLEMENTED (mechanism-only, UNWIRED)** — pending gate review + merge
Taskbook: `DEVELOPMENT_CLAUDE_TASK_ODOO18_CONSUMPTION_MES_ASYNC_INBOX_TASKBOOK_20260617.md` (#788).

## 1. Scope

Delivers the durable **inbox mechanism** for async MES ingestion, **unwired**: nothing calls it
at runtime, so it changes **no behavior** (mirrors how R1 was contract-only and ECM-P1B was
outbox+enqueue before the worker). The behavior-changing **wiring** — the route async-mode, the
worker daemon, the ops routes, and the producer-contract shift (conflicts surface on the inbox,
not a sync `409`) — is **R2.5b**, gated on owner ratification of the #788 taskbook (esp. OQ1
"build now vs defer").

## 2. What's here

- **Model** `MesConsumptionInbox` (`models/parallel_tasks.py`), table `meta_mes_consumption_inbox`:
  the raw validated event (original uom/qty), a **unique `idempotency_key`** (R1 derived key →
  accept is idempotent), `state` (`pending|processed|conflict|failed`), retry bookkeeping,
  `record_id`. Migration `mes_inbox_001` (`down_revision=consumption_mes_idem_001`, single head).
- **Service** `MesConsumptionInboxService`:
  - `accept_event(event) -> (row, ACCEPTED|DUPLICATE)` — insert-then-catch on the unique key
    (a replay is one inbox row). Conversion deferred to process (raw kept).
  - `process_row(row)` — drains one row through the **same** `ingest_mes_consumption` as the sync
    path → `processed` (+ `record_id`) / `conflict` (divergent same-key) / `failed` (retryable
    reschedules with backoff up to `max_attempts`). **Two-level idempotency**: inbox unique key
    (accept) + ConsumptionRecord unique key (process).
  - `claim_due(limit)` — for the future worker daemon (R2.5b).

## 3. Verification (`test_consumption_mes_inbox_service.py`, 7 pass)

accept idempotent (replay → one row); accept stores the raw event; process CREATED → `processed`
+ record; a re-drain of the same logical event → record-level dedup, one ConsumptionRecord,
**variance counts once**; divergent same-key → `conflict`, no second record; missing plan →
retry then `failed`; `claim_due` returns pending. Single Alembic head (`mes_inbox_001`); migration
table-coverage contract green; **no route added (count 713)**; CI dual-registered.

## 4. Finding (separate, pre-existing): tenant baseline is stale

Adding this per-tenant table surfaced that the **tenant baseline** (`migrations_tenant/…/
t1_initial_tenant_baseline.py`) is **stale** — regenerating it (`scripts/generate_tenant_baseline.py`)
adds not just this inbox table but the already-merged `meta_ecm_publication_outbox`,
`meta_erp_publication_outbox`, and the R2 `meta_consumption_records.idempotency_key` column. Those
prior slices merged **without regenerating the baseline**, and `test_tenant_baseline_revision`
(which catches this) is **not in `ci.yml`**, so it went unnoticed. This slice deliberately does
**not** bundle that 4-table catch-up (it would touch live ECM/erp provisioning); it is a separate
**recommended fix**: a focused PR that regenerates the baseline + adds the test to CI. The inbox
table being absent from the baseline is harmless until R2.5b wires it (it's unused).

## 5. Boundary

Mechanism only, unwired, default-no-behavior-change. No route/pin change, no worker daemon, no
ops surface, no uom-conversion-at-process — all R2.5b after ratification.
