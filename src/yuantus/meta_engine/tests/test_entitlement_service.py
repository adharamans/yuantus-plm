"""PLM-COLLAB-P1-B: EntitlementService.is_entitled - the single feature-check kernel.

Real in-memory SQLite. Pins the P1-B acceptance:
- tenant A's active license does not unlock tenant B
- a legacy tenant_id=NULL license never unlocks
- revoked / expired / wrong app_name do not unlock
- future-expiry / null-expiry unlock
- unknown feature_key raises ValueError
- a reserved-but-unlit feature_key returns False
- license_data is NOT an authorization source
- non-single + missing tenant context raises (not swallowed to False)
- the kernel adds no route (service only -> route count stays 691)
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.context import tenant_id_var, org_id_var
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.security.rbac.models import RBACUser

FEATURE = "plm_collaboration_pro"
APP = "plm.collab"


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[RBACUser.__table__, AppRegistry.__table__, AppLicense.__table__],
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


def _lic(session, *, tenant_id, app_name=APP, status="Active", expires_at=None, license_data=None):
    lic = AppLicense(
        id=str(uuid.uuid4()),
        app_name=app_name,
        license_key=str(uuid.uuid4()).upper(),
        status=status,
        tenant_id=tenant_id,
        expires_at=expires_at,
        license_data=license_data or {},
    )
    session.add(lic)
    session.commit()
    return lic


def _entitled(session, feature=FEATURE):
    return EntitlementService(session).is_entitled(feature)


def test_active_license_unlocks_own_tenant(session):
    _lic(session, tenant_id="acme")
    tenant_id_var.set("acme")
    assert _entitled(session) is True


def test_tenant_a_license_does_not_unlock_tenant_b(session):
    _lic(session, tenant_id="acme")
    tenant_id_var.set("beta")
    assert _entitled(session) is False


def test_legacy_null_tenant_license_never_unlocks(session):
    _lic(session, tenant_id=None)
    tenant_id_var.set("acme")
    assert _entitled(session) is False
    tenant_id_var.set(None)  # single -> "default", still never matches NULL
    assert _entitled(session) is False


def test_revoked_does_not_unlock(session):
    _lic(session, tenant_id="acme", status="Revoked")
    tenant_id_var.set("acme")
    assert _entitled(session) is False


def test_expired_does_not_unlock(session):
    _lic(session, tenant_id="acme", expires_at=datetime.utcnow() - timedelta(days=1))
    tenant_id_var.set("acme")
    assert _entitled(session) is False


def test_future_expiry_unlocks(session):
    _lic(session, tenant_id="acme", expires_at=datetime.utcnow() + timedelta(days=30))
    tenant_id_var.set("acme")
    assert _entitled(session) is True


def test_null_expiry_unlocks(session):
    _lic(session, tenant_id="acme", expires_at=None)
    tenant_id_var.set("acme")
    assert _entitled(session) is True


def test_wrong_app_name_does_not_unlock(session):
    _lic(session, tenant_id="acme", app_name="plm.other")
    tenant_id_var.set("acme")
    assert _entitled(session) is False


def test_unknown_feature_key_raises(session):
    tenant_id_var.set("acme")
    with pytest.raises(ValueError, match="unknown feature_key"):
        EntitlementService(session).is_entitled("nope_not_a_feature")


def test_reserved_but_unlit_feature_returns_false(session):
    # a valid plm.collab license exists, but a reserved key maps to no app -> False
    # (automation_enterprise is still reserved; bom_multitable was lit in P3-B)
    _lic(session, tenant_id="acme")
    tenant_id_var.set("acme")
    assert EntitlementService(session).is_entitled("automation_enterprise") is False


def test_bom_multitable_lit_to_its_own_independent_sku(session):
    # P3-B: bom_multitable is lit, unlocked ONLY by its own SKU app_name plm.bom_multitable.
    _lic(session, tenant_id="acme", app_name="plm.bom_multitable")
    tenant_id_var.set("acme")
    assert EntitlementService(session).is_entitled("bom_multitable") is True


def test_bom_multitable_not_unlocked_by_collab_license(session):
    # Independence: the plm.collab SKU does NOT entitle bom_multitable (not bundled, not a
    # reuse of plm_collaboration_pro -- same discipline as approval_automation in P2-A).
    _lic(session, tenant_id="acme", app_name="plm.collab")
    tenant_id_var.set("acme")
    assert EntitlementService(session).is_entitled("bom_multitable") is False


def test_license_data_is_not_an_authorization_source(session):
    # license is for a DIFFERENT app but its license_data claims the feature -> False
    _lic(
        session,
        tenant_id="acme",
        app_name="plm.other",
        license_data={"feature_keys": ["plm_collaboration_pro"]},
    )
    tenant_id_var.set("acme")
    assert _entitled(session) is False


def test_non_single_missing_tenant_raises(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "db-per-tenant")
    get_settings.cache_clear()
    _lic(session, tenant_id="acme")
    # no tenant context -> resolver raises, not swallowed to False
    with pytest.raises(ValueError, match="tenant context is required"):
        _entitled(session)


def test_reserved_key_also_raises_on_non_single_missing_tenant(session, monkeypatch):
    # a reserved (unlit) key must still pass the tenant guard, not silently return
    # False -- it goes through resolve_license_scope() before the reserved shortcut.
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "db-per-tenant")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="tenant context is required"):
        EntitlementService(session).is_entitled("automation_enterprise")


def test_kernel_is_service_only_no_router():
    import yuantus.meta_engine.app_framework.entitlement_service as es

    assert not hasattr(es, "router"), (
        "P1-B entitlement kernel must add no route (route count stays 691)"
    )
