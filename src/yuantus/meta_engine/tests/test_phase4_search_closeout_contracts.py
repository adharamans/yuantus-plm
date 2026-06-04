from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.services import search_indexer
from yuantus.meta_engine.web.search_router import SearchIndexerStatusResponse
from yuantus.observability.metrics import render_search_indexer_metrics


EXPECTED_SEARCH_INDEXER_STATUS_FIELDS = {
    "registered",
    "registered_at",
    "status_started_at",
    "uptime_seconds",
    "health",
    "health_reasons",
    "item_index_ready",
    "eco_index_ready",
    "handlers",
    "indexed_event_types",
    "unindexed_event_types",
    "event_coverage",
    "subscription_counts",
    "missing_handlers",
    "duplicate_handlers",
    "event_counts",
    "success_counts",
    "skipped_counts",
    "error_counts",
    "last_event_type",
    "last_event_at",
    "last_event_age_seconds",
    "last_outcome",
    "last_success_event_type",
    "last_success_at",
    "last_success_age_seconds",
    "last_skipped_event_type",
    "last_skipped_at",
    "last_skipped_age_seconds",
    "last_skipped_reason",
    "last_error_event_type",
    "last_error_at",
    "last_error_age_seconds",
    "last_error",
}
EXPECTED_INDEXED_EVENTS = [
    "item.created",
    "item.updated",
    "item.state_changed",
    "item.deleted",
    "eco.created",
    "eco.updated",
    "eco.deleted",
]
EXPECTED_UNINDEXED_EVENTS = [
    "file.uploaded",
    "file.checked_in",
    "cad.attributes_synced",
]
EXPECTED_PHASE4_ROUTES = {
    ("GET", "/api/v1/search/indexer/status"): (
        "yuantus.meta_engine.web.search_router",
        "search_indexer_status",
    ),
    ("GET", "/api/v1/search/reports/summary"): (
        "yuantus.meta_engine.web.search_router",
        "search_reports_summary",
    ),
    ("GET", "/api/v1/search/reports/eco-stage-aging"): (
        "yuantus.meta_engine.web.search_router",
        "search_reports_eco_stage_aging",
    ),
    ("GET", "/api/v1/search/reports/eco-state-trend"): (
        "yuantus.meta_engine.web.search_router",
        "search_reports_eco_state_trend",
    ),
    ("GET", "/api/v1/metrics"): (
        "yuantus.api.routers.metrics",
        "metrics_endpoint",
    ),
}
EXPECTED_SEARCH_INDEXER_METRICS = {
    "yuantus_search_indexer_registered",
    "yuantus_search_indexer_uptime_seconds",
    "yuantus_search_indexer_health",
    "yuantus_search_indexer_health_reason",
    "yuantus_search_indexer_index_ready",
    "yuantus_search_indexer_subscriptions",
    "yuantus_search_indexer_events_total",
    "yuantus_search_indexer_event_coverage",
    "yuantus_search_indexer_last_event_age_seconds",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _model_fields(model: type[Any]) -> set[str]:
    fields = getattr(model, "model_fields", None)
    if fields is None:
        fields = getattr(model, "__fields__", {})
    return set(fields)


def _route_entries() -> dict[tuple[str, str], list[APIRoute]]:
    app = create_app()
    entries: dict[tuple[str, str], list[APIRoute]] = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            entries.setdefault((method, route.path), []).append(route)
    return entries


def _metric_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        names.add(line.split("{", 1)[0].split(" ", 1)[0])
    return names


def test_phase4_public_route_surface_is_pinned() -> None:
    entries = _route_entries()

    for key, (expected_module, expected_name) in EXPECTED_PHASE4_ROUTES.items():
        routes = entries.get(key, [])
        assert len(routes) == 1, f"{key} must be registered exactly once"
        endpoint = routes[0].endpoint
        assert endpoint.__module__ == expected_module
        assert endpoint.__name__ == expected_name


def test_phase4_route_count_is_pinned_after_search_reports_closeout() -> None:
    app = create_app()

    # Count history (each entry = one route added since the pin began):
    # - 676: baseline at phase-4 search closeout pin.
    # - 677: + `POST /breakages/{incident_id}/design-loopback/eco`
    #   (PR #602, breakage state-machine integration §3.1 R1 — the
    #   HTTP seam over `BreakageIncidentService.create_breakage_design_loopback_eco`
    #   shipped by #596).
    # - 678: + `GET /api/v1/plm-erp/items/{item_id}/publication-readiness`
    #   (PLM→ERP G2 R1-B publication-readiness API).
    # - 683: + 5 PLM→ERP G2 R2 publication-outbox manual routes
    #   (enqueue / dry-run / process / replay / status).
    # - 684: + `GET /api/v1/plm-erp/items/{item_id}/publication/export`
    #   (PLM→ERP G2 R4 read-only export).
    # - 688: + 4 OdooPLM G5 spare-parts routes (list / add / remove + explode)
    #   on `/api/v1/items/{item_id}/spares`.
    # - 690: + 2 OdooPLM G3 3D-explode routes (PUT/GET) on
    #   `/api/v1/cad-3d/explode/{document_item_id}`.
    # - 691: + 1 OdooPLM G3 BOM auto-layout route (POST) on
    #   `/api/v1/cad-3d/explode/{document_item_id}/auto-layout`.
    # - 693: + 2 PLM-COLLAB-P1-D feature-affordance routes (GET /features/{key};
    #   POST /features/{key}/mock-activate), both unconditional.
    # - 695: + 2 PLM-COLLAB-P2-B approval-automation routes (GET
    #   /approvals/automation/templates; POST /approvals/automation/provision),
    #   both unconditional.
    assert len(app.routes) == 695


def test_search_indexer_status_schema_is_phase4_final_contract() -> None:
    assert _model_fields(SearchIndexerStatusResponse) == EXPECTED_SEARCH_INDEXER_STATUS_FIELDS

    status = search_indexer.indexer_status()
    assert set(status) == EXPECTED_SEARCH_INDEXER_STATUS_FIELDS
    assert status["indexed_event_types"] == EXPECTED_INDEXED_EVENTS
    assert status["unindexed_event_types"] == EXPECTED_UNINDEXED_EVENTS
    assert set(status["event_coverage"]) == set(EXPECTED_INDEXED_EVENTS) | set(
        EXPECTED_UNINDEXED_EVENTS
    )
    assert all(status["event_coverage"][event] == "indexed" for event in EXPECTED_INDEXED_EVENTS)
    assert all(
        status["event_coverage"][event] == "not_indexed"
        for event in EXPECTED_UNINDEXED_EVENTS
    )


def test_search_indexer_prometheus_surface_is_phase4_final_contract() -> None:
    text = render_search_indexer_metrics(
        {
            "registered": True,
            "uptime_seconds": 42,
            "health": "degraded",
            "health_reasons": ["missing-handlers"],
            "item_index_ready": True,
            "eco_index_ready": False,
            "subscription_counts": {"item.created": 1},
            "event_counts": {"item.created": 3},
            "success_counts": {"item.created": 2},
            "skipped_counts": {"item.created": 1},
            "error_counts": {"eco.deleted": 1},
            "event_coverage": {
                "item.created": "indexed",
                "file.uploaded": "not_indexed",
            },
            "last_event_age_seconds": 7,
            "last_success_age_seconds": 6,
            "last_skipped_age_seconds": 5,
            "last_error_age_seconds": 4,
        }
    )

    assert _metric_names(text) == EXPECTED_SEARCH_INDEXER_METRICS
    labels = set(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)="', text))
    assert labels <= {"state", "reason", "index", "event_type", "outcome", "coverage", "kind"}
    assert "tenant_id" not in labels
    assert "org_id" not in labels
    assert "user_id" not in labels


def test_phase4_runtime_runbook_documents_final_search_surface() -> None:
    runbook = (_repo_root() / "docs" / "RUNBOOK_RUNTIME.md").read_text()

    required = [
        "### Search indexer metrics",
        "### Search reports",
        "GET /api/v1/search/indexer/status",
        "GET /api/v1/search/reports/summary",
        "GET /api/v1/search/reports/eco-stage-aging",
        "GET /api/v1/search/reports/eco-state-trend",
        "yuantus_search_indexer_registered",
        "yuantus_search_indexer_events_total",
        "yuantus_search_indexer_last_event_age_seconds",
    ]
    for snippet in required:
        assert snippet in runbook


def test_phase4_closeout_md_links_all_phase4_artifacts_and_pause_gate() -> None:
    closeout = (
        _repo_root()
        / "docs"
        / "DEV_AND_VERIFICATION_PHASE4_SEARCH_CLOSEOUT_20260507.md"
    ).read_text()

    required = [
        "P4.1",
        "P4.2",
        "P4.3",
        "docs/DEV_AND_VERIFICATION_PHASE4_SEARCH_INDEXER_EVENT_COVERAGE_STATUS_20260507.md",
        "docs/DEV_AND_VERIFICATION_PHASE4_SEARCH_REPORTS_CLOSEOUT_20260507.md",
        "src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py",
        "Phase 5 requires explicit opt-in",
    ]
    for snippet in required:
        assert snippet in closeout
