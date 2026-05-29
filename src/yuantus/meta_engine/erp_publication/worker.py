"""PLM->ERP publication outbox worker (G2 R2 worker daemon).

A dedicated, standalone worker that auto-drains the publication outbox, modeled
on `JobWorker` + `JobService.poll_next_job` but polling
`meta_erp_publication_outbox` directly (one source of truth; honors the outbox's
reason-based retry rule). Per the R2 worker taskbook:

  run_once = reclaim-stale  ->  claim a due batch (FOR UPDATE SKIP LOCKED / SQLite
             fallback)  ->  process each (NullErpPublicationAdapter, with a
             version-drift revalidate reusing build_publication_readiness)  ->
             reschedule retryable failures (deferred, via reschedule_retry).

R2 uses the no-I/O Null adapter (a worker `send` reaches `sent` via Null, exactly
as the manual route does); the real ERP connector is a later slice.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional

from sqlalchemy import asc, or_
from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.database import get_db_session
from yuantus.meta_engine.erp_publication.adapter import ErpPublicationAdapter
from yuantus.meta_engine.erp_publication.adapter_registry import resolve_adapter
from yuantus.meta_engine.erp_publication.models import (
    ErpPublicationOutbox,
    ErpPublicationReason,
    ErpPublicationState,
)
from yuantus.meta_engine.erp_publication.service import ErpPublicationOutboxService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.web.plm_erp_publication_router import (
    build_publication_readiness,
)

logger = logging.getLogger(__name__)

_RETRYABLE = (
    ErpPublicationReason.REMOTE_ERROR.value,
    ErpPublicationReason.ADAPTER_ERROR.value,
)


class PublicationOutboxWorker:
    """Polls + drains the publication outbox. `run_once_with_session(session)` is
    the testable core; `run_once()` wraps it in a managed session; `run_forever()`
    is the CLI daemon loop."""

    def __init__(
        self,
        worker_id: str,
        *,
        adapter: Optional[ErpPublicationAdapter] = None,
        readiness_builder: Optional[Callable[..., Any]] = None,
        batch_size: Optional[int] = None,
        backoff_seconds: Optional[int] = None,
        stale_timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
    ) -> None:
        s = get_settings()
        self.worker_id = worker_id
        # When no adapter override is given, resolve per-row by target_system via
        # the registry (R3): a configured target -> HttpErpPublicationAdapter,
        # otherwise the no-I/O Null adapter. `adapter` (when set) overrides for
        # tests / a fixed deployment.
        self.adapter = adapter
        # Reuse R1-B's exact logic for the version-drift revalidate; injectable.
        self.readiness_builder = readiness_builder or build_publication_readiness
        self.batch_size = (
            batch_size if batch_size is not None else s.PUBLICATION_OUTBOX_BATCH_SIZE
        )
        self.backoff_seconds = (
            backoff_seconds
            if backoff_seconds is not None
            else s.PUBLICATION_OUTBOX_RETRY_BACKOFF_SECONDS
        )
        self.stale_timeout_seconds = (
            stale_timeout_seconds
            if stale_timeout_seconds is not None
            else s.PUBLICATION_OUTBOX_STALE_TIMEOUT_SECONDS
        )
        self.poll_interval_seconds = (
            poll_interval_seconds
            if poll_interval_seconds is not None
            else s.PUBLICATION_OUTBOX_POLL_INTERVAL_SECONDS
        )
        self._running = False

    # -- core ------------------------------------------------------------
    def run_once(self) -> int:
        """Drain one batch in a managed session. Returns the rows processed."""
        with get_db_session() as session:
            return self.run_once_with_session(session)

    def run_once_with_session(self, session: Session) -> int:
        service = ErpPublicationOutboxService(session)
        self._reclaim_stale(session)
        rows = self._claim_batch(session)
        for row in rows:
            self._process_row(session, service, row)
        return len(rows)

    # -- steps -----------------------------------------------------------
    def _reclaim_stale(self, session: Session) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.stale_timeout_seconds)
        stale = (
            session.query(ErpPublicationOutbox)
            .filter(
                ErpPublicationOutbox.state == ErpPublicationState.PENDING.value,
                ErpPublicationOutbox.worker_id.isnot(None),
                ErpPublicationOutbox.claimed_at.isnot(None),
                ErpPublicationOutbox.claimed_at < cutoff,
            )
            .all()
        )
        for row in stale:
            row.worker_id = None
            row.claimed_at = None
        if stale:
            session.commit()
            logger.warning(
                "publication-worker '%s' reclaimed %d stale row(s)",
                self.worker_id,
                len(stale),
            )
        return len(stale)

    def _claim_batch(self, session: Session) -> List[ErpPublicationOutbox]:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(seconds=self.stale_timeout_seconds)
        dialect = session.bind.dialect.name if session.bind else "unknown"
        query = (
            session.query(ErpPublicationOutbox)
            .filter(
                ErpPublicationOutbox.state == ErpPublicationState.PENDING.value,
                ErpPublicationOutbox.next_attempt_at <= now,
                or_(
                    ErpPublicationOutbox.claimed_at.is_(None),
                    ErpPublicationOutbox.claimed_at < stale_cutoff,
                ),
            )
            .order_by(
                asc(ErpPublicationOutbox.next_attempt_at),
                asc(ErpPublicationOutbox.created_at),
            )
        )
        if dialect == "postgresql":
            # Concurrent workers must not claim the same row.
            query = query.with_for_update(skip_locked=True)
        rows = query.limit(self.batch_size).all()
        for row in rows:
            row.worker_id = self.worker_id
            row.claimed_at = now
        if rows:
            # Commit the claim so concurrent workers see it (claimed_at gate).
            session.commit()
        return rows

    def _process_row(
        self,
        session: Session,
        service: ErpPublicationOutboxService,
        row: ErpPublicationOutbox,
    ) -> None:
        item = session.get(Item, row.item_id)
        if item is None:
            # Backing item gone -> cannot revalidate -> cannot safely send.
            # Mark skipped (never crash the loop) and release the claim.
            service._mark_revalidated_not_eligible(row)
            row.worker_id = None
            row.claimed_at = None
            session.commit()
            return

        snap = row.snapshot or {}
        ruleset_id = snap.get("ruleset_id", "readiness")
        limits = snap.get("limits") or {}

        def _revalidate():
            return self.readiness_builder(
                session,
                item,
                row.item_id,
                ruleset_id=ruleset_id,
                mbom_limit=int(limits.get("mbom_limit", 20)),
                routing_limit=int(limits.get("routing_limit", 20)),
                baseline_limit=int(limits.get("baseline_limit", 20)),
            )

        adapter = self.adapter or resolve_adapter(row.target_system)
        attempt_before = row.attempt_count or 0
        try:
            service.process(row, adapter, revalidate=_revalidate)
        except Exception as exc:  # never crash the loop (e.g. revalidate raised)
            session.rollback()
            logger.error(
                "publication-worker '%s' error processing row %s: %s",
                self.worker_id,
                row.id,
                exc,
                exc_info=True,
            )
            # A process() exception (e.g. the revalidate readiness build raised)
            # CONSUMES an attempt so it cannot defer forever (#673 §10): release
            # the claim and dead-letter at max_attempts; otherwise back off and
            # stay pending for the next poll.
            row = session.get(ErpPublicationOutbox, row.id)
            if row is not None:
                row.attempt_count = (row.attempt_count or 0) + 1
                row.worker_id = None
                row.claimed_at = None
                row.error_message = str(exc)
                if (row.attempt_count or 0) >= (row.max_attempts or 0):
                    row.state = ErpPublicationState.FAILED.value
                    row.reason = ErpPublicationReason.REMOTE_ERROR.value
                else:
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=max(self.backoff_seconds, 1)
                    )
                session.commit()
            return

        if row.state == ErpPublicationState.FAILED.value and row.reason in _RETRYABLE:
            service.reschedule_retry(
                row,
                attempt_count_before=attempt_before,
                backoff_seconds=self.backoff_seconds,
            )
        session.commit()

    # -- daemon loop -----------------------------------------------------
    def run_forever(self) -> None:
        self._running = True
        logger.info("publication-worker '%s' started", self.worker_id)
        while self._running:
            try:
                self.run_once()
            except Exception as exc:  # keep the daemon alive across ticks
                logger.error(
                    "publication-worker '%s' tick error: %s",
                    self.worker_id,
                    exc,
                    exc_info=True,
                )
            time.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False
