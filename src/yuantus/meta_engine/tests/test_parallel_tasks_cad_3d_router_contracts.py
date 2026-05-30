from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.web.parallel_tasks_cad_3d_router import (
    parallel_tasks_cad_3d_router,
)
from yuantus.meta_engine.web.parallel_tasks_router import parallel_tasks_router


_CAD_3D_ROUTE_KEYS = {
    ("POST", "/cad-3d/overlays"),
    ("GET", "/cad-3d/overlays/cache/stats"),
    ("GET", "/cad-3d/overlays/{document_item_id}"),
    ("POST", "/cad-3d/overlays/{document_item_id}/components/resolve-batch"),
    ("GET", "/cad-3d/overlays/{document_item_id}/components/{component_ref}"),
    # G3 3D visual explode (validated explode-config persistence).
    ("PUT", "/cad-3d/explode/{document_item_id}"),
    ("GET", "/cad-3d/explode/{document_item_id}"),
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


def test_cad_3d_routes_owned_by_split_router():
    assert _CAD_3D_ROUTE_KEYS <= _route_keys(parallel_tasks_cad_3d_router)


def test_parallel_tasks_router_no_longer_owns_cad_3d_routes():
    assert not {
        path
        for _method, path in _route_keys(parallel_tasks_router)
        if path.startswith("/cad-3d")
    }


def test_cache_stats_route_precedes_dynamic_overlay_lookup_route():
    paths = [
        route.path
        for route in parallel_tasks_cad_3d_router.routes
        if isinstance(route, APIRoute)
    ]
    assert paths.index("/cad-3d/overlays/cache/stats") < paths.index(
        "/cad-3d/overlays/{document_item_id}"
    )


def test_create_app_registers_cad_3d_routes_once():
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/api/v1/cad-3d"):
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            counts[(method, route.path)] += 1

    expected = {(method, f"/api/v1{path}") for method, path in _CAD_3D_ROUTE_KEYS}
    assert set(counts) == expected
    assert all(count == 1 for count in counts.values())
