"""PLM->ECM publication OUTBOX ops API (ECM-P1C).

A thin, admin + ``ecm_publish``-entitlement gated HTTP surface over the ECM
publication outbox -- a MINIMAL 3-route operational subset (the worker, not a
manual route, is the normal dispatch path):

    GET  /plm-ecm/publication-outbox          (list, optional ?state=)
    GET  /plm-ecm/publication-outbox/{id}     (get one)
    POST /plm-ecm/publication-outbox/{id}/replay  (failed -> pending reset)

``replay`` is a PURE state reset for the worker to re-pick-up -- it does NOT
itself resend; the worker (with the live Transfer Receiver adapter) performs the
actual resend on its next tick. Gate order is admin -> is_entitled, both 403
BEFORE any row read (no existence leak).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import (
    CurrentUser,
    get_current_user,
    require_admin_permission,
)
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import (
    EcmPublicationOutboxService,
    EcmPublicationReplayError,
)

FEATURE_KEY = "ecm_publish"

ecm_publication_outbox_router = APIRouter(
    prefix="/plm-ecm",
    tags=["PLM-ECM Publication Outbox"],
)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EcmOutboxRowResponse(BaseModel):
    id: str
    item_id: str
    version_id: str
    file_id: str
    file_role: str
    target_system: str
    state: str
    reason: Optional[str] = None
    attempt_count: int
    max_attempts: int
    payload_fingerprint: Optional[str] = None
    replay_of: Optional[str] = None
    error_message: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    snapshot: Optional[dict] = None
    properties: Optional[dict] = None


class EcmOutboxListResponse(BaseModel):
    rows: List[EcmOutboxRowResponse]
    count: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _gate(user: CurrentUser, db: Session) -> None:
    """Admin -> entitlement, in this PINNED order, both 403 before any row read."""
    require_admin_permission(user)
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        raise HTTPException(status_code=403, detail="ecm_publish not entitled")


def _load_row(db: Session, outbox_id: str) -> EcmPublicationOutbox:
    row = db.get(EcmPublicationOutbox, outbox_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Outbox row {outbox_id} not found")
    return row


def _row_response(row: EcmPublicationOutbox) -> EcmOutboxRowResponse:
    return EcmOutboxRowResponse(
        id=row.id,
        item_id=row.item_id,
        version_id=row.version_id,
        file_id=row.file_id,
        file_role=row.file_role,
        target_system=row.target_system,
        state=row.state,
        reason=row.reason,
        attempt_count=row.attempt_count or 0,
        max_attempts=row.max_attempts or 0,
        payload_fingerprint=row.payload_fingerprint,
        replay_of=row.replay_of,
        error_message=row.error_message,
        dispatched_at=row.dispatched_at,
        created_at=row.created_at,
        snapshot=row.snapshot,
        properties=row.properties,
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@ecm_publication_outbox_router.get(
    "/publication-outbox", response_model=EcmOutboxListResponse
)
def list_publication_outbox(
    state: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EcmOutboxListResponse:
    _gate(user, db)
    query = db.query(EcmPublicationOutbox)
    if state is not None:
        valid = {s.value for s in EcmPublicationState}
        if state not in valid:
            raise HTTPException(
                status_code=422,
                detail=f"invalid state {state!r}; expected one of {sorted(valid)}",
            )
        query = query.filter(EcmPublicationOutbox.state == state)
    # Bounded: the outbox accumulates SENT rows indefinitely (no retention path yet).
    rows = query.order_by(EcmPublicationOutbox.created_at.desc()).limit(limit).all()
    return EcmOutboxListResponse(
        rows=[_row_response(r) for r in rows], count=len(rows)
    )


@ecm_publication_outbox_router.get(
    "/publication-outbox/{outbox_id}", response_model=EcmOutboxRowResponse
)
def get_publication_outbox(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EcmOutboxRowResponse:
    _gate(user, db)
    return _row_response(_load_row(db, outbox_id))


@ecm_publication_outbox_router.post(
    "/publication-outbox/{outbox_id}/replay", response_model=EcmOutboxRowResponse
)
def replay_publication_outbox(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EcmOutboxRowResponse:
    _gate(user, db)
    row = _load_row(db, outbox_id)
    try:
        EcmPublicationOutboxService(db).replay(row)
    except EcmPublicationReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _row_response(row)
