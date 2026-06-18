from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.web.parallel_tasks_consumption_router import (
    parallel_tasks_consumption_router,
)
from yuantus.meta_engine.web.parallel_tasks_router import parallel_tasks_router


_CONSUMPTION_ROUTE_KEYS = {
    ("POST", "/consumption/plans"),
    ("GET", "/consumption/plans"),
    ("POST", "/consumption/templates/{template_key}/versions"),
    ("GET", "/consumption/templates/{template_key}/versions"),
    ("POST", "/consumption/templates/versions/{plan_id}/state"),
    ("POST", "/consumption/templates/{template_key}/impact-preview"),
    ("POST", "/consumption/plans/{plan_id}/actuals"),
    ("POST", "/consumption/plans/{plan_id}/mes-actuals"),
    ("GET", "/consumption/plans/{plan_id}/variance"),
    ("GET", "/consumption/mes-inbox"),
    ("GET", "/consumption/mes-inbox/{inbox_id}"),
    ("POST", "/consumption/mes-inbox/{inbox_id}/replay"),
    ("GET", "/consumption/dashboard"),
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


def test_consumption_routes_owned_by_split_router():
    assert _CONSUMPTION_ROUTE_KEYS <= _route_keys(parallel_tasks_consumption_router)


def test_parallel_tasks_router_no_longer_owns_consumption_routes():
    assert not {
        path
        for _method, path in _route_keys(parallel_tasks_router)
        if path.startswith("/consumption")
    }


def test_create_app_registers_consumption_routes_once():
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/v1/consumption"):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            counts[(method, route.path)] += 1

    expected = {(method, f"/api/v1{path}") for method, path in _CONSUMPTION_ROUTE_KEYS}
    assert set(counts) == expected
    assert all(count == 1 for count in counts.values())
