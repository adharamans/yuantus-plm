# P0-1 notification delivery + subscriptions — taskbook

**Date:** 2026-07-02  
**Branch:** `codex/p0-1-notification-taskbook`  
**Status:** scope-lock only; no runtime in this slice

## 1. Current State

The current notification surface is not durable:

- `src/yuantus/meta_engine/services/notification_service.py` only logs `[NOTIFY]` entries and never persists delivery intent, state, recipients, attempts, or failure.
- `src/yuantus/meta_engine/events/transactional.py` queues domain events on the SQLAlchemy session and publishes them after commit.
- `src/yuantus/meta_engine/events/event_bus.py` is an in-memory process-local dispatcher. Handler exceptions are logged and swallowed; there is no retry, replay, dead-letter, or cross-process durability.

The reliable pattern already exists elsewhere:

- `erp_publication` and `ecm_publication` each use a dedicated outbox model, state/reason split, payload fingerprint, retry fields, worker claim markers, stale-claim reclaim, and worker-side deferred retry/dead-letter behavior.
- Those publication outboxes are domain-specific and must not be reused for notifications, but their state-machine shape is the correct local pattern.

## 2. Invariant

The in-memory `EventBus` must never be the reliable entrypoint for user-visible notification delivery. A process can crash after the business transaction commits but before an `after_commit` handler publishes; that gap is not recoverable with the current in-memory bus.

Notification delivery source of truth is a durable notification outbox and delivery table. Critical business paths must call `NotificationService.notify(...)` inside their own transaction so the delivery intent commits atomically with the business change. EventBus handlers may enqueue secondary/best-effort notifications, but only after accepting that reliability begins when the durable row is written.

## 3. First Build Slice

The first implementation slice must be one complete reliability cut:

1. Add persistent models and migrations:
   - `NotificationOutbox` for one logical notification event.
   - `NotificationDelivery` for one recipient/channel attempt stream.
2. Add a service that:
   - enqueues one logical notification idempotently;
   - expands recipients/subscriptions into delivery rows;
   - stores a payload snapshot and idempotency fingerprint;
   - never performs remote I/O inside the caller transaction.
3. Add a worker that:
   - claims due `pending` delivery rows;
   - calls a channel adapter;
   - marks `sent`, `failed`, or schedules retry;
   - releases stale claims;
   - dead-letters at max attempts.
4. Wire current `NotificationService.notify(...)` to enqueue durable notifications, not log-only, so existing ECO call sites gain durability without depending on `after_commit` timing.
5. Register the worker/CLI entrypoint and CI tests.

This slice must not ship as "schema only" or "log plus async stub." It must include the worker because delivery state without a drain path is another silent queue.

## 4. Data Shape

Recommended enums:

- Outbox state: `pending`, `ready`, `cancelled`.
- Delivery state: `pending`, `sent`, `failed`.
- Delivery reason: `adapter_error`, `remote_error`, `validation_error`, `suppressed`, `recipient_missing`.

Recommended `NotificationOutbox` columns:

- `id` string UUID primary key.
- `tenant_id`, `org_id`, `event_type`, `object_type`, `object_id`.
- `title`, `body`, `payload`.
- `idempotency_key` or `(tenant_id, event_type, object_type, object_id, payload_fingerprint)` unique key.
- `payload_fingerprint`.
- `created_by_id`, `created_at`, `updated_at`.
- `properties`.

Recommended `NotificationDelivery` columns:

- `id` string UUID primary key.
- `notification_id` foreign key.
- `tenant_id`, `org_id`, `recipient_user_id`, `recipient_email`.
- `channel` (`email` first; `in_app`, IM, webhook later).
- `state`, `reason`, `attempt_count`, `max_attempts`, `error_message`.
- `next_attempt_at`, `worker_id`, `claimed_at`, `sent_at`.
- `payload`, `properties`.

Keep logical notification and recipient delivery separate. One event can fan out to multiple recipients/channels without duplicating the event snapshot.

## 5. Recipient and Subscription Scope

First slice:

- supports explicit recipients passed to `NotificationService.notify(...)`;
- resolves user email from the identity/user model when available;
- tolerates missing recipient email by marking the delivery `failed` with `recipient_missing`, not crashing enqueue.

Subscription model is a follow-up slice:

- user x object/type x event category subscriptions;
- default subscriptions for ECO assignment/approval events;
- digest/coalescing controls to reduce noise.

Do not block the reliability slice on the full subscription UI/model.

## 6. Channel Adapter Scope

First slice:

- `NullNotificationAdapter` for deterministic tests.
- SMTP adapter behind explicit config only, fail-closed when host/from is missing.

Later slices:

- IM/webhook adapters.
- digest batching.
- user-facing notification center.

## 7. Failure Semantics

- Enqueue is pure database work and participates in the caller transaction when called from business services.
- Remote delivery is worker-only.
- Retry only retryable transport/adapter errors.
- Validation errors, suppressed notifications, and missing recipients are terminal failures unless explicitly replayed by an operator.
- Worker exceptions must not crash the loop; they consume an attempt and either reschedule or dead-letter.
- Operator replay resets retryable failed delivery rows to pending; it must not resend synchronously inside the HTTP request.

## 8. Tests Required

First implementation slice must add tests for:

- `NotificationService.notify(...)` persists outbox + delivery rows and does not perform remote I/O.
- duplicate enqueue reuses the existing logical notification or refuses changed payload after terminal send, mirroring the publication outbox idempotency rule chosen in the implementation.
- worker sends pending deliveries via the adapter and marks `sent`.
- retryable adapter failure reschedules until max attempts, then dead-letters.
- missing recipient is terminal and does not crash enqueue.
- existing ECO notification call sites enqueue durable rows in the same transaction as the business change.
- any EventBus listener added for notifications is explicitly best-effort and is not the only trigger for a critical notification.
- worker stale-claim reclaim.
- CI explicit list includes the new tests.

## 9. Out of Scope

- unified task inbox / escalation workflow (P0-2/P0-3);
- document full-text search (P0-4);
- bulk import notifications;
- UI notification center;
- IM/webhook delivery;
- distributed worker lease store beyond the database rows;
- per-user notification preferences beyond the subscription model named above.

## 10. Acceptance

P0-1 is not done until a real durable delivery path exists:

- migration creates the two durable tables;
- `NotificationService.notify(...)` enqueues into those tables;
- a worker drains delivery rows with retry/dead-letter semantics;
- tests pin commit/rollback behavior and worker retry behavior;
- a design & verification record is added and indexed.

Until then, the current log-only notification service must be treated as a placeholder, not as a delivered notification system.
