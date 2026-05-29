"""Tests for the generic-HTTP ERP publication connector (G2 R3).

All HTTP is mocked via `httpx.MockTransport` — these tests NEVER touch a real
endpoint. Covers the status->reason mapping, local-only build/validate, the
on-wire idempotency + auth headers, circuit-open handling, the breaker
is_failure predicate, and the target_system->adapter resolver.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from yuantus.integrations.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitOpenError,
)
from yuantus.meta_engine.erp_publication.adapter import NullErpPublicationAdapter
from yuantus.meta_engine.erp_publication.adapter_registry import resolve_adapter
from yuantus.meta_engine.erp_publication.http_adapter import (
    HttpErpPublicationAdapter,
    build_idempotency_key,
    is_publication_breaker_failure,
)


def _fresh_breaker() -> CircuitBreaker:
    # Per-adapter fresh breaker so tests don't share the module-global cached
    # "publication_erp" breaker (parametrized 5xx tests would otherwise trip it
    # OPEN for later tests).
    return CircuitBreaker(
        CircuitBreakerConfig(
            name="test-pub-erp", enabled=True, is_failure=is_publication_breaker_failure
        )
    )

_SNAP = {
    "item": {"item_id": "ITEM-1"},
    "version": {"version_id": "VER-1"},
    "target_system": "erp1",
    "publication_kind": "readiness",
    "eligible": True,
    "file_refs": [],
    "summary": {"ok": True},
    "ruleset_id": "readiness",
}


def _settings(**over):
    base = dict(
        PUBLICATION_ERP_BASE_URL="http://erp",
        PUBLICATION_ERP_PATH="/pub",
        PUBLICATION_ERP_SERVICE_TOKEN="tok",
        PUBLICATION_ERP_TIMEOUT_SECONDS=5.0,
        PUBLICATION_ERP_TARGET_SYSTEM="erp1",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _adapter(handler):
    return HttpErpPublicationAdapter(
        settings=_settings(),
        transport=httpx.MockTransport(handler),
        breaker=_fresh_breaker(),
    )


def _payload():
    return HttpErpPublicationAdapter(settings=_settings()).build_payload(_SNAP)


# --- local build / validate (no network) ------------------------------------


def test_build_payload_is_local_with_idempotency_key():
    p = _payload()
    assert p["idempotency_key"] == "ITEM-1:VER-1:erp1:readiness"
    assert p["item_id"] == "ITEM-1" and p["version_id"] == "VER-1"


def test_validate_contract_is_local_and_ok():
    # No transport configured -> if this touched the network it would fail; it
    # must be purely local.
    a = HttpErpPublicationAdapter(settings=_settings())
    assert a.validate_contract(_payload()).ok is True


def test_validate_contract_flags_missing_fields():
    a = HttpErpPublicationAdapter(settings=_settings())
    res = a.validate_contract({"item_id": "", "version_id": "", "target_system": ""})
    assert res.ok is False and len(res.errors) == 3


# --- status -> reason mapping -----------------------------------------------


def test_2xx_sends_with_remote_id():
    r = _adapter(lambda req: httpx.Response(201, json={"id": "REMOTE-9"})).send(_payload())
    assert r.ok and r.remote_id == "REMOTE-9"


def test_2xx_without_body_falls_back_to_idempotency_key():
    r = _adapter(lambda req: httpx.Response(200, text="")).send(_payload())
    assert r.ok and r.remote_id == "ITEM-1:VER-1:erp1:readiness"


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_4xx_is_validation_error_non_retryable(code):
    r = _adapter(lambda req: httpx.Response(code, json={"e": "x"})).send(_payload())
    assert (not r.ok) and r.error_kind == "validation_error"


@pytest.mark.parametrize("code", [500, 502, 503, 408, 429])
def test_5xx_and_retryable_client_codes_are_remote_error(code):
    r = _adapter(lambda req: httpx.Response(code)).send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


def test_timeout_is_remote_error():
    def boom(req):
        raise httpx.ConnectTimeout("t", request=req)

    r = _adapter(boom).send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


def test_circuit_open_is_remote_error():
    class _Open:
        def call_sync(self, fn, *a, **k):
            raise CircuitOpenError("publication_erp", 30.0)

    a = HttpErpPublicationAdapter(settings=_settings(), breaker=_Open())
    r = a.send(_payload())
    assert (not r.ok) and r.error_kind == "remote_error"


# --- headers ----------------------------------------------------------------


def test_send_includes_idempotency_key_and_bearer_auth():
    seen = {}

    def cap(req):
        seen.update({k.lower(): v for k, v in req.headers.items()})
        return httpx.Response(200, json={})

    _adapter(cap).send(_payload())
    assert seen.get("idempotency-key") == "ITEM-1:VER-1:erp1:readiness"
    assert seen.get("authorization") == "Bearer tok"


# --- breaker predicate ------------------------------------------------------


def test_breaker_failure_predicate():
    def status_err(code):
        return httpx.HTTPStatusError(
            "x", request=httpx.Request("POST", "http://e"),
            response=httpx.Response(code),
        )

    assert is_publication_breaker_failure(status_err(503)) is True
    assert is_publication_breaker_failure(status_err(429)) is True
    assert is_publication_breaker_failure(status_err(422)) is False  # 4xx not counted
    assert is_publication_breaker_failure(httpx.ConnectError("x")) is True
    assert is_publication_breaker_failure(OSError("local")) is False


# --- resolver ---------------------------------------------------------------


def test_resolver_unconfigured_is_null():
    s = _settings(PUBLICATION_ERP_TARGET_SYSTEM="", PUBLICATION_ERP_BASE_URL="")
    assert isinstance(resolve_adapter("erp1", settings=s), NullErpPublicationAdapter)


def test_resolver_configured_match_is_http():
    assert isinstance(
        resolve_adapter("erp1", settings=_settings()), HttpErpPublicationAdapter
    )


def test_resolver_configured_other_target_is_null():
    assert isinstance(
        resolve_adapter("other", settings=_settings()), NullErpPublicationAdapter
    )


def test_resolver_no_base_url_is_null():
    s = _settings(PUBLICATION_ERP_BASE_URL="")
    assert isinstance(resolve_adapter("erp1", settings=s), NullErpPublicationAdapter)
