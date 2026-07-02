from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from yuantus.config import get_settings


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class _TokenBucketLimiter:
    """Small in-process token bucket keyed by tenant/IP.

    This is intentionally process-local: it protects a single API process from
    bursts and abuse. Multi-replica global quotas need a distributed store and
    are out of scope for P0-8b.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, _Bucket] = {}

    def allow(self, key: str, *, rate_per_minute: int, burst: int) -> tuple[bool, int, int]:
        now = time.monotonic()
        refill_per_second = rate_per_minute / 60.0
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=float(burst), updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            bucket.tokens = min(float(burst), bucket.tokens + elapsed * refill_per_second)
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, int(bucket.tokens), 0

            wait_seconds = 1
            if refill_per_second > 0:
                wait_seconds = max(1, int((1.0 - bucket.tokens) / refill_per_second) + 1)
            return False, 0, wait_seconds


_limiter = _TokenBucketLimiter()


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _is_exempt_path(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("*") and path.startswith(pattern[:-1]):
            return True
        if path == pattern or path.startswith(pattern.rstrip("/") + "/"):
            return True
    return False


def _client_host(request: Request) -> str:
    client = request.client
    if client and client.host:
        return client.host
    return "unknown"


def _rate_limit_key(request: Request) -> str:
    host = _client_host(request)
    verified_tenant = getattr(request.state, "tenant_id", None)
    if verified_tenant:
        return f"tenant:{verified_tenant}"

    # Before/without auth, tenant headers are untrusted and must not create
    # fresh buckets. Public/unauthenticated traffic falls back to client IP.
    return f"ip:{host}"


class InboundRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        if not settings.INBOUND_RATE_LIMIT_ENABLED:
            return await call_next(request)

        if _is_exempt_path(
            request.url.path,
            _split_csv(settings.INBOUND_RATE_LIMIT_EXEMPT_PATHS),
        ):
            return await call_next(request)

        rate_per_minute = int(settings.INBOUND_RATE_LIMIT_PER_MINUTE)
        burst = int(settings.INBOUND_RATE_LIMIT_BURST)
        if rate_per_minute <= 0 or burst <= 0:
            return await call_next(request)

        allowed, remaining, retry_after = _limiter.allow(
            _rate_limit_key(request),
            rate_per_minute=rate_per_minute,
            burst=burst,
        )
        if allowed:
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(burst)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        return JSONResponse(
            {"detail": "Rate limit exceeded"},
            status_code=HTTP_429_TOO_MANY_REQUESTS,
            headers={
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(burst),
                "X-RateLimit-Remaining": "0",
            },
        )
