from __future__ import annotations

from datetime import datetime, timezone
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.services.parallel_tasks_service import BreakageIncidentService


parallel_tasks_breakage_router = APIRouter(tags=["ParallelTasks"])


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


def _parse_utc_datetime(raw: Optional[str], *, field_name: str) -> Optional[datetime]:
    if raw is None:
        return None
    value = str(raw).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _raise_api_error(
            status_code=400,
            code="invalid_datetime",
            message=f"{field_name} must be an ISO-8601 datetime",
            context={"field": field_name, "value": raw},
        )
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


# ---------------------------
# P1-F Breakage + Metrics
# ---------------------------


class BreakageCreateRequest(BaseModel):
    description: str = Field(..., min_length=1)
    severity: str = "medium"
    status: str = "open"
    product_item_id: Optional[str] = None
    bom_id: Optional[str] = None
    bom_line_item_id: Optional[str] = None
    routing_id: Optional[str] = None
    mbom_id: Optional[str] = None
    production_order_id: Optional[str] = None
    version_id: Optional[str] = None
    batch_code: Optional[str] = None
    customer_name: Optional[str] = None
    responsibility: Optional[str] = None


class BreakageStatusUpdateRequest(BaseModel):
    status: str


class BreakageDesignLoopbackEcoCreateRequest(BaseModel):
    allow_duplicate: bool = Field(
        default=False,
        description=(
            "When True, bypass the best-effort query-before-create dedupe "
            "and force a new ECO. Default False — repeated calls return "
            "the existing ECO when one is already linked to this breakage."
        ),
    )


class BreakageHelpdeskSyncRequest(BaseModel):
    metadata_json: Optional[Dict[str, Any]] = None
    provider: str = "stub"
    integration_json: Optional[Dict[str, Any]] = None
    idempotency_key: Optional[str] = None
    retry_max_attempts: Optional[int] = Field(None, ge=1, le=10)


class BreakageHelpdeskSyncResultRequest(BaseModel):
    sync_status: str = Field(..., description="completed|failed")
    job_id: Optional[str] = None
    external_ticket_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None


class BreakageHelpdeskSyncExecuteRequest(BaseModel):
    simulate_status: str = Field("completed", description="completed|failed")
    job_id: Optional[str] = None
    external_ticket_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    metadata_json: Optional[Dict[str, Any]] = None


class BreakageHelpdeskTicketUpdateRequest(BaseModel):
    provider_ticket_status: str = Field(..., min_length=1)
    job_id: Optional[str] = None
    event_id: Optional[str] = None
    external_ticket_id: Optional[str] = None
    provider: Optional[str] = Field(None, description="stub|jira|zendesk")
    provider_updated_at: Optional[str] = Field(
        None, description="ISO-8601 datetime"
    )
    provider_assignee: Optional[str] = None
    provider_payload: Optional[Dict[str, Any]] = None
    incident_status: Optional[str] = None
    incident_responsibility: Optional[str] = None


class BreakageExportJobCreateRequest(BaseModel):
    status: Optional[str] = None
    severity: Optional[str] = None
    product_item_id: Optional[str] = None
    bom_line_item_id: Optional[str] = None
    batch_code: Optional[str] = None
    responsibility: Optional[str] = None
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=200)
    export_format: str = Field("json", description="json|csv|md")
    execute_immediately: bool = True


class BreakageExportCleanupRequest(BaseModel):
    ttl_hours: int = Field(24, ge=1, le=720)
    limit: int = Field(200, ge=1, le=1000)


@parallel_tasks_breakage_router.get("/breakages/metrics")
async def get_breakage_metrics(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.metrics(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_metrics_invalid_request",
            message=str(exc),
            context={
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.get("/breakages/metrics/groups")
async def get_breakage_metrics_groups(
    group_by: str = Query(
        "responsibility",
        description=(
            "bom_id|product_item_id|batch_code|bom_line_item_id|mbom_id|responsibility|routing_id"
        ),
    ),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.metrics_groups(
            group_by=group_by,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_metrics_invalid_request",
            message=str(exc),
            context={
                "group_by": group_by,
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.get("/breakages/metrics/export")
async def export_breakage_metrics(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    export_format: str = Query("json", description="json|csv|md"),
    report_lang: Optional[str] = Query(None),
    report_type: Optional[str] = Query(None),
    locale_profile_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        exported = service.export_metrics(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
            export_format=export_format,
            report_lang=report_lang,
            report_type=report_type,
            locale_profile_id=locale_profile_id,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_metrics_invalid_request",
            message=str(exc),
            context={
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
                "export_format": export_format,
                "report_lang": report_lang,
                "report_type": report_type,
                "locale_profile_id": locale_profile_id,
            },
        )
    return StreamingResponse(
        io.BytesIO(exported["content"]),
        media_type=str(exported.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exported.get("filename") or "breakage-metrics.bin"}"'
            ),
            "X-Operator-Id": str(int(user.id)),
        },
    )


@parallel_tasks_breakage_router.get("/breakages/metrics/groups/export")
async def export_breakage_metrics_groups(
    group_by: str = Query(
        "responsibility",
        description=(
            "bom_id|product_item_id|batch_code|bom_line_item_id|mbom_id|responsibility|routing_id"
        ),
    ),
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    export_format: str = Query("json", description="json|csv|md"),
    report_lang: Optional[str] = Query(None),
    report_type: Optional[str] = Query(None),
    locale_profile_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        exported = service.export_metrics_groups(
            group_by=group_by,
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
            export_format=export_format,
            report_lang=report_lang,
            report_type=report_type,
            locale_profile_id=locale_profile_id,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_metrics_invalid_request",
            message=str(exc),
            context={
                "group_by": group_by,
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
                "export_format": export_format,
                "report_lang": report_lang,
                "report_type": report_type,
                "locale_profile_id": locale_profile_id,
            },
        )
    return StreamingResponse(
        io.BytesIO(exported["content"]),
        media_type=str(exported.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exported.get("filename") or "breakage-metrics-groups.bin"}"'
            ),
            "X-Operator-Id": str(int(user.id)),
        },
    )


@parallel_tasks_breakage_router.post("/breakages")
async def create_breakage_incident(
    payload: BreakageCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        incident = service.create_incident(
            description=payload.description,
            severity=payload.severity,
            status=payload.status,
            product_item_id=payload.product_item_id,
            bom_id=payload.bom_id,
            bom_line_item_id=payload.bom_line_item_id,
            routing_id=payload.routing_id,
            mbom_id=payload.mbom_id,
            production_order_id=payload.production_order_id,
            version_id=payload.version_id,
            batch_code=payload.batch_code,
            customer_name=payload.customer_name,
            responsibility=payload.responsibility,
            created_by_id=int(user.id),
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="breakage_invalid_request",
            message=str(exc),
            context={
                "severity": payload.severity,
                "status": payload.status,
                "product_item_id": payload.product_item_id,
                "bom_id": payload.bom_id,
                "mbom_id": payload.mbom_id or payload.version_id,
                "routing_id": payload.routing_id or payload.production_order_id,
            },
        )
    return {
        "id": incident.id,
        "incident_code": getattr(incident, "incident_code", None),
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
        "product_item_id": incident.product_item_id,
        "bom_id": getattr(incident, "bom_id", None),
        "bom_line_item_id": incident.bom_line_item_id,
        "routing_id": getattr(incident, "routing_id", None)
        or incident.production_order_id,
        "mbom_id": getattr(incident, "mbom_id", None) or incident.version_id,
        "production_order_id": getattr(incident, "routing_id", None)
        or incident.production_order_id,
        "version_id": getattr(incident, "mbom_id", None) or incident.version_id,
        "created_at": incident.created_at.isoformat() if incident.created_at else None,
    }


@parallel_tasks_breakage_router.get("/breakages")
async def list_breakage_incidents(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    incidents = service.list_incidents(
        status=status,
        severity=severity,
        product_item_id=product_item_id,
        bom_line_item_id=bom_line_item_id,
        batch_code=batch_code,
        responsibility=responsibility,
    )
    latest_helpdesk_summary_by_incident = service.build_latest_helpdesk_summary_map(
        [incident.id for incident in incidents]
    )
    return {
        "total": len(incidents),
        "incidents": [
            {
                "id": incident.id,
                "incident_code": getattr(incident, "incident_code", None),
                "description": incident.description,
                "severity": incident.severity,
                "status": incident.status,
                "product_item_id": incident.product_item_id,
                "bom_id": getattr(incident, "bom_id", None),
                "bom_line_item_id": incident.bom_line_item_id,
                "routing_id": getattr(incident, "routing_id", None)
                or incident.production_order_id,
                "mbom_id": getattr(incident, "mbom_id", None) or incident.version_id,
                "production_order_id": getattr(incident, "routing_id", None)
                or incident.production_order_id,
                "version_id": getattr(incident, "mbom_id", None) or incident.version_id,
                "batch_code": incident.batch_code,
                "customer_name": incident.customer_name,
                "responsibility": incident.responsibility,
                "helpdesk_ticket_summary": (
                    dict(latest_helpdesk_summary_by_incident.get(str(incident.id)) or {})
                    or None
                ),
                "created_at": (
                    incident.created_at.isoformat() if incident.created_at else None
                ),
                "updated_at": (
                    incident.updated_at.isoformat() if incident.updated_at else None
                ),
            }
            for incident in incidents
        ],
        "operator_id": int(user.id),
    }


@parallel_tasks_breakage_router.get("/breakages/export")
async def export_breakage_incidents(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    export_format: str = Query("json", description="json|csv|md"),
    report_lang: Optional[str] = Query(None),
    report_type: Optional[str] = Query(None),
    locale_profile_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        exported = service.export_incidents(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            page=page,
            page_size=page_size,
            export_format=export_format,
            report_lang=report_lang,
            report_type=report_type,
            locale_profile_id=locale_profile_id,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_invalid_request",
            message=str(exc),
            context={
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "page": page,
                "page_size": page_size,
                "export_format": export_format,
                "report_lang": report_lang,
                "report_type": report_type,
                "locale_profile_id": locale_profile_id,
            },
        )
    return StreamingResponse(
        io.BytesIO(exported["content"]),
        media_type=str(exported.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exported.get("filename") or "breakage-incidents.bin"}"'
            ),
            "X-Operator-Id": str(int(user.id)),
        },
    )


@parallel_tasks_breakage_router.get("/breakages/cockpit")
async def get_breakage_cockpit(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.cockpit(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_cockpit_invalid_request",
            message=str(exc),
            context={
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.get("/breakages/cockpit/export")
async def export_breakage_cockpit(
    status: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    product_item_id: Optional[str] = Query(None),
    bom_line_item_id: Optional[str] = Query(None),
    batch_code: Optional[str] = Query(None),
    responsibility: Optional[str] = Query(None),
    trend_window_days: int = Query(14, description="7|14|30"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    export_format: str = Query("json", description="json|csv|md"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        exported = service.export_cockpit(
            status=status,
            severity=severity,
            product_item_id=product_item_id,
            bom_line_item_id=bom_line_item_id,
            batch_code=batch_code,
            responsibility=responsibility,
            trend_window_days=trend_window_days,
            page=page,
            page_size=page_size,
            export_format=export_format,
        )
    except ValueError as exc:
        _raise_api_error(
            status_code=400,
            code="breakage_cockpit_invalid_request",
            message=str(exc),
            context={
                "status": status,
                "severity": severity,
                "product_item_id": product_item_id,
                "bom_line_item_id": bom_line_item_id,
                "batch_code": batch_code,
                "responsibility": responsibility,
                "trend_window_days": trend_window_days,
                "page": page,
                "page_size": page_size,
                "export_format": export_format,
            },
        )
    return StreamingResponse(
        io.BytesIO(exported["content"]),
        media_type=str(exported.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exported.get("filename") or "breakage-cockpit.bin"}"'
            ),
            "X-Operator-Id": str(int(user.id)),
        },
    )


@parallel_tasks_breakage_router.post("/breakages/export/jobs")
async def create_breakage_incidents_export_job(
    payload: BreakageExportJobCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.enqueue_incidents_export_job(
            status=payload.status,
            severity=payload.severity,
            product_item_id=payload.product_item_id,
            bom_line_item_id=payload.bom_line_item_id,
            batch_code=payload.batch_code,
            responsibility=payload.responsibility,
            page=payload.page,
            page_size=payload.page_size,
            export_format=payload.export_format,
            execute_immediately=payload.execute_immediately,
            user_id=int(user.id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="breakage_export_job_invalid",
            message=str(exc),
            context={
                "status": payload.status,
                "severity": payload.severity,
                "product_item_id": payload.product_item_id,
                "bom_line_item_id": payload.bom_line_item_id,
                "batch_code": payload.batch_code,
                "responsibility": payload.responsibility,
                "page": payload.page,
                "page_size": payload.page_size,
                "export_format": payload.export_format,
                "execute_immediately": payload.execute_immediately,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.post("/breakages/export/jobs/cleanup")
async def cleanup_breakage_incidents_export_job_results(
    payload: BreakageExportCleanupRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.cleanup_expired_incidents_export_results(
            ttl_hours=payload.ttl_hours,
            limit=payload.limit,
            user_id=int(user.id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="breakage_export_job_invalid",
            message=str(exc),
            context={
                "ttl_hours": payload.ttl_hours,
                "limit": payload.limit,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.get("/breakages/export/jobs/{job_id}")
async def get_breakage_incidents_export_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.get_incidents_export_job(job_id)
    except ValueError as exc:
        code = "breakage_export_job_not_found"
        status_code = 404
        if "not found" not in str(exc).lower():
            code = "breakage_export_job_invalid"
            status_code = 400
        _raise_api_error(
            status_code=status_code,
            code=code,
            message=str(exc),
            context={"job_id": job_id},
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.get("/breakages/export/jobs/{job_id}/download")
async def download_breakage_incidents_export_job(
    job_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        exported = service.download_incidents_export_job(job_id)
    except ValueError as exc:
        code = "breakage_export_job_not_found"
        status_code = 404
        if "not found" not in str(exc).lower():
            code = "breakage_export_job_invalid"
            status_code = 400
        _raise_api_error(
            status_code=status_code,
            code=code,
            message=str(exc),
            context={"job_id": job_id},
        )
    return StreamingResponse(
        io.BytesIO(exported["content"]),
        media_type=str(exported.get("media_type") or "application/octet-stream"),
        headers={
            "Content-Disposition": (
                f'attachment; filename="{exported.get("filename") or "breakage-incidents.bin"}"'
            ),
            "X-Operator-Id": str(int(user.id)),
        },
    )


@parallel_tasks_breakage_router.post("/breakages/{incident_id}/status")
async def update_breakage_status(
    incident_id: str,
    payload: BreakageStatusUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        incident = service.update_status(incident_id, status=payload.status)
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=404,
            code="breakage_not_found",
            message=str(exc),
            context={"incident_id": incident_id},
        )
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="breakage_status_invalid",
            message=str(exc),
            context={
                "incident_id": incident_id,
                "status": payload.status,
            },
        )
    return {
        "id": incident.id,
        "status": incident.status,
        "updated_at": incident.updated_at.isoformat() if incident.updated_at else None,
        "operator_id": int(user.id),
    }


@parallel_tasks_breakage_router.post(
    "/breakages/{incident_id}/design-loopback/eco"
)
async def create_breakage_design_loopback_eco(
    incident_id: str,
    payload: BreakageDesignLoopbackEcoCreateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Spawn (or reuse) the design-loopback ECO for an eligible breakage.

    Delegates entirely to
    ``BreakageIncidentService.create_breakage_design_loopback_eco`` —
    this route is the HTTP seam for the service method shipped in
    PR #596 (`6e4ce54`). Permission to create an ECO comes via the
    underlying ``ECOService.create_eco`` call; no dedicated capability
    is gated here (taskbook §3.5 author-recommended reject).

    Dedupe is best-effort post-PR #596 (`description`-envelope
    substring scan, race-unsafe). A 200 + ``created=False`` response
    indicates an existing ECO matched the breakage's closeout
    reference; the caller's intent ("get me the ECO for this
    breakage") is satisfied either way. Durable race-safe idempotency
    is the §3.2 follow-up.
    """

    service = BreakageIncidentService(db)
    try:
        result = service.create_breakage_design_loopback_eco(
            incident_id,
            user_id=int(user.id),
            allow_duplicate=payload.allow_duplicate,
        )
        db.commit()
    except ValueError as exc:
        # The service method raises ValueError in two cases:
        # 1) "Breakage incident not found: <id>" — true 404.
        # 2) "breakage status <s> is not eligible for design loopback"
        #    — caller's request is well-formed but the incident's
        #    state forbids the action; 409 with a discriminator code.
        # The merged eligibility predicate
        # (`is_breakage_eligible_for_design_loopback`) is the
        # authoritative source for #2's message prefix.
        db.rollback()
        message = str(exc)
        if message.startswith("Breakage incident not found"):
            _raise_api_error(
                status_code=404,
                code="breakage_not_found",
                message=message,
                context={"incident_id": incident_id},
            )
        _raise_api_error(
            status_code=409,
            code="breakage_not_eligible_for_loopback",
            message=message,
            context={"incident_id": incident_id},
        )
    except Exception:
        # Defense in depth: any other exception path
        # (`HTTPException` from `ECOService.create_eco` permission
        # check, an unrelated runtime error, etc.) must roll back
        # the session before re-raising. `ECOService.create_eco`
        # checks permission BEFORE its `session.add` / `flush`
        # (verified `eco_service.py:520` vs. `534-535`), so today
        # this only matters for a hypothetical future reorder; we
        # roll back unconditionally to keep that future safe.
        # Re-raising preserves the original exception type (and
        # its status code if it's an `HTTPException`) so FastAPI's
        # handler surfaces the upstream error verbatim — we do not
        # invent a breakage-route-local translation that would
        # diverge from how ECO routes already surface the same
        # permission errors.
        db.rollback()
        raise

    return {
        "incident_id": result.incident_id,
        "eco_id": result.eco.id,
        "reference": result.reference,
        "created": result.created,
        "operator_id": int(user.id),
    }


@parallel_tasks_breakage_router.post("/breakages/{incident_id}/helpdesk-sync")
async def sync_breakage_to_helpdesk_stub(
    incident_id: str,
    payload: BreakageHelpdeskSyncRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        job = service.enqueue_helpdesk_stub_sync(
            incident_id,
            user_id=int(user.id),
            metadata_json=payload.metadata_json,
            provider=payload.provider,
            integration_json=payload.integration_json,
            idempotency_key=payload.idempotency_key,
            retry_max_attempts=payload.retry_max_attempts,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        _raise_api_error(
            status_code=404,
            code="breakage_not_found",
            message=str(exc),
            context={"incident_id": incident_id},
        )
    except Exception as exc:
        db.rollback()
        _raise_api_error(
            status_code=400,
            code="breakage_helpdesk_sync_invalid",
            message=str(exc),
            context={"incident_id": incident_id},
        )
    return {
        "incident_id": incident_id,
        "job_id": job.id,
        "task_type": job.task_type,
        "status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@parallel_tasks_breakage_router.get("/breakages/{incident_id}/helpdesk-sync/status")
async def get_breakage_helpdesk_sync_status(
    incident_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.get_helpdesk_sync_status(incident_id)
    except ValueError as exc:
        _raise_api_error(
            status_code=404,
            code="breakage_not_found",
            message=str(exc),
            context={"incident_id": incident_id},
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.post("/breakages/{incident_id}/helpdesk-sync/execute")
async def execute_breakage_helpdesk_sync(
    incident_id: str,
    payload: BreakageHelpdeskSyncExecuteRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.execute_helpdesk_sync(
            incident_id,
            simulate_status=payload.simulate_status,
            job_id=payload.job_id,
            external_ticket_id=payload.external_ticket_id,
            error_code=payload.error_code,
            error_message=payload.error_message,
            metadata_json=payload.metadata_json,
            user_id=int(user.id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        if "Breakage incident not found" in str(exc):
            _raise_api_error(
                status_code=404,
                code="breakage_not_found",
                message=str(exc),
                context={"incident_id": incident_id},
            )
        _raise_api_error(
            status_code=400,
            code="breakage_helpdesk_sync_invalid",
            message=str(exc),
            context={
                "incident_id": incident_id,
                "simulate_status": payload.simulate_status,
                "job_id": payload.job_id,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.post("/breakages/{incident_id}/helpdesk-sync/result")
async def record_breakage_helpdesk_sync_result(
    incident_id: str,
    payload: BreakageHelpdeskSyncResultRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        result = service.record_helpdesk_sync_result(
            incident_id,
            sync_status=payload.sync_status,
            job_id=payload.job_id,
            external_ticket_id=payload.external_ticket_id,
            error_code=payload.error_code,
            error_message=payload.error_message,
            metadata_json=payload.metadata_json,
            user_id=int(user.id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        if "Breakage incident not found" in str(exc):
            _raise_api_error(
                status_code=404,
                code="breakage_not_found",
                message=str(exc),
                context={"incident_id": incident_id},
            )
        _raise_api_error(
            status_code=400,
            code="breakage_helpdesk_sync_invalid",
            message=str(exc),
            context={
                "incident_id": incident_id,
                "sync_status": payload.sync_status,
                "job_id": payload.job_id,
            },
        )
    result["operator_id"] = int(user.id)
    return result


@parallel_tasks_breakage_router.post("/breakages/{incident_id}/helpdesk-sync/ticket-update")
async def apply_breakage_helpdesk_ticket_update(
    incident_id: str,
    payload: BreakageHelpdeskTicketUpdateRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    service = BreakageIncidentService(db)
    try:
        provider_updated_at = _parse_utc_datetime(
            payload.provider_updated_at,
            field_name="provider_updated_at",
        )
        result = service.apply_helpdesk_ticket_update(
            incident_id,
            provider_ticket_status=payload.provider_ticket_status,
            job_id=payload.job_id,
            external_ticket_id=payload.external_ticket_id,
            provider=payload.provider,
            provider_updated_at=provider_updated_at,
            provider_assignee=payload.provider_assignee,
            provider_payload=payload.provider_payload,
            event_id=payload.event_id,
            incident_status=payload.incident_status,
            incident_responsibility=payload.incident_responsibility,
            user_id=int(user.id),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        if "Breakage incident not found" in str(exc):
            _raise_api_error(
                status_code=404,
                code="breakage_not_found",
                message=str(exc),
                context={"incident_id": incident_id},
            )
        _raise_api_error(
            status_code=400,
            code="breakage_helpdesk_sync_invalid",
            message=str(exc),
            context={
                "incident_id": incident_id,
                "provider_ticket_status": payload.provider_ticket_status,
                "job_id": payload.job_id,
            },
        )
    result["operator_id"] = int(user.id)
    return result
