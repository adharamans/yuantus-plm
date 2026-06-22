"""ECM-P1B enqueue service.

`enqueue_release(version, user_id)` snapshots the released version's controlled files
(one outbox row per file) for later async publication. **Pure DB: no remote I/O, no file
byte reads** (the content fingerprint uses `FileContainer.checksum`, with a composed
non-blocking fallback per taskbook D3). Idempotent; a changed fingerprint vs an
already-SENT row is **recorded (conflict-as-audit), never raised** — the call site is
`release()`, which must never fail.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional

from .adapter import EcmPublicationAdapter
from .models import (
    DEFAULT_ECM_TARGET_SYSTEM,
    EcmPublicationOutbox,
    EcmPublicationReason,
    EcmPublicationState,
)


class EcmPublicationReplayError(Exception):
    """Replay requested on a non-replayable row (wrong state or non-retryable
    reason). Mirrors erp_publication.PublicationReplayError."""


@dataclass
class EcmRevalidation:
    """The ECM analog of erp's readiness verdict, consumed by
    ``_revalidate_allows_send`` before a ``sent`` transition. ``eligible`` =
    the version is still released and the controlled file is still present;
    ``fingerprint`` = the freshly recomputed content fingerprint (drift vs the
    enqueued ``payload_fingerprint`` means the snapshot is stale and must not be
    sent)."""

    eligible: bool
    version_id: Optional[str] = None
    fingerprint: Optional[str] = None

# Controlled-record file roles published to ECM (engineering deliverables; not
# previews/loose attachments). Tuple, lower-case compared.
CONTROLLED_FILE_ROLES = ("native_cad", "drawing", "geometry")

# Snapshot keys excluded from the content fingerprint. The fingerprint must be
# STABLE across the enqueue session (release()) and the worker's SEPARATE revalidate
# session, which reloads the version from the DB. It must therefore not depend on a
# datetime's SERIALIZED REPRESENTATION: a value that round-trips with a different
# isoformat (e.g. naive "...T00:00:00" vs tz-aware "...T00:00:00+00:00") would look
# like content drift and wrongly SKIP the row. ItemVersion.released_at is currently a
# naive DateTime (TIMESTAMP WITHOUT TIME ZONE), so it round-trips naive today and the
# drift does NOT occur -- but released_at is immutable provenance, not file content
# (the real drift signal is content_fingerprint_basis), so excluding it makes the
# fingerprint robust regardless (e.g. a future timestamptz migration, or any reloaded
# datetime added to build_snapshot). ("snapshotted_at" is reserved / not emitted.)
_VOLATILE_SNAPSHOT_KEYS = frozenset({"snapshotted_at", "released_at"})


def _content_fingerprint_basis(file: Any, vf: Any, version: Any) -> str:
    """D3: prefer the FileContainer checksum; the fallback is a composed,
    non-blocking hash (NO byte reads / no `download_file`)."""
    checksum = getattr(file, "checksum", None)
    if checksum:
        return f"checksum:{checksum}"
    parts = [
        str(getattr(file, "id", "") or ""),
        str(getattr(file, "system_path", "") or ""),
        str(getattr(file, "file_size", "") or ""),
        str(getattr(file, "mime_type", "") or ""),
        str(getattr(version, "generation", "") or ""),
        str(getattr(version, "revision", "") or ""),
        str(getattr(vf, "file_role", "") or ""),
    ]
    return "composed:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def build_snapshot(version: Any, vf: Any, file: Any, *, target_system: str) -> dict:
    released_at = getattr(version, "released_at", None)
    return {
        "item_id": version.item_id,
        "version_id": version.id,
        "version_label": getattr(version, "version_label", None),
        "generation": getattr(version, "generation", None),
        "revision": getattr(version, "revision", None),
        "file_id": vf.file_id,
        "file_role": vf.file_role,
        "filename": getattr(file, "filename", None),
        # Dispatch-time content read key. Enqueue remains byte-free: this is just
        # the storage path/key already present on FileContainer.
        "system_path": getattr(file, "system_path", None),
        "mime_type": getattr(file, "mime_type", None),
        "file_size": getattr(file, "file_size", None),
        "cad_format": getattr(file, "cad_format", None),
        "content_fingerprint_basis": _content_fingerprint_basis(file, vf, version),
        "released_at": released_at.isoformat() if released_at else None,
        "released_by_id": getattr(version, "released_by_id", None),
        "target_system": target_system,
    }


def fingerprint(snapshot: dict) -> str:
    """SHA-256 over the snapshot CONTENT (volatile keys excluded)."""
    content = {k: v for k, v in snapshot.items() if k not in _VOLATILE_SNAPSHOT_KEYS}
    blob = json.dumps(content, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EcmPublicationOutboxService:
    def __init__(self, session) -> None:
        self.session = session

    def _find(
        self,
        item_id: str,
        version_id: str,
        file_id: str,
        file_role: str,
        target_system: str,
    ) -> Optional[EcmPublicationOutbox]:
        return (
            self.session.query(EcmPublicationOutbox)
            .filter_by(
                item_id=item_id,
                version_id=version_id,
                file_id=file_id,
                file_role=file_role,
                target_system=target_system,
            )
            .one_or_none()
        )

    def enqueue_release(
        self,
        version: Any,
        *,
        user_id: Optional[int] = None,
        target_system: str = DEFAULT_ECM_TARGET_SYSTEM,
        controlled_roles=CONTROLLED_FILE_ROLES,
    ) -> List[EcmPublicationOutbox]:
        """Enqueue the released version's controlled files (one row per file). Pure DB;
        no remote I/O, no byte reads. Idempotent + conflict-as-audit. Returns the rows."""
        roles = {str(r).lower() for r in controlled_roles}
        rows: List[EcmPublicationOutbox] = []
        for vf in (getattr(version, "version_files", None) or []):
            if str(getattr(vf, "file_role", "") or "").lower() not in roles:
                continue
            file = getattr(vf, "file", None)
            if file is None:
                continue
            snapshot = build_snapshot(version, vf, file, target_system=target_system)
            fp = fingerprint(snapshot)
            existing = self._find(
                version.item_id, version.id, vf.file_id, vf.file_role, target_system
            )
            if existing is not None:
                rows.append(self._enqueue_existing(existing, snapshot, fp))
                continue
            row = EcmPublicationOutbox(
                id=uuid.uuid4().hex,
                item_id=version.item_id,
                version_id=version.id,
                file_id=vf.file_id,
                file_role=vf.file_role,
                target_system=target_system,
                snapshot=snapshot,
                payload_fingerprint=fp,
                state=EcmPublicationState.PENDING.value,
                reason=None,
                created_by_id=user_id if isinstance(user_id, int) else None,
            )
            self.session.add(row)
            self.session.flush()
            rows.append(row)
        return rows

    def _enqueue_existing(
        self, existing: EcmPublicationOutbox, snapshot: dict, fp: str
    ) -> EcmPublicationOutbox:
        if existing.payload_fingerprint == fp:
            return existing  # idempotent reuse — unchanged content
        if existing.state == EcmPublicationState.SENT.value:
            # D3 / R3: a changed fingerprint vs an already-SENT row is conflict-as-audit
            # -- record it on the published row, do NOT raise (call site is release()).
            existing.properties = {
                **(existing.properties or {}),
                "conflict_after_sent": True,
                "conflict_fingerprint": fp,
                "conflict_basis": snapshot.get("content_fingerprint_basis"),
            }
            self.session.flush()
            return existing
        # non-terminal (pending/failed/skipped/dry_run_ready): re-snapshot in place.
        existing.snapshot = snapshot
        existing.payload_fingerprint = fp
        existing.state = EcmPublicationState.PENDING.value
        existing.reason = None
        existing.error_message = None
        existing.properties = {**(existing.properties or {}), "re_snapshotted": True}
        self.session.flush()
        return existing

    # -- revalidation helpers (mirror erp_publication.service) -----------
    @staticmethod
    def _fresh_version_id(fresh: Any) -> Optional[str]:
        version_id = getattr(fresh, "version_id", None)
        return str(version_id) if version_id else None

    def _mark_revalidated_not_eligible(
        self,
        row: EcmPublicationOutbox,
        *,
        version_mismatch: bool = False,
        fingerprint_drift: bool = False,
        fresh_version_id: Optional[str] = None,
    ) -> EcmPublicationOutbox:
        row.state = EcmPublicationState.SKIPPED.value
        row.reason = EcmPublicationReason.NOT_ELIGIBLE.value
        props = {**(row.properties or {}), "revalidated_ineligible": True}
        if version_mismatch:
            # The published version itself changed (reserved; ECM rows are
            # per-(version,file) so the worker fetches by row.version_id).
            props["revalidated_version_mismatch"] = True
            props["revalidated_version_id"] = fresh_version_id
        if fingerprint_drift:
            # Same version, but the controlled file's content changed since
            # enqueue -> the snapshot is stale and must not be published.
            props["revalidated_fingerprint_drift"] = True
            props["revalidated_version_id"] = fresh_version_id
        row.properties = props
        self.session.flush()
        return row

    def _revalidate_allows_send(
        self, row: EcmPublicationOutbox, fresh: Any
    ) -> bool:
        """True iff the row may transition to ``sent``. False (and the row is
        marked SKIPPED/NOT_ELIGIBLE) when the version is no longer eligible OR the
        recomputed content fingerprint drifted from the enqueued snapshot (a stale
        snapshot must never be published)."""
        if not bool(getattr(fresh, "eligible", False)):
            self._mark_revalidated_not_eligible(row)
            return False
        fresh_fp = getattr(fresh, "fingerprint", None)
        if fresh_fp is not None and fresh_fp != row.payload_fingerprint:
            # content drift on the SAME version -> distinct audit flag (not
            # version_mismatch, whose revalidated_version_id would == row.version_id).
            self._mark_revalidated_not_eligible(
                row,
                fingerprint_drift=True,
                fresh_version_id=self._fresh_version_id(fresh),
            )
            return False
        return True

    def _fail_adapter_error(
        self, row: EcmPublicationOutbox, exc: Exception
    ) -> EcmPublicationOutbox:
        row.state = EcmPublicationState.FAILED.value
        row.reason = EcmPublicationReason.ADAPTER_ERROR.value
        row.error_message = str(exc)
        self.session.flush()
        return row

    # -- process (send) -------------------------------------------------
    def process(
        self,
        row: EcmPublicationOutbox,
        adapter: EcmPublicationAdapter,
        *,
        revalidate: Optional[Callable[[], Any]] = None,
    ) -> EcmPublicationOutbox:
        """Mirror of erp_publication.process: revalidate -> build_payload ->
        validate_contract -> send, with the single pre-send attempt increment
        (guard #1) and adapter-error classification."""
        if row.state not in (
            EcmPublicationState.PENDING.value,
            EcmPublicationState.DRY_RUN_READY.value,
        ):
            raise EcmPublicationReplayError(
                f"state {row.state!r} cannot be processed directly "
                "(only pending / dry_run_ready)"
            )

        if revalidate is not None:
            fresh = revalidate()
            if not self._revalidate_allows_send(row, fresh):
                return row

        try:
            payload = adapter.build_payload(row.snapshot)
            result = adapter.validate_contract(payload)
        except Exception as exc:  # pre-send adapter error (count NOT yet bumped)
            return self._fail_adapter_error(row, exc)
        if not result.ok:
            row.state = EcmPublicationState.FAILED.value
            row.reason = EcmPublicationReason.VALIDATION_ERROR.value
            row.error_message = "; ".join(result.errors) or "validation failed"
            self.session.flush()
            return row

        row.attempt_count = (row.attempt_count or 0) + 1  # the ONLY pre-send bump
        try:
            send_result = adapter.send(payload)
        except Exception as exc:  # the adapter itself broke (count WAS bumped)
            return self._fail_adapter_error(row, exc)

        if getattr(send_result, "ok", False):
            row.state = EcmPublicationState.SENT.value
            row.reason = None
            row.error_message = None
            row.dispatched_at = datetime.now(timezone.utc)
            # merge (do not overwrite): the real Transfer Receiver adapter may add
            # athena_document_id/athena_disposition alongside remote_id.
            row.properties = {
                **(row.properties or {}),
                "remote_id": getattr(send_result, "remote_id", None),
                **(getattr(send_result, "properties", None) or {}),
            }
        else:
            row.state = EcmPublicationState.FAILED.value
            row.reason = (
                getattr(send_result, "error_kind", None)
                or EcmPublicationReason.REMOTE_ERROR.value
            )
            row.error_message = getattr(send_result, "error", None)
        self.session.flush()
        return row

    # -- worker-side deferred retry -------------------------------------
    def reschedule_retry(
        self,
        row: EcmPublicationOutbox,
        *,
        attempt_count_before: int,
        backoff_seconds: int,
        now: Optional[datetime] = None,
    ) -> EcmPublicationOutbox:
        """Deferred retry of a just-processed failed row (worker path). Retries
        ONLY remote_error/adapter_error; leaves the terminal reasons
        (not_eligible/config_missing/conflict/validation_error) failed. Guard #1:
        a pre-send adapter_error never hit the increment, so count it once here
        (detected via attempt_count_before) or it would retry forever at 0. At
        max_attempts the row stays failed (dead-letter); else PENDING with the
        claim cleared and linear backoff ``backoff_seconds * attempt_count``."""
        if row.state != EcmPublicationState.FAILED.value:
            return row
        if row.reason not in (
            EcmPublicationReason.REMOTE_ERROR.value,
            EcmPublicationReason.ADAPTER_ERROR.value,
        ):
            return row  # terminal reason -> stays failed

        if (row.attempt_count or 0) == (attempt_count_before or 0):
            row.attempt_count = (row.attempt_count or 0) + 1

        if (row.attempt_count or 0) >= (row.max_attempts or 0):
            row.worker_id = None
            row.claimed_at = None
            self.session.flush()
            return row

        now = now or datetime.now(timezone.utc)
        backoff = max(int(backoff_seconds), 0) * max(row.attempt_count or 1, 1)
        row.state = EcmPublicationState.PENDING.value
        row.reason = None
        row.error_message = None
        row.worker_id = None
        row.claimed_at = None
        row.next_attempt_at = now + timedelta(seconds=backoff)
        self.session.flush()
        return row

    # -- replay (operator action: pure failed->pending reset) -----------
    def replay(self, row: EcmPublicationOutbox) -> EcmPublicationOutbox:
        """Operator replay from the ops router: reset a retryable FAILED row to
        PENDING for the worker to pick up again. PURE state reset -- NO adapter
        resend here (the worker's live Transfer Receiver adapter does the resend on its
        next tick). Only remote_error /
        adapter_error are replayable; the terminal reasons raise
        EcmPublicationReplayError (router -> 409). attempt_count is RESET to 0 so a
        dead-lettered row gets fresh retries (a deliberate P1C choice; documented
        in the DEV/V doc)."""
        if row.state != EcmPublicationState.FAILED.value:
            raise EcmPublicationReplayError(
                f"state {row.state!r} is not replayable (only failed)"
            )
        if row.reason not in (
            EcmPublicationReason.REMOTE_ERROR.value,
            EcmPublicationReason.ADAPTER_ERROR.value,
        ):
            raise EcmPublicationReplayError(
                f"reason {row.reason!r} is not retryable "
                "(only remote_error / adapter_error)"
            )
        row.state = EcmPublicationState.PENDING.value
        row.reason = None
        row.error_message = None
        row.attempt_count = 0
        row.worker_id = None
        row.claimed_at = None
        row.next_attempt_at = datetime.now(timezone.utc)
        row.properties = {**(row.properties or {}), "replayed": True}
        self.session.flush()
        return row
