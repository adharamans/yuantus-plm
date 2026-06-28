"""Admin seat-cap change-history read surface (L4 Fork B).

`GET /api/v1/admin/license-cap-history?tenant_id=...` — a superuser HTTP read of a
tenant's seat-cap change history, derived from the LICENSE audit trail that
`record_seat_cap_audit` writes (AuditLog method="LICENSE",
path="cli:license/seat-cap?max_users={N|cleared}"). Until now that trail was only
reachable via the generic /admin/audit log query. Read-only; append-only history.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.license_cap_history import collect_seat_cap_history

license_cap_change_history_router = APIRouter(
    prefix="/admin/license-cap-history", tags=["License Cap History"]
)


@license_cap_change_history_router.get("")
def get_license_cap_history(
    response: Response,
    tenant_id: str = Query(..., description="Tenant whose seat-cap change history to read."),
    limit: Optional[int] = Query(None, ge=1, le=500),
    _admin: object = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Superuser read of a tenant's seat-cap change history, newest first.

    Derived from the LICENSE audit trail (``record_seat_cap_audit``): each change
    is ``{created_at, max_users, cleared}`` where ``max_users`` is the int the cap
    was set to, or ``None`` with ``cleared=true`` for an explicit clear
    (``seats: null`` → unlimited). **No existence leak**: an unknown tenant returns
    an empty list (200), not 404. Blank ``tenant_id`` → 400.
    ``Cache-Control: no-store`` — license state must not be cached.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        return collect_seat_cap_history(db, tenant_id, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
