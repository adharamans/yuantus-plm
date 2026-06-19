"""Lifecycle transition-history read surface (Slice 2).

A read API over the audit rows written by ``LifecycleService.promote()`` (Slice 1):
``GET /api/v1/items/{item_id}/transition-history`` — who moved the item from which state to
which, when, with what comment, and the from/to permission move. Read-only: this slice does
not write history and does not touch all-attempts. Auth follows the item-read pattern (an
authenticated user, not admin).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.lifecycle.models import LifecycleTransitionHistory
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.item import Item

lifecycle_transition_history_router = APIRouter(tags=["Lifecycle"])


def _serialize(row: LifecycleTransitionHistory) -> Dict[str, Any]:
    return {
        "id": row.id,
        "item_id": row.item_id,
        "from_state_id": row.from_state_id,
        "from_state_name": row.from_state_name,
        "to_state_id": row.to_state_id,
        "to_state_name": row.to_state_name,
        "from_permission_id": row.from_permission_id,
        "to_permission_id": row.to_permission_id,
        "transition_id": row.transition_id,
        "lifecycle_map_id": row.lifecycle_map_id,
        "actor_user_id": row.actor_user_id,
        "comment": row.comment,
        "outcome": row.outcome,
        "properties": row.properties,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@lifecycle_transition_history_router.get("/items/{item_id}/transition-history")
def get_item_transition_history(
    item_id: str,
    limit: Optional[int] = Query(None, ge=1, le=500),
    _user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List an item's lifecycle transitions, most-recent first.

    404 if the item does not exist; an empty list for an existing item with no history.
    """
    if db.get(Item, item_id) is None:
        raise HTTPException(status_code=404, detail="Item not found")
    rows = LifecycleService(db).get_transition_history(item_id, limit=limit)
    return {"items": [_serialize(r) for r in rows], "count": len(rows)}
