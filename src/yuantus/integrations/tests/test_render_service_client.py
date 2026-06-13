"""Unit tests for the render service client (S1) — httpx mocked, no live
render service and no local renderer needed."""

from __future__ import annotations

import httpx
from yuantus.config import get_settings
import pytest

from yuantus.integrations import render_service as rs
from yuantus.integrations.render_service import (
    RenderServiceClient,
    is_render_service_breaker_failure,
)


# ── failure classification (breaker policy, pure) ──
def test_failure_predicate_counts_upstream_not_caller():
    assert is_render_service_breaker_failure(httpx.ConnectError("x")) is True
    req = httpx.Request("POST", "http://r/render")
    for code in (500, 503, 408, 429):
        err = httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))
        assert is_render_service_breaker_failure(err) is True, code
    for code in (404, 415, 422, 400):  # caller-side → re-raised, not counted
        err = httpx.HTTPStatusError("e", request=req, response=httpx.Response(code, request=req))
        assert is_render_service_breaker_failure(err) is False, code
    assert is_render_service_breaker_failure(OSError("disk")) is False
    assert is_render_service_breaker_failure(ValueError("?")) is True  # unknown → count


def test_resolve_authorization_normalizes_bearer():
    c = RenderServiceClient(base_url="http://r")
    assert c._resolve_authorization("tok") == "Bearer tok"
    assert c._resolve_authorization("Bearer tok") == "Bearer tok"
    assert c._resolve_authorization("  ") is None
    # empty service token (test env) → no auth header
    assert c._resolve_authorization(None) is None


def test_configured_reflects_base_url():
    assert RenderServiceClient(base_url="http://r").configured is True
    assert RenderServiceClient(base_url="").configured is False


# ── HTTP path via MockTransport (breaker default-off) ──
def _mock_client(monkeypatch, handler):
    real = httpx.Client

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(rs.httpx, "Client", factory)


def test_render_preview_sync_posts_file_and_params(tmp_path, monkeypatch):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nEOF\n")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        seen["has_multipart"] = b"drawing.dxf" in request.content
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nDATA", headers={"content-type": "image/png"})

    _mock_client(monkeypatch, handler)
    c = RenderServiceClient(base_url="http://render:8077")
    out = c.render_preview_sync(file_path=str(dxf), fmt="png", width=800, height=500, bg="white")
    assert out.startswith(b"\x89PNG")
    assert seen["path"] == "/render"
    assert seen["params"] == {"format": "png", "width": "800", "height": "500", "bg": "white"}
    assert seen["has_multipart"] is True


def test_render_preview_sync_raises_on_4xx(tmp_path, monkeypatch):
    dxf = tmp_path / "x.dxf"; dxf.write_bytes(b"junk")

    def handler(request):
        return httpx.Response(415, json={"status": "error", "error_code": "UNSUPPORTED_INPUT"})

    _mock_client(monkeypatch, handler)
    c = RenderServiceClient(base_url="http://render:8077")
    with pytest.raises(httpx.HTTPStatusError):
        c.render_preview_sync(file_path=str(dxf))


def test_bad_format_rejected(tmp_path):
    dxf = tmp_path / "x.dxf"; dxf.write_bytes(b"0")
    c = RenderServiceClient(base_url="http://render:8077")
    with pytest.raises(ValueError):
        c.render_preview_sync(file_path=str(dxf), fmt="pdf")


def test_timeout_wired_from_settings(monkeypatch):
    from yuantus.config import get_settings
    monkeypatch.setattr(get_settings(), "RENDER_SERVICE_TIMEOUT_SECONDS", 77)
    assert RenderServiceClient(base_url="http://r").timeout_s == 77.0
    assert RenderServiceClient(base_url="http://r", timeout_s=5).timeout_s == 5.0


def test_bad_format_does_not_trip_breaker(tmp_path, monkeypatch):
    # fmt is validated BEFORE the breaker, so a caller-side ValueError must not
    # be counted as an upstream failure.
    dxf = tmp_path / "x.dxf"; dxf.write_bytes(b"0")
    monkeypatch.setattr(get_settings(), "CIRCUIT_BREAKER_RENDER_SERVICE_ENABLED", True)
    c = RenderServiceClient(base_url="http://r")
    before = c._breaker.snapshot() if hasattr(c._breaker, "snapshot") else None
    for _ in range(10):
        with pytest.raises(ValueError):
            c.render_preview_sync(file_path=str(dxf), fmt="pdf")
    # breaker never saw a failure → still closed / usable
    state = getattr(c._breaker, "_state", None)
    assert state is None or getattr(state, "state", "closed") == "closed"
