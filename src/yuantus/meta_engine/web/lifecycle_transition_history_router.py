"""Lifecycle transition-history read surface.

Read APIs over the audit rows written by ``LifecycleService.promote()`` (Slice 1):

- ``GET /api/v1/items/{item_id}/transition-history`` (Slice 2) — the item-scoped read; **per-item
  ACL** (``check_permission(item_type_id, AMLAction.get)`` → **403**, matching
  ``bom_where_used``/``impact``), **404** if the item does not exist.
- ``GET /api/v1/transition-history/forensic/{item_id}`` (forensic admin route) — retrieval by
  recorded ``item_id`` with **no item-existence gate**, so a *deleted* item's retained (FK-free)
  history stays reachable (the #819-archived forensic item). **Superuser-gated** — the item-scoped
  read above uses a **per-item ACL** (the settled two-tier model).

Read-only: does not write history and does not touch all-attempts.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.api.dependencies.auth import CurrentUser, Identity, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.lifecycle.models import LifecycleTransitionHistory
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.schemas.aml import AMLAction
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService

lifecycle_transition_history_router = APIRouter(tags=["Lifecycle"])

# The full LifecycleTransitionHistory.outcome vocabulary (see lifecycle/models.py): one success
# value + the four failed-attempt discriminators. Used to validate the forensic ?outcome filter.
_VALID_OUTCOMES = ("success", "denied", "blocked", "aborted", "failed")


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
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List an item's lifecycle transitions, most-recent first.

    404 if the item does not exist; **403** if the caller lacks read permission on the item's
    type (per-item ACL via ``check_permission(item_type_id, AMLAction.get)``, matching
    ``bom_where_used``/``impact``); an empty list for an existing, readable item with no history.
    """
    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    if not MetaPermissionService(db).check_permission(
        item.item_type_id,
        AMLAction.get,
        user_id=str(user.id),
        user_roles=user.roles,
    ):
        raise HTTPException(status_code=403, detail="Permission denied")
    # success_only: the item-scoped read must NOT surface failed/denied/blocked/aborted attempts —
    # those are a forensic-tier signal (a denial reveals "who was blocked"). The forensic route below
    # returns every outcome.
    rows = LifecycleService(db).get_transition_history(item_id, limit=limit, success_only=True)
    return {"items": [_serialize(r) for r in rows], "count": len(rows)}


@lifecycle_transition_history_router.get("/transition-history/forensic/{item_id}")
def get_forensic_transition_history(
    item_id: str,
    limit: Optional[int] = Query(None, ge=1, le=500),
    outcome: Optional[List[str]] = Query(
        None,
        description=(
            "Filter to one or more outcomes (repeatable), e.g. "
            "?outcome=denied&outcome=blocked for failed-attempt triage. "
            "Allowed: success|denied|blocked|aborted|failed. Omit for all outcomes."
        ),
    ),
    reason_code: Optional[List[str]] = Query(
        None,
        description=(
            "Filter to one or more reason codes (repeatable), e.g. "
            "?reason_code=permission_denied&reason_code=condition_failed for "
            "cause-of-denial triage. ANY string is accepted; an unknown code "
            "matches nothing (no whitelist, no 400). Omit for all reason codes."
        ),
    ),
    actor: Optional[List[int]] = Query(
        None,
        description=(
            "Filter to one or more recorded actor user ids (repeatable), e.g. "
            "?actor=42&actor=7 for 'what did these users attempt'. actor_user_id is "
            "FK-free (a system/automated promote may use an id with no current row); "
            "an unknown id matches nothing. Omit for all actors."
        ),
    ),
    created_after: Optional[str] = Query(
        None,
        description=(
            "Inclusive lower time bound on created_at (ISO-8601, e.g. 2026-06-01 or "
            "2026-06-01T08:00:00). Invalid format → 400. Omit for no lower bound."
        ),
    ),
    created_before: Optional[str] = Query(
        None,
        description=(
            "Inclusive upper time bound on created_at (ISO-8601). A date-only value "
            "(e.g. 2026-06-05) includes the WHOLE day; pass a datetime "
            "(2026-06-05T12:00:00) for an exact instant. Invalid format → 400. "
            "Omit for no upper bound."
        ),
    ),
    _admin: Identity = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Forensic/admin retrieval of an item's transition-history by recorded ``item_id``.

    Unlike the item-scoped route, this does **not** gate on item existence: the audit rows are
    FK-free and retained after item deletion, so a deleted item's history stays reachable here
    (it underpins the #819-archived deleted-item forensic retrieval). A never-existed id with no
    history returns an empty list (200), not 404.

    Auth: ``require_superuser`` — the high-privilege gate for a sensitive surface that exposes
    deleted-item history. The auth model is settled (the per-item-ACL decision chose 2a, #831):
    the forensic route **stays superuser**, while the item-scoped read
    (``/items/{item_id}/transition-history``) uses a **per-item ACL**
    (``check_permission(item_type_id, AMLAction.get)``). This route returns **all** outcomes,
    including failed/denied/blocked/aborted attempts — those are forensic-tier-only; the item-scoped
    route filters to ``success_only``.
    """
    outcomes: Optional[List[str]] = None
    if outcome:
        invalid = sorted({o for o in outcome if o not in _VALID_OUTCOMES})
        if invalid:
            raise HTTPException(
                status_code=400,
                detail="invalid outcome(s): %s; allowed: %s"
                % (", ".join(invalid), ", ".join(_VALID_OUTCOMES)),
            )
        outcomes = outcome

    def _parse_dt(value: Optional[str], field: str, *, end_of_day: bool = False) -> Optional[datetime]:
        if value is None:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="invalid %s: %r is not an ISO-8601 date/datetime" % (field, value),
            )
        # A date-only UPPER bound (no time component) must include the whole day, else
        # `?created_before=2026-06-05` (parsed as 00:00) would exclude every daytime row on
        # the 5th. Lower bound is fine at 00:00 (it includes the day). A datetime with an
        # explicit time is used exactly as given.
        if end_of_day and "T" not in value and ":" not in value:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return dt

    after_dt = _parse_dt(created_after, "created_after")
    before_dt = _parse_dt(created_before, "created_before", end_of_day=True)
    rows = LifecycleService(db).get_transition_history(
        item_id,
        limit=limit,
        success_only=False,
        outcomes=outcomes,
        reason_codes=reason_code or None,
        actor_user_ids=actor or None,
        created_after=after_dt,
        created_before=before_dt,
    )
    return {"items": [_serialize(r) for r in rows], "count": len(rows)}
