"""Entitlement-check kernel for PLM Collaboration features (PLM-COLLAB-P1-B).

`is_entitled(feature_key)` converges the "is this feature available for the current
tenant?" decision to ONE place. It reuses the P1-A tenant scope resolver
(``license_scope.resolve_license_scope``) so there is no second, unscoped license
read path -- a NULL-tenant or other-tenant license can never unlock the current
tenant, and a non-single deployment without tenant context raises (it is not
swallowed into a plain False).

Lit keys: ``plm_collaboration_pro`` (P1-B), ``approval_automation`` (P2-A ->
``plm.approval_automation``) and ``bom_multitable`` (P3-B -> ``plm.bom_multitable``) --
each an independent, separately-sellable SKU. The other canonical feature keys are
accepted (so a typo is caught, not silently treated as unlicensed) but map to an
empty app-name set -> always False until a later slice lights them. ``license_data``
is NOT an authorization source.
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
    # PLM-COLLAB-P2-A: approval automation is an independent, separately-sellable SKU
    # -- it is NOT bundled into plm.collab and does NOT reuse plm_collaboration_pro.
    "approval_automation": frozenset({"plm.approval_automation"}),
    # PLM-COLLAB-P3-B: BOM multi-table is its OWN independent SKU -- NOT bundled into
    # plm.collab, NOT reusing plm_collaboration_pro (same discipline as approval_automation).
    "bom_multitable": frozenset({"plm.bom_multitable"}),
    # ECM-P1B: PLM->ECM publish is its OWN independent SKU (same discipline as
    # approval_automation / bom_multitable -- NOT bundled into plm.collab).
    "ecm_publish": frozenset({"plm.ecm_publish"}),
    # reserved (canonical 6.1 vocabulary) -- accepted but not license-unlockable yet:
    "plm": frozenset(),
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
        # Resolve the tenant for EVERY known key BEFORE the reserved-key shortcut,
        # so a non-single deployment with no tenant context raises uniformly -- a
        # reserved (unlit) key must not be a silent False that bypasses the tenant
        # guard. (tenant_id participates in the filter; org_id is recorded only.)
        tenant_id, _ = resolve_license_scope()
        app_names = FEATURE_APP_NAMES[feature_key]
        if not app_names:
            return False  # reserved but not yet lit in P1-B
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
