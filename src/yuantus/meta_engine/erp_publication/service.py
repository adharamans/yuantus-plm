"""PLM->ERP publication outbox service (G2 R2).

Enqueue / dry-run / process(send) / replay over `ErpPublicationOutbox`, modeled
on the JobService verbs. It CONSUMES the R1-B publication-readiness verdict
(a PublicationReadinessResponse) — it does NOT re-derive eligibility (R2 build
taskbook §8). The verdict is passed in (built via the shared
`build_publication_readiness` so the production path reuses R1-B's exact logic),
and revalidation on `sent` is a `revalidate` callable returning a fresh verdict.

Key behaviors (R2 build taskbook §5/§6/§8):
  - enqueue: snapshot the verdict; eligible -> pending, ineligible(with version)
    -> skipped/not_eligible; versionless -> no row (no version-scoped key).
  - duplicate enqueue: reuse-existing-row (never conflict-fail) EXCEPT changed
    content against an already-`sent` row -> PublicationConflictError. A
    payload_fingerprint over the verdict CONTENT (excluding volatile timestamps)
    distinguishes idempotent re-enqueue from a real content change.
  - dry_run: build_payload + validate_contract only, never send -> dry_run_ready.
  - process: re-validate (D-R2-1) -> send via adapter -> sent / failed+reason.
  - replay: retry failed only for remote_error/adapter_error; re-open a skipped
    row only if revalidate now eligible.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from yuantus.meta_engine.erp_publication.adapter import ErpPublicationAdapter
from yuantus.meta_engine.erp_publication.models import (
    DEFAULT_PUBLICATION_KIND,
    ErpPublicationOutbox,
    ErpPublicationReason,
    ErpPublicationState,
)

# Fields excluded from the content fingerprint: they vary per readiness
# computation even when the verdict is identical, so including them would break
# idempotency (an unchanged verdict would look "changed").
_VOLATILE_SNAPSHOT_KEYS = ("generated_at",)


class PublicationConflictError(Exception):
    """A duplicate enqueue with CHANGED content targets an already-`sent` row.

    The sole case where the outbox does NOT reuse-existing-row: a published
    version must not be silently superseded (R2 build taskbook §6)."""


class PublicationReplayError(Exception):
    """Replay requested on a non-replayable row (wrong state, non-retryable
    reason, or retries exhausted)."""


def build_snapshot(readiness: Any, *, target_system: str, publication_kind: str) -> dict:
    """Map a PublicationReadinessResponse 1:1 to the persisted snapshot dict.

    Duck-typed on purpose so the service does not import web types at runtime.
    Carries the R2 build taskbook §7 fidelity notes (e.g. version.released_at is
    already an ISO string; esign.is_complete may be None != False).
    """
    version = getattr(readiness, "version", None)
    version_block = None
    if version is not None:
        version_block = {
            "version_id": version.version_id,
            "generation": version.generation,
            "revision": version.revision,
            "version_label": version.version_label,
            "state": version.state,
            "is_current": version.is_current,
            "is_released": version.is_released,
            "released_at": version.released_at,
            "primary_file_id": version.primary_file_id,
        }
    summary = readiness.summary
    esign = readiness.esign
    generated_at = getattr(readiness, "generated_at", None)
    return {
        "target_system": target_system,
        "publication_kind": publication_kind,
        "eligible": bool(readiness.eligible),
        "blocking_reasons": [
            {"reason": b.reason, "detail": b.detail} for b in readiness.blocking_reasons
        ],
        "ruleset_id": readiness.ruleset_id,
        "limits": {
            "mbom_limit": readiness.limits.mbom_limit,
            "routing_limit": readiness.limits.routing_limit,
            "baseline_limit": readiness.limits.baseline_limit,
        },
        "item": {
            "item_id": readiness.item.item_id,
            "lifecycle_state": readiness.item.lifecycle_state,
        },
        "version": version_block,
        "file_refs": [
            {
                "file_id": f.file_id,
                "file_role": f.file_role,
                "is_primary": f.is_primary,
                "sequence": f.sequence,
                "snapshot_path": f.snapshot_path,
            }
            for f in readiness.file_refs
        ],
        "summary": {
            "ok": summary.ok,
            "resources": summary.resources,
            "ok_resources": summary.ok_resources,
            "error_count": summary.error_count,
            "warning_count": summary.warning_count,
        },
        "esign": {
            "present": esign.present,
            "is_complete": esign.is_complete,
            "completed_at": esign.completed_at,
        },
        "generated_at": (
            generated_at.isoformat()
            if hasattr(generated_at, "isoformat")
            else generated_at
        ),
    }


def fingerprint(snapshot: dict) -> str:
    """SHA-256 over the verdict CONTENT (volatile timestamps excluded)."""
    content = {k: v for k, v in snapshot.items() if k not in _VOLATILE_SNAPSHOT_KEYS}
    blob = json.dumps(content, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class ErpPublicationOutboxService:
    def __init__(self, session) -> None:
        self.session = session

    # -- helpers ---------------------------------------------------------
    def _find(
        self, item_id: str, version_id: str, target_system: str, publication_kind: str
    ) -> Optional[ErpPublicationOutbox]:
        return (
            self.session.query(ErpPublicationOutbox)
            .filter_by(
                item_id=item_id,
                version_id=version_id,
                target_system=target_system,
                publication_kind=publication_kind,
            )
            .one_or_none()
        )

    @staticmethod
    def _apply_eligibility(row: ErpPublicationOutbox, eligible: bool) -> None:
        if eligible:
            row.state = ErpPublicationState.PENDING.value
            row.reason = None
        else:
            row.state = ErpPublicationState.SKIPPED.value
            row.reason = ErpPublicationReason.NOT_ELIGIBLE.value

    def _fail_adapter_error(
        self, row: ErpPublicationOutbox, exc: Exception
    ) -> ErpPublicationOutbox:
        row.state = ErpPublicationState.FAILED.value
        row.reason = ErpPublicationReason.ADAPTER_ERROR.value
        row.error_message = str(exc)
        self.session.flush()
        return row

    # -- enqueue ---------------------------------------------------------
    def enqueue(
        self,
        *,
        target_system: str,
        readiness: Any,
        publication_kind: str = DEFAULT_PUBLICATION_KIND,
        created_by_id: Optional[int] = None,
    ) -> Optional[ErpPublicationOutbox]:
        version = getattr(readiness, "version", None)
        if version is None:
            # §6.4: no version -> no version-scoped key -> no row. The verdict
            # (skipped / not_eligible) is conveyed by the readiness itself.
            return None

        item_id = readiness.item.item_id
        version_id = version.version_id
        snapshot = build_snapshot(
            readiness, target_system=target_system, publication_kind=publication_kind
        )
        fp = fingerprint(snapshot)

        existing = self._find(item_id, version_id, target_system, publication_kind)
        if existing is not None:
            return self._enqueue_existing(existing, readiness, snapshot, fp)

        row = ErpPublicationOutbox(
            id=uuid.uuid4().hex,
            item_id=item_id,
            version_id=version_id,
            target_system=target_system,
            publication_kind=publication_kind,
            snapshot=snapshot,
            payload_fingerprint=fp,
            created_by_id=created_by_id,
        )
        self._apply_eligibility(row, bool(readiness.eligible))
        self.session.add(row)
        self.session.flush()
        return row

    def _enqueue_existing(
        self,
        existing: ErpPublicationOutbox,
        readiness: Any,
        snapshot: dict,
        fp: str,
    ) -> ErpPublicationOutbox:
        if existing.payload_fingerprint == fp:
            return existing  # idempotent reuse
        if existing.state == ErpPublicationState.SENT.value:
            raise PublicationConflictError(
                f"outbox row {existing.id} already sent; refusing to supersede a "
                f"published version with changed content"
            )
        # non-terminal / skipped: re-snapshot in place (latest enqueue wins).
        existing.snapshot = snapshot
        existing.payload_fingerprint = fp
        existing.error_message = None
        existing.properties = {
            **(existing.properties or {}),
            "re_snapshotted": True,
        }
        self._apply_eligibility(existing, bool(readiness.eligible))
        self.session.flush()
        return existing

    # -- dry-run (NO send) ----------------------------------------------
    def dry_run(
        self, row: ErpPublicationOutbox, adapter: ErpPublicationAdapter
    ) -> ErpPublicationOutbox:
        if row.state == ErpPublicationState.SENT.value:
            raise PublicationReplayError("cannot dry-run an already-sent row")
        if row.reason == ErpPublicationReason.NOT_ELIGIBLE.value:
            return row  # ineligible: nothing to validate/publish
        try:
            payload = adapter.build_payload(row.snapshot)
            result = adapter.validate_contract(payload)
        except Exception as exc:
            return self._fail_adapter_error(row, exc)
        if not result.ok:
            row.state = ErpPublicationState.FAILED.value
            row.reason = ErpPublicationReason.VALIDATION_ERROR.value
            row.error_message = "; ".join(result.errors) or "validation failed"
        else:
            row.state = ErpPublicationState.DRY_RUN_READY.value
            row.reason = None
            row.error_message = None
        self.session.flush()
        return row

    # -- process (send) -------------------------------------------------
    def process(
        self,
        row: ErpPublicationOutbox,
        adapter: ErpPublicationAdapter,
        *,
        revalidate: Optional[Callable[[], Any]] = None,
    ) -> ErpPublicationOutbox:
        if row.state not in (
            ErpPublicationState.PENDING.value,
            ErpPublicationState.DRY_RUN_READY.value,
        ):
            raise PublicationReplayError(
                f"state {row.state!r} cannot be processed directly "
                "(only pending / dry_run_ready)"
            )

        # D-R2-1: re-validate eligibility for a `sent` transition.
        if revalidate is not None:
            fresh = revalidate()
            if not bool(fresh.eligible):
                row.state = ErpPublicationState.SKIPPED.value
                row.reason = ErpPublicationReason.NOT_ELIGIBLE.value
                row.properties = {
                    **(row.properties or {}),
                    "revalidated_ineligible": True,
                }
                self.session.flush()
                return row

        try:
            payload = adapter.build_payload(row.snapshot)
            result = adapter.validate_contract(payload)
        except Exception as exc:
            return self._fail_adapter_error(row, exc)
        if not result.ok:
            row.state = ErpPublicationState.FAILED.value
            row.reason = ErpPublicationReason.VALIDATION_ERROR.value
            row.error_message = "; ".join(result.errors) or "validation failed"
            self.session.flush()
            return row

        row.attempt_count = (row.attempt_count or 0) + 1
        try:
            send_result = adapter.send(payload)
        except Exception as exc:  # the adapter itself broke
            return self._fail_adapter_error(row, exc)

        if getattr(send_result, "ok", False):
            row.state = ErpPublicationState.SENT.value
            row.reason = None
            row.error_message = None
            row.dispatched_at = datetime.now(timezone.utc)
            row.properties = {
                **(row.properties or {}),
                "remote_id": getattr(send_result, "remote_id", None),
            }
        else:
            row.state = ErpPublicationState.FAILED.value
            row.reason = (
                getattr(send_result, "error_kind", None)
                or ErpPublicationReason.REMOTE_ERROR.value
            )
            row.error_message = getattr(send_result, "error", None)
        self.session.flush()
        return row

    # -- replay ----------------------------------------------------------
    def replay(
        self,
        row: ErpPublicationOutbox,
        adapter: ErpPublicationAdapter,
        *,
        revalidate: Optional[Callable[[], Any]] = None,
    ) -> ErpPublicationOutbox:
        if row.state == ErpPublicationState.FAILED.value:
            if row.reason not in (
                ErpPublicationReason.REMOTE_ERROR.value,
                ErpPublicationReason.ADAPTER_ERROR.value,
            ):
                raise PublicationReplayError(
                    f"reason {row.reason!r} is not retryable "
                    f"(only remote_error / adapter_error)"
                )
            if (row.attempt_count or 0) >= (row.max_attempts or 0):
                raise PublicationReplayError("max attempts exhausted (dead-letter)")
            row.state = ErpPublicationState.PENDING.value
            row.reason = None
            row.error_message = None
            return self.process(row, adapter, revalidate=revalidate)

        if row.state == ErpPublicationState.SKIPPED.value:
            if revalidate is None:
                raise PublicationReplayError(
                    "skipped row requires a revalidate callable to re-open"
                )
            fresh = revalidate()
            if not bool(fresh.eligible):
                return row  # still ineligible
            snapshot = build_snapshot(
                fresh,
                target_system=row.target_system,
                publication_kind=row.publication_kind,
            )
            row.snapshot = snapshot
            row.payload_fingerprint = fingerprint(snapshot)
            row.state = ErpPublicationState.PENDING.value
            row.reason = None
            return self.process(row, adapter, revalidate=revalidate)

        raise PublicationReplayError(
            f"state {row.state!r} is not replayable (only failed / skipped)"
        )
