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
from typing import Any, List, Optional

from .models import (
    DEFAULT_ECM_TARGET_SYSTEM,
    EcmPublicationOutbox,
    EcmPublicationState,
)

# Controlled-record file roles published to ECM (engineering deliverables; not
# previews/loose attachments). Tuple, lower-case compared.
CONTROLLED_FILE_ROLES = ("native_cad", "drawing", "geometry")

# Snapshot keys excluded from the content fingerprint (volatile / non-content).
_VOLATILE_SNAPSHOT_KEYS = frozenset({"snapshotted_at"})


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
