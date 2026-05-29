"""Generic outbound-HTTP ERP publication connector (G2 R3).

A concrete `ErpPublicationAdapter` that POSTs the publication payload to a
configurable, vendor-agnostic HTTP endpoint, modeled on `DedupVisionClient`
(settings base-url + token, `CircuitBreaker`, `build_outbound_headers`, httpx +
timeout). GPL/AGPL-clean: built from the endpoint's documented HTTP contract
only — no vendor SDK, no odooplm code.

Boundaries (R3 connector taskbook #673):
- `build_payload` / `validate_contract` are **local** (no network), so dry-run
  stays side-effect-free even with this adapter configured; `send()` is the ONLY
  network call.
- HTTP status -> SendResult.error_kind: 2xx ok; 5xx/timeout/connection/429/408/
  circuit-open -> remote_error (retryable); other 4xx -> validation_error
  (non-retryable). The CircuitBreaker counts only the retryable (service/transport)
  failures, never client 4xx.
- Every POST carries an `Idempotency-Key` (version-scoped) so the target dedupes
  the at-least-once outbox delivery.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from yuantus.config import get_settings
from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    get_or_create_breaker,
)
from yuantus.integrations.http import build_outbound_headers
from yuantus.meta_engine.erp_publication.adapter import (
    ErpPublicationAdapter,
    SendResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)

_BREAKER_NAME = "publication_erp"
# Client statuses that are nonetheless retryable + count toward the breaker.
_COUNT_STATUS = {408, 429}


def is_publication_breaker_failure(exc: Exception) -> bool:
    """Count only service/transport failures toward the breaker — never client
    4xx (mirrors the DedupVision predicate)."""
    if isinstance(exc, OSError):
        return False
    if isinstance(exc, httpx.RequestError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(status, int):
            return 500 <= status < 600 or status in _COUNT_STATUS
        return True
    return True


def build_publication_erp_breaker() -> CircuitBreaker:
    return get_or_create_breaker(
        CircuitBreakerConfig(
            name=_BREAKER_NAME,
            enabled=True,
            is_failure=is_publication_breaker_failure,
        )
    )


def build_idempotency_key(snapshot: dict) -> str:
    item = snapshot.get("item") or {}
    version = snapshot.get("version") or {}
    return ":".join(
        [
            str(item.get("item_id") or ""),
            str(version.get("version_id") or ""),
            str(snapshot.get("target_system") or ""),
            str(snapshot.get("publication_kind") or ""),
        ]
    )


class HttpErpPublicationAdapter(ErpPublicationAdapter):
    def __init__(
        self,
        *,
        settings: Any = None,
        transport: Optional[httpx.BaseTransport] = None,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        s = settings or get_settings()
        self.base_url = (getattr(s, "PUBLICATION_ERP_BASE_URL", "") or "").rstrip("/")
        self.path = getattr(s, "PUBLICATION_ERP_PATH", "") or "/publications"
        self._token = getattr(s, "PUBLICATION_ERP_SERVICE_TOKEN", "") or ""
        self.timeout_s = float(getattr(s, "PUBLICATION_ERP_TIMEOUT_SECONDS", 30.0) or 30.0)
        self._transport = transport  # injectable for tests (httpx.MockTransport)
        self._breaker = breaker or build_publication_erp_breaker()

    # -- local (no network) ---------------------------------------------
    def build_payload(self, snapshot: dict) -> dict:
        item = snapshot.get("item") or {}
        version = snapshot.get("version") or {}
        return {
            "idempotency_key": build_idempotency_key(snapshot),
            "target_system": snapshot.get("target_system"),
            "publication_kind": snapshot.get("publication_kind"),
            "item_id": item.get("item_id"),
            "version_id": version.get("version_id"),
            "eligible": snapshot.get("eligible"),
            "item": item,
            "version": snapshot.get("version"),
            "file_refs": snapshot.get("file_refs"),
            "summary": snapshot.get("summary"),
            "ruleset_id": snapshot.get("ruleset_id"),
        }

    def validate_contract(self, payload: dict) -> ValidationResult:
        # LOCAL ONLY — no network, so dry-run never reaches the ERP.
        errors = [f"missing {k}" for k in ("item_id", "version_id", "target_system") if not payload.get(k)]
        return ValidationResult(ok=not errors, errors=errors)

    # -- network (send only) --------------------------------------------
    def _authorization(self) -> Optional[str]:
        token = (self._token or "").strip()
        if not token:
            return None
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def _client(self) -> httpx.Client:
        kwargs: dict = {"base_url": self.base_url or "http://erp.invalid", "timeout": self.timeout_s}
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _post(self, payload: dict, idem: str) -> httpx.Response:
        headers = build_outbound_headers(authorization=self._authorization()).as_dict()
        headers["Idempotency-Key"] = idem
        with self._client() as client:
            resp = client.post(self.path, json=payload, headers=headers)
        resp.raise_for_status()  # non-2xx -> HTTPStatusError (mapped in send)
        return resp

    def send(self, payload: dict) -> SendResult:
        idem = payload.get("idempotency_key") or payload.get("item_id") or ""
        try:
            resp = self._breaker.call_sync(self._post, payload, idem)
        except CircuitOpenError:
            return SendResult(ok=False, error="publication ERP circuit open", error_kind="remote_error")
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code in _COUNT_STATUS or code >= 500:
                return SendResult(ok=False, error=f"HTTP {code}", error_kind="remote_error")
            return SendResult(ok=False, error=f"HTTP {code}", error_kind="validation_error")
        except httpx.RequestError as exc:  # timeout / connection
            return SendResult(ok=False, error=type(exc).__name__, error_kind="remote_error")

        remote_id: Optional[str] = None
        try:
            body = resp.json()
            if isinstance(body, dict):
                remote_id = body.get("id") or body.get("remote_id")
        except Exception:
            pass
        return SendResult(ok=True, remote_id=remote_id or idem)
