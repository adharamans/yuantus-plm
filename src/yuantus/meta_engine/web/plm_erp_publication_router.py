"""PLM->ERP publication-readiness API (G2 R1-B).

Read-only outbound publication-readiness verdict over EXISTING verdicts, per the
R1-A contract taskbook
(`docs/DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R1A_TASKBOOK_20260527.md`):

    eligible = latest_released_guard passes
           AND suspended_guard passes
           AND get_item_release_readiness(...).summary.ok
           AND esign_ok (mirrors release_orchestration_router._plan_steps)

It WRAPS `ReleaseReadinessService.get_item_release_readiness` (no readiness
re-derivation) and reuses the `release_readiness_router` response mapper. No ERP
write, no purchase/sale transaction, no Odoo dependency.

Auth: `require_admin_permission`, identical to the sibling `/release-readiness`
endpoint that exposes the SAME underlying readiness data — exposing it un-gated
via a new route would be an access downgrade. A dedicated ERP-adapter principal /
permission is an R2 (adapter) concern.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import (
    CurrentUser,
    get_current_user,
    require_admin_permission,
)
from yuantus.database import get_db
from yuantus.meta_engine.erp_publication.service import build_snapshot
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.latest_released_guard import (
    LatestReleasedGuardService,
    NotLatestReleasedError,
)
from yuantus.meta_engine.services.release_readiness_service import ReleaseReadinessService
from yuantus.meta_engine.services.suspended_guard import (
    SuspendedGuardService,
    SuspendedStateError,
)
from yuantus.meta_engine.web.release_readiness_router import (
    ReadinessResource,
    ReadinessSummary,
    _build_response,
)


publication_readiness_router = APIRouter(
    prefix="/plm-erp",
    tags=["PLM-ERP Publication"],
)


class ItemBlock(BaseModel):
    item_id: str
    lifecycle_state: Optional[str] = None


class VersionBlock(BaseModel):
    # Sourced from Item.current_version (ItemVersion). version.generation is
    # ItemVersion.generation, NOT Item.generation.
    version_id: str
    generation: Optional[int] = None
    revision: Optional[str] = None
    version_label: Optional[str] = None
    state: Optional[str] = None
    is_current: Optional[bool] = None
    is_released: Optional[bool] = None
    released_at: Optional[str] = None
    primary_file_id: Optional[str] = None


class FileRef(BaseModel):
    file_id: str
    file_role: Optional[str] = None
    is_primary: bool = False
    sequence: Optional[int] = None
    snapshot_path: Optional[str] = None


class EsignBlock(BaseModel):
    present: bool
    is_complete: Optional[bool] = None
    completed_at: Optional[str] = None


class BlockingReason(BaseModel):
    reason: str
    detail: Optional[str] = None


class Limits(BaseModel):
    mbom_limit: int
    routing_limit: int
    baseline_limit: int


class PublicationReadinessResponse(BaseModel):
    item: ItemBlock
    version: Optional[VersionBlock] = None
    eligible: bool
    generated_at: Optional[datetime] = None
    ruleset_id: str
    limits: Limits
    summary: ReadinessSummary
    resources: List[ReadinessResource] = Field(default_factory=list)
    esign: EsignBlock
    file_refs: List[FileRef] = Field(default_factory=list)
    blocking_reasons: List[BlockingReason] = Field(default_factory=list)


class PublicationExportResponse(BaseModel):
    item_id: str
    eligible: bool
    generated_at: Optional[datetime] = None
    blocking_reasons: List[BlockingReason] = Field(default_factory=list)
    # The canonical, target-agnostic publishable package (build_snapshot output),
    # present ONLY when eligible; null otherwise (read-only export).
    snapshot: Optional[dict] = None


@publication_readiness_router.get(
    "/items/{item_id}/publication-readiness",
    response_model=PublicationReadinessResponse,
)
def get_publication_readiness(
    item_id: str,
    ruleset_id: str = Query("readiness"),
    mbom_limit: int = Query(20, ge=0, le=200),
    routing_limit: int = Query(20, ge=0, le=200),
    baseline_limit: int = Query(20, ge=0, le=200),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicationReadinessResponse:
    require_admin_permission(user)

    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    try:
        return build_publication_readiness(
            db,
            item,
            item_id,
            ruleset_id=ruleset_id,
            mbom_limit=mbom_limit,
            routing_limit=routing_limit,
            baseline_limit=baseline_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@publication_readiness_router.get(
    "/items/{item_id}/publication/export",
    response_model=PublicationExportResponse,
)
def get_publication_export(
    item_id: str,
    ruleset_id: str = Query("readiness"),
    mbom_limit: int = Query(20, ge=0, le=200),
    routing_limit: int = Query(20, ge=0, le=200),
    baseline_limit: int = Query(20, ge=0, le=200),
    publication_kind: str = Query("readiness"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicationExportResponse:
    """R4 read-only PULL: the publishable package for an item.

    Reuses build_publication_readiness (R1-B) + build_snapshot (R2). Read-only:
    no enqueue, no POST, no adapter, no write. `snapshot` is the canonical,
    target-agnostic package (`target_system=""`), present only when eligible; an
    ineligible item returns 200 with `snapshot=null` + blocking_reasons.
    """
    require_admin_permission(user)

    item = db.get(Item, item_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    try:
        readiness = build_publication_readiness(
            db,
            item,
            item_id,
            ruleset_id=ruleset_id,
            mbom_limit=mbom_limit,
            routing_limit=routing_limit,
            baseline_limit=baseline_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    snapshot = None
    if readiness.eligible:
        snapshot = build_snapshot(
            readiness, target_system="", publication_kind=publication_kind
        )

    return PublicationExportResponse(
        item_id=item_id,
        eligible=readiness.eligible,
        generated_at=readiness.generated_at,
        blocking_reasons=readiness.blocking_reasons,
        snapshot=snapshot,
    )


def build_publication_readiness(
    db: Session,
    item: Item,
    item_id: str,
    *,
    ruleset_id: str = "readiness",
    mbom_limit: int = 20,
    routing_limit: int = 20,
    baseline_limit: int = 20,
) -> PublicationReadinessResponse:
    """Shared, HTTP-agnostic publication-readiness builder (R1-B logic).

    Extracted so R2 (the publication outbox) can reuse the EXACT R1-A/R1-B
    eligibility + snapshot logic instead of copying it (R2 build taskbook §8).
    Deliberately kept in THIS module so the existing R1-B tests — which patch
    the names referenced below (ReleaseReadinessService, the guards,
    _build_response) — stay green unchanged. Raises ValueError for unknown
    ruleset / item-state problems (callers map to HTTP 400); the latest-released
    and suspended typed guards are converted to blocking_reasons here.
    """
    service = ReleaseReadinessService(db)
    payload = service.get_item_release_readiness(
        item_id=item_id,
        ruleset_id=ruleset_id,
        mbom_limit=mbom_limit,
        routing_limit=routing_limit,
        baseline_limit=baseline_limit,
    )

    readiness = _build_response(payload=payload, ruleset_id=ruleset_id)

    blocking_reasons: List[BlockingReason] = []

    # latest-released guard: typed error -> blocking_reason; other ValueError
    # (e.g. item not found) -> 400 chained.
    latest_released_ok = True
    try:
        LatestReleasedGuardService(db).assert_latest_released(item_id, context="publication")
    except NotLatestReleasedError as exc:
        latest_released_ok = False
        blocking_reasons.append(
            BlockingReason(reason="not_latest_released", detail=getattr(exc, "reason", None))
        )

    # suspended guard
    suspended_ok = True
    try:
        SuspendedGuardService(db).assert_not_suspended(item_id, context="publication")
    except SuspendedStateError as exc:
        suspended_ok = False
        blocking_reasons.append(
            BlockingReason(reason="suspended", detail=getattr(exc, "reason", None))
        )

    # readiness resource errors (mbom_release / routing_release / baseline_release)
    if not readiness.summary.ok:
        for res in readiness.resources:
            if res.diagnostics.errors:
                blocking_reasons.append(
                    BlockingReason(reason=res.kind, detail=res.diagnostics.resource_id or None)
                )

    # esign: mirror release_orchestration_router._plan_steps EXACTLY. A dict with
    # is_complete present and falsy blocks; None / missing-key do NOT block. The
    # field is is_complete (there is no `status` field).
    em = readiness.esign_manifest
    esign_incomplete = (
        isinstance(em, dict)
        and ("is_complete" in em)
        and (not bool(em.get("is_complete")))
    )
    esign_ok = not esign_incomplete
    esign = EsignBlock(
        present=em is not None,
        is_complete=(
            bool(em.get("is_complete"))
            if isinstance(em, dict) and "is_complete" in em
            else None
        ),
        completed_at=(em.get("completed_at") if isinstance(em, dict) else None),
    )
    if esign_incomplete:
        blocking_reasons.append(BlockingReason(reason="esign", detail="is_complete=false"))

    # item{} + version{} + file_refs[] (version & files from Item.current_version)
    item_block = ItemBlock(item_id=item_id, lifecycle_state=getattr(item, "state", None))

    version_block: Optional[VersionBlock] = None
    file_refs: List[FileRef] = []
    current_version = getattr(item, "current_version", None)
    if current_version is not None:
        released_at = getattr(current_version, "released_at", None)
        version_block = VersionBlock(
            version_id=str(getattr(current_version, "id", "") or ""),
            generation=getattr(current_version, "generation", None),
            revision=getattr(current_version, "revision", None),
            version_label=getattr(current_version, "version_label", None),
            state=getattr(current_version, "state", None),
            is_current=getattr(current_version, "is_current", None),
            is_released=getattr(current_version, "is_released", None),
            released_at=(released_at.isoformat() if released_at else None),
            primary_file_id=getattr(current_version, "primary_file_id", None),
        )
        for vf in (getattr(current_version, "version_files", None) or []):
            file_refs.append(
                FileRef(
                    file_id=str(getattr(vf, "file_id", "") or ""),
                    file_role=getattr(vf, "file_role", None),
                    is_primary=bool(getattr(vf, "is_primary", False)),
                    sequence=getattr(vf, "sequence", None),
                    snapshot_path=getattr(vf, "snapshot_path", None),
                )
            )

    # eligible is the R1-A formula DIRECTLY (not "no blocking_reasons"): a
    # summary.ok == false with no per-resource errors must still be ineligible.
    eligible = (
        latest_released_ok
        and suspended_ok
        and bool(readiness.summary.ok)
        and esign_ok
    )

    return PublicationReadinessResponse(
        item=item_block,
        version=version_block,
        eligible=eligible,
        generated_at=readiness.generated_at,
        ruleset_id=ruleset_id,
        limits=Limits(
            mbom_limit=mbom_limit,
            routing_limit=routing_limit,
            baseline_limit=baseline_limit,
        ),
        summary=readiness.summary,
        resources=readiness.resources,
        esign=esign,
        file_refs=file_refs,
        blocking_reasons=blocking_reasons,
    )
