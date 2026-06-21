"""Lifecycle transition-history read surface.

Read APIs over the audit rows written by ``LifecycleService.promote()`` (Slice 1):

- ``GET /api/v1/items/{item_id}/transition-history`` (Slice 2) — the item-scoped read; an
  authenticated user, **404** if the item does not exist.
- ``GET /api/v1/transition-history/forensic/{item_id}`` (forensic admin route) — retrieval by
  recorded ``item_id`` with **no item-existence gate**, so a *deleted* item's retained (FK-free)
  history stays reachable (the #819-archived forensic item). **Superuser-gated**; see the route
  docstring for the auth-model note.

Read-only: does not write history and does not touch all-attempts.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.api.dependencies.auth import CurrentUser, Identity, get_current_user
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


@lifecycle_transition_history_router.get("/transition-history/forensic/{item_id}")
def get_forensic_transition_history(
    item_id: str,
    limit: Optional[int] = Query(None, ge=1, le=500),
    _admin: Identity = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Forensic/admin retrieval of an item's transition-history by recorded ``item_id``.

    Unlike the item-scoped route, this does **not** gate on item existence: the audit rows are
    FK-free and retained after item deletion, so a deleted item's history stays reachable here
    (it underpins the #819-archived deleted-item forensic retrieval). A never-existed id with no
    history returns an empty list (200), not 404.

    Auth: ``require_superuser`` — the conservative high-privilege gate for a sensitive surface
    that exposes deleted-item history. NOTE: the precise "who may call this" (superuser vs an
    org/tenant-admin role vs a unified per-item ACL) is the auth-model decision reserved on the
    per-item-ACL-hardening item; this route defaults to the most restrictive option pending it.
    """
    rows = LifecycleService(db).get_transition_history(item_id, limit=limit)
    return {"items": [_serialize(r) for r in rows], "count": len(rows)}
