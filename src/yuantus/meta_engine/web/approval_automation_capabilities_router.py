"""PLM-COLLAB-P2-D: ECO-scenario capability/upgrade entry route.

``GET /api/v1/approvals/automation/eco/capabilities`` -- the scenario-level product
entry: "what approval automation can I do in the ECO scenario, and if not, how do I
upgrade?". Scenario-level (NO ``eco_id``): it never queries an ECO, so there is no
object-existence surface at all.

UNGATED affordance surface (like the P1-D feature status + the P2-B GET /templates):
it returns only the product capability / upgrade affordance, no PLM/ECO data. The ONE
judgment is ``EntitlementService.is_entitled("approval_automation")`` -- it does not
read ``license_data`` and does not query ``AppLicense`` on a second path. It performs
NO write and NO ECO lookup.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.services.approval_automation_capabilities_service import (
    UPGRADE_HINTS,
    eco_capability_descriptor,
)
from yuantus.meta_engine.services.approval_automation_eco_service import FEATURE_KEY

approval_automation_capabilities_router = APIRouter(
    prefix="/approvals/automation/eco", tags=["Approval Automation"]
)


@approval_automation_capabilities_router.get("/capabilities")
def eco_capabilities(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Entitled -> the capability descriptor; unentitled -> the upgrade affordance.

    Consumes the single entitlement judgment (is_entitled). No ECO lookup, no write.
    """
    entitled = EntitlementService(db).is_entitled(FEATURE_KEY)
    upgrade: Dict[str, Any] = {"available": not entitled}
    if not entitled:
        # Read-only upgrade hints; production stays on the P1-C signed license.
        upgrade.update(UPGRADE_HINTS)
    return {
        "feature_key": FEATURE_KEY,
        "entitled": entitled,
        "upgrade": upgrade,
        "capability": eco_capability_descriptor() if entitled else None,
    }
