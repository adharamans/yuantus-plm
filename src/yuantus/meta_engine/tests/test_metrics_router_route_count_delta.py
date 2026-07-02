from __future__ import annotations

from yuantus.api.app import create_app


# 691 = 690 (through G3 3D-explode) + 1 OdooPLM G3 BOM auto-layout route (POST on
# /api/v1/cad-3d/explode/{document_item_id}/auto-layout).
# 693 = 691 + 2 PLM-COLLAB-P1-D feature-affordance routes (GET /api/v1/features/{key};
#   POST /api/v1/features/{key}/mock-activate) -- both unconditional.
# 695 = 693 + 2 PLM-COLLAB-P2-B approval-automation routes (GET
#   /api/v1/approvals/automation/templates; POST /api/v1/approvals/automation/provision)
#   -- both unconditional.
# 697 = 695 + 2 PLM-COLLAB-P2-C ECO approval-automation routes (GET
#   /api/v1/approvals/automation/eco/{eco_id}/context; POST .../actions)
#   -- both unconditional.
# 698 = 697 + 1 PLM-COLLAB-P2-D ECO-scenario capability/upgrade entry
#   (GET /api/v1/approvals/automation/eco/capabilities) -- unconditional.
# 699 = 698 + 1 PLM-COLLAB-P2.5 integration capability manifest
#   (GET /api/v1/integrations/capabilities) -- unconditional.
# 701 = 699 + 2 WP1.3 CAD 2D/3D staleness routes (GET
#   /api/v1/cad/items/{item_id}/staleness; POST .../staleness/recompute)
#   -- both unconditional.
# 702 = 701 + 1 PLM-COLLAB-P3-A BOM multi-table governed projection
#   (GET /api/v1/bom/multitable/{part_id}/context) -- unconditional route
#   (the entitlement gate is INSIDE the handler, not a separate route).
# 704 = 702 + 2 WP1.2 PDM traversal routes (GET /api/v1/pdm/items/{id}/relationships;
#   GET /api/v1/pdm/items/{id}/relationship-tree) -- both unconditional.
# 705 = 704 + 1 WP1.2 stale-drawings route (GET /api/v1/cad/items/{root}/stale-drawings)
#   -- unconditional.
# 706 = 705 + 1 PLM-COLLAB-P3-D1 embed-token mint route
#   (POST /api/v1/bom/multitable/{part_id}/embed-token) -- unconditional route
#   (the entitlement/permission gate is INSIDE the handler, not a separate route).
# 707 = 706 + 1 CAD-PDM Superseded read-surface route
#   (GET /api/v1/versions/items/{item_id}/versions) -- unconditional.
# 708 = 707 + 1 CAD-PDM B2b assembly promotion route
#   (POST /api/v1/pdm/items/{root_id}/promote-assembly) -- unconditional.
# 709 = 708 + 1 L1 visual-diff route
#   (GET /api/v1/cad/files/{file_id}/visual-diff) -- unconditional (gated by
#   RENDER_SERVICE_BASE_URL inside the handler, not as a separate route).
# 712 = 709 + 3 ECM publication-outbox ops routes (ECM-P1C) -- unconditional
#   (GET /api/v1/plm-ecm/publication-outbox[?state]; GET .../{outbox_id};
#   POST .../{outbox_id}/replay) -- admin + ecm_publish gated INSIDE the handler.
# NOTE: this pin had drifted STALE at 676 (never bumped through the 677/678
# route additions) and is not in the CI contracts list / no-DB allowlist, so the
# drift went unobserved until the R2 routes slice reconciled it.
# 713 = 712 + 1 MES consumption ingestion route (Consumption R2:
#   POST /api/v1/consumption/plans/{plan_id}/mes-actuals).
# 716 = 713 + 3 MES inbox ops routes (Consumption R2.5b: list/get/replay).
# 719 = 716 + 3 CAD-PDM C3 date-obsolete impact ops routes (list/get/acknowledge).
# 720 = 719 + 1 lifecycle transition-history read route (Slice 2:
#   GET /api/v1/items/{item_id}/transition-history) -- per-item ACL since #831 (was authenticated read).
# 721 = 720 + 1 lifecycle transition-history forensic admin route
#   (GET /api/v1/transition-history/forensic/{item_id}) -- superuser-gated, no item-existence gate.
# 722 = 721 + 1 L4-1 admin license-status read route
#   (GET /api/v1/admin/license-status) -- superuser-gated CLI->HTTP status surface.
# 723 = 722 + 1 L3-1 effectivity-date PATCH route
#   (PATCH /api/v1/effectivities/{id}) -- Date-window edit with elapsed-window guard.
# 724 = 723 + 1 L4 Fork B seat-cap change-history audit read
#   (GET /api/v1/admin/license-cap-history) -- superuser-gated, no existence leak.
# 725 = 724 + 1 L4 Fork C license revoke
#   (POST /api/v1/admin/licenses/{license_key}/revoke) -- append-only, no cap clear.
# 728 = 727 + 1 Phase-7 Day-2 governed BOM multi-table write-back
#   (PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}) -- distinct write SKU,
#   lifecycle-guarded + single-use replay + atomic write-back audit.
# 729 = 728 + 1 CAD-PDM C3 date-obsolete impact export route
#   (GET /api/v1/cadpdm/date-obsolete-impacts/export) -- admin-gated ops export.
# 730 = 729 + 1 lifecycle forensic summary route
#   (GET /api/v1/transition-history/forensic/summary) -- superuser-gated ops aggregate.
# 732 = 730 + 2 lifecycle forensic drill-down/export routes
#   (GET /api/v1/transition-history/forensic;
#    GET /api/v1/transition-history/forensic/export) -- superuser-gated ops surfaces.
# 733 = 732 + 1 Phase-7 BOM write-back audit readout
#   (GET /api/v1/bom/multitable/writeback-audit) -- superuser-gated ops readout.
# 735 = 733 + 2 CAD-PDM date-obsolete DP1 revert routes
#   (POST /api/v1/cadpdm/date-obsolete-impacts/revert-batch;
#    POST /api/v1/cadpdm/date-obsolete-impacts/{impact_id}/revert) -- admin-gated table-local correction.
EXPECTED_TOTAL_ROUTES = 735


def test_metrics_router_keeps_post_p4_route_count_at_expected_count() -> None:
    """Route-count guard after P4.1 added search indexer status.

    The metrics router still owns exactly one route; the app-level total is now
    683. NOTE: this guard had drifted stale at 676 (never bumped through the
    677/678 route additions) and is not in the CI contracts list / no-DB
    allowlist, so the drift went unobserved until the PLM->ERP G2 R2
    publication-outbox routes reconciled it (678 baseline + 5 outbox routes).
    If a future change adds another route in this scope, this test fails and
    forces a conversation about scope creep.
    """
    app = create_app()
    assert len(app.routes) == EXPECTED_TOTAL_ROUTES, (
        f"expected {EXPECTED_TOTAL_ROUTES} total routes after P4.2.3, "
        f"got {len(app.routes)}"
    )


def test_metrics_endpoint_is_the_single_route_added_by_metrics_router() -> None:
    app = create_app()
    metrics_owned = [
        r
        for r in app.routes
        if getattr(r, "endpoint", None) is not None
        and getattr(r.endpoint, "__module__", "") == "yuantus.api.routers.metrics"
    ]
    assert len(metrics_owned) == 1, (
        f"metrics_router must own exactly 1 route in P2.2 scope; "
        f"found {len(metrics_owned)}"
    )
    assert metrics_owned[0].path == "/api/v1/metrics"
