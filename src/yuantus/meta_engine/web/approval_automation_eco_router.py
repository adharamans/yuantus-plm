"""PLM-COLLAB-P2-C: ECO approval-automation product routes (projection + notify).

- ``GET  /api/v1/approvals/automation/eco/{eco_id}/context`` -- governed READ-ONLY
  projection of an ECO into an approval context. Order is PINNED: authenticate
  (get_current_user) -> is_entitled -> ONLY THEN look up the ECO. An unentitled
  caller gets ``context: null`` + upgrade affordance and the ECO is NEVER queried, so
  object existence is not leaked.
- ``POST /api/v1/approvals/automation/eco/{eco_id}/actions`` -- governed NOTIFY action
  placeholder. Order is PINNED: require_admin_user -> is_entitled (403 upgrade, zero
  audit) -> action allowlist (400) -> ECO lookup (404) -> AuditLog. It is a STUB: no
  DingTalk call, no ECO/approval write-back.

Single entitlement check is ``is_entitled`` -- no second license read, no license_data
authorization. Real write-back stays on the existing governed ECO approve/reject.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user, require_admin_user
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.services.approval_automation_eco_service import (
    ACTION_ALLOWLIST,
    FEATURE_KEY,
    ECOApprovalAutomationService,
)

approval_automation_eco_router = APIRouter(
    prefix="/approvals/automation/eco", tags=["Approval Automation"]
)


class ECOActionRequest(BaseModel):
    action: str


def _affordance(entitled: bool) -> Dict[str, Any]:
    return {
        "feature_key": FEATURE_KEY,
        "entitled": entitled,
        "upgrade": {"available": not entitled},
    }


@approval_automation_eco_router.get("/{eco_id}/context")
def eco_context(
    eco_id: str,
    _user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Read-only ECO approval-context projection. PINNED order: auth -> entitled -> ECO.

    Unentitled -> ``context: null`` + upgrade affordance, WITHOUT touching the ECO
    (no existence leak). Entitled but ECO absent -> 404.
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        # Do NOT look up the ECO here -- unentitled callers must not learn whether it
        # exists. They always get the same null-context affordance.
        return {**_affordance(False), "context": None}
    context = ECOApprovalAutomationService(db).project_context(eco_id)
    if context is None:
        raise HTTPException(status_code=404, detail="ECO not found")
    return {**_affordance(True), "context": context}


@approval_automation_eco_router.post("/{eco_id}/actions")
def eco_action(
    eco_id: str,
    req: ECOActionRequest,
    user: CurrentUser = Depends(require_admin_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Governed NOTIFY action (stub). PINNED order: admin -> entitled -> allowlist -> ECO -> audit.

    require_admin_user runs first (unauth 401 / non-admin 403). Then unentitled ->
    403 upgrade with ZERO audit. Then an unsupported action -> 400. Then an absent ECO
    -> 404 (no audit). Otherwise records an AuditLog row and returns a stubbed result.
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        raise HTTPException(status_code=403, detail=_affordance(False))
    if req.action not in ACTION_ALLOWLIST:
        raise HTTPException(status_code=400, detail=f"unsupported action: {req.action!r}")
    svc = ECOApprovalAutomationService(db)
    result = svc.record_notify(eco_id, user_id=getattr(user, "id", None), action=req.action)
    if result is None:
        raise HTTPException(status_code=404, detail="ECO not found")
    return result
