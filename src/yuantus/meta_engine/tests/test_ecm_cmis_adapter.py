"""ECM-P1D (skeleton): the Athena CMIS publication connector.

All HTTP is mocked via ``httpx.MockTransport`` -- these tests NEVER touch a real
Athena. Covers the status->reason mapping, local-only build/validate, the on-wire
idempotency + bearer headers, circuit-open handling, the breaker predicate, and the
target_system->adapter resolver (default Null, configured -> CMIS).
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuantus.integrations.athena import is_athena_breaker_failure
from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
)
from yuantus.meta_engine.ecm_publication.adapter import NullEcmPublicationAdapter
from yuantus.meta_engine.ecm_publication.adapter_registry import resolve_adapter
from yuantus.meta_engine.ecm_publication.cmis_adapter import (
    AthenaCmisPublicationAdapter,
    build_idempotency_key,
    build_publication_ecm_breaker,
)

_SNAP = {
    "item_id": "P1",
    "version_id": "v1",
    "file_id": "f1",
    "file_role": "native_cad",
    "target_system": "athena",
    "filename": "x.step",
    "mime_type": "model/step",
    "file_size": 10,
    "cad_format": "STEP",
    "generation": 1,
    "revision": "A",
    "content_fingerprint_basis": "checksum:c1",
}
_IDEM = "P1:v1:f1:native_cad:athena"


def _fresh_breaker() -> CircuitBreaker:
    return CircuitBreaker(
        CircuitBreakerConfig(
            name="test-pub-ecm", enabled=True, is_failure=is_athena_breaker_failure
        )
    )


def _settings(**over):
    base = dict(
        ATHENA_BASE_URL="http://athena",
        ATHENA_SERVICE_TOKEN="tok",
        PUBLICATION_ECM_PATH="/cmis",
        PUBLICATION_ECM_TARGET_SYSTEM="athena",
        PUBLICATION_ECM_REPOSITORY_ID="repo1",
        PUBLICATION_ECM_ROOT_FOLDER_PATH="/PLM",
        PUBLICATION_ECM_OBJECT_TYPE_ID="cmis:document",
        PUBLICATION_ECM_TIMEOUT_SECONDS=5.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _adapter(handler):
    return AthenaCmisPublicationAdapter(
        settings=_settings(),
        transport=httpx.MockTransport(handler),
        breaker=_fresh_breaker(),
    )


def _payload():
    return AthenaCmisPublicationAdapter(settings=_settings()).build_payload(_SNAP)


# --- local build / validate (no network) ------------------------------------
def test_build_payload_is_local_with_identity_and_folder():
    p = _payload()
    assert p["idempotency_key"] == _IDEM
    assert build_idempotency_key(_SNAP) == _IDEM
    assert p["folder_path"] == "/PLM/P1/v1"
    assert p["object_type_id"] == "cmis:document"
    assert p["properties"]["plm:contentFingerprintBasis"] == "checksum:c1"


def test_validate_contract_is_local_and_ok():
    a = AthenaCmisPublicationAdapter(settings=_settings())  # no transport
    assert a.validate_contract(_payload()).ok is True


def test_validate_contract_flags_missing_identity():
    a = AthenaCmisPublicationAdapter(settings=_settings())
    res = a.validate_contract({"item_id": "P1"})  # everything else missing
    assert res.ok is False
    assert "missing version_id" in res.errors
    assert "missing folder_path" in res.errors


# --- status -> reason mapping -----------------------------------------------
def test_2xx_sends_with_remote_id():
    r = _adapter(lambda req: httpx.Response(201, json={"objectId": "CMIS-9"})).send(_payload())
    assert r.ok and r.remote_id == "CMIS-9"


def test_2xx_without_body_falls_back_to_idempotency_key():
    r = _adapter(lambda req: httpx.Response(200, text="")).send(_payload())
    assert r.ok and r.remote_id == _IDEM


@pytest.mark.parametrize("key", ["objectId", "id", "remote_id"])
def test_remote_id_extracted_from_each_alternate_key(key):
    r = _adapter(lambda req: httpx.Response(200, json={key: "X-7"})).send(_payload())
    assert r.ok and r.remote_id == "X-7"


@pytest.mark.parametrize("code", [400, 404, 409, 422])
def test_contract_4xx_is_validation_error_non_retryable(code):
    r = _adapter(lambda req: httpx.Response(code, json={"e": "x"})).send(_payload())
    assert (not r.ok) and r.error_kind == "validation_error"


@pytest.mark.parametrize("code", [500, 502, 503, 408, 429, 401, 403])
def test_5xx_retryable_client_and_auth_codes_are_remote_error(code):
    # 401/403 are RETRYABLE (token expiry/rotation is the expected steady-state
    # failure) -- they must not terminally dead-letter a durable outbox row.
    r = _adapter(lambda req: httpx.Response(code)).send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


@pytest.mark.parametrize("code", [301, 302, 307])
def test_3xx_is_not_success(code):
    # A redirect (e.g. to a login page) must NOT be mistaken for a created object.
    r = _adapter(lambda req: httpx.Response(code, text="<html>login</html>")).send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


def test_timeout_is_remote_error():
    def boom(req):
        raise httpx.ConnectTimeout("t", request=req)

    r = _adapter(boom).send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


def test_circuit_open_is_remote_error():
    class _Open:
        def call_sync(self, fn, *a, **k):
            raise CircuitOpenError("publication_ecm", 30.0)

    a = AthenaCmisPublicationAdapter(settings=_settings(), breaker=_Open())
    r = a.send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


# --- headers ----------------------------------------------------------------
def test_send_includes_idempotency_key_and_bearer_auth():
    seen = {}

    def cap(req):
        seen.update({k.lower(): v for k, v in req.headers.items()})
        return httpx.Response(200, json={})

    _adapter(cap).send(_payload())
    assert seen.get("idempotency-key") == _IDEM
    assert seen.get("authorization") == "Bearer tok"  # falls back to ATHENA_SERVICE_TOKEN


def test_publication_ecm_override_takes_precedence_over_athena():
    # The declared PUBLICATION_ECM_BASE_URL / SERVICE_TOKEN overrides MUST win over
    # the ATHENA_* fallback (regression against the silent-drop config trap).
    s = _settings(
        PUBLICATION_ECM_BASE_URL="http://ecm-only",
        PUBLICATION_ECM_SERVICE_TOKEN="ecm-tok",
    )
    seen = {}

    def cap(req):
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={})

    AthenaCmisPublicationAdapter(
        settings=s, transport=httpx.MockTransport(cap), breaker=_fresh_breaker()
    ).send(AthenaCmisPublicationAdapter(settings=s).build_payload(_SNAP))
    assert seen["url"].startswith("http://ecm-only")
    assert seen["auth"] == "Bearer ecm-tok"


# --- breaker predicate (reuses Athena's classification) ---------------------
def test_breaker_uses_athena_failure_classification():
    def status_err(code):
        return httpx.HTTPStatusError(
            "x", request=httpx.Request("POST", "http://a"), response=httpx.Response(code)
        )

    assert is_athena_breaker_failure(status_err(503)) is True
    assert is_athena_breaker_failure(status_err(429)) is True
    assert is_athena_breaker_failure(status_err(422)) is False
    assert is_athena_breaker_failure(httpx.ConnectError("x")) is True
    assert is_athena_breaker_failure(OSError("local")) is False
    assert build_publication_ecm_breaker() is not None


# --- resolver ---------------------------------------------------------------
def test_resolver_unconfigured_is_null():
    s = _settings(PUBLICATION_ECM_TARGET_SYSTEM="")
    assert isinstance(resolve_adapter("athena", settings=s), NullEcmPublicationAdapter)


def test_resolver_no_base_url_fails_closed_to_null():
    # configured target but NO reachable base url -> fail CLOSED to Null (never a
    # live adapter pointing at a bogus host that would churn the outbox forever).
    s = _settings(PUBLICATION_ECM_BASE_URL="", ATHENA_BASE_URL="")
    assert isinstance(resolve_adapter("athena", settings=s), NullEcmPublicationAdapter)


def test_resolver_configured_match_is_cmis():
    assert isinstance(
        resolve_adapter("athena", settings=_settings()), AthenaCmisPublicationAdapter
    )


def test_resolver_configured_other_target_is_null():
    assert isinstance(
        resolve_adapter("other", settings=_settings()), NullEcmPublicationAdapter
    )
