"""Offline license import: verify a signed license file and activate it (P1-C).

Verifies the Ed25519 signature (``license_verification``), then activates the
license by upserting tenant-scoped ``AppLicense`` rows -- one per app_name in the
SIGNED payload. The tenant comes from the signed payload (NOT request context), so
a license signed for tenant X activates only for tenant X; the P1-B kernel then
matches the runtime tenant against it.

Verification metadata is written into ``AppLicense.license_data`` (NOT an
authorization source -- the auth source remains app_name / status / expires_at /
tenant_id plus the P1-B kernel) and an audit row. No route; driven by the
``yuantus license import`` CLI.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.license_verification import (
    canonical_payload_bytes,
    verify_license,
)
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.models.audit import AuditLog


@dataclass
class LicenseImportResult:
    """Result of :meth:`LicenseImportService.import_license`.

    ``activated`` is the list of upserted ``AppLicense`` rows; ``payload`` is the
    VERIFIED, signature-covered payload (the dict ``verify_license`` returned). Callers
    needing post-import data (e.g. the CLI's seat-cap projection) read it from here
    instead of re-reading the raw, unverified ``license_obj["payload"]`` off disk.
    """

    activated: List[AppLicense]
    payload: Mapping[str, Any]
    # the NORMALIZED (stripped) tenant id the activation + projection use, so downstream
    # callers (e.g. the CLI seat-cap audit) record the SAME tenant, never a raw padded value.
    tenant_id: str


def _parse_dt(value: Any) -> Optional[datetime]:
    """Parse an ISO-8601 string to a naive UTC datetime (matches the model + P1-B)."""
    if value in (None, ""):
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class LicenseImportService:
    def __init__(self, session: Session):
        self.session = session

    def import_license(
        self,
        license_obj: Mapping[str, Any],
        public_keys: Mapping[str, str],
        *,
        installed_by: Optional[int] = None,
    ) -> "LicenseImportResult":
        # Verify first; raises LicenseVerificationError on any failure.
        payload = verify_license(license_obj, public_keys)

        tenant_id = str(payload.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError(
                "license payload missing tenant_id (offline licenses are tenant-scoped)"
            )
        app_names = list(payload.get("app_names") or [])
        if not app_names:
            raise ValueError("license payload has no app_names to activate")
        base_key = str(payload.get("license_key") or "").strip()
        if not base_key:
            raise ValueError("license payload missing license_key")

        meta = {
            "payload_hash": hashlib.sha256(canonical_payload_bytes(payload)).hexdigest(),
            "kid": license_obj.get("kid"),
            "subject": payload.get("subject"),
            "verified_at": datetime.utcnow().isoformat() + "Z",
            "features": list(payload.get("features") or []),
        }
        issued_at = _parse_dt(payload.get("issued_at")) or datetime.utcnow()
        expires_at = _parse_dt(payload.get("expires_at"))
        plan_type = payload.get("plan_type")

        activated: List[AppLicense] = []
        for app_name in app_names:
            lic_key = base_key if len(app_names) == 1 else f"{base_key}#{app_name}"
            lic = self.session.query(AppLicense).filter_by(license_key=lic_key).first()
            if lic is None:
                lic = AppLicense(id=uuid.uuid4().hex, license_key=lic_key)
                self.session.add(lic)
            lic.app_name = app_name
            lic.tenant_id = tenant_id  # trusted: from the signed payload
            lic.plan_type = plan_type
            lic.status = "Active"
            lic.issued_at = issued_at
            lic.expires_at = expires_at
            # verification metadata only -- NOT an authorization source
            lic.license_data = dict(meta)
            activated.append(lic)

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=installed_by,
                method="LICENSE",
                path="cli:license/import",
                status_code=200,
                duration_ms=0,
            )
        )
        return LicenseImportResult(activated=activated, payload=payload, tenant_id=tenant_id)


def record_seat_cap_audit(
    meta_session: Session,
    *,
    tenant_id: str,
    max_users: Optional[int],
    installed_by: Optional[int] = None,
) -> None:
    """Write a meta-side audit row noting a seat cap was PROJECTED (set to N), or CLEARED
    (explicit seats:null -> unlimited), at license import.

    Mirrors the import audit (``method="LICENSE"``, synthetic CLI ``path``). Kept
    meta-side -- where ``audit_logs`` reliably lives and the import audit already is --
    not the identity DB: ``audit_logs`` is not guaranteed there under
    ``SCHEMA_MODE=migrations``, and a failed audit write in the identity session would
    roll back the seat-cap projection itself. The caller commits; this is best-effort
    observability, decoupled from the cap write.

    ``project_license_seats`` reports a set-or-clear outcome on every valid import (idempotent
    re-imports included), so this records "projected", not "changed" -- consistent with
    the import audit, which likewise fires each run. The value rides in ``path`` because
    ``AuditLog`` has no structured-detail column.
    """
    # ``max_users is None`` marks a CLEAR (explicit seats:null) -> record "cleared", never a
    # false ``?max_users=N``. A set keeps the numeric value, as before.
    cap = max_users if max_users is not None else "cleared"
    meta_session.add(
        AuditLog(
            tenant_id=tenant_id,
            user_id=installed_by,
            method="LICENSE",
            path=f"cli:license/seat-cap?max_users={cap}",
            status_code=200,
            duration_ms=0,
        )
    )
