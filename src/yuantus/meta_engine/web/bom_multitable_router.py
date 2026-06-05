"""PLM-COLLAB-P3-A: BOM multi-table governed projection route.

``GET /api/v1/bom/multitable/{part_id}/context`` -- a governed READ-ONLY projection of a
part + its direct BOM lines into a review-table context (the consumer side is P3-C). Order
is PINNED: authenticate (get_current_user) -> is_entitled("bom_multitable") -> ONLY THEN
query the part -> PLM read permission -> project. An unentitled caller gets ``context:null``
+ upgrade affordance and the part is NEVER queried, so object existence is not leaked.

ADVISORY-style entitlement gate is the single ``is_entitled`` check (no second license read,
no ``license_data`` authorization). NO write-back, NO audit, NO embed. ``bom_multitable`` is
still a reserved key (lit later in P3-B), so this returns unentitled until then.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.schemas.aml import AMLAction
from yuantus.meta_engine.services.bom_multitable_projection_service import (
    FEATURE_KEY,
    BOMMultitableProjectionService,
)
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService

bom_multitable_router = APIRouter(prefix="/bom", tags=["BOM"])


def _affordance(entitled: bool) -> Dict[str, Any]:
    return {
        "feature_key": FEATURE_KEY,
        "entitled": entitled,
        "upgrade": {"available": not entitled},
    }


@bom_multitable_router.get("/multitable/{part_id}/context")
def bom_multitable_context(
    part_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Read-only BOM-context projection. PINNED order: auth -> entitled -> part -> permission.

    Unentitled -> ``context: null`` + upgrade affordance, WITHOUT touching the part (no
    existence leak). Entitled but part absent -> 404; read permission denied -> 403.
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        # Do NOT look up the part -- unentitled callers must not learn whether it exists.
        return {**_affordance(False), "context": None}

    root = db.get(Item, part_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Part not found")

    perm = MetaPermissionService(db)
    user_id = str(user.id)
    if not perm.check_permission(
        root.item_type_id, AMLAction.get, user_id=user_id, user_roles=user.roles
    ) or not perm.check_permission(
        "Part BOM", AMLAction.get, user_id=user_id, user_roles=user.roles
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    context = BOMMultitableProjectionService(db).project_context(part_id)
    return {**_affordance(True), "context": context}
