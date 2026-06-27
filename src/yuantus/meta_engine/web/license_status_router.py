"""Admin license-status read surface (L4-1).

`GET /api/v1/admin/license-status?tenant_id=...` — a superuser HTTP read over the
SAME summary the `yuantus license status` CLI emits (`collect_license_status`):
an entitlement map + whitelisted license rows. Read-only; never exposes the raw
`license_data` blob. Until now license status was CLI-only.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.license_status import collect_license_status

license_status_router = APIRouter(prefix="/admin/license-status", tags=["License Status"])


@license_status_router.get("")
def get_license_status(
    response: Response,
    tenant_id: str = Query(..., description="Tenant whose license status to read."),
    _admin: object = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Superuser read of a tenant's license status (entitlements + license rows).

    Reuses ``collect_license_status`` (the CLI's summary builder): ``features``
    comes from ``EntitlementService.is_entitled`` (the sole entitlement check) and
    ``licenses`` is a field whitelist (never the raw ``license_data``). Auth →
    query order: ``require_superuser`` (admin gate) runs before any read. **No
    existence leak**: a tenant with no licenses returns an empty ``licenses`` list
    + all-false ``features`` (200), not a 404. Blank ``tenant_id`` → 400.
    ``Cache-Control: no-store`` — license state must not be cached.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        status = collect_license_status(db, tenant_id)
    except ValueError as exc:  # blank/whitespace tenant_id
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return asdict(status)
