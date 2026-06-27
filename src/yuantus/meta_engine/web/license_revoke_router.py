"""License revocation admin route (L4 Fork C).

`POST /api/v1/admin/licenses/{license_key}/revoke` — superuser, append-only revoke.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.license_revocation_service import (
    LicenseRevocationService,
)

license_revoke_router = APIRouter(prefix="/admin/licenses", tags=["License Revocation"])


class LicenseRevokeRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Why the license is being revoked (audited).")


@license_revoke_router.post("/{license_key}/revoke")
def revoke_license(
    license_key: str,
    request: LicenseRevokeRequest,
    response: Response,
    _admin: object = Depends(require_superuser),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Superuser revoke of a license (append-only).

    Sets ``AppLicense.status='Revoked'`` so ``is_entitled`` flips the feature off;
    writes a LICENSE audit row; **does NOT clear the seat cap** (no implicit
    rollback). 404 if the ``license_key`` is unknown. ``Cache-Control: no-store``.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        rows = LicenseRevocationService(db).revoke_license(license_key, reason=request.reason)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if rows is None:
        raise HTTPException(status_code=404, detail="license not found")
    db.commit()
    return {
        "license_key": license_key,
        "revoked": [{"app_name": r.app_name, "status": r.status} for r in rows],
        "count": len(rows),
    }
