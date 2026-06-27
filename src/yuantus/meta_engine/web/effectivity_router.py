from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.services.latest_released_guard import NotLatestReleasedError
from yuantus.meta_engine.services.suspended_guard import SuspendedStateError
from yuantus.meta_engine.services.effectivity_service import (
    EffectivityElapsedError,
    EffectivityNotDateError,
    EffectivityService,
)


effectivity_router = APIRouter(prefix="/effectivities", tags=["Effectivity"])


class EffectivityCreateRequest(BaseModel):
    item_id: Optional[str] = Field(
        None, description="Target item ID (relationship item for BOM lines)."
    )
    version_id: Optional[str] = Field(
        None, description="Target version ID for version-level effectivity."
    )
    effectivity_type: str = Field("Date", description="Date, Lot, Serial, Unit")
    start_date: Optional[datetime] = Field(None, description="Start date (Date type)")
    end_date: Optional[datetime] = Field(None, description="End date (Date type)")
    payload: Optional[Dict[str, Any]] = Field(
        None, description="Extension payload for Lot/Serial/Unit"
    )


class EffectivityDateUpdateRequest(BaseModel):
    start_date: Optional[datetime] = Field(
        None, description="New window start (Date type); omit to leave unchanged, null to clear."
    )
    end_date: Optional[datetime] = Field(
        None,
        description="New window end (Date type); omit to leave unchanged, null to clear (open-ended).",
    )


class EffectivityResponse(BaseModel):
    id: str
    item_id: Optional[str] = None
    version_id: Optional[str] = None
    effectivity_type: str
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    payload: Dict[str, Any] = {}
    created_at: Optional[datetime] = None
    created_by_id: Optional[int] = None


def _normalize_type(value: str) -> str:
    return (value or "").strip().title()


def _serialize_effectivity(eff) -> EffectivityResponse:
    return EffectivityResponse(
        id=eff.id,
        item_id=eff.item_id,
        version_id=eff.version_id,
        effectivity_type=eff.effectivity_type,
        start_date=eff.start_date,
        end_date=eff.end_date,
        payload=eff.payload or {},
        created_at=eff.created_at,
        created_by_id=eff.created_by_id,
    )


@effectivity_router.post("", response_model=EffectivityResponse)
def create_effectivity(
    request: EffectivityCreateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not request.item_id and not request.version_id:
        raise HTTPException(
            status_code=400, detail="item_id or version_id is required"
        )

    effectivity_type = _normalize_type(request.effectivity_type)
    payload = request.payload or {}

    allowed = {"Date", "Lot", "Serial", "Unit"}
    if effectivity_type not in allowed:
        raise HTTPException(
            status_code=400, detail=f"Unsupported effectivity_type: {request.effectivity_type}"
        )

    if effectivity_type == "Date":
        if request.start_date is None and request.end_date is None:
            raise HTTPException(
                status_code=400, detail="start_date or end_date is required for Date"
            )
    elif effectivity_type == "Lot":
        if not payload.get("lot_start") and not payload.get("lot_end"):
            raise HTTPException(
                status_code=400, detail="payload.lot_start or payload.lot_end is required for Lot"
            )
    elif effectivity_type == "Serial":
        serials = payload.get("serials") or []
        if not isinstance(serials, list) or not serials:
            raise HTTPException(
                status_code=400, detail="payload.serials must be a non-empty list for Serial"
            )
    elif effectivity_type == "Unit":
        positions = payload.get("unit_positions") or []
        if not isinstance(positions, list) or not positions:
            raise HTTPException(
                status_code=400,
                detail="payload.unit_positions must be a non-empty list for Unit",
            )

    service = EffectivityService(db)
    try:
        eff = service.create_effectivity(
            item_id=request.item_id,
            version_id=request.version_id,
            effectivity_type=effectivity_type,
            start_date=request.start_date,
            end_date=request.end_date,
            payload=payload,
            created_by_id=user.id,
        )
    except NotLatestReleasedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except SuspendedStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    db.commit()
    return _serialize_effectivity(eff)


@effectivity_router.get("/{effectivity_id}", response_model=EffectivityResponse)
def get_effectivity(effectivity_id: str, db: Session = Depends(get_db)):
    service = EffectivityService(db)
    eff = service.get_effectivity(effectivity_id)
    if not eff:
        raise HTTPException(status_code=404, detail="Effectivity not found")
    return _serialize_effectivity(eff)


@effectivity_router.get("/items/{item_id}", response_model=List[EffectivityResponse])
def get_item_effectivities(item_id: str, db: Session = Depends(get_db)):
    service = EffectivityService(db)
    return [_serialize_effectivity(eff) for eff in service.get_item_effectivities(item_id)]


@effectivity_router.get("/versions/{version_id}", response_model=List[EffectivityResponse])
def get_version_effectivities(version_id: str, db: Session = Depends(get_db)):
    service = EffectivityService(db)
    return [
        _serialize_effectivity(eff)
        for eff in service.get_version_effectivities(version_id)
    ]


@effectivity_router.patch("/{effectivity_id}", response_model=EffectivityResponse)
def update_effectivity_dates(
    effectivity_id: str,
    request: EffectivityDateUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Edit a **Date** effectivity's window (start_date/end_date). v1: Date only.

    409 if the window already elapsed (``end_date < now``) — it may have been swept by
    the date-obsolete worker — or if the target is not latest-released / is suspended;
    400 for a non-Date target, ``start_date >= end_date``, or no fields; 404 if not found.
    """
    provided = request.model_fields_set & {"start_date", "end_date"}
    if not provided:
        raise HTTPException(status_code=400, detail="start_date or end_date is required")
    kwargs = {field: getattr(request, field) for field in provided}
    service = EffectivityService(db)
    try:
        eff = service.update_effectivity(effectivity_id, **kwargs)
    except EffectivityNotDateError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=exc.to_detail()) from exc
    except EffectivityElapsedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except NotLatestReleasedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except SuspendedStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if eff is None:
        raise HTTPException(status_code=404, detail="Effectivity not found")
    db.commit()
    return _serialize_effectivity(eff)


@effectivity_router.delete("/{effectivity_id}", response_model=Dict[str, Any])
def delete_effectivity(effectivity_id: str, db: Session = Depends(get_db)):
    service = EffectivityService(db)
    try:
        ok = service.delete_effectivity(effectivity_id)
    except NotLatestReleasedError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    except SuspendedStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.to_detail()) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="Effectivity not found")
    db.commit()
    return {"ok": True}
