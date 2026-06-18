# Dev & Verification: MES async **wiring** (Consumption R2.5b)

Date: 2026-06-17
Status: **IMPLEMENTED** — pending gate review + merge. **Stacked on R2.5a (#789)**.
Taskbook: `…CONSUMPTION_MES_ASYNC_INBOX_TASKBOOK…` (#788). Default-OFF.

## 1. Summary

Wires the R2.5a inbox into the runtime, **default-off**: when `MES_INGEST_ASYNC=true` the MES
ingest route persists the event to the inbox and returns `202`; the worker drains it through the
**same** `ingest_mes_consumption`. Adds admin-gated inbox **ops routes** (visibility + replay).
With the flag off (default) the synchronous path is **unchanged**.

## 2. As built (my OQ decisions, per #788)

- **Setting** `MES_INGEST_ASYNC` (`config/settings.py`, bool default false, restart-only).
- **Route async-mode** (`parallel_tasks_consumption_router.py`): after the `plan_id` match, if
  async is on → `MesConsumptionInboxService.accept_event(event)` (idempotent) → **`202`**
  `{disposition, inbox_id, state}`. **OQ2**=202, **OQ3**=conversion-at-process (the accept stores
  raw; the worker does R2.4-style conversion later — note: this stack predates the R2.4 merge, so
  worker-side conversion lands when R2.4 + this both reach main), **OQ4**=reuse the R2.2 credential.
- **Ops routes** (+3, admin-gated via `require_admin_permission`): `GET /consumption/mes-inbox
  [?state]`, `GET .../{inbox_id}`, `POST .../{inbox_id}/replay` (failed→pending). These are the
  async analogue of the sync `409` — conflicts/failures are reconciled here.
- **Worker** `MesConsumptionInboxService.drain_once(limit)` — claim due → process each → commit
  per row. A long-running daemon is a thin interval wrapper (mirrors the ECM worker; operational).

## 3. Verification (`test_consumption_mes_async_wiring.py` + inbox, 17 pass)

- async **off** → `200` sync, no inbox row; async **on** → `202` ACCEPTED, one inbox row, **no
  ConsumptionRecord yet**; async accept idempotent (replay → `202 DUPLICATE`, one row).
- ops: list + `?state` filter; invalid state `422`; get `404`; replay failed→pending; replay
  non-failed `409`.
- worker: `drain_once` processes pending → ConsumptionRecord created, row `processed` + `record_id`.
- **route count 713 → 716** (4 pins) + `_CONSUMPTION_ROUTE_KEYS` (+3) updated; CI dual-registered.

## 4. Boundary

Default-off; sync path unchanged. The standalone worker-daemon CLI + worker-side uom conversion
(post-R2.4-merge) are thin follow-ups. The producer-contract shift (async-surfaced conflicts) is
documented and only applies when the flag is enabled.
