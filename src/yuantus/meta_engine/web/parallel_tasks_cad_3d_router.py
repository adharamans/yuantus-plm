from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.services.parallel_tasks_service import ThreeDOverlayService


parallel_tasks_cad_3d_router = APIRouter(tags=["ParallelTasks"])


def _as_roles(user: CurrentUser) -> List[str]:
    return [str(role) for role in (getattr(user, "roles", []) or [])]


def _error_detail(
    code: str,
    message: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "code": str(code),
        "message": str(message),
        "context": context or {},
    }


def _raise_api_error(
    *,
    status_code: int,
    code: str,
    message: str,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    raise HTTPException(
        status_code=status_code,
        detail=_error_detail(code, message, context=context),
    )


class ThreeDOverlayUpsertRequest(BaseModel):
    document_item_id: str
    version_label: Optional[str] = None
    status: Optional[str] = None
    visibility_role: Optional[str] = None
    part_refs: Optional[List[Dict[str, Any]]] = None
    properties: Optional[Dict[str, Any]] = None


class ThreeDOverlayBatchResolveRequest(BaseModel):
    component_refs: List[str] = Field(..., min_length=1)
    include_missing: bool = True


class ExplodeOffset(BaseModel):
    component_ref: str = Field(..., min_length=1)
    # [x, y, z], applied client-side to the component_ref's viewer node.
    offset: List[float] = Field(..., min_length=3, max_length=3)


class ExplodeConfigRequest(BaseModel):
    # G3: a thin, VALIDATED explode config. The server validates structure only
    # (numeric offsets, well-formed refs) and stores it verbatim; the viewer
    # applies it to the geometry it holds. `component_ref` is the same opaque,
    # client-defined identity the overlay uses.
    factor: float = Field(..., ge=0, le=1000)
    mode: str = Field("radial", min_length=1, max_length=40)
    offsets: List[ExplodeOffset] = Field(default_factory=list)


@parallel_tasks_cad_3d_router.post("/cad-3d/overlays")
async def upsert_3d_overlay(
    payload: ThreeDOverlayUpsertRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ThreeDOverlayService(db)
    try:
        overlay = service.upsert_overlay(
            document_item_id=payload.document_item_id,
            version_label=payload.version_label,
            status=payload.status,
            visibility_role=payload.visibility_role,
            part_refs=payload.part_refs,
            properties=payload.properties,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="overlay_upsert_invalid",
            message=str(exc),
            context={
                "document_item_id": payload.document_item_id,
                "visibility_role": payload.visibility_role,
            },
        )
    return {
        "id": overlay.id,
        "document_item_id": overlay.document_item_id,
        "version_label": overlay.version_label,
        "status": overlay.status,
        "visibility_role": overlay.visibility_role,
        "part_refs_count": len(overlay.part_refs or []),
    }


@parallel_tasks_cad_3d_router.get("/cad-3d/overlays/cache/stats")
async def get_3d_overlay_cache_stats(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ThreeDOverlayService(db)
    result = service.cache_stats()
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_cad_3d_router.get("/cad-3d/overlays/{document_item_id}")
async def get_3d_overlay(
    document_item_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ThreeDOverlayService(db)
    try:
        overlay = service.get_overlay(
            document_item_id=document_item_id, user_roles=_as_roles(user)
        )
    except PermissionError as exc:
        _raise_api_error(
            status_code=403,
            code="overlay_access_denied",
            message=str(exc),
            context={"document_item_id": document_item_id},
        )
    if not overlay:
        _raise_api_error(
            status_code=404,
            code="overlay_not_found",
            message=f"Overlay not found: {document_item_id}",
            context={"document_item_id": document_item_id},
        )
    return {
        "id": overlay.id,
        "document_item_id": overlay.document_item_id,
        "version_label": overlay.version_label,
        "status": overlay.status,
        "visibility_role": overlay.visibility_role,
        "part_refs": overlay.part_refs or [],
        "properties": overlay.properties or {},
    }


@parallel_tasks_cad_3d_router.post(
    "/cad-3d/overlays/{document_item_id}/components/resolve-batch"
)
async def resolve_overlay_components_batch(
    document_item_id: str,
    payload: ThreeDOverlayBatchResolveRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ThreeDOverlayService(db)
    try:
        result = service.resolve_components(
            document_item_id=document_item_id,
            component_refs=payload.component_refs,
            user_roles=_as_roles(user),
            include_missing=payload.include_missing,
        )
    except PermissionError as exc:
        _raise_api_error(
            status_code=403,
            code="overlay_access_denied",
            message=str(exc),
            context={"document_item_id": document_item_id},
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=404,
            code="overlay_not_found",
            message=str(exc),
            context={"document_item_id": document_item_id},
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_cad_3d_router.get(
    "/cad-3d/overlays/{document_item_id}/components/{component_ref}"
)
async def resolve_overlay_component(
    document_item_id: str,
    component_ref: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ThreeDOverlayService(db)
    try:
        result = service.resolve_component(
            document_item_id=document_item_id,
            component_ref=component_ref,
            user_roles=_as_roles(user),
        )
    except PermissionError as exc:
        _raise_api_error(
            status_code=403,
            code="overlay_access_denied",
            message=str(exc),
            context={
                "document_item_id": document_item_id,
                "component_ref": component_ref,
            },
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=404,
            code="overlay_not_found",
            message=str(exc),
            context={
                "document_item_id": document_item_id,
                "component_ref": component_ref,
            },
        )
    return result


@parallel_tasks_cad_3d_router.put("/cad-3d/explode/{document_item_id}")
async def upsert_3d_explode(
    document_item_id: str,
    payload: ExplodeConfigRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """G3: persist a single VALIDATED explode config for a 3D document.

    Stored under the existing 3D-overlay row (no migration). Validates structure
    only (numeric offsets, well-formed refs) — never geometry; the viewer applies
    the offsets to the geometry it holds, keyed by the opaque client-defined
    ``component_ref``.
    """
    service = ThreeDOverlayService(db)
    try:
        config = service.upsert_explode(
            document_item_id=document_item_id,
            explode_config=payload.model_dump(),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="explode_upsert_invalid",
            message=str(exc),
            context={"document_item_id": document_item_id},
        )
    return {"document_item_id": document_item_id, "explode": config}


@parallel_tasks_cad_3d_router.get("/cad-3d/explode/{document_item_id}")
async def get_3d_explode(
    document_item_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """G3: the document's explode config (or null).

    Inherits the overlay's role-visibility gate (403 when hidden).
    """
    service = ThreeDOverlayService(db)
    try:
        config = service.get_explode(
            document_item_id=document_item_id, user_roles=_as_roles(user)
        )
    except PermissionError as exc:
        _raise_api_error(
            status_code=403,
            code="explode_access_denied",
            message=str(exc),
            context={"document_item_id": document_item_id},
        )
    return {"document_item_id": document_item_id, "explode": config}
