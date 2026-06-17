"""Async MES consumption ingestion — inbox accept + process (Consumption R2.5).

Mechanism only (no route wiring / worker daemon yet — those are the behavior-changing
R2.5b wiring, gated on owner ratification of the R2.5 taskbook OQ1). This module is safe to
land: nothing calls it at runtime.

- ``accept_event``: persist a validated raw event to the inbox, keyed by the R1 idempotency
  key (insert-then-catch -> a replay is one inbox row: ACCEPTED / DUPLICATE).
- ``process_row``: drain one inbox row through the SAME
  ``ConsumptionPlanService.ingest_mes_consumption`` as the sync path, recording the outcome
  (processed / conflict / failed) on the row. Two-level idempotency: the inbox unique key
  (accept) + the ConsumptionRecord unique key (process).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Tuple

from sqlalchemy import asc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yuantus.meta_engine.models.parallel_tasks import (
    ConsumptionPlan,
    MesConsumptionInbox,
)
from yuantus.meta_engine.services.consumption_mes_contract import (
    RESERVED_PROPERTIES_KEY,
    MesConsumptionEvent,
    derive_consumption_idempotency_key,
    map_mes_event_to_consumption_record_inputs,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService

_RETRY_BACKOFF_SECONDS = 30


class MesConsumptionInboxService:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- accept --------------------------------------------------------------
    def accept_event(self, event: MesConsumptionEvent) -> Tuple[MesConsumptionInbox, str]:
        """Idempotently persist a validated raw event. Returns ``(row, disposition)``
        where disposition is ``ACCEPTED`` (new) or ``DUPLICATE`` (replay → existing row).
        Conversion is deliberately deferred to ``process_row`` (raw uom/qty kept).
        Pure-event validation that the sync route enforces at the boundary (the
        reserved ``_ingestion`` key) is enforced here too, so a payload the sync
        path would 400 is NOT durably accepted (sync/async symmetry; raises
        ValueError, which the route maps to 400)."""
        if RESERVED_PROPERTIES_KEY in event.attributes:
            raise ValueError(
                f"attributes must not contain the reserved key "
                f"{RESERVED_PROPERTIES_KEY!r}"
            )
        key = derive_consumption_idempotency_key(event)
        row = MesConsumptionInbox(
            idempotency_key=key,
            plan_id=event.plan_id,
            mes_event_id=event.mes_event_id,
            source_type=event.source_type,
            source_id=event.source_id,
            actual_quantity=float(event.actual_quantity),
            uom=event.uom,
            recorded_at=event.recorded_at,
            attributes=dict(event.attributes),
            state="pending",
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            existing = (
                self.session.query(MesConsumptionInbox)
                .filter(MesConsumptionInbox.idempotency_key == key)
                .one_or_none()
            )
            if existing is None:
                raise
            return existing, "DUPLICATE"
        return row, "ACCEPTED"

    # -- process -------------------------------------------------------------
    def process_row(self, row: MesConsumptionInbox) -> str:
        """Drain one inbox row through ``ingest_mes_consumption``. Sets the row state
        (``processed`` / ``conflict`` / ``failed``) and returns it. A retryable failure
        reschedules with backoff up to ``max_attempts``."""
        # Reconstruct + validate/map INSIDE the guard: a deterministic
        # reconstruction/validation failure must reach a TERMINAL state, not crash
        # the worker and loop forever on a poison row.
        try:
            event = MesConsumptionEvent(
                plan_id=row.plan_id,
                mes_event_id=row.mes_event_id,
                actual_quantity=row.actual_quantity,
                source_type=row.source_type,
                source_id=row.source_id,
                recorded_at=row.recorded_at,
                uom=row.uom,
                attributes=dict(row.attributes or {}),
            )
            inputs = map_mes_event_to_consumption_record_inputs(event)
        except Exception as exc:
            # Deterministic -> NOT retryable; fail terminally on the first drain.
            row.attempt_count = (row.attempt_count or 0) + 1
            row.state = "failed"
            row.error = f"{type(exc).__name__}: {exc}"
            return row.state
        # uom reconciliation, parity with the sync route (R2.1): a declared uom that
        # disagrees with plan.uom must NOT be ingested with the wrong unit (variance
        # sums regardless of unit). Surface it as a conflict on the inbox rather than
        # silently miscounting. (Conversion is a follow-up once R2.4 lands.)
        if row.uom is not None:
            plan = self.session.get(ConsumptionPlan, row.plan_id)
            if plan is not None and row.uom.strip().upper() != (
                (plan.uom or "EA").strip().upper()
            ):
                row.attempt_count = (row.attempt_count or 0) + 1
                row.state = "conflict"
                row.error = f"uom mismatch: event {row.uom!r} vs plan {plan.uom!r}"
                return row.state
        service = ConsumptionPlanService(self.session)
        try:
            record, disposition = service.ingest_mes_consumption(inputs)
        except Exception as exc:  # plan-not-found / transient -> retryable failure
            row.attempt_count = (row.attempt_count or 0) + 1
            row.error = f"{type(exc).__name__}: {exc}"
            if row.attempt_count >= (row.max_attempts or 5):
                row.state = "failed"
            else:
                row.state = "pending"
                row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                    seconds=_RETRY_BACKOFF_SECONDS
                )
            return row.state
        if disposition == "CONFLICT":
            row.state = "conflict"
            row.record_id = record.id
            row.error = "idempotency_key already recorded with a different payload"
        else:  # CREATED / DUPLICATE
            row.state = "processed"
            row.record_id = record.id
            row.error = None
        return row.state

    # -- claim (for a future worker daemon; testable now) --------------------
    def claim_due(self, *, limit: int = 50) -> List[MesConsumptionInbox]:
        now = datetime.now(timezone.utc)
        return (
            self.session.query(MesConsumptionInbox)
            .filter(
                MesConsumptionInbox.state == "pending",
                MesConsumptionInbox.next_attempt_at <= now,
            )
            .order_by(asc(MesConsumptionInbox.next_attempt_at))
            .limit(limit)
            .all()
        )
