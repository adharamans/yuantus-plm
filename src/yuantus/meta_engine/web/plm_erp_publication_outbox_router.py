"""PLM->ERP publication OUTBOX API (G2 R2 HTTP routes).

Thin, admin-gated HTTP surface exposing controlled MANUAL entries to the merged
R2 outbox service (`ErpPublicationOutboxService`), per the R2 HTTP routes
taskbook (`docs/DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R2_HTTP_ROUTES_TASKBOOK_20260528.md`):

    POST /plm-erp/items/{item_id}/publication-outbox/enqueue
    POST /plm-erp/publication-outbox/{outbox_id}/dry-run
    POST /plm-erp/publication-outbox/{outbox_id}/process   (manual process-one)
    POST /plm-erp/publication-outbox/{outbox_id}/replay
    GET  /plm-erp/publication-outbox/{outbox_id}

`process` is a MANUAL single-row send (not a worker daemon — that is a later
slice); the only adapter in R2 is the no-I/O `NullErpPublicationAdapter` (the
real ERP connector is a later slice). `process`/`replay` re-validate eligibility
(D-R2-1) by REUSING `build_publication_readiness` (R1-B's exact logic, not a
copy). Auth mirrors the sibling `/release-readiness` / publication-readiness
endpoints (`require_admin_permission`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import (
    CurrentUser,
    get_current_user,
    require_admin_permission,
)
from yuantus.database import get_db
from yuantus.meta_engine.erp_publication.adapter import NullErpPublicationAdapter
from yuantus.meta_engine.erp_publication.models import (
    DEFAULT_PUBLICATION_KIND,
    ErpPublicationOutbox,
)
from yuantus.meta_engine.erp_publication.service import (
    ErpPublicationOutboxService,
    PublicationConflictError,
    PublicationReplayError,
)
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.web.plm_erp_publication_router import (
    build_publication_readiness,
)


publication_outbox_router = APIRouter(
    prefix="/plm-erp",
    tags=["PLM-ERP Publication Outbox"],
)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #


class OutboxEnqueueRequest(BaseModel):
    target_system: str = Field(..., min_length=1, max_length=120)
    publication_kind: str = Field(DEFAULT_PUBLICATION_KIND, min_length=1, max_length=60)
    ruleset_id: str = "readiness"
    mbom_limit: int = Field(20, ge=0, le=200)
    routing_limit: int = Field(20, ge=0, le=200)
    baseline_limit: int = Field(20, ge=0, le=200)


class OutboxRowResponse(BaseModel):
    id: str
    item_id: str
    version_id: str
    target_system: str
    publication_kind: str
    state: str
    reason: Optional[str] = None
    attempt_count: int
    max_attempts: int
    payload_fingerprint: Optional[str] = None
    error_message: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    snapshot: Optional[dict] = None
    properties: Optional[dict] = None


class OutboxEnqueueResponse(BaseModel):
    # `persisted` is False only for a versionless item (no version-scoped key ->
    # no row); the verdict (skipped / not_eligible) is still surfaced.
    persisted: bool
    eligible: bool
    state: str
    reason: Optional[str] = None
    outbox: Optional[OutboxRowResponse] = None


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _row_response(row: ErpPublicationOutbox) -> OutboxRowResponse:
    return OutboxRowResponse(
        id=row.id,
        item_id=row.item_id,
        version_id=row.version_id,
        target_system=row.target_system,
        publication_kind=row.publication_kind,
        state=row.state,
        reason=row.reason,
        attempt_count=row.attempt_count or 0,
        max_attempts=row.max_attempts or 0,
        payload_fingerprint=row.payload_fingerprint,
        error_message=row.error_message,
        dispatched_at=row.dispatched_at,
        created_at=row.created_at,
        snapshot=row.snapshot,
        properties=row.properties,
    )


def _load_row(db: Session, outbox_id: str) -> ErpPublicationOutbox:
    row = db.get(ErpPublicationOutbox, outbox_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Outbox row {outbox_id} not found")
    return row


def _revalidate_for(db: Session, row: ErpPublicationOutbox):
    """Build the D-R2-1 revalidate callable, reusing R1-B's exact logic.

    The backing item must still exist to revalidate; if it is gone we cannot
    safely send -> 409 (taskbook §5/§6).
    """
    item = db.get(Item, row.item_id)
    if item is None:
        raise HTTPException(
            status_code=409,
            detail=f"Backing item {row.item_id} no longer exists; cannot revalidate for send",
        )
    snap = row.snapshot or {}
    ruleset_id = snap.get("ruleset_id", "readiness")
    limits = snap.get("limits") or {}

    def _revalidate():
        return build_publication_readiness(
            db,
            item,
            row.item_id,
            ruleset_id=ruleset_id,
            mbom_limit=int(limits.get("mbom_limit", 20)),
            routing_limit=int(limits.get("routing_limit", 20)),
            baseline_limit=int(limits.get("baseline_limit", 20)),
        )

    return _revalidate


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@publication_outbox_router.post(
    "/items/{item_id}/publication-outbox/enqueue",
    response_model=OutboxEnqueueResponse,
)
def enqueue_publication(
    item_id: str,
    body: OutboxEnqueueRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutboxEnqueueResponse:
    require_admin_permission(user)

    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    try:
        readiness = build_publication_readiness(
            db,
            item,
            item_id,
            ruleset_id=body.ruleset_id,
            mbom_limit=body.mbom_limit,
            routing_limit=body.routing_limit,
            baseline_limit=body.baseline_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    service = ErpPublicationOutboxService(db)

    def _do_enqueue() -> Optional[ErpPublicationOutbox]:
        return service.enqueue(
            target_system=body.target_system,
            readiness=readiness,
            publication_kind=body.publication_kind,
            created_by_id=getattr(user, "id", None),
        )

    try:
        try:
            row = _do_enqueue()
        except IntegrityError:
            # Concurrent first-enqueue of the same version-scoped key won the
            # DB UNIQUE; reuse the committed row (never conflict-fail, §7).
            db.rollback()
            row = _do_enqueue()
    except PublicationConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if row is None:
        # Versionless item: no row persisted; surface the skipped verdict (§6).
        return OutboxEnqueueResponse(
            persisted=False,
            eligible=bool(readiness.eligible),
            state="skipped",
            reason="not_eligible",
            outbox=None,
        )

    return OutboxEnqueueResponse(
        persisted=True,
        eligible=bool(readiness.eligible),
        state=row.state,
        reason=row.reason,
        outbox=_row_response(row),
    )


@publication_outbox_router.post(
    "/publication-outbox/{outbox_id}/dry-run",
    response_model=OutboxRowResponse,
)
def dry_run_publication(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutboxRowResponse:
    require_admin_permission(user)
    row = _load_row(db, outbox_id)
    service = ErpPublicationOutboxService(db)
    try:
        service.dry_run(row, NullErpPublicationAdapter())
    except PublicationReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _row_response(row)


@publication_outbox_router.post(
    "/publication-outbox/{outbox_id}/process",
    response_model=OutboxRowResponse,
)
def process_publication(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutboxRowResponse:
    require_admin_permission(user)
    row = _load_row(db, outbox_id)
    revalidate = _revalidate_for(db, row)
    service = ErpPublicationOutboxService(db)
    try:
        service.process(row, NullErpPublicationAdapter(), revalidate=revalidate)
    except PublicationReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        # revalidate (build_publication_readiness) rejected the stored ruleset.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _row_response(row)


@publication_outbox_router.post(
    "/publication-outbox/{outbox_id}/replay",
    response_model=OutboxRowResponse,
)
def replay_publication(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutboxRowResponse:
    require_admin_permission(user)
    row = _load_row(db, outbox_id)
    revalidate = _revalidate_for(db, row)
    service = ErpPublicationOutboxService(db)
    try:
        service.replay(row, NullErpPublicationAdapter(), revalidate=revalidate)
    except PublicationReplayError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _row_response(row)


@publication_outbox_router.get(
    "/publication-outbox/{outbox_id}",
    response_model=OutboxRowResponse,
)
def get_publication_outbox(
    outbox_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OutboxRowResponse:
    require_admin_permission(user)
    row = _load_row(db, outbox_id)
    return _row_response(row)
