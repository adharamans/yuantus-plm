"""License revocation (L4 Fork C) — append-only, no implicit rollback.

Revoking sets ``AppLicense.status='Revoked'`` (so ``EntitlementService.is_entitled``,
which requires ``status=='Active'``, flips the feature off) and writes a meta-side
LICENSE audit row. Per the ratified principle it is **append-only**: it does NOT
clear the seat cap (``TenantQuota.max_users``) — cap rollback, if ever wanted, is a
separate explicit operation with its own previous-state record. Re-importing the same
signed license re-activates it (revoke is an admin operator action, not a seal).
"""
from __future__ import annotations

from typing import List, Optional
from urllib.parse import quote

from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.models.audit import AuditLog


class LicenseRevocationService:
    def __init__(self, session: Session):
        self.session = session

    def revoke_license(
        self, license_key: str, *, reason: str, revoked_by: Optional[int] = None
    ) -> Optional[List[AppLicense]]:
        """Mark the AppLicense row(s) for ``license_key`` as Revoked (append-only).

        Returns the affected rows, or ``None`` if no license matches the key.
        Idempotent: re-revoking an already-Revoked license is a no-op that still
        returns the rows (and records another audit line). Does NOT touch the seat cap.
        """
        key = (license_key or "").strip()
        if not key:
            raise ValueError("license_key is required (got blank/whitespace)")
        rows = (
            self.session.query(AppLicense)
            .filter(AppLicense.license_key == key)
            .all()
        )
        if not rows:
            return None
        for lic in rows:
            lic.status = "Revoked"
        # Append-only audit; the reason rides in the path (AuditLog has no detail column),
        # url-quoted + bounded to the column width. NOTE: no seat-cap clear here.
        path = f"admin:license/revoke?license_key={quote(key, safe='')}&reason={quote(reason or '', safe='')}"
        self.session.add(
            AuditLog(
                tenant_id=rows[0].tenant_id,
                user_id=revoked_by,
                method="LICENSE",
                path=path[:500],
                status_code=200,
                duration_ms=0,
            )
        )
        self.session.flush()
        return rows
