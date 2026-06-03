"""Shared tenant/org scope resolver for entitlement & license operations.

Single source of the PLM-COLLAB-P1-A F2 hard boundary, so every license read/write
goes through the SAME tenant scoping. Both AppStoreService (purchase/install) and
EntitlementService (feature checks, PLM-COLLAB-P1-B) call resolve_license_scope();
there is no second, unscoped license read path.
"""
from __future__ import annotations

from typing import Optional, Tuple

from yuantus.config import get_settings
from yuantus.context import get_request_context


def resolve_license_scope() -> Tuple[str, Optional[str]]:
    """Resolve (tenant_id, org_id) for a license operation.

    tenant_id comes from the request context. If absent, fall back to "default"
    ONLY when TENANCY_MODE == "single" (dev/test/local); in any multi-tenant mode a
    missing tenant RAISES rather than silently creating/honoring a global license
    (never swallowed into a plain False). org_id is recorded but is NOT an
    entitlement filter (collaboration licensing is tenant/company-level).
    """
    ctx = get_request_context()
    tenant_id = str(ctx.tenant_id).strip() if ctx.tenant_id else ""
    org_id = str(ctx.org_id).strip() if ctx.org_id else None
    if not tenant_id:
        mode = get_settings().TENANCY_MODE
        if mode == "single":
            tenant_id = "default"
        else:
            raise ValueError(
                "tenant context is required for license operations when "
                f"TENANCY_MODE={mode!r} (refusing a silent global license)"
            )
    return tenant_id, org_id
