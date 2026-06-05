"""WP1.2 PDM relationship traversal API (item-centered, read-only).

GET /pdm/items/{item_id}/relationships        one-level (any kind, incl REFERENCE)
GET /pdm/items/{item_id}/relationship-tree     recursive (ASSEMBLY only; tree|flat)

Locked contract: DEVELOPMENT_WP1_2_PDM_TRAVERSAL_AND_STALE_DRAWINGS_TASKBOOK.
- Tree follows containment (ASSEMBLY) only; REFERENCE / unknown kind into the tree
  -> 422 (REFERENCE is a cross-link; following it would turn the tree into a graph).
- Path-based cycle guard, root included, max_depth default 10 / cap 50.
- Naming uses the WP1.0-D4 /pdm/items/... prefix; no /pdm/cad/ or /documents/...
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.relationship.service import (
    RelationshipService,
    TraversalBudgetError,
)
from yuantus.meta_engine.schemas.aml import AMLAction
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService

pdm_relationship_router = APIRouter(prefix="/pdm", tags=["PDM"])

_VALID_DIRECTIONS = {"outgoing", "incoming", "both"}
_VALID_PROJECTIONS = {"tree", "flat"}
_MAX_DEPTH_CAP = RelationshipService.MAX_DEPTH_CAP  # 50


def _require_part(db: Session, item_id: str) -> Item:
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    if item.item_type_id != "Part":
        raise HTTPException(
            status_code=400, detail="Only Part items have PDM relationships"
        )
    return item


def _require_get(db: Session, item: Item, user: CurrentUser) -> None:
    perm = MetaPermissionService(db)
    if not perm.check_permission(
        item.item_type_id,
        AMLAction.get,
        user_id=str(user.id),
        user_roles=user.roles,
    ):
        raise HTTPException(status_code=403, detail="Permission denied")


@pdm_relationship_router.get(
    "/items/{item_id}/relationships", response_model=Dict[str, Any]
)
async def get_item_relationships(
    item_id: str,
    kind: Optional[str] = Query(
        None, description="Filter by relationship kind (e.g. ASSEMBLY, REFERENCE)"
    ),
    direction: str = Query("outgoing", description="outgoing|incoming|both"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    item = _require_part(db, item_id)
    _require_get(db, item, user)
    if direction not in _VALID_DIRECTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"direction must be one of {sorted(_VALID_DIRECTIONS)}",
        )
    service = RelationshipService(db)
    try:
        rows = service.get_item_relationships(item_id, kind=kind, direction=direction)
    except ValueError as exc:  # unknown / non-relationship kind
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "item_id": item_id,
        "kind": kind,
        "direction": direction,
        "relationships": rows,
    }


@pdm_relationship_router.get(
    "/items/{item_id}/relationship-tree", response_model=Dict[str, Any]
)
async def get_item_relationship_tree(
    item_id: str,
    kinds: str = Query("ASSEMBLY", description="containment kinds (v0: ASSEMBLY only)"),
    max_depth: int = Query(10, description="recursion depth (1..50)"),
    projection: str = Query("tree", description="tree|flat"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    item = _require_part(db, item_id)
    _require_get(db, item, user)
    # v0: the tree follows containment only. REFERENCE / unknown -> 422 (no silent
    # ignore, never fold REFERENCE into the tree).
    requested = {k.strip() for k in kinds.split(",") if k.strip()}
    if requested != {"ASSEMBLY"}:
        raise HTTPException(
            status_code=422,
            detail="relationship-tree v0 only supports kinds=ASSEMBLY",
        )
    if projection not in _VALID_PROJECTIONS:
        raise HTTPException(
            status_code=422, detail="projection must be 'tree' or 'flat'"
        )
    if max_depth < 1 or max_depth > _MAX_DEPTH_CAP:
        raise HTTPException(
            status_code=422, detail=f"max_depth must be between 1 and {_MAX_DEPTH_CAP}"
        )
    service = RelationshipService(db)
    try:
        return service.get_relationship_tree(
            item_id, kinds=["ASSEMBLY"], max_depth=max_depth, projection=projection
        )
    except TraversalBudgetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
