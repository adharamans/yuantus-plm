from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

from yuantus.api.middleware.rate_limit import InboundRateLimitMiddleware, _limiter
from yuantus.config import get_settings


class _VerifiedTenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        tenant = request.headers.get("x-verified-tenant")
        if tenant:
            request.state.tenant_id = tenant
        return await call_next(request)


def _reset_rate_limit_state() -> None:
    get_settings.cache_clear()
    with _limiter._lock:
        _limiter._buckets.clear()


def _set_enabled(monkeypatch, *, enabled: bool, rate: int = 1, burst: int = 1) -> None:
    monkeypatch.setenv("YUANTUS_INBOUND_RATE_LIMIT_ENABLED", "true" if enabled else "false")
    monkeypatch.setenv("YUANTUS_INBOUND_RATE_LIMIT_PER_MINUTE", str(rate))
    monkeypatch.setenv("YUANTUS_INBOUND_RATE_LIMIT_BURST", str(burst))
    monkeypatch.setenv(
        "YUANTUS_INBOUND_RATE_LIMIT_EXEMPT_PATHS",
        "/favicon.ico,/docs,/redoc,/openapi.json,/api/v1/health,/api/v1/metrics",
    )
    get_settings.cache_clear()


def _client(*, verified_tenant: bool = False) -> TestClient:
    app = FastAPI()

    @app.get("/limited")
    async def limited() -> dict[str, str]:
        return {"ok": "limited"}

    @app.get("/api/v1/health")
    async def health() -> dict[str, str]:
        return {"ok": "health"}

    app.add_middleware(InboundRateLimitMiddleware)
    if verified_tenant:
        app.add_middleware(_VerifiedTenantMiddleware)
    return TestClient(app)


def test_disabled_by_default_does_not_limit(monkeypatch) -> None:
    _reset_rate_limit_state()
    monkeypatch.delenv("YUANTUS_INBOUND_RATE_LIMIT_ENABLED", raising=False)
    get_settings.cache_clear()
    client = _client()

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200


def test_public_requests_ignore_untrusted_tenant_header_for_bucket_key(monkeypatch) -> None:
    _reset_rate_limit_state()
    _set_enabled(monkeypatch, enabled=True, rate=1, burst=1)
    client = _client()

    first = client.get("/limited", headers={"x-tenant-id": "tenant-a"})
    second = client.get("/limited", headers={"x-tenant-id": "tenant-b"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "Rate limit exceeded"}
    assert second.headers["Retry-After"]


def test_verified_tenant_state_gets_independent_buckets(monkeypatch) -> None:
    _reset_rate_limit_state()
    _set_enabled(monkeypatch, enabled=True, rate=1, burst=1)
    client = _client(verified_tenant=True)

    assert client.get("/limited", headers={"x-verified-tenant": "tenant-a"}).status_code == 200
    assert client.get("/limited", headers={"x-verified-tenant": "tenant-a"}).status_code == 429
    assert client.get("/limited", headers={"x-verified-tenant": "tenant-b"}).status_code == 200


def test_exempt_paths_are_never_limited(monkeypatch) -> None:
    _reset_rate_limit_state()
    _set_enabled(monkeypatch, enabled=True, rate=1, burst=1)
    client = _client()

    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/health").status_code == 200


def test_zero_budget_disables_enforcement(monkeypatch) -> None:
    _reset_rate_limit_state()
    _set_enabled(monkeypatch, enabled=True, rate=0, burst=1)
    client = _client()

    assert client.get("/limited").status_code == 200
    assert client.get("/limited").status_code == 200
