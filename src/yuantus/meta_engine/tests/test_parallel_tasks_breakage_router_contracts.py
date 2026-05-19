from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.web.parallel_tasks_breakage_router import (
    parallel_tasks_breakage_router,
)
from yuantus.meta_engine.web.parallel_tasks_router import parallel_tasks_router


_BREAKAGE_ROUTE_KEYS = {
    ("GET", "/breakages/metrics"),
    ("GET", "/breakages/metrics/groups"),
    ("GET", "/breakages/metrics/export"),
    ("GET", "/breakages/metrics/groups/export"),
    ("POST", "/breakages"),
    ("GET", "/breakages"),
    ("GET", "/breakages/export"),
    ("GET", "/breakages/cockpit"),
    ("GET", "/breakages/cockpit/export"),
    ("POST", "/breakages/export/jobs"),
    ("POST", "/breakages/export/jobs/cleanup"),
    ("GET", "/breakages/export/jobs/{job_id}"),
    ("GET", "/breakages/export/jobs/{job_id}/download"),
    ("POST", "/breakages/{incident_id}/status"),
    ("POST", "/breakages/{incident_id}/design-loopback/eco"),
    ("POST", "/breakages/{incident_id}/helpdesk-sync"),
    ("GET", "/breakages/{incident_id}/helpdesk-sync/status"),
    ("POST", "/breakages/{incident_id}/helpdesk-sync/execute"),
    ("POST", "/breakages/{incident_id}/helpdesk-sync/result"),
    ("POST", "/breakages/{incident_id}/helpdesk-sync/ticket-update"),
}


def _route_keys(router, *, prefix: str = "") -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            keys.add((method, f"{prefix}{route.path}"))
    return keys


def test_breakage_routes_owned_by_split_router():
    assert _BREAKAGE_ROUTE_KEYS <= _route_keys(parallel_tasks_breakage_router)


def test_parallel_tasks_router_no_longer_owns_breakage_routes():
    assert not {
        path
        for _method, path in _route_keys(parallel_tasks_router)
        if path.startswith("/breakages")
    }


def test_parallel_tasks_router_no_longer_owns_parallel_ops_breakage_helpdesk_routes():
    assert not {
        path
        for _method, path in _route_keys(parallel_tasks_router)
        if path.startswith("/parallel-ops/breakage-helpdesk")
    }


def test_create_app_registers_breakage_routes_once():
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/v1/breakages"):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            counts[(method, route.path)] += 1

    expected = {(method, f"/api/v1{path}") for method, path in _BREAKAGE_ROUTE_KEYS}
    assert set(counts) == expected
    assert all(count == 1 for count in counts.values())
