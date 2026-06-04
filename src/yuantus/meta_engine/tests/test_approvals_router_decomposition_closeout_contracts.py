from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.web import approvals_router as approvals_shim_module


_EXPECTED_APPROVAL_ROUTE_OWNERS = {
    ("POST", "/api/v1/approvals/categories"): "yuantus.meta_engine.web.approval_category_router",
    ("GET", "/api/v1/approvals/categories"): "yuantus.meta_engine.web.approval_category_router",
    ("POST", "/api/v1/approvals/requests"): "yuantus.meta_engine.web.approval_request_router",
    ("POST", "/api/v1/approvals/requests/{request_id}/transition"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests/export"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests/{request_id}/lifecycle"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests/{request_id}/consumer-summary"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests/{request_id}/history"): "yuantus.meta_engine.web.approval_request_router",
    ("POST", "/api/v1/approvals/requests/pack-summary"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/requests/{request_id}"): "yuantus.meta_engine.web.approval_request_router",
    ("GET", "/api/v1/approvals/summary"): "yuantus.meta_engine.web.approval_ops_router",
    ("GET", "/api/v1/approvals/summary/export"): "yuantus.meta_engine.web.approval_ops_router",
    ("GET", "/api/v1/approvals/ops-report"): "yuantus.meta_engine.web.approval_ops_router",
    ("GET", "/api/v1/approvals/ops-report/export"): "yuantus.meta_engine.web.approval_ops_router",
    ("GET", "/api/v1/approvals/queue-health"): "yuantus.meta_engine.web.approval_ops_router",
    ("GET", "/api/v1/approvals/queue-health/export"): "yuantus.meta_engine.web.approval_ops_router",
    # PLM-COLLAB-P2-B: approval-automation product entry. A separate feature router
    # (NOT part of the legacy approvals_router decomposition), but its paths live
    # under /api/v1/approvals/automation so they are owned here for completeness.
    ("GET", "/api/v1/approvals/automation/templates"): "yuantus.meta_engine.web.approval_automation_router",
    ("POST", "/api/v1/approvals/automation/provision"): "yuantus.meta_engine.web.approval_automation_router",
}

_ROUTER_REGISTRATION_ORDER = [
    "approval_category_router",
    "approval_request_router",
    "approval_ops_router",
]


def _is_approval_route(path: str) -> bool:
    return path == "/api/v1/approvals" or path.startswith("/api/v1/approvals/")


def _app_approval_routes() -> dict[tuple[str, str], str]:
    routes: dict[tuple[str, str], str] = {}
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or not _is_approval_route(route.path):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            routes[(method, route.path)] = route.endpoint.__module__
    return routes


def test_all_approval_routes_have_explicit_split_router_owner() -> None:
    assert _app_approval_routes() == _EXPECTED_APPROVAL_ROUTE_OWNERS


def test_all_approval_routes_are_registered_exactly_once() -> None:
    counts: Counter[tuple[str, str]] = Counter()
    for route in create_app().routes:
        if not isinstance(route, APIRoute) or not _is_approval_route(route.path):
            continue
        for method in route.methods or set():
            if method != "HEAD":
                counts[(method, route.path)] += 1

    assert set(counts) == set(_EXPECTED_APPROVAL_ROUTE_OWNERS)
    assert all(count == 1 for count in counts.values())


def test_legacy_approvals_router_module_is_registered_shell_only() -> None:
    text = Path(approvals_shim_module.__file__).read_text(encoding="utf-8")
    assert re.findall(r"@approvals_router\.(get|post|delete|put|patch)\(", text) == []
    assert "approvals_router = APIRouter" in text


def test_app_registers_approval_routers_in_decomposition_order_before_legacy_shell() -> None:
    app_py = Path(__file__).resolve().parents[4] / "src" / "yuantus" / "api" / "app.py"
    text = app_py.read_text(encoding="utf-8")
    positions = [
        text.find(f"app.include_router({router_name}") for router_name in _ROUTER_REGISTRATION_ORDER
    ]
    assert all(position != -1 for position in positions)
    assert positions == sorted(positions)


def test_app_does_not_register_legacy_approvals_router_shell() -> None:
    app_py = Path(__file__).resolve().parents[4] / "src" / "yuantus" / "api" / "app.py"
    text = app_py.read_text(encoding="utf-8")

    assert "from yuantus.meta_engine.web.approvals_router import approvals_router" not in text
    assert "app.include_router(approvals_router" not in text


def test_legacy_approvals_router_owns_no_runtime_paths() -> None:
    leaked = sorted(
        (method, path, owner)
        for (method, path), owner in _app_approval_routes().items()
        if owner == "yuantus.meta_engine.web.approvals_router"
    )
    assert leaked == []
