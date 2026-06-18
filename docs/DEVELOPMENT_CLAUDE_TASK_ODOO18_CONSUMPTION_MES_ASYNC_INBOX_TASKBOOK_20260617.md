# Claude Taskbook: MES async ingestion **inbox + worker** (Consumption R2.5)

Date: 2026-06-17
Status: **DECISION — doc-only, pending gate review + a separate build opt-in**
Roadmap: `DEVELOPMENT_ROADMAP_AND_TODO_20260617.md` §2 R2.5 (explicitly "do last, not urgent").
Follows the synchronous line R2 (#778) → R2.1 (#779) → R2.2 (#781/#782) → R2.3 (#785) → R2.4 (#786).

## 0. Why / the load-bearing question first

The synchronous ingest (R2–R2.4) already idempotent-dedupes, converts units, and surfaces
conflicts as a `409`. Async ingestion adds **resilience/throughput**: the route persists the raw
event to an **inbox** and returns fast (`202`), and a **worker** drains the inbox and runs the
existing `ingest_mes_consumption`. It mirrors the ECM/erp outbox+worker (inbound flavour).

**It also changes the producer contract**: with async, a conflict/validation problem is no longer
a synchronous `409`/`422` — the producer already got `202`, so the failure surfaces **later** on
the inbox row (status + ops surface), not in the HTTP response. That semantic shift is the reason
the roadmap puts this last.

**D0 (recommended): build the capability DEFAULT-OFF.** The sync path stays the default; a setting
(`MES_INGEST_ASYNC`, restart-only, default false) opts a deployment into async. So v1 adds the
inbox+worker without changing any current behavior; async is enabled only where the throughput/
resilience trade is wanted and the async-conflict surface is acceptable.

## 1. Baseline / reference

- Mirror `meta_engine/ecm_publication/{models,service,worker}.py` (outbox table + claim/drain
  worker with backoff + ops routes) — the inbound analogue.
- Reuse the existing `ConsumptionPlanService.ingest_mes_consumption` (idempotent CREATED/
  DUPLICATE/CONFLICT) as the worker's per-row processor — no new ingest logic.
- The R1 derived idempotency key (`sha256(plan_id|source_type|mes_event_id)`) is the inbox's
  dedupe key too.

## 2. Locked decisions

- **D1 — Inbox table** `meta_mes_consumption_inbox`: `id`, the raw validated event fields
  (`plan_id, mes_event_id, source_type, source_id, actual_quantity, uom, recorded_at, attributes`),
  `idempotency_key` (**unique** — a replayed event is one inbox row), `state`
  (`pending|processed|conflict|failed`), `attempt_count`, `max_attempts`, `next_attempt_at`,
  `error`, `record_id` (the ConsumptionRecord it produced), `created_at`, timestamps. Single
  Alembic head; tenant baseline updated.
- **D2 — Accept path** (async mode): the route validates the DTO + the plan_id-match, then INSERTs
  the inbox row (insert-then-catch on the unique key → a replay returns the existing inbox row,
  `202` + inbox id, idempotent accept). It does **not** run ingest. Sync mode (default) is
  unchanged.
- **D3 — uom conversion at PROCESS, not accept.** The worker (which loads the plan) does the R2.4
  conversion + the R2.1/unconvertible reject (→ inbox `failed` with the reason). Accept stays
  minimal (store the raw event); the inbox keeps the original uom/qty for audit.
- **D4 — Worker** `mes_consumption_inbox_worker`: claim a due batch (FOR UPDATE SKIP LOCKED on
  PG) → per row run `ingest_mes_consumption` → mark `processed` (+ `record_id`) / `conflict`
  (divergent same-key) / `failed` (retryable → reschedule with backoff; terminal → stay failed).
  Mirrors `EcmPublicationOutboxWorker`. Kill-switch via the async setting.
- **D5 — Ops surface**: `GET /consumption/mes-inbox[?state]`, `GET .../{id}`, and a `replay`
  (failed→pending) — admin + entitlement gated, mirroring the ECM outbox ops. Conflicts are
  reconciled here (the async analogue of the sync `409`). **+N routes → route-count pins.**
- **D6 — Idempotency is two-level**: inbox unique key (accept dedupe) + ConsumptionRecord unique
  key (process dedupe). A replay is deduped at accept; a worker re-run is deduped at the record.
  No double-count.

## 3. Open questions to ratify

- **OQ1**: build now vs defer until a concrete throughput/resilience need (roadmap says not urgent).
  Recommend **build default-off** so the capability exists without changing behavior.
- **OQ2**: accept response — `202 {inbox_id, state}` vs `200`. Recommend `202`.
- **OQ3**: conversion at accept vs process — recommend **process** (D3).
- **OQ4**: a separate inbox credential/route vs reuse the R2.2 mes-actuals credential + a mode
  switch on the same route. Recommend reuse (same machine credential).

## 4. Verification plan

Inbox model + migration single-head + tenant baseline; accept idempotence (replay → one inbox
row, `202`); worker drains → CREATED/DUPLICATE/CONFLICT/failed-with-backoff; the
variance-counts-once invariant end-to-end; sync mode unchanged when async off; ops routes +
route-count pins; CI dual-registration; DEV/V doc.

## 5. Boundary

Async ingestion capability only, default-off. No change to the sync default behavior, the
idempotency key, uom conversion semantics, or manual `/actuals`. A tenant-configurable async
policy and streaming are later follow-ups.
