"""Integration test for the /cad/files/{id}/visual-diff route through the REAL
app stack (create_app + TestClient + URL routing + auth DI + response
serialization) — the layer the unit tests in test_cad_render_diff_service.py do
NOT cover (those call the handler function directly). The route handler's
internals (DB resolution + render) stay covered by those unit tests; here we
stub render_containers_visual_diff and assert the route is wired, auth-gated,
and serialises correctly end-to-end over HTTP.

Mirrors the repo's router-test convention (test_version_file_checkout_router.py /
test_document_sync_router.py): mock DB via dependency_overrides, AUTH_MODE=optional,
get_current_user overridden. DB-backed; runs in the contracts CI step.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.integrations.render_service import RenderDiffResult
import yuantus.meta_engine.web.cad_diff_router as cdr


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # Override route auth dependency; middleware auth is out of scope (repo convention).
    monkeypatch.setattr(get_settings(), "AUTH_MODE", "optional")


def _fc(*, ext="dxf", cid="fc"):
    c = MagicMock()
    c.id = cid
    c.filename = f"drawing.{ext}"
    c.system_path = f"/vault/drawing.{ext}"
    c.get_extension.return_value = ext
    return c


def _build(monkeypatch, *, render_url="http://render:8077", db_get=None):
    monkeypatch.setattr(get_settings(), "RENDER_SERVICE_BASE_URL", render_url)
    db = MagicMock()
    if db_get is not None:
        db.get.side_effect = db_get

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1")
    return TestClient(app), db


def _two_dxf(a, b):
    return lambda _model, fid: {"a": a, "b": b}.get(fid)


URL = "/api/v1/cad/files/a/visual-diff"


def test_visual_diff_route_registered_in_create_app():
    app = create_app()
    paths = {
        (r.path, tuple(sorted(r.methods or [])))
        for r in app.routes
        if hasattr(r, "methods")
    }
    assert ("/api/v1/cad/files/{file_id}/visual-diff", ("GET",)) in paths


def test_happy_path_returns_png_through_app(monkeypatch):
    a, b = _fc(cid="a"), _fc(cid="b")
    monkeypatch.setattr(
        cdr, "render_containers_visual_diff",
        lambda *args, **kw: RenderDiffResult(
            content=b"\x89PNGoverlay",
            content_type="image/png",
            summary={"X-Diff-Comparable": "true", "X-Diff-Changed-Fraction": "0.13"},
        ),
    )
    client, _ = _build(monkeypatch, db_get=_two_dxf(a, b))
    r = client.get(URL, params={"other_file_id": "b"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["X-Diff-Comparable"] == "true"
    assert r.headers["X-Diff-Changed-Fraction"] == "0.13"
    assert r.content == b"\x89PNGoverlay"


def test_json_passthrough_when_not_comparable(monkeypatch):
    # render_diff_sync returns JSON (not-comparable) → route relays content_type verbatim.
    a, b = _fc(cid="a"), _fc(cid="b")
    monkeypatch.setattr(
        cdr, "render_containers_visual_diff",
        lambda *args, **kw: RenderDiffResult(
            content=b'{"status":"ok","comparable":false,"skip_reason":"view-space-mismatch"}',
            content_type="application/json",
            summary={"X-Diff-Comparable": "false", "X-Diff-Skip-Reason": "view-space-mismatch"},
        ),
    )
    client, _ = _build(monkeypatch, db_get=_two_dxf(a, b))
    r = client.get(URL, params={"other_file_id": "b"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["X-Diff-Comparable"] == "false"


def test_503_when_render_service_unconfigured(monkeypatch):
    a, b = _fc(cid="a"), _fc(cid="b")
    client, _ = _build(monkeypatch, render_url="", db_get=_two_dxf(a, b))
    r = client.get(URL, params={"other_file_id": "b"})
    assert r.status_code == 503


def test_422_when_other_file_id_missing(monkeypatch):
    a = _fc(cid="a")
    client, _ = _build(monkeypatch, db_get=_two_dxf(a, _fc(cid="b")))
    r = client.get(URL)  # no other_file_id
    assert r.status_code == 422


def test_404_when_file_missing(monkeypatch):
    client, _ = _build(monkeypatch, db_get=lambda _m, _fid: None)
    r = client.get(URL, params={"other_file_id": "b"})
    assert r.status_code == 404


def test_422_when_not_dxf(monkeypatch):
    a, b = _fc(ext="dwg", cid="a"), _fc(ext="dwg", cid="b")
    client, _ = _build(monkeypatch, db_get=_two_dxf(a, b))
    r = client.get(URL, params={"other_file_id": "b"})
    assert r.status_code == 422
