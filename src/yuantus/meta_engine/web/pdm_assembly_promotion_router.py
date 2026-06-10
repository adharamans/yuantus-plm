"""B2b CAD-PDM assembly promotion API.

POST /pdm/items/{root_id}/promote-assembly
"""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.assembly_promotion_service import (
    AssemblyPromotionService,
)

pdm_assembly_promotion_router = APIRouter(prefix="/pdm", tags=["PDM"])


class PromoteAssemblyRequest(BaseModel):
    target_state: Literal["Released"] = "Released"
    dry_run: bool = True
    max_depth: int = Field(default=10, ge=1, le=50)
    comment: str = ""


def _require_part(db: Session, item_id: str) -> Item:
    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
    if item.item_type_id != "Part":
        raise HTTPException(
            status_code=400, detail="Only Part items can be assembly-promoted"
        )
    return item


@pdm_assembly_promotion_router.post(
    "/items/{root_id}/promote-assembly", response_model=Dict[str, Any]
)
async def promote_assembly(
    root_id: str,
    request: PromoteAssemblyRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    _require_part(db, root_id)
    try:
        result = AssemblyPromotionService(db).promote_assembly(
            root_id,
            target_state=request.target_state,
            dry_run=request.dry_run,
            max_depth=request.max_depth,
            user_id=user.id,
            user_roles=user.roles,
            comment=request.comment,
        )
        if not request.dry_run and result["ok"]:
            db.commit()
        else:
            db.rollback()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
