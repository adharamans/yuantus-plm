"""PLM-COLLAB-P1-C: offline license import (Ed25519 verify -> tenant-scoped activate).

The Ed25519 keypair is generated INSIDE this test -- the private key never lives in
the repo. Pins:
- a valid vendor-signed license activates a tenant-scoped AppLicense from the SIGNED
  payload tenant_id (not request context)
- the imported license unlocks via the P1-B kernel for its tenant ONLY
- tampered payload / unknown kid / wrong alg / bad signature all fail verification
- canonical signing is field-order independent
- re-import is an idempotent upsert
- license_data carries verification metadata but is NOT an authorization source
- an audit row is written
"""
from __future__ import annotations

import base64
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.context import tenant_id_var, org_id_var
from yuantus.models.audit import AuditLog
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.app_framework.license_verification import (
    LicenseVerificationError,
    canonical_payload_bytes,
)
from yuantus.meta_engine.app_framework.license_import_service import LicenseImportService
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.security.rbac.models import RBACUser

KID = "test-license-kid-1"


@pytest.fixture
def keypair():
    """Ephemeral Ed25519 keypair -- the PRIVATE key exists only in this test process."""
    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    public_keys = {KID: base64.b64encode(pub_raw).decode()}
    return priv, public_keys


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            RBACUser.__table__,
            AppRegistry.__table__,
            AppLicense.__table__,
            AuditLog.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    t = tenant_id_var.set(None)
    o = org_id_var.set(None)
    yield
    tenant_id_var.reset(t)
    org_id_var.reset(o)
    get_settings.cache_clear()


def _payload(**over):
    p = {
        "tenant_id": "tenant-1",
        "app_names": ["plm.collab"],
        "features": ["plm_collaboration_pro"],
        "plan_type": "Pro",
        "license_key": uuid.uuid4().hex,
        "subject": "ACME Corp",
        "issued_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z",
    }
    p.update(over)
    return p


def _sign(priv, payload, *, alg="Ed25519", kid=KID):
    sig = priv.sign(canonical_payload_bytes(payload))
    return {"alg": alg, "kid": kid, "payload": payload, "signature": base64.b64encode(sig).decode()}


def test_valid_license_activates_tenant_scoped_app_license(session, keypair):
    priv, pubkeys = keypair
    payload = _payload(tenant_id="tenant-1")
    lic_obj = _sign(priv, payload)
    activated = LicenseImportService(session).import_license(lic_obj, pubkeys)
    session.commit()
    assert len(activated) == 1
    lic = activated[0]
    assert lic.tenant_id == "tenant-1"  # from the SIGNED payload, not request context
    assert lic.app_name == "plm.collab"
    assert lic.status == "Active"
    assert lic.license_data["kid"] == KID
    assert lic.license_data["subject"] == "ACME Corp"
    assert "payload_hash" in lic.license_data and "verified_at" in lic.license_data


def test_imported_license_unlocks_p1b_for_its_tenant_only(session, keypair):
    priv, pubkeys = keypair
    LicenseImportService(session).import_license(_sign(priv, _payload(tenant_id="tenant-1")), pubkeys)
    session.commit()
    kernel = EntitlementService(session)
    tenant_id_var.set("tenant-1")
    assert kernel.is_entitled("plm_collaboration_pro") is True
    tenant_id_var.set("tenant-2")
    assert kernel.is_entitled("plm_collaboration_pro") is False


def test_tampered_payload_fails_verification(session, keypair):
    priv, pubkeys = keypair
    lic_obj = _sign(priv, _payload(tenant_id="tenant-1"))
    lic_obj["payload"]["tenant_id"] = "attacker"  # tamper after signing
    with pytest.raises(LicenseVerificationError, match="signature verification failed"):
        LicenseImportService(session).import_license(lic_obj, pubkeys)


def test_unknown_kid_fails(session, keypair):
    priv, pubkeys = keypair
    lic_obj = _sign(priv, _payload(), kid="not-allowlisted")
    with pytest.raises(LicenseVerificationError, match="unknown license kid"):
        LicenseImportService(session).import_license(lic_obj, pubkeys)


def test_wrong_alg_fails(session, keypair):
    priv, pubkeys = keypair
    lic_obj = _sign(priv, _payload(), alg="HS256")
    with pytest.raises(LicenseVerificationError, match="unsupported license alg"):
        LicenseImportService(session).import_license(lic_obj, pubkeys)


def test_bad_signature_fails(session, keypair):
    priv, pubkeys = keypair
    lic_obj = _sign(priv, _payload())
    lic_obj["signature"] = base64.b64encode(b"\x00" * 64).decode()
    with pytest.raises(LicenseVerificationError, match="signature verification failed"):
        LicenseImportService(session).import_license(lic_obj, pubkeys)


def test_malformed_signature_base64_fails(session, keypair):
    priv, pubkeys = keypair
    lic_obj = _sign(priv, _payload())
    lic_obj["signature"] = "!!!not-valid-base64!!!"
    with pytest.raises(LicenseVerificationError, match="signature verification failed"):
        LicenseImportService(session).import_license(lic_obj, pubkeys)


def test_malformed_public_key_base64_fails(session, keypair):
    priv, _ = keypair
    lic_obj = _sign(priv, _payload())
    bad_keys = {KID: "!!!not-valid-base64!!!"}
    with pytest.raises(LicenseVerificationError, match="signature verification failed"):
        LicenseImportService(session).import_license(lic_obj, bad_keys)


def test_canonical_signing_is_field_order_independent(session, keypair):
    priv, pubkeys = keypair
    payload = _payload(tenant_id="tenant-1")
    lic_obj = _sign(priv, payload)
    # rebuild the payload dict with the keys in a different insertion order
    reordered = {k: payload[k] for k in reversed(list(payload.keys()))}
    lic_obj["payload"] = reordered
    activated = LicenseImportService(session).import_license(lic_obj, pubkeys)  # must still verify
    assert activated[0].tenant_id == "tenant-1"


def test_reimport_is_idempotent_upsert(session, keypair):
    priv, pubkeys = keypair
    key = uuid.uuid4().hex
    svc = LicenseImportService(session)
    svc.import_license(_sign(priv, _payload(license_key=key, plan_type="Pro")), pubkeys)
    session.commit()
    svc.import_license(_sign(priv, _payload(license_key=key, plan_type="Enterprise")), pubkeys)
    session.commit()
    rows = session.query(AppLicense).filter_by(license_key=key).all()
    assert len(rows) == 1
    assert rows[0].plan_type == "Enterprise"  # updated in place


def test_license_data_is_not_an_authorization_source(session, keypair):
    priv, pubkeys = keypair
    # license is for a DIFFERENT app, but its features claim plm_collaboration_pro
    payload = _payload(tenant_id="tenant-1", app_names=["plm.other"], features=["plm_collaboration_pro"])
    LicenseImportService(session).import_license(_sign(priv, payload), pubkeys)
    session.commit()
    tenant_id_var.set("tenant-1")
    assert EntitlementService(session).is_entitled("plm_collaboration_pro") is False


def test_missing_tenant_or_app_names_raises(session, keypair):
    priv, pubkeys = keypair
    svc = LicenseImportService(session)
    with pytest.raises(ValueError, match="missing tenant_id"):
        svc.import_license(_sign(priv, _payload(tenant_id="")), pubkeys)
    with pytest.raises(ValueError, match="no app_names"):
        svc.import_license(_sign(priv, _payload(app_names=[])), pubkeys)


def test_audit_row_written(session, keypair):
    priv, pubkeys = keypair
    LicenseImportService(session).import_license(_sign(priv, _payload(tenant_id="tenant-1")), pubkeys)
    session.commit()
    rows = session.query(AuditLog).filter_by(method="LICENSE", tenant_id="tenant-1").all()
    assert len(rows) == 1
    assert rows[0].path == "cli:license/import"
