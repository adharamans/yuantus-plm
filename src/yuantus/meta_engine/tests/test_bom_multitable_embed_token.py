"""PLM-COLLAB-P3-D1: the Ed25519 embed-token mint route.

`POST /api/v1/bom/multitable/{part_id}/embed-token` mints a short-lived EdDSA embed token,
gated by the SAME pinned order as the P3-A GET (auth -> is_entitled -> part -> Part-type ->
read permission), then [mint configured? else 503] -> origin allowlist (403) -> mint + audit.

The tests use a REAL freshly-generated Ed25519 keypair: the deployment's base64 private seed is
fed via `YUANTUS_EMBED_TOKEN_SIGNING_KEY` and the minted token is verified with the public key
(`decode_eddsa`), so claim completeness + signature are checked end to end, not stubbed.

Pins: unauthenticated -> 401; unentitled -> no token, part NEVER queried, permission NEVER
checked; missing part -> 404; non-Part -> 400; permission denied -> 403; not configured -> 503
(fail-closed); an invalid signing key -> 503 + zero audit; the TTL is capped; cross-origin -> 403;
wildcard '*' is NEVER honored; success -> a verifiable token with the full claim set (aud == the
service audience, embed_origin == the requested origin) + a jti-trackable AuditLog row (never the
token itself); the router owns exactly 2 routes and the live app owns the POST path.
"""
from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app  # noqa: F401  (import side-effect: model registration)
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.audit import AuditLog
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework import entitlement_service as es
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.bom_multitable_embed_token_service import (
    is_origin_allowed,
    parse_allowed_origins,
)
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService
from yuantus.meta_engine.web.bom_multitable_router import bom_multitable_router
from yuantus.security.auth.jwt import JWTError, decode_eddsa, encode_eddsa, now_ts

FEATURE = "bom_multitable"
APP = "plm.collab"
TENANT = "default"
ORIGIN = "https://plm.example.com"
KEY_ID = "embed-1"
AUDIENCE = "metasheet2.embed"
URL = "/api/v1/bom/multitable/{part_id}/embed-token"

_USER = type("_User", (), {"id": 7, "roles": ["engineer"], "is_superuser": False})()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _single_mode(monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(db_session, *, user="auth"):
    app = FastAPI()
    app.include_router(bom_multitable_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    if user == "auth":
        app.dependency_overrides[get_current_user] = lambda: _USER
    elif user == "unauth":
        def _unauth():
            raise HTTPException(status_code=401, detail="Unauthorized")
        app.dependency_overrides[get_current_user] = _unauth
    return TestClient(app)


def _gen_keypair():
    priv = Ed25519PrivateKey.generate()
    raw_priv = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    raw_pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw_priv).decode(), base64.b64encode(raw_pub).decode()


def _configure_embed(monkeypatch, *, with_key=True, origins=ORIGIN, ttl="120"):
    """Configure the deployment's embed signing env; returns the base64 PUBLIC key for verify.

    with_key: True -> a valid keypair; "invalid" -> a malformed signing key; False -> unset.
    """
    priv_b64, pub_b64 = _gen_keypair()
    if with_key is True:
        key = priv_b64
    elif with_key == "invalid":
        key = "not-valid-base64!!!"  # malformed -> must map to 503, never a 500
    else:
        key = ""
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_SIGNING_KEY", key)
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_KEY_ID", KEY_ID)
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_TTL_SECONDS", str(ttl))
    monkeypatch.setenv("YUANTUS_EMBED_ALLOWED_ORIGINS", origins)
    get_settings.cache_clear()
    return pub_b64


def _light_entitlement(monkeypatch, db_session):
    monkeypatch.setitem(es.FEATURE_APP_NAMES, FEATURE, frozenset({APP}))
    db_session.add(AppLicense(id="lic1", app_name=APP, license_key="key1", status="Active", tenant_id=TENANT))
    db_session.commit()


def _allow_permission(monkeypatch, *, allow=True):
    monkeypatch.setattr(MetaPermissionService, "check_permission", lambda self, *a, **k: allow)


def _add_part(db_session, item_id="P1", item_type="Part"):
    db_session.add(Item(id=item_id, item_type_id=item_type, config_id=item_id, generation=1, is_current=True, state="Released", properties={"item_number": "P-001", "name": "Assembly"}))
    db_session.commit()


def _post(client, part_id="P1", origin=ORIGIN):
    return client.post(URL.format(part_id=part_id), json={"origin": origin})


# --- gating -------------------------------------------------------------------

def test_post_unauthenticated_is_401(db_session):
    assert _post(_client(db_session, user="unauth")).status_code == 401


def test_post_unentitled_no_token_no_part_query_no_permission(db_session, monkeypatch):
    _add_part(db_session)

    def _explode(self, *a, **k):
        raise AssertionError("permission must not be checked when unentitled")
    monkeypatch.setattr(MetaPermissionService, "check_permission", _explode)

    client = _client(db_session)
    existing = client.post(URL.format(part_id="P1"), json={"origin": ORIGIN})
    missing = client.post(URL.format(part_id="P999"), json={"origin": ORIGIN})
    assert existing.status_code == 200 and missing.status_code == 200
    assert existing.json() == missing.json()  # part never queried -> identical
    body = existing.json()
    assert body["entitled"] is False
    assert body["upgrade"]["available"] is True
    assert body["embed_token"] is None


def test_post_missing_part_is_404(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch)
    assert _post(_client(db_session), part_id="NOPE").status_code == 404


def test_post_non_part_item_is_400(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch)
    _add_part(db_session, item_id="DOC1", item_type="Document")
    assert _post(_client(db_session), part_id="DOC1").status_code == 400


def test_post_permission_denied_is_403(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch, allow=False)
    _configure_embed(monkeypatch)
    _add_part(db_session)
    assert _post(_client(db_session)).status_code == 403


def test_post_not_configured_is_503_fail_closed(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch, with_key=False)  # no signing key -> fail closed
    _add_part(db_session)
    assert _post(_client(db_session)).status_code == 503


def test_post_invalid_signing_key_is_503_no_token_no_audit(db_session, monkeypatch):
    # a NON-empty but malformed signing key must FAIL CLOSED (503), not crash with a 500.
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch, with_key="invalid")
    _add_part(db_session)
    r = _post(_client(db_session))
    assert r.status_code == 503
    assert db_session.query(AuditLog).count() == 0  # no token issued -> no audit row


def test_post_ttl_is_capped_to_max(db_session, monkeypatch):
    # a misconfigured day-long TTL is capped at MAX_EMBED_TTL_SECONDS (600), never minted long.
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    pub_b64 = _configure_embed(monkeypatch, ttl="86400")
    _add_part(db_session)
    body = _post(_client(db_session)).json()
    assert body["expires_in"] == 600
    claims = decode_eddsa(body["embed_token"], public_keys={KEY_ID: pub_b64})
    assert claims["exp"] - claims["iat"] == 600


def test_post_cross_origin_is_403(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch, origins="https://allowed.example.com")
    _add_part(db_session)
    r = _post(_client(db_session), origin="https://evil.example.com")
    assert r.status_code == 403


# --- success: a real, verifiable token ----------------------------------------

def test_post_mints_verifiable_token_with_full_claims_and_audit(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    pub_b64 = _configure_embed(monkeypatch)
    _add_part(db_session)

    body = _post(_client(db_session)).json()
    assert body["entitled"] is True
    assert body["token_type"] == "embed"
    assert body["expires_in"] == 120
    # aud is the recipient SERVICE (standard audience); the iframe origin is embed_origin
    assert body["aud"] == AUDIENCE
    assert body["embed_origin"] == ORIGIN
    assert body["jti"]
    token = body["embed_token"]
    assert token

    # verify the signature with the PUBLIC key + assert the full claim set
    claims = decode_eddsa(token, public_keys={KEY_ID: pub_b64})
    assert claims["sub"] == str(_USER.id)
    assert claims["tenant_id"] == TENANT
    assert claims["part_id"] == "P1"
    assert claims["feature_key"] == FEATURE
    assert claims["aud"] == AUDIENCE  # the SERVICE audience (P3-D2 can standard-validate it)
    assert claims["embed_origin"] == ORIGIN  # the iframe origin (validated separately)
    assert claims["typ"] == "embed"
    assert claims["jti"] == body["jti"]
    assert claims["exp"] > claims["iat"]

    # jti-trackable audit row written; the token itself is NEVER recorded
    audits = db_session.query(AuditLog).all()
    assert len(audits) == 1
    assert audits[0].method == "MINT"
    assert body["jti"] in audits[0].path
    assert token not in (audits[0].path or "")
    assert token not in (audits[0].error or "")


def test_wrong_public_key_fails_verification(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _configure_embed(monkeypatch)
    _add_part(db_session)
    token = _post(_client(db_session)).json()["embed_token"]
    _, other_pub = _gen_keypair()
    with pytest.raises(Exception):
        decode_eddsa(token, public_keys={KEY_ID: other_pub})


# --- EdDSA verification: expired + tampered are rejected -----------------------

def test_decode_eddsa_rejects_expired_token():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    token = encode_eddsa({"sub": "7", "exp": now_ts() - 10}, private_key=priv, kid=KEY_ID)
    with pytest.raises(JWTError):
        decode_eddsa(token, public_keys={KEY_ID: pub_b64})


def test_decode_eddsa_rejects_tampered_signature():
    priv = Ed25519PrivateKey.generate()
    pub_b64 = base64.b64encode(
        priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    token = encode_eddsa({"sub": "7", "exp": now_ts() + 120}, private_key=priv, kid=KEY_ID)
    header_b64, payload_b64, _sig = token.split(".")
    tampered = f"{header_b64}.{payload_b64}.{'A' * 86}"  # wrong signature
    with pytest.raises(JWTError):
        decode_eddsa(tampered, public_keys={KEY_ID: pub_b64})


# --- origin allowlist: '*' is never honored -----------------------------------

def test_allowlist_drops_wildcard_structurally():
    # even a misconfigured EMBED_ALLOWED_ORIGINS='*' must not allow-all
    assert parse_allowed_origins("*") == ()
    assert is_origin_allowed("https://x.example.com", "*") is False
    # a real origin alongside '*' is allowed; '*' is just dropped
    assert is_origin_allowed("https://x.example.com", "https://x.example.com, *") is True
    assert is_origin_allowed("", "https://x.example.com") is False


# --- route surface ------------------------------------------------------------

def test_router_exposes_exactly_two_routes():
    assert len(bom_multitable_router.routes) == 2


def test_live_app_owns_the_embed_token_post_route():
    app = create_app()
    matches = [r for r in app.routes if getattr(r, "path", None) == "/api/v1/bom/multitable/{part_id}/embed-token"]
    assert len(matches) == 1
    route = matches[0]
    assert "POST" in route.methods
    assert route.endpoint.__name__ == "bom_multitable_embed_token"
    assert route.endpoint.__module__ == "yuantus.meta_engine.web.bom_multitable_router"
