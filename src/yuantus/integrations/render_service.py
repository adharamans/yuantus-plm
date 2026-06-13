"""Client for the external VemCAD render service (high-fidelity DXF → PNG/SVG).

Mirrors the CadMLClient pattern: settings-driven base_url + service token,
circuit breaker with the same P6 failure classification, tenancy propagation
via build_outbound_headers. Used by cad_preview() to render a high-fidelity
preview, falling back to the existing CAD-ML / connector path on failure.

Contract: VemCAD_RENDER_SERVICE_CONTRACT.md — POST /render takes a multipart
`file` (DXF; .dwg is rejected 415, so callers convert DWG→DXF first) plus
query params format=png|svg, width, height, bg; returns the image bytes.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from yuantus.config import get_settings
from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    get_or_create_breaker,
)
from yuantus.integrations.http import build_outbound_headers

RENDER_SERVICE_BREAKER_NAME = "render_service"

_RENDER_BREAKER_COUNT_STATUS = {408, 429}


def is_render_service_breaker_failure(exc: Exception) -> bool:
    """Failure classification mirroring CAD-ML (P6.2).

    Counted (upstream unhealthy): httpx.RequestError; HTTP 5xx; HTTP 408/429.
    NOT counted (re-raised, breaker not implicated): OSError; other 4xx
    (caller-side, e.g. 415 unsupported / 422 bad params). Unknown types count.
    """
    if isinstance(exc, OSError):
        return False
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if not isinstance(status, int):
            return True
        if 500 <= status < 600 or status in _RENDER_BREAKER_COUNT_STATUS:
            return True
        return False
    return True


def build_render_service_breaker() -> CircuitBreaker:
    settings = get_settings()
    config = CircuitBreakerConfig(
        name=RENDER_SERVICE_BREAKER_NAME,
        enabled=bool(settings.CIRCUIT_BREAKER_RENDER_SERVICE_ENABLED),
        failure_threshold=int(settings.CIRCUIT_BREAKER_RENDER_SERVICE_FAILURE_THRESHOLD),
        window_seconds=float(settings.CIRCUIT_BREAKER_RENDER_SERVICE_WINDOW_SECONDS),
        recovery_seconds=float(settings.CIRCUIT_BREAKER_RENDER_SERVICE_RECOVERY_SECONDS),
        half_open_max_calls=int(settings.CIRCUIT_BREAKER_RENDER_SERVICE_HALF_OPEN_MAX_CALLS),
        backoff_max_seconds=float(settings.CIRCUIT_BREAKER_RENDER_SERVICE_BACKOFF_MAX_SECONDS),
        is_failure=is_render_service_breaker_failure,
    )
    return get_or_create_breaker(config)


_ALLOWED_FORMATS = ("png", "svg")


class RenderServiceClient:
    def __init__(self, *, base_url: Optional[str] = None, timeout_s: Optional[float] = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.RENDER_SERVICE_BASE_URL).rstrip("/")
        self._service_token = settings.RENDER_SERVICE_SERVICE_TOKEN
        self.timeout_s = (
            timeout_s if timeout_s is not None
            else float(settings.RENDER_SERVICE_TIMEOUT_SECONDS)
        )
        self._breaker = build_render_service_breaker()

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _resolve_authorization(self, authorization: Optional[str]) -> Optional[str]:
        token = (authorization or self._service_token or "").strip()
        if not token:
            return None
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def render_preview_sync(
        self,
        *,
        file_path: str,
        filename: Optional[str] = None,
        fmt: str = "png",
        width: int = 2400,
        height: int = 1697,
        bg: str = "white",
        authorization: Optional[str] = None,
    ) -> bytes:
        """Render `file_path` (a DXF) to image bytes via the render service.
        Wrapped by the circuit breaker (4xx re-raised, 5xx/timeouts counted)."""
        # Caller-side validation BEFORE the breaker, so a bad arg can't be
        # misclassified as an upstream failure and trip protection.
        if fmt not in _ALLOWED_FORMATS:
            raise ValueError("fmt must be one of %s" % (_ALLOWED_FORMATS,))
        return self._breaker.call_sync(
            self._render_preview_sync_inner,
            file_path=file_path,
            filename=filename,
            fmt=fmt,
            width=width,
            height=height,
            bg=bg,
            authorization=authorization,
        )

    def _render_preview_sync_inner(
        self,
        *,
        file_path: str,
        filename: Optional[str],
        fmt: str,
        width: int,
        height: int,
        bg: str,
        authorization: Optional[str],
    ) -> bytes:
        headers = build_outbound_headers(
            authorization=self._resolve_authorization(authorization)
        ).as_dict()
        name = filename or os.path.basename(file_path)
        params = {"format": fmt, "width": str(width), "height": str(height), "bg": bg}
        with httpx.Client(base_url=self.base_url, timeout=self.timeout_s) as client:
            with open(file_path, "rb") as fh:
                resp = client.post(
                    "/render",
                    params=params,
                    files={"file": (name, fh, "application/octet-stream")},
                    headers=headers,
                )
            resp.raise_for_status()
            return resp.content
