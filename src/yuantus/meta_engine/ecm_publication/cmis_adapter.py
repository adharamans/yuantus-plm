"""Athena CMIS publication connector (ECM-P1D — SKELETON).

A concrete ``EcmPublicationAdapter`` that POSTs the published controlled-record to
Athena's CMIS endpoint, modeled on ``erp_publication.http_adapter`` (sync httpx +
injectable transport, CircuitBreaker, ``build_outbound_headers``, status->reason
mapping). The CONNECTION reuses the existing ``ATHENA_BASE_URL`` / ``ATHENA_SERVICE_TOKEN``
settings and the Athena failure classifier.

STATUS: this is the P1D **skeleton**. It is structurally complete and fully unit-tested
with the network mocked, but:
- it is NOT wired on by default (``resolve_adapter`` returns Null unless
  ``PUBLICATION_ECM_TARGET_SYSTEM`` is configured), so dev/CI never performs a real write;
- the CMIS WIRE MAPPING in ``build_payload`` (repository / folder / object-type /
  property names, and the browser-binding vs AtomPub choice) is PROVISIONAL and must be
  validated/adjusted against a live Athena during Phase 0 (U1-U5);
- OAuth client-credentials (the async ``AthenaClient`` token flow) is not yet wired here;
  the skeleton authenticates with the static ``ATHENA_SERVICE_TOKEN`` bearer. Full OAuth +
  shared-breaker integration is a live-bring-up step.

Boundaries (mirrors the erp connector): ``build_payload`` / ``validate_contract`` are
LOCAL (no network) so dry-run stays side-effect-free; ``send()`` is the ONLY network call.
HTTP status -> SendResult.error_kind: 2xx ok; 5xx / timeout / connection / 429 / 408 /
circuit-open -> remote_error (retryable); other 4xx -> validation_error (non-retryable).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from yuantus.config import get_settings
from yuantus.integrations.athena import is_athena_breaker_failure
from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
    get_or_create_breaker,
)
from yuantus.integrations.http import build_outbound_headers
from yuantus.meta_engine.ecm_publication.adapter import (
    EcmPublicationAdapter,
    SendResult,
    ValidationResult,
)

logger = logging.getLogger(__name__)

_BREAKER_NAME = "publication_ecm"
# Client statuses that are retryable + count toward the breaker.
_COUNT_STATUS = {408, 429}
# Auth statuses: retryable in a durable outbox (an expired/rotated bearer or OAuth
# token is the EXPECTED steady-state failure -> must NOT terminally dead-letter).
_RETRYABLE_AUTH_STATUS = {401, 403}

_IDENTITY_FIELDS = ("item_id", "version_id", "file_id", "file_role", "target_system")


def build_publication_ecm_breaker() -> CircuitBreaker:
    # Reuse Athena's failure classification (same upstream), distinct breaker name
    # so a publish failure does not couple into the health breaker's window.
    return get_or_create_breaker(
        CircuitBreakerConfig(
            name=_BREAKER_NAME, enabled=True, is_failure=is_athena_breaker_failure
        )
    )


def build_idempotency_key(snapshot: dict) -> str:
    # Per-file identity = the ECM outbox idempotency key (so Athena dedupes the
    # at-least-once outbox delivery).
    return ":".join(str(snapshot.get(k) or "") for k in _IDENTITY_FIELDS)


class AthenaCmisPublicationAdapter(EcmPublicationAdapter):
    def __init__(
        self,
        *,
        settings: Any = None,
        transport: Optional[httpx.BaseTransport] = None,
        breaker: Optional[CircuitBreaker] = None,
    ) -> None:
        s = settings or get_settings()
        # Connection reuses the existing Athena settings.
        self.base_url = (
            getattr(s, "PUBLICATION_ECM_BASE_URL", "")
            or getattr(s, "ATHENA_BASE_URL", "")
            or ""
        ).rstrip("/")
        self.path = getattr(s, "PUBLICATION_ECM_PATH", "") or "/cmis/browser"
        self._token = (
            getattr(s, "PUBLICATION_ECM_SERVICE_TOKEN", "")
            or getattr(s, "ATHENA_SERVICE_TOKEN", "")
            or ""
        )
        self.repository_id = getattr(s, "PUBLICATION_ECM_REPOSITORY_ID", "") or ""
        self.root_folder_path = getattr(s, "PUBLICATION_ECM_ROOT_FOLDER_PATH", "") or "/PLM"
        self.object_type_id = getattr(s, "PUBLICATION_ECM_OBJECT_TYPE_ID", "") or "cmis:document"
        self.timeout_s = float(getattr(s, "PUBLICATION_ECM_TIMEOUT_SECONDS", 30.0) or 30.0)
        self._transport = transport  # injectable for tests (httpx.MockTransport)
        self._breaker = breaker or build_publication_ecm_breaker()

    # -- local (no network) ---------------------------------------------
    def build_payload(self, snapshot: dict) -> dict:
        """PROVISIONAL CMIS createDocument envelope (validated in Phase 0). The ECM
        snapshot is FLAT (top-level identity keys)."""
        folder_path = "/".join(
            [
                self.root_folder_path.rstrip("/"),
                str(snapshot.get("item_id") or ""),
                str(snapshot.get("version_id") or ""),
            ]
        )
        return {
            "idempotency_key": build_idempotency_key(snapshot),
            "cmis_action": "createDocument",
            "repository_id": self.repository_id,
            "object_type_id": self.object_type_id,
            "folder_path": folder_path,
            "name": snapshot.get("filename") or snapshot.get("file_id"),
            # identity (the per-file outbox key)
            "item_id": snapshot.get("item_id"),
            "version_id": snapshot.get("version_id"),
            "file_id": snapshot.get("file_id"),
            "file_role": snapshot.get("file_role"),
            "target_system": snapshot.get("target_system"),
            # provisional CMIS properties
            "properties": {
                "cmis:name": snapshot.get("filename") or snapshot.get("file_id"),
                "cmis:contentStreamMimeType": snapshot.get("mime_type"),
                "cmis:contentStreamLength": snapshot.get("file_size"),
                "plm:generation": snapshot.get("generation"),
                "plm:revision": snapshot.get("revision"),
                "plm:cadFormat": snapshot.get("cad_format"),
                "plm:fileRole": snapshot.get("file_role"),
                "plm:contentFingerprintBasis": snapshot.get("content_fingerprint_basis"),
            },
        }

    def validate_contract(self, payload: dict) -> ValidationResult:
        # LOCAL ONLY — no network, so dry-run never reaches Athena.
        errors = [f"missing {k}" for k in _IDENTITY_FIELDS if not payload.get(k)]
        if not payload.get("folder_path"):
            errors.append("missing folder_path")
        if not payload.get("object_type_id"):
            errors.append("missing object_type_id")
        return ValidationResult(ok=not errors, errors=errors)

    # -- network (send only) --------------------------------------------
    def _authorization(self) -> Optional[str]:
        token = (self._token or "").strip()
        if not token:
            return None
        return token if token.lower().startswith("bearer ") else f"Bearer {token}"

    def _client(self) -> httpx.Client:
        kwargs: dict = {
            "base_url": self.base_url or "http://athena.invalid",
            "timeout": self.timeout_s,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.Client(**kwargs)

    def _post(self, payload: dict, idem: str) -> httpx.Response:
        headers = build_outbound_headers(authorization=self._authorization()).as_dict()
        headers["Idempotency-Key"] = idem
        # follow_redirects stays False (httpx default): a 3xx (e.g. a proxy login
        # redirect) must NOT be silently followed and mistaken for success.
        with self._client() as client:
            resp = client.post(self.path, json=payload, headers=headers)
        resp.raise_for_status()  # >=400 -> HTTPStatusError (mapped in send)
        return resp

    def send(self, payload: dict) -> SendResult:
        idem = payload.get("idempotency_key") or payload.get("file_id") or ""
        try:
            resp = self._breaker.call_sync(self._post, payload, idem)
        except CircuitOpenError:
            return SendResult(
                ok=False, error="publication ECM circuit open", error_kind="remote_error"
            )
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            # remote_error (RETRYABLE): 5xx, 408/429, 401/403 (token expiry is the
            # expected steady-state failure), and 3xx (an un-followed redirect, e.g. a
            # proxy login bounce -- infra, not a payload defect). raise_for_status may
            # raise on 3xx in some httpx versions, hence handled here too.
            if (
                code in _COUNT_STATUS
                or code in _RETRYABLE_AUTH_STATUS
                or code >= 500
                or 300 <= code < 400
            ):
                return SendResult(ok=False, error=f"HTTP {code}", error_kind="remote_error")
            # validation_error (TERMINAL): genuine payload-contract 4xx (400/404/409/422).
            return SendResult(ok=False, error=f"HTTP {code}", error_kind="validation_error")
        except httpx.RequestError as exc:  # timeout / connection
            return SendResult(ok=False, error=type(exc).__name__, error_kind="remote_error")

        # raise_for_status only fires on >=400; a 3xx returns here. Require a real 2xx
        # so a redirect (e.g. to a login page) is never mistaken for a created object.
        if not (200 <= resp.status_code < 300):
            return SendResult(
                ok=False, error=f"HTTP {resp.status_code}", error_kind="remote_error"
            )

        remote_id: Optional[str] = None
        try:
            body = resp.json()
            if isinstance(body, dict):
                # CMIS browser binding returns the new object id under succinct keys.
                remote_id = body.get("objectId") or body.get("id") or body.get("remote_id")
        except Exception:
            pass
        return SendResult(ok=True, remote_id=remote_id or idem)
