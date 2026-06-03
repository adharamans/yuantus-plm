"""Minimal MetaSheet collaboration bridge seam (PLM-COLLAB-P0-A).

This router exists ONLY when ``Settings.ENABLE_METASHEET`` is true (the mount is
gated in ``yuantus.api.app.create_app``). It is inert by design: NO MetaSheet
I/O, NO entitlement bypass, NO event subscription, NO database access.

It exists so the collaboration layer has a single mountable seam; real
capability is gated by per-tenant entitlement in a later phase. Keeping the flag
judgment at the registration boundary (and the seam inert) is what lets the base
PLM SKU keep its exact route surface when ``ENABLE_METASHEET`` is off.

See ``docs/DEVELOPMENT_PLM_COLLABORATION_PHASE0_SCOPE_MAPPING_TASKBOOK_20260602.md``
(§5 first slice) and ``docs/development/plm-collaboration-automation-development-plan-20260602.md``.
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/metasheet-bridge", tags=["MetaSheet Bridge"])


@router.get("/health")
def metasheet_bridge_health() -> dict:
    """Report that the bridge seam is mounted but not yet entitlement-activated.

    Inert: returns a static status only. It does not call MetaSheet, read the
    database, evaluate entitlement, or mutate any state.
    """
    return {
        "bridge": "metasheet",
        "enabled": True,
        "active": False,
        "entitlement_required": True,
    }
