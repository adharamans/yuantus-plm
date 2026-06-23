"""PLM->ECM publication outbox worker (ECM-P1C worker daemon).

A standalone worker that auto-drains ``meta_ecm_publication_outbox``, mirroring
``erp_publication.worker.PublicationOutboxWorker``:

  run_once = reclaim-stale -> claim a due batch (FOR UPDATE SKIP LOCKED on
             postgres / SQLite fallback) -> process each (NullEcmPublicationAdapter
             by default, with a release-revalidate that drops stale snapshots) ->
             reschedule retryable failures (reschedule_retry).

The worker resolves the adapter per row via the registry: the real Athena
Transfer Receiver adapter (``transfer_receiver_adapter.py``) when a live target
is configured, else the no-I/O Null adapter (tests / unconfigured). The
revalidation is the one part with no mechanical erp mirror
(erp reuses a readiness verdict; ECM has none) -- it re-fetches the released
version + the specific controlled file and recomputes the content fingerprint;
a no-longer-released version, a deleted controlled file, or fingerprint drift =>
SKIPPED/NOT_ELIGIBLE (a documented P1C design choice).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import asc, or_
from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.database import get_db_session
from yuantus.meta_engine.ecm_publication.adapter import EcmPublicationAdapter
from yuantus.meta_engine.ecm_publication.adapter_registry import resolve_adapter
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationReason,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import (
    EcmPublicationOutboxService,
    EcmRevalidation,
    build_snapshot,
    fingerprint,
)
from yuantus.meta_engine.version.models import ItemVersion, VersionFile

logger = logging.getLogger(__name__)

_RETRYABLE = (
    EcmPublicationReason.REMOTE_ERROR.value,
    EcmPublicationReason.ADAPTER_ERROR.value,
)


class EcmPublicationOutboxWorker:
    """Polls + drains the ECM publication outbox. ``run_once_with_session`` is the
    testable core; ``run_once`` wraps it in a managed session; ``run_forever`` is
    the CLI daemon loop."""

    def __init__(
        self,
        worker_id: str,
        *,
        adapter: Optional[EcmPublicationAdapter] = None,
        batch_size: Optional[int] = None,
        backoff_seconds: Optional[int] = None,
        stale_timeout_seconds: Optional[int] = None,
        poll_interval_seconds: Optional[int] = None,
    ) -> None:
        s = get_settings()
        self.worker_id = worker_id
        # No adapter override -> resolve per-row by target_system via the registry
        # (Null in P1C). `adapter` (when set) overrides for tests / a fixed deploy.
        self.adapter = adapter
        # The generic PUBLICATION_OUTBOX_* settings are shared with the erp worker.
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
        service = EcmPublicationOutboxService(session)
        self._reclaim_stale(session)
        rows = self._claim_batch(session)
        for row in rows:
            self._process_row(session, service, row)
        return len(rows)

    # -- steps -----------------------------------------------------------
    def _reclaim_stale(self, session: Session) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            seconds=self.stale_timeout_seconds
        )
        stale = (
            session.query(EcmPublicationOutbox)
            .filter(
                EcmPublicationOutbox.state == EcmPublicationState.PENDING.value,
                EcmPublicationOutbox.worker_id.isnot(None),
                EcmPublicationOutbox.claimed_at.isnot(None),
                EcmPublicationOutbox.claimed_at < cutoff,
            )
            .all()
        )
        for row in stale:
            row.worker_id = None
            row.claimed_at = None
        if stale:
            session.commit()
            logger.warning(
                "ecm-publication-worker '%s' reclaimed %d stale row(s)",
                self.worker_id,
                len(stale),
            )
        return len(stale)

    def _claim_batch(self, session: Session) -> List[EcmPublicationOutbox]:
        now = datetime.now(timezone.utc)
        stale_cutoff = now - timedelta(seconds=self.stale_timeout_seconds)
        dialect = session.bind.dialect.name if session.bind else "unknown"
        query = (
            session.query(EcmPublicationOutbox)
            .filter(
                EcmPublicationOutbox.state == EcmPublicationState.PENDING.value,
                EcmPublicationOutbox.next_attempt_at <= now,
                or_(
                    EcmPublicationOutbox.claimed_at.is_(None),
                    EcmPublicationOutbox.claimed_at < stale_cutoff,
                ),
            )
            .order_by(
                asc(EcmPublicationOutbox.next_attempt_at),
                asc(EcmPublicationOutbox.created_at),
            )
        )
        settings = get_settings()
        configured_target = (
            getattr(settings, "PUBLICATION_ECM_TARGET_SYSTEM", "") or ""
        ).strip()
        if configured_target and not bool(
            getattr(settings, "ECM_PUBLISH_ENABLED", False)
        ):
            # P1D safety: enqueue already honors ECM_PUBLISH_ENABLED; once the
            # adapter is real, a long-running worker must also stop draining the
            # configured live target when ops flips the kill-switch off. Rows for
            # other target systems (or the default Null path with no configured
            # target) keep existing behavior.
            query = query.filter(EcmPublicationOutbox.target_system != configured_target)
        if dialect == "postgresql":
            query = query.with_for_update(skip_locked=True)
        rows = query.limit(self.batch_size).all()
        for row in rows:
            row.worker_id = self.worker_id
            row.claimed_at = now
        if rows:
            session.commit()
        return rows

    def _revalidate(
        self, session: Session, row: EcmPublicationOutbox, version: ItemVersion
    ) -> EcmRevalidation:
        """Build the fresh ECM verdict for `version`/`row`. Ineligible when the
        version is no longer released or the controlled file is gone; otherwise
        carries the recomputed content fingerprint so the service can drop a
        stale snapshot."""
        if not bool(getattr(version, "is_released", False)):
            return EcmRevalidation(eligible=False, version_id=version.id)
        vf = (
            session.query(VersionFile)
            .filter(
                VersionFile.version_id == row.version_id,
                VersionFile.file_id == row.file_id,
                VersionFile.file_role == row.file_role,
            )
            .one_or_none()
        )
        file = getattr(vf, "file", None) if vf is not None else None
        if vf is None or file is None:
            return EcmRevalidation(eligible=False, version_id=version.id)
        snapshot = build_snapshot(
            version, vf, file, target_system=row.target_system
        )
        return EcmRevalidation(
            eligible=True, version_id=version.id, fingerprint=fingerprint(snapshot)
        )

    def _superseded_by_newer_sent(
        self, session: Session, row: EcmPublicationOutbox
    ) -> bool:
        """A1 latest-wins: True iff a SENT row for the SAME
        ``(item_id, file_role, target_system)`` carries a strictly NEWER snapshot
        ``released_at`` than this row. ``released_at`` is a naive ISO string in the
        snapshot (build_snapshot), so a lexicographic compare == chronological."""
        my_released = str((row.snapshot or {}).get("released_at") or "")
        if not my_released:
            return False
        sent = (
            session.query(EcmPublicationOutbox)
            .filter(
                EcmPublicationOutbox.item_id == row.item_id,
                EcmPublicationOutbox.file_role == row.file_role,
                EcmPublicationOutbox.target_system == row.target_system,
                EcmPublicationOutbox.state == EcmPublicationState.SENT.value,
                EcmPublicationOutbox.id != row.id,
            )
            .all()
        )
        for other in sent:
            other_released = str((other.snapshot or {}).get("released_at") or "")
            if other_released and other_released > my_released:
                return True
        return False

    def _process_row(
        self,
        session: Session,
        service: EcmPublicationOutboxService,
        row: EcmPublicationOutbox,
    ) -> None:
        version = session.get(ItemVersion, row.version_id)
        if version is None:
            # Backing version gone -> cannot revalidate -> cannot safely send.
            service._mark_revalidated_not_eligible(row)
            row.worker_id = None
            row.claimed_at = None
            session.commit()
            return

        # A1 ordering guard (latest-wins): if a SENT row for the same lineage already
        # carries a NEWER released_at, this row is superseded -> SKIPPED/not_eligible,
        # never dispatched (the stable (item, file_role) Athena identity would otherwise
        # let an out-of-order older publish overwrite the newer content).
        if self._superseded_by_newer_sent(session, row):
            row.state = EcmPublicationState.SKIPPED.value
            row.reason = EcmPublicationReason.NOT_ELIGIBLE.value
            row.properties = {**(row.properties or {}), "superseded_by_newer_sent": True}
            row.worker_id = None
            row.claimed_at = None
            session.commit()
            logger.info(
                "ecm-publication-worker '%s' skipped row %s: superseded by a newer SENT "
                "publish for (item=%s role=%s).",
                self.worker_id,
                row.id,
                row.item_id,
                row.file_role,
            )
            return

        def _revalidate() -> EcmRevalidation:
            return self._revalidate(session, row, version)

        adapter = self.adapter or resolve_adapter(row.target_system)
        attempt_before = row.attempt_count or 0
        try:
            service.process(row, adapter, revalidate=_revalidate)
        except Exception as exc:  # never crash the loop (e.g. revalidate raised)
            session.rollback()
            logger.error(
                "ecm-publication-worker '%s' error processing row %s: %s",
                self.worker_id,
                row.id,
                exc,
                exc_info=True,
            )
            # A process() exception CONSUMES an attempt so it cannot defer forever:
            # release the claim and dead-letter at max_attempts, else flat backoff.
            row = session.get(EcmPublicationOutbox, row.id)
            if row is not None:
                row.attempt_count = (row.attempt_count or 0) + 1
                row.worker_id = None
                row.claimed_at = None
                row.error_message = str(exc)
                if (row.attempt_count or 0) >= (row.max_attempts or 0):
                    row.state = EcmPublicationState.FAILED.value
                    row.reason = EcmPublicationReason.REMOTE_ERROR.value
                else:
                    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                        seconds=max(self.backoff_seconds, 1)
                    )
                session.commit()
            return

        if row.state == EcmPublicationState.FAILED.value and row.reason in _RETRYABLE:
            service.reschedule_retry(
                row,
                attempt_count_before=attempt_before,
                backoff_seconds=self.backoff_seconds,
            )
        session.commit()

    # -- daemon loop -----------------------------------------------------
    def run_forever(self) -> None:
        self._running = True
        logger.info("ecm-publication-worker '%s' started", self.worker_id)
        while self._running:
            try:
                self.run_once()
            except Exception as exc:  # keep the daemon alive across ticks
                logger.error(
                    "ecm-publication-worker '%s' tick error: %s",
                    self.worker_id,
                    exc,
                    exc_info=True,
                )
            time.sleep(self.poll_interval_seconds)

    def stop(self) -> None:
        self._running = False
