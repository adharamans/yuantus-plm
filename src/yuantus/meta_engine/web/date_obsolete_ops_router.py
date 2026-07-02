"""CAD-PDM C3 — admin ops for date-obsolete where-used impact flags (Slice 2).

The async analogue of a review surface: list / inspect / acknowledge the depth-1 parent
flags raised when a date effectivity expires. Read + ack only — never re-triggers obsolete.
"""
from __future__ import annotations

import csv
import json
from io import StringIO
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import (
    CurrentUser,
    get_current_user,
    require_admin_permission,
)
from yuantus.database import get_db
from yuantus.meta_engine.models.date_obsolete import (
    DateObsoleteImpact,
    DateObsoleteImpactCorrection,
)
from yuantus.meta_engine.web.csv_export_safety import neutralize_csv_formula

date_obsolete_ops_router = APIRouter(tags=["CADPDM"])

_IMPACT_STATES = {"open", "acknowledged"}
_EXPORT_FORMATS = {"csv", "json"}
_EXPORT_COLUMNS = [
    "id",
    "effectivity_id",
    "child_item_id",
    "parent_item_id",
    "child_obsoleted",
    "reason",
    "state",
    "detected_at",
    "acknowledged_at",
    "acknowledged_by_id",
    "properties",
]


def _impact(row: DateObsoleteImpact) -> Dict[str, Any]:
    return {
        "id": row.id,
        "effectivity_id": row.effectivity_id,
        "child_item_id": row.child_item_id,
        "parent_item_id": row.parent_item_id,
        "child_obsoleted": row.child_obsoleted,
        "reason": row.reason,
        "state": row.state,
        "detected_at": row.detected_at.isoformat() if row.detected_at else None,
        "acknowledged_at": row.acknowledged_at.isoformat() if row.acknowledged_at else None,
        "acknowledged_by_id": row.acknowledged_by_id,
        "properties": row.properties,
    }


def _validate_state(state: Optional[str]) -> None:
    if state is not None and state not in _IMPACT_STATES:
        raise HTTPException(
            status_code=422,
            detail={"code": "cadpdm_date_obsolete_invalid_state",
                    "message": f"state must be one of {sorted(_IMPACT_STATES)}"},
        )


def _impact_query(
    db: Session,
    *,
    state: Optional[str] = None,
    child_obsoleted: Optional[bool] = None,
):
    _validate_state(state)
    q = db.query(DateObsoleteImpact)
    if state is not None:
        q = q.filter(DateObsoleteImpact.state == state)
    if child_obsoleted is not None:
        q = q.filter(DateObsoleteImpact.child_obsoleted == child_obsoleted)
    return q.order_by(DateObsoleteImpact.detected_at.desc())


def _export_csv(rows: List[Dict[str, Any]]) -> str:
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_EXPORT_COLUMNS)
    for row in rows:
        writer.writerow(
            [
                neutralize_csv_formula(json.dumps(row.get("properties") or {}, ensure_ascii=False))
                if column == "properties"
                else neutralize_csv_formula(row.get(column))
                for column in _EXPORT_COLUMNS
            ]
        )
    return buffer.getvalue()


@date_obsolete_ops_router.get("/cadpdm/date-obsolete-impacts")
async def list_date_obsolete_impacts(
    state: Optional[str] = Query(None),
    child_obsoleted: Optional[bool] = Query(
        None,
        description="Optional filter: only impacts whose child was/was not obsoleted.",
    ),
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    rows = _impact_query(db, state=state, child_obsoleted=child_obsoleted).limit(limit).all()
    return {"count": len(rows), "rows": [_impact(r) for r in rows]}


@date_obsolete_ops_router.get("/cadpdm/date-obsolete-impacts/summary")
async def date_obsolete_impacts_summary(
    child_obsoleted: Optional[bool] = Query(
        None,
        description="Optional filter: count only impacts where the child was (true) / was "
        "not (false) obsoleted. Omit to count all.",
    ),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Count-by-state summary of date-obsolete impacts (admin ops dashboard).

    Stable shape — every known state present (0 when none) + the total — so a dashboard
    renders without guessing which states exist. Declared BEFORE the /{impact_id} route so
    the literal "summary" is not captured as an impact id.
    """
    require_admin_permission(user)
    q = db.query(DateObsoleteImpact.state, func.count(DateObsoleteImpact.id))
    if child_obsoleted is not None:
        q = q.filter(DateObsoleteImpact.child_obsoleted == child_obsoleted)
    counts = dict(q.group_by(DateObsoleteImpact.state).all())
    by_state = {s: int(counts.get(s, 0)) for s in sorted(_IMPACT_STATES)}
    return {"by_state": by_state, "total": sum(by_state.values())}


@date_obsolete_ops_router.get("/cadpdm/date-obsolete-impacts/export")
async def export_date_obsolete_impacts(
    export_format: str = Query("csv", alias="format", description="csv or json"),
    state: Optional[str] = Query(None),
    child_obsoleted: Optional[bool] = Query(
        None,
        description="Optional filter: only impacts whose child was/was not obsoleted.",
    ),
    limit: int = Query(1000, ge=1, le=1000),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Export date-obsolete impacts for admin ops triage.

    Read-only: this uses the same list filters and never acknowledges, reverts, or re-runs
    the obsolete worker. Declared before ``/{impact_id}`` so the literal "export" is not
    captured as a dynamic impact id.
    """
    require_admin_permission(user)
    fmt = (export_format or "csv").lower().strip()
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail={"code": "cadpdm_date_obsolete_invalid_export_format",
                    "message": "format must be csv or json"},
        )
    rows = [
        _impact(r)
        for r in _impact_query(db, state=state, child_obsoleted=child_obsoleted)
        .limit(limit)
        .all()
    ]
    headers = {
        "Content-Disposition": f'attachment; filename="date-obsolete-impacts.{fmt}"',
    }
    if fmt == "json":
        return Response(
            json.dumps({"count": len(rows), "rows": rows}, ensure_ascii=False),
            media_type="application/json",
            headers=headers,
        )
    return Response(_export_csv(rows), media_type="text/csv", headers=headers)


class _BatchAcknowledgeRequest(BaseModel):
    impact_ids: List[str]


@date_obsolete_ops_router.post("/cadpdm/date-obsolete-impacts/acknowledge-batch")
async def acknowledge_date_obsolete_impacts_batch(
    payload: _BatchAcknowledgeRequest = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Acknowledge many impacts in one atomic transaction (admin ops bulk action).

    Idempotent + no existence leak: unknown ids and already-acknowledged rows are simply
    skipped (not an error), so re-running a batch is safe. Returns only the rows actually
    transitioned open -> acknowledged this call. Acknowledge never re-triggers obsolete.
    """
    require_admin_permission(user)
    # de-dup, preserving order; one IN query for all requested ids
    ids = list(dict.fromkeys(payload.impact_ids))
    acknowledged = []
    if ids:
        rows = db.query(DateObsoleteImpact).filter(DateObsoleteImpact.id.in_(ids)).all()
        now = datetime.utcnow()
        uid = int(user.id)
        for row in rows:
            if row.state != "acknowledged":
                row.state = "acknowledged"
                row.acknowledged_at = now
                row.acknowledged_by_id = uid
                acknowledged.append(row)
        if acknowledged:
            db.commit()
    return {
        "requested": len(ids),
        "acknowledged_count": len(acknowledged),
        "rows": [_impact(r) for r in acknowledged],
    }


# -- DP1 (i)/(ii) review-flag revert (reopen + un-acknowledge) ----------------
# Ratified scope (#932): revert ONLY the review axis (state acknowledged -> open, clear the
# ack fields) and record it as an append-only correction event. It NEVER touches
# child_obsoleted / reason / the child Item's lifecycle state — DP1 (iii) undo-promotion is
# deferred to a separate ratification. The append-only trail lives in its own table
# (meta_date_obsolete_impact_corrections) because the worker overwrites .properties each
# re-scan; the review axis itself is worker-stable (_upsert_impact never touches state/ack).


class _RevertRequest(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=400)


class _BatchRevertRequest(BaseModel):
    impact_ids: List[str]
    reason: Optional[str] = Field(default=None, max_length=400)


def _revert_impact(
    db: Session, row: DateObsoleteImpact, uid: int, reason: Optional[str], now: datetime
) -> bool:
    """Revert one impact's review flag acknowledged -> open. Returns True iff it transitioned
    (was 'acknowledged'); already-'open' rows are a no-op (False). Appends an append-only
    correction event snapshotting the prior review-axis state BEFORE clearing it, so ack
    history is never destroyed. Touches ONLY state + acknowledged_* — never child_obsoleted /
    reason / Item lifecycle (DP1 iii). The caller commits."""
    if row.state != "acknowledged":
        return False
    db.add(
        DateObsoleteImpactCorrection(
            impact_id=row.id,
            action="revert_reopen",
            prior_state=row.state,
            prior_acknowledged_at=row.acknowledged_at,
            prior_acknowledged_by_id=row.acknowledged_by_id,
            reason=reason,
            reverted_by_id=uid,
            created_at=now,
        )
    )
    row.state = "open"
    row.acknowledged_at = None
    row.acknowledged_by_id = None
    return True


@date_obsolete_ops_router.post("/cadpdm/date-obsolete-impacts/revert-batch")
async def revert_date_obsolete_impacts_batch(
    payload: _BatchRevertRequest = Body(...),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Revert many acknowledged impacts back to open (DP1 i/ii) in one atomic transaction.

    Idempotent + no existence leak (mirrors acknowledge-batch): unknown ids and
    already-open rows are simply skipped (not an error), so re-running is safe. Each revert
    APPENDS a correction event (prior ack snapshot preserved) — it never destroys history and
    never touches child_obsoleted / Item lifecycle. Returns only the rows actually
    transitioned acknowledged -> open this call.
    """
    require_admin_permission(user)
    ids = list(dict.fromkeys(payload.impact_ids))
    reverted: List[DateObsoleteImpact] = []
    if ids:
        rows = db.query(DateObsoleteImpact).filter(DateObsoleteImpact.id.in_(ids)).all()
        now = datetime.utcnow()
        uid = int(user.id)
        for row in rows:
            if _revert_impact(db, row, uid, payload.reason, now):
                reverted.append(row)
        if reverted:
            db.commit()
    return {
        "requested": len(ids),
        "reverted_count": len(reverted),
        "rows": [_impact(r) for r in reverted],
    }


@date_obsolete_ops_router.get("/cadpdm/date-obsolete-impacts/{impact_id}")
async def get_date_obsolete_impact(
    impact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    row = db.get(DateObsoleteImpact, impact_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "cadpdm_date_obsolete_not_found", "message": "impact not found"},
        )
    return _impact(row)


@date_obsolete_ops_router.post("/cadpdm/date-obsolete-impacts/{impact_id}/acknowledge")
async def acknowledge_date_obsolete_impact(
    impact_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_admin_permission(user)
    row = db.get(DateObsoleteImpact, impact_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "cadpdm_date_obsolete_not_found", "message": "impact not found"},
        )
    if row.state != "acknowledged":
        row.state = "acknowledged"
        row.acknowledged_at = datetime.utcnow()
        row.acknowledged_by_id = int(user.id)
        db.commit()
    return _impact(row)


@date_obsolete_ops_router.post("/cadpdm/date-obsolete-impacts/{impact_id}/revert")
async def revert_date_obsolete_impact(
    impact_id: str,
    payload: Optional[_RevertRequest] = Body(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Revert one impact's review flag acknowledged -> open (DP1 i/ii).

    404 if the id is unknown. An already-open row is a no-op (returned unchanged). Appends an
    append-only correction event (prior ack snapshot preserved); never touches
    child_obsoleted / Item lifecycle.
    """
    require_admin_permission(user)
    row = db.get(DateObsoleteImpact, impact_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "cadpdm_date_obsolete_not_found", "message": "impact not found"},
        )
    reason = payload.reason if payload else None
    if _revert_impact(db, row, int(user.id), reason, datetime.utcnow()):
        db.commit()
    return _impact(row)
