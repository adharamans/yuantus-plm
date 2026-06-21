"""Read-only license/entitlement status for the offline support bundle (Phase 4).

A tenant-scoped, read-only summary of which collaboration SKUs a tenant currently
holds -- computed via the centralized ``EntitlementService.is_entitled`` (NOT a second,
unscoped auth path; "entitlement is centralized" is a non-negotiable invariant) -- plus
a WHITELISTED summary of its ``AppLicense`` rows. It never emits private key material or
the raw ``license_data`` blob, so it is safe to print in an operator support bundle.

Driven by ``yuantus license status --tenant-id <tenant>``; no route, no mutation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from yuantus.context import tenant_id_var
from yuantus.meta_engine.app_framework.entitlement_service import (
    FEATURE_APP_NAMES,
    EntitlementService,
)
from yuantus.meta_engine.app_framework.store_models import AppLicense


@dataclass
class LicenseRowStatus:
    """A single ``AppLicense`` row, reduced to operator-safe fields only."""

    app_name: str
    status: str
    plan_type: Optional[str]
    expires_at: Optional[str]  # ISO-8601, or None for a perpetual license
    license_key: str


@dataclass
class LicenseStatus:
    tenant_id: str
    features: Dict[str, bool]  # lit feature_key -> entitled (via is_entitled)
    licenses: List[LicenseRowStatus] = field(default_factory=list)


# The lit (license-unlockable) SKUs. Reserved keys map to an empty app-name set and are
# always False, so they are not reported as sellable features.
_LIT_FEATURES = tuple(sorted(k for k, apps in FEATURE_APP_NAMES.items() if apps))


def collect_license_status(session: Session, tenant_id: str) -> LicenseStatus:
    """Tenant-scoped, read-only entitlement + license summary. Key-leak-safe.

    Entitlement is decided ONLY by ``EntitlementService.is_entitled`` (the centralized
    gate); the ``AppLicense`` query here is a display summary, never an auth decision.
    """
    tenant_id = str(tenant_id or "").strip()

    # is_entitled() scopes via tenant_id_var (resolve_license_scope); set it for the read.
    token = tenant_id_var.set(tenant_id)
    try:
        svc = EntitlementService(session)
        features = {key: svc.is_entitled(key) for key in _LIT_FEATURES}
    finally:
        tenant_id_var.reset(token)

    rows = (
        session.query(AppLicense)
        .filter(AppLicense.tenant_id == tenant_id)
        .order_by(AppLicense.app_name)
        .all()
    )
    licenses = [
        LicenseRowStatus(
            app_name=row.app_name,
            status=row.status,
            plan_type=row.plan_type,
            expires_at=row.expires_at.isoformat() if row.expires_at is not None else None,
            license_key=row.license_key,
        )
        for row in rows
    ]
    return LicenseStatus(tenant_id=tenant_id, features=features, licenses=licenses)
