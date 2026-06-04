"""PLM-COLLAB-P2.5 (Integration Handshake): the integration capability manifest route.

``GET /api/v1/integrations/capabilities`` -- the provider-wide handshake a consumer
(MetaSheet) queries on connect to discover what this PLM instance supports and what the
current tenant is entitled to, so it can degrade/upgrade gracefully.

Mounted under the existing ``/integrations`` namespace (a separate router from the
async downstream-health probes, to keep dependencies unmixed). UNGATED advisory surface
(like the P1-D feature status / P2-D capability entry): it returns only the capability /
entitlement manifest, no PLM/ECO data, no write. ``entitled`` is the single
``is_entitled`` judgment; the manifest is ADVISORY ONLY -- the real gate stays enforced
at every feature endpoint.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.meta_engine.services.integration_capabilities_service import build_manifest

integration_capabilities_router = APIRouter(prefix="/integrations", tags=["Integration"])


@integration_capabilities_router.get("/capabilities")
def integration_capabilities(
    response: Response, db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Advisory integration capability manifest for the current tenant.

    The response carries tenant-scoped ``entitled`` flags, so it MUST NOT be cached or
    shared across tenants. The body's ``cache_scope`` is metadata only -- HTTP caches do
    not read it -- so enforcement is at the HTTP layer here: ``Cache-Control: no-store``
    (do not store the response anywhere) plus ``Vary`` on the tenant-identifying headers
    (defense in depth for any cache that would ignore no-store and key on headers).
    """
    settings = get_settings()
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = (
        f"Authorization, {settings.TENANT_HEADER}, {settings.ORG_HEADER}"
    )
    return build_manifest(db)
