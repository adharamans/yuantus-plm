from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from uuid import uuid4

from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.meta_engine.manufacturing.mbom_service import MBOMService
from yuantus.meta_engine.manufacturing.models import ManufacturingBOM
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.eco_service import ECOApprovalService
from yuantus.security.audit_retention import mark_prune, prune_audit_logs


def eco_approval_escalation(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    user_id = int(payload.get("user_id") or get_settings().SCHEDULER_SYSTEM_USER_ID or 1)
    result = ECOApprovalService(session).escalate_overdue_approvals(user_id=user_id)
    return {"ok": True, "task": "eco_approval_escalation", **result}


def audit_retention_prune(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    settings = get_settings()
    tenant_id = payload.get("tenant_id")
    retention_days = int(settings.AUDIT_RETENTION_DAYS or 0)
    retention_max_rows = int(settings.AUDIT_RETENTION_MAX_ROWS or 0)
    if retention_days <= 0 and retention_max_rows <= 0:
        return {
            "ok": True,
            "task": "audit_retention_prune",
            "skipped": True,
            "reason": "retention_disabled",
            "deleted": 0,
            "tenant_id": tenant_id,
        }

    deleted = prune_audit_logs(
        session,
        retention_days=retention_days,
        retention_max_rows=retention_max_rows,
        tenant_id=tenant_id,
    )
    mark_prune(tenant_id)
    return {
        "ok": True,
        "task": "audit_retention_prune",
        "deleted": deleted,
        "tenant_id": tenant_id,
        "retention_days": retention_days,
        "retention_max_rows": retention_max_rows,
    }


def ecm_publication_outbox_prune(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    settings = get_settings()
    retention_days = int(settings.PUBLICATION_ECM_OUTBOX_RETENTION_DAYS or 0)
    if retention_days <= 0:
        return {
            "ok": True,
            "task": "ecm_publication_outbox_prune",
            "skipped": True,
            "reason": "retention_disabled",
            "deleted": 0,
        }
    batch_size = int(settings.PUBLICATION_ECM_OUTBOX_RETENTION_BATCH_SIZE or 0)
    from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService

    deleted = EcmPublicationOutboxService(session).prune_terminal(
        retention_days=retention_days, limit=batch_size
    )
    return {
        "ok": True,
        "task": "ecm_publication_outbox_prune",
        "deleted": deleted,
        "retention_days": retention_days,
        "batch_size": batch_size,
    }


def bom_to_mbom_sync(payload: Dict[str, Any], session: Session) -> Dict[str, Any]:
    source_item_ids = _source_item_ids(payload)
    if not source_item_ids:
        return {
            "ok": True,
            "task": "bom_to_mbom_sync",
            "skipped": True,
            "reason": "no_source_item_ids",
            "created": 0,
            "skipped_count": 0,
            "errors": [],
            "items": [],
        }

    user_id = int(payload.get("user_id") or get_settings().SCHEDULER_SYSTEM_USER_ID or 1)
    service = MBOMService(session)
    effective_from = _parse_effective_from(payload.get("effective_from"))
    transformation_rules = payload.get("transformation_rules") or {}
    if not isinstance(transformation_rules, dict):
        transformation_rules = {}

    created = []
    skipped = []
    errors = []
    for source_item_id in source_item_ids:
        item = session.get(Item, source_item_id)
        if item is None:
            errors.append({"source_item_id": source_item_id, "error": "item_not_found"})
            continue
        if item.item_type_id != "Part":
            skipped.append(
                {
                    "source_item_id": source_item_id,
                    "reason": "unsupported_item_type",
                    "item_type_id": item.item_type_id,
                }
            )
            continue
        if item.is_current is not True:
            skipped.append({"source_item_id": source_item_id, "reason": "not_current"})
            continue
        if _requires_released(payload) and str(item.state or "").lower() != "released":
            skipped.append(
                {
                    "source_item_id": source_item_id,
                    "reason": "not_released",
                    "state": item.state,
                }
            )
            continue

        existing = _latest_mbom_for_source(session, source_item_id)
        if existing is not None:
            skipped.append(
                {
                    "source_item_id": source_item_id,
                    "reason": "mbom_exists",
                    "mbom_id": existing.id,
                }
            )
            continue

        mbom = service.create_mbom_from_ebom(
            source_item_id,
            _mbom_name(item),
            version=str(payload.get("version") or "1.0"),
            plant_code=_optional_str(payload.get("plant_code")),
            effective_from=effective_from,
            user_id=user_id,
            transformation_rules=transformation_rules,
        )
        created.append(
            {
                "source_item_id": source_item_id,
                "mbom_id": mbom.id,
                "name": mbom.name,
            }
        )

    return {
        "ok": not errors,
        "task": "bom_to_mbom_sync",
        "created": len(created),
        "skipped_count": len(skipped),
        "errors": errors,
        "items": created,
        "skipped_items": skipped,
    }


def _source_item_ids(payload: Dict[str, Any]) -> list[str]:
    raw = payload.get("source_item_ids")
    if raw is None:
        raw = payload.get("source_item_id")
    if raw is None:
        return []
    raw_values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
    result: list[str] = []
    seen: set[str] = set()
    for value in raw_values:
        item_id = str(value or "").strip()
        if item_id and item_id not in seen:
            seen.add(item_id)
            result.append(item_id)
    return result


def _parse_effective_from(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _requires_released(payload: Dict[str, Any]) -> bool:
    raw = payload.get("require_released", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def _latest_mbom_for_source(session: Session, source_item_id: str) -> ManufacturingBOM | None:
    return (
        session.query(ManufacturingBOM)
        .filter(ManufacturingBOM.source_item_id == source_item_id)
        .order_by(ManufacturingBOM.created_at.desc())
        .first()
    )


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _mbom_name(item: Item) -> str:
    props = item.properties or {}
    number = props.get("item_number") or props.get("number") or item.id
    return f"MBOM {number}-{uuid4().hex[:8]}"
