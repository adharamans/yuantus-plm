from __future__ import annotations

from collections import Counter

from fastapi.routing import APIRoute

from yuantus.api.app import create_app
from yuantus.meta_engine.web.cad_diff_router import cad_diff_router
from yuantus.meta_engine.web.cad_router import router as cad_router


_CAD_DIFF_ROUTE_KEYS = {
    ("GET", "/cad/files/{file_id}/diff"),
    ("GET", "/cad/files/{file_id}/visual-diff"),
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


def test_cad_diff_route_owned_by_split_router() -> None:
    assert _CAD_DIFF_ROUTE_KEYS <= _route_keys(cad_diff_router)


def test_cad_router_no_longer_owns_diff_route() -> None:
    moved_paths = {route_path for _method, route_path in _CAD_DIFF_ROUTE_KEYS}
    assert not {
        path for _method, path in _route_keys(cad_router) if path in moved_paths
    }


def test_create_app_registers_cad_diff_route_once() -> None:
    app = create_app()
    counts: Counter[tuple[str, str]] = Counter()
    moved_paths = {
        f"/api/v1{route_path}" for _method, route_path in _CAD_DIFF_ROUTE_KEYS
    }

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path not in moved_paths:
            continue
        for method in route.methods or set():
            if method == "HEAD":
                continue
            counts[(method, route.path)] += 1

    expected = {
        (method, f"/api/v1{path}") for method, path in _CAD_DIFF_ROUTE_KEYS
    }
    assert set(counts) == expected
    assert all(count == 1 for count in counts.values())
