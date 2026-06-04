"""PLM-COLLAB-P2-B: approval-automation product-entry routes.

- ``GET  /api/v1/approvals/automation/templates`` -- available template definitions
  + this tenant's provisioned drafts + entitlement/upgrade affordance. NEVER gated:
  this IS the upgrade-affordance surface (mirrors the P1-D ``GET /features/{key}``
  shape), so an unentitled tenant still sees the catalog and ``upgrade.available``.
- ``POST /api/v1/approvals/automation/provision`` -- provision DRAFT templates.
  DOUBLY gated: (1) identity -- ``require_admin_user`` (writing tenant config requires
  an admin caller; unauth -> 401, non-admin -> 403), THEN (2) SKU entitlement --
  ``EntitlementService.is_entitled("approval_automation")`` (admin but unentitled ->
  403 upgrade affordance). The SKU answers "did this tenant buy it"; the admin gate
  answers "is this caller allowed to mutate tenant config" -- both are required.

The single entitlement check is ``is_entitled`` -- no second license read, no
``license_data`` authorization. The service ALSO re-asserts entitlement so the write
path cannot be bypassed.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, require_admin_user
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.services.approval_automation_service import (
    FEATURE_KEY,
    ApprovalAutomationService,
)

approval_automation_router = APIRouter(
    prefix="/approvals/automation", tags=["Approval Automation"]
)


def _affordance(entitled: bool) -> Dict[str, Any]:
    return {
        "feature_key": FEATURE_KEY,
        "entitled": entitled,
        "upgrade": {"available": not entitled},
    }


@approval_automation_router.get("/templates")
def list_templates(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Template definitions + this tenant's provisioned drafts + upgrade affordance.

    Never gated -- this is the upgrade-affordance surface. An unentitled tenant still
    sees the available definitions and ``upgrade.available = true``; its provisioned
    list is whatever it has (empty until it upgrades and provisions).
    """
    svc = ApprovalAutomationService(db)
    entitled = EntitlementService(db).is_entitled(FEATURE_KEY)
    return {
        **_affordance(entitled),
        "templates": svc.list_definitions(),
        "provisioned": svc.list_provisioned(),
    }


@approval_automation_router.post("/provision")
def provision(
    _: CurrentUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Provision DRAFT templates -- admin-gated, then entitlement-gated.

    ``require_admin_user`` runs first (a dependency): unauth -> 401, non-admin -> 403,
    BEFORE any entitlement decision. Then unentitled -> 403 + upgrade affordance,
    nothing written. Entitled admin -> idempotent get-or-create of draft rows
    (re-provision returns existing drafts, no duplicates).
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        # Not an error condition for the product -- it is the upgrade path. 403 with
        # the same affordance body the GET returns, so the front end can react.
        raise HTTPException(status_code=403, detail=_affordance(False))
    provisioned = ApprovalAutomationService(db).provision()
    return {**_affordance(True), "provisioned": provisioned}
