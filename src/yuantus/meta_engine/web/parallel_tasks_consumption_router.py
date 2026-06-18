from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import (
    CurrentUser,
    get_current_user,
    require_admin_permission,
)
from fastapi.responses import JSONResponse

from yuantus.api.dependencies.mes_ingest_auth import require_mes_ingest_credential
from yuantus.config import get_settings
from yuantus.database import get_db
from dataclasses import replace

from yuantus.meta_engine.models.parallel_tasks import MesConsumptionInbox
from yuantus.meta_engine.services.consumption_mes_inbox_service import (
    MesConsumptionInboxService,
)
from yuantus.meta_engine.models.parallel_tasks import ConsumptionPlan
from yuantus.meta_engine.services.consumption_mes_contract import (
    RESERVED_PROPERTIES_KEY,
    MesConsumptionEvent,
    map_mes_event_to_consumption_record_inputs,
)
from yuantus.meta_engine.services.consumption_uom_conversion import (
    CONVERSION_TABLE_VERSION,
    UnconvertibleUnitsError,
    convert_quantity,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService


parallel_tasks_consumption_router = APIRouter(tags=["ParallelTasks"])


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


class ConsumptionPlanCreateRequest(BaseModel):
    name: str
    planned_quantity: float = Field(..., gt=0)
    uom: str = "EA"
    period_unit: str = "week"
    item_id: Optional[str] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None


class ConsumptionActualRequest(BaseModel):
    actual_quantity: float = Field(..., gt=0)
    source_type: str = "workorder"
    source_id: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None


class ConsumptionTemplateVersionCreateRequest(BaseModel):
    name: str
    planned_quantity: float = Field(..., gt=0)
    version_label: Optional[str] = None
    uom: str = "EA"
    period_unit: str = "week"
    item_id: Optional[str] = None
    activate: bool = True
    properties: Optional[Dict[str, Any]] = None


class ConsumptionTemplateVersionStateRequest(BaseModel):
    activate: bool = True


class ConsumptionTemplateImpactPreviewRequest(BaseModel):
    planned_quantity: float = Field(..., gt=0)
    uom: Optional[str] = None
    period_unit: Optional[str] = None


@parallel_tasks_consumption_router.post("/consumption/plans")
async def create_consumption_plan(
    payload: ConsumptionPlanCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        plan = service.create_plan(
            name=payload.name,
            planned_quantity=payload.planned_quantity,
            uom=payload.uom,
            period_unit=payload.period_unit,
            item_id=payload.item_id,
            created_by_id=int(user.id),
            properties=payload.properties,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="consumption_plan_invalid_request",
            message=str(exc),
            context={
                "name": payload.name,
                "item_id": payload.item_id,
            },
        )
    return {
        "id": plan.id,
        "name": plan.name,
        "planned_quantity": plan.planned_quantity,
        "uom": plan.uom,
        "period_unit": plan.period_unit,
        "state": plan.state,
        "item_id": plan.item_id,
    }


@parallel_tasks_consumption_router.get("/consumption/plans")
async def list_consumption_plans(
    state: Optional[str] = Query(None),
    item_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    plans = service.list_plans(state=state, item_id=item_id)
    return {
        "total": len(plans),
        "plans": [
            {
                "id": plan.id,
                "name": plan.name,
                "state": plan.state,
                "planned_quantity": float(plan.planned_quantity or 0.0),
                "uom": plan.uom,
                "period_unit": plan.period_unit,
                "item_id": plan.item_id,
            }
            for plan in plans
        ],
        "operator_id": int(user.id),
    }


@parallel_tasks_consumption_router.post("/consumption/templates/{template_key}/versions")
async def create_consumption_template_version(
    template_key: str,
    payload: ConsumptionTemplateVersionCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        plan = service.create_template_version(
            template_key=template_key,
            name=payload.name,
            planned_quantity=payload.planned_quantity,
            version_label=payload.version_label,
            uom=payload.uom,
            period_unit=payload.period_unit,
            item_id=payload.item_id,
            activate=payload.activate,
            created_by_id=int(user.id),
            properties=payload.properties,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="consumption_template_version_invalid",
            message=str(exc),
            context={"template_key": template_key},
        )
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="consumption_template_version_invalid",
            message=str(exc),
            context={"template_key": template_key},
        )

    template = (plan.properties or {}).get("template") if isinstance(plan.properties, dict) else {}
    if not isinstance(template, dict):
        template = {}
    return {
        "id": plan.id,
        "name": plan.name,
        "state": plan.state,
        "planned_quantity": float(plan.planned_quantity or 0.0),
        "uom": plan.uom,
        "period_unit": plan.period_unit,
        "item_id": plan.item_id,
        "template": {
            "key": template.get("key"),
            "version": template.get("version"),
            "is_template_version": bool(template.get("is_template_version")),
            "is_active": bool(template.get("is_active")),
        },
        "operator_id": int(user.id),
    }


@parallel_tasks_consumption_router.get("/consumption/templates/{template_key}/versions")
async def list_consumption_template_versions(
    template_key: str,
    include_inactive: bool = Query(True),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    versions = service.list_template_versions(
        template_key=template_key,
        include_inactive=include_inactive,
    )
    return {
        "template_key": template_key,
        "total": len(versions),
        "versions": versions,
        "operator_id": int(user.id),
    }


@parallel_tasks_consumption_router.post("/consumption/templates/versions/{plan_id}/state")
async def set_consumption_template_version_state(
    plan_id: str,
    payload: ConsumptionTemplateVersionStateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        plan = service.set_template_version_state(
            plan_id=plan_id,
            activate=payload.activate,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        message = str(exc)
        lowered = message.lower()
        if "not found" in lowered:
            _raise_api_error(
                status_code=404,
                code="consumption_template_version_not_found",
                message=message,
                context={"plan_id": plan_id},
            )
        _raise_api_error(
            status_code=400,
            code="consumption_template_version_invalid",
            message=message,
            context={"plan_id": plan_id},
        )
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="consumption_template_version_invalid",
            message=str(exc),
            context={"plan_id": plan_id},
        )

    template = (plan.properties or {}).get("template") if isinstance(plan.properties, dict) else {}
    if not isinstance(template, dict):
        template = {}
    return {
        "id": plan.id,
        "state": plan.state,
        "template": {
            "key": template.get("key"),
            "version": template.get("version"),
            "is_template_version": bool(template.get("is_template_version")),
            "is_active": bool(template.get("is_active")),
        },
        "operator_id": int(user.id),
    }


@parallel_tasks_consumption_router.post("/consumption/templates/{template_key}/impact-preview")
async def preview_consumption_template_impact(
    template_key: str,
    payload: ConsumptionTemplateImpactPreviewRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        result = service.preview_template_impact(
            template_key=template_key,
            planned_quantity=payload.planned_quantity,
            uom=payload.uom,
            period_unit=payload.period_unit,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="consumption_template_preview_invalid",
            message=str(exc),
            context={"template_key": template_key},
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_consumption_router.post("/consumption/plans/{plan_id}/actuals")
async def add_consumption_actual(
    plan_id: str,
    payload: ConsumptionActualRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        record = service.add_actual(
            plan_id=plan_id,
            actual_quantity=payload.actual_quantity,
            source_type=payload.source_type,
            source_id=payload.source_id,
            properties=payload.properties,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=404,
            code="consumption_plan_not_found",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="consumption_actual_invalid_request",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    return {
        "id": record.id,
        "plan_id": record.plan_id,
        "source_type": record.source_type,
        "source_id": record.source_id,
        "actual_quantity": record.actual_quantity,
        "recorded_at": record.recorded_at.isoformat() if record.recorded_at else None,
    }


@parallel_tasks_consumption_router.post("/consumption/plans/{plan_id}/mes-actuals")
async def ingest_mes_consumption_actual(
    plan_id: str,
    event: MesConsumptionEvent,
    db: Session = Depends(require_mes_ingest_credential),
):
    """MES -> ConsumptionRecord ingestion (R2). Typed, idempotent ingest of a
    single MES consumption event. Distinct from the generic manual `/actuals`
    route: it enforces replay idempotency via the unique `idempotency_key`
    column so a retried at-least-once MES delivery never double-counts
    `variance`. A same-key event with a divergent business payload
    (`actual_quantity` / `source_id`) is a 409 conflict, never silently dropped.
    """
    if event.plan_id != plan_id:
        _raise_api_error(
            status_code=400,
            code="consumption_mes_plan_id_mismatch",
            message="event.plan_id must match the path plan_id",
            context={"path_plan_id": plan_id, "body_plan_id": event.plan_id},
        )
    # R2.5 async mode (default OFF): persist the raw event to the inbox + return
    # 202; the inbox worker drains it later through the SAME ingest path. Accept
    # is idempotent (the inbox unique key). Conflicts/validation surface on the
    # inbox row (ops surface), not synchronously -- the documented producer-contract
    # shift. The synchronous path below is unchanged when async is off.
    if bool(getattr(get_settings(), "MES_INGEST_ASYNC", False)):
        try:
            row, disposition = MesConsumptionInboxService(db).accept_event(event)
            db.commit()
        except ValueError as exc:
            # Boundary validation the sync path also enforces (e.g. the reserved
            # `_ingestion` key) -> 400, symmetric with the sync route, so an
            # invalid payload is not durably accepted.
            db.rollback()
            _raise_api_error(
                status_code=400,
                code="consumption_mes_invalid_event",
                message=str(exc),
                context={"plan_id": plan_id},
            )
        except Exception as exc:
            db.rollback()
            _raise_api_error(
                status_code=500,
                code="consumption_mes_accept_failed",
                message=str(exc),
                context={"plan_id": plan_id},
            )
        return JSONResponse(
            status_code=202,
            content={"disposition": disposition, "inbox_id": row.id, "state": row.state},
        )
    service = ConsumptionPlanService(db)
    try:
        inputs = map_mes_event_to_consumption_record_inputs(event)
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="consumption_mes_invalid_event",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    # uom reconciliation + conversion (R2.1 -> R2.4). A DECLARED event.uom is
    # reconciled against the plan's unit: same unit (or omitted) -> pass through;
    # different-but-convertible (same dimension) -> CONVERT the quantity into the
    # plan's unit (variance stays unit-consistent) and record the conversion in the
    # audit envelope; genuinely unconvertible (different dimension / unknown unit)
    # -> 422, no write. Conversion happens BEFORE ingest, so the stored quantity and
    # the R2 conflict-compare both see the converted value; the idempotency key is
    # unchanged (no qty/uom in it), so an equivalent-unit replay stays DUPLICATE.
    # The plan is loaded once here and reused (identity-map cached) by add_actual; a
    # not-found plan is left to ingest_mes_consumption's 404.
    if event.uom is not None:
        plan = db.get(ConsumptionPlan, plan_id)
        if plan is not None and event.uom.strip().upper() != (
            (plan.uom or "EA").strip().upper()
        ):
            try:
                converted, factor = convert_quantity(
                    event.actual_quantity, event.uom, plan.uom or "EA"
                )
            except UnconvertibleUnitsError:
                _raise_api_error(
                    status_code=422,
                    code="consumption_mes_uom_unconvertible",
                    message=(
                        "event uom is not convertible to the plan uom (different "
                        "dimension or unknown unit); send the plan's unit"
                    ),
                    context={
                        "plan_id": plan_id,
                        "plan_uom": plan.uom,
                        "event_uom": event.uom,
                    },
                )
            envelope = {
                **inputs.properties.get(RESERVED_PROPERTIES_KEY, {}),
                "original_uom": event.uom,
                "original_quantity": event.actual_quantity,
                "converted_to_uom": plan.uom,
                "conversion_factor": factor,
                "conversion_table_version": CONVERSION_TABLE_VERSION,
            }
            inputs = replace(
                inputs,
                actual_quantity=converted,
                properties={**inputs.properties, RESERVED_PROPERTIES_KEY: envelope},
            )
    try:
        record, disposition = service.ingest_mes_consumption(inputs)
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=404,
            code="consumption_plan_not_found",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    except OperationalError:
        # Transient/retryable DB failure (deadlock, serialization, connection
        # blip) under concurrent ingest. This endpoint is at-least-once and
        # idempotent, so a 5xx tells the producer to RETRY safely -- a 4xx here
        # would tell it to drop the event, which (variance sums all rows) is a
        # silent UNDERCOUNT, the symmetric failure to the double-count the
        # idempotency design prevents.
        db.rollback()
        _raise_api_error(
            status_code=503,
            code="consumption_mes_ingest_unavailable",
            message="transient database error; safe to retry (ingestion is idempotent)",
            context={"plan_id": plan_id},
        )
    except Exception as exc:
        # Any other unexpected error reaching here is server-side: the only
        # client-caused failures (invalid event, plan-not-found) are handled
        # above. Surface as 500 so an at-least-once producer can retry, never 4xx.
        db.rollback()
        _raise_api_error(
            status_code=500,
            code="consumption_mes_ingest_error",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    if disposition == "CONFLICT":
        db.rollback()
        _raise_api_error(
            status_code=409,
            code="consumption_mes_idempotency_conflict",
            message=(
                "idempotency_key already recorded with a different payload; "
                "send a corrected value as a new mes_event_id"
            ),
            context={
                "plan_id": plan_id,
                "idempotency_key": inputs.idempotency_key,
                "existing_record_id": record.id,
            },
        )
    db.commit()
    return {
        "disposition": disposition,
        "idempotency_key": inputs.idempotency_key,
        "id": record.id,
        "plan_id": record.plan_id,
        "source_type": record.source_type,
        "source_id": record.source_id,
        "actual_quantity": record.actual_quantity,
        "recorded_at": record.recorded_at.isoformat() if record.recorded_at else None,
    }


@parallel_tasks_consumption_router.get("/consumption/plans/{plan_id}/variance")
async def get_consumption_variance(
    plan_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    try:
        result = service.variance(plan_id)
    except ValueError as exc:
        _raise_api_error(
            status_code=404,
            code="consumption_plan_not_found",
            message=str(exc),
            context={"plan_id": plan_id},
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_consumption_router.get("/consumption/dashboard")
async def get_consumption_dashboard(
    item_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = ConsumptionPlanService(db)
    result = service.dashboard(item_id=item_id)
    result["operator_id"] = int(user.id)
    return result


# --- MES inbox ops (R2.5) — admin-gated visibility + replay -----------------
_INBOX_STATES = {"pending", "processed", "conflict", "failed"}


def _inbox_row(row: MesConsumptionInbox) -> Dict[str, Any]:
    return {
        "id": row.id,
        "idempotency_key": row.idempotency_key,
        "plan_id": row.plan_id,
        "mes_event_id": row.mes_event_id,
        "source_type": row.source_type,
        "actual_quantity": row.actual_quantity,
        "uom": row.uom,
        "state": row.state,
        "attempt_count": row.attempt_count,
        "error": row.error,
        "record_id": row.record_id,
    }


@parallel_tasks_consumption_router.get("/consumption/mes-inbox")
async def list_mes_inbox(
    state: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    if state is not None and state not in _INBOX_STATES:
        _raise_api_error(
            status_code=422, code="consumption_mes_inbox_invalid_state",
            message=f"state must be one of {sorted(_INBOX_STATES)}",
        )
    q = db.query(MesConsumptionInbox)
    if state is not None:
        q = q.filter(MesConsumptionInbox.state == state)
    rows = q.order_by(MesConsumptionInbox.created_at.desc()).limit(limit).all()
    return {"count": len(rows), "rows": [_inbox_row(r) for r in rows]}


@parallel_tasks_consumption_router.get("/consumption/mes-inbox/{inbox_id}")
async def get_mes_inbox(
    inbox_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    row = db.get(MesConsumptionInbox, inbox_id)
    if row is None:
        _raise_api_error(
            status_code=404, code="consumption_mes_inbox_not_found",
            message="inbox row not found", context={"inbox_id": inbox_id},
        )
    return _inbox_row(row)


@parallel_tasks_consumption_router.post("/consumption/mes-inbox/{inbox_id}/replay")
async def replay_mes_inbox(
    inbox_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    row = db.get(MesConsumptionInbox, inbox_id)
    if row is None:
        _raise_api_error(
            status_code=404, code="consumption_mes_inbox_not_found",
            message="inbox row not found", context={"inbox_id": inbox_id},
        )
    if row.state != "failed":
        _raise_api_error(
            status_code=409, code="consumption_mes_inbox_not_replayable",
            message="only failed rows can be replayed", context={"state": row.state},
        )
    row.state = "pending"
    row.attempt_count = 0
    row.error = None
    db.commit()
    return _inbox_row(row)
