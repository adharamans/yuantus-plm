"""Entitlement-check kernel for PLM Collaboration features (PLM-COLLAB-P1-B).

`is_entitled(feature_key)` converges the "is this feature available for the current
tenant?" decision to ONE place. It reuses the P1-A tenant scope resolver
(``license_scope.resolve_license_scope``) so there is no second, unscoped license
read path -- a NULL-tenant or other-tenant license can never unlock the current
tenant, and a non-single deployment without tenant context raises (it is not
swallowed into a plain False).

P1-B lights only ``plm_collaboration_pro``. The other canonical feature keys are
accepted (so a typo is caught, not silently treated as unlicensed) but map to an
empty app-name set -> always False until a later slice lights them. ``license_data``
is NOT an authorization source in P1-B.
"""
from __future__ import annotations

from datetime import datetime
from typing import FrozenSet, Mapping

from sqlalchemy import or_
from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.app_framework.store_models import AppLicense

# feature_key -> the app_name(s) whose active, unexpired, tenant-scoped license
# grants it. Locked in code; license_data is NOT consulted.
FEATURE_APP_NAMES: Mapping[str, FrozenSet[str]] = {
    "plm_collaboration_pro": frozenset({"plm.collab"}),
    # reserved (canonical 6.1 vocabulary) -- accepted but not license-unlockable yet:
    "plm": frozenset(),
    "bom_multitable": frozenset(),
    "approval_automation": frozenset(),
    "automation_enterprise": frozenset(),
    "plm_offline_license": frozenset(),
}


class EntitlementService:
    """Single source of truth for "is feature X available for the current tenant?"."""

    def __init__(self, session: Session):
        self.session = session

    def is_entitled(self, feature_key: str) -> bool:
        if feature_key not in FEATURE_APP_NAMES:
            # Fail loud so a typo never silently reads as "not entitled".
            raise ValueError(f"unknown feature_key: {feature_key!r}")
        app_names = FEATURE_APP_NAMES[feature_key]
        if not app_names:
            return False  # reserved but not yet lit in P1-B
        # Reuses the P1-A resolver: tenant_id participates in the filter, org_id is
        # recorded only; a missing tenant in non-single mode raises here.
        tenant_id, _ = resolve_license_scope()
        now = datetime.utcnow()
        lic = (
            self.session.query(AppLicense)
            .filter(
                AppLicense.tenant_id == tenant_id,
                AppLicense.status == "Active",
                AppLicense.app_name.in_(tuple(app_names)),
                or_(AppLicense.expires_at.is_(None), AppLicense.expires_at > now),
            )
            .first()
        )
        return lic is not None
