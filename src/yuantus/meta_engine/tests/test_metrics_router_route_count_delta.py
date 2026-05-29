from __future__ import annotations

from yuantus.api.app import create_app


# 684 = 683 (through R2 outbox routes) + 1 PLM->ERP G2 R4 /publication/export route.
# NOTE: this pin had drifted STALE at 676 (never bumped through the 677/678
# route additions) and is not in the CI contracts list / no-DB allowlist, so the
# drift went unobserved until the R2 routes slice reconciled it.
EXPECTED_TOTAL_ROUTES = 684


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
