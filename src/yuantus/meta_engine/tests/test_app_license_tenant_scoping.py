"""PLM-COLLAB-P1-A: AppLicense tenant scoping + single-mode guard (D0-3).

Uses a REAL in-memory SQLite session (not a mock) so the tenant filter actually
executes. Pins the four PLM-COLLAB-P1-A acceptance conditions plus the F2 guard:

  1. the model carries tenant_id/org_id columns
  2. purchase writes the resolved tenant_id (org_id recorded only)
  3. the entitlement query matches ONLY the resolved tenant
  4. a legacy NULL-tenant active license never unlocks
  F2. non-single TENANCY_MODE with no tenant context raises (no silent global license)
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.context import tenant_id_var, org_id_var
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import (
    MarketplaceAppListing,
    AppLicense,
)
from yuantus.meta_engine.app_framework.store_service import AppStoreService
from yuantus.security.rbac.models import RBACUser


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            RBACUser.__table__,
            AppRegistry.__table__,
            MarketplaceAppListing.__table__,
            AppLicense.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _isolate_context_and_settings():
    """Each test starts with empty tenant context + a fresh Settings cache."""
    get_settings.cache_clear()
    t = tenant_id_var.set(None)
    o = org_id_var.set(None)
    yield
    tenant_id_var.reset(t)
    org_id_var.reset(o)
    get_settings.cache_clear()


def _paid_listing(session, name="plm.collab"):
    listing = MarketplaceAppListing(
        id=str(uuid.uuid4()), name=name, latest_version="1.0.0",
        price_model="Subscription", price_amount=1000,
    )
    session.add(listing)
    session.commit()
    return listing


def _active_license(session, *, app_name, tenant_id, key=None):
    lic = AppLicense(
        id=str(uuid.uuid4()),
        app_name=app_name,
        license_key=key or str(uuid.uuid4()).upper(),
        status="Active",
        tenant_id=tenant_id,
    )
    session.add(lic)
    session.commit()
    return lic


# 1. model carries the columns
def test_model_has_tenant_scoping_columns():
    cols = AppLicense.__table__.columns
    assert "tenant_id" in cols
    assert "org_id" in cols


# 2. purchase writes the resolved tenant; org_id recorded only
def test_purchase_writes_resolved_tenant(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    tenant_id_var.set("acme")
    org_id_var.set("dept-7")
    listing = _paid_listing(session)
    lic = AppStoreService(session).purchase_app(listing.id, plan_type="Pro")
    session.commit()
    assert lic.tenant_id == "acme"
    assert lic.org_id == "dept-7"  # recorded, not used as an entitlement filter


def test_single_mode_falls_back_to_default_when_no_tenant(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    listing = _paid_listing(session)
    lic = AppStoreService(session).purchase_app(listing.id)
    assert lic.tenant_id == "default"


# F2. non-single + missing tenant must refuse (no silent global license)
def test_non_single_mode_missing_tenant_raises(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "db-per-tenant")
    get_settings.cache_clear()
    listing = _paid_listing(session)
    with pytest.raises(ValueError, match="tenant context is required"):
        AppStoreService(session).purchase_app(listing.id)


# 3. the entitlement query matches ONLY the resolved tenant
def test_query_matches_only_resolved_tenant(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    listing = _paid_listing(session, name="plm.collab")
    _active_license(session, app_name="plm.collab", tenant_id="other", key="K-OTHER")
    svc = AppStoreService(session)

    # current tenant = acme, only an "other"-scoped license exists -> no unlock
    tenant_id_var.set("acme")
    with pytest.raises(ValueError, match="No active license"):
        svc.install_from_store(listing.id, user_id=1)

    # the acme-scoped license is found only by the acme-resolved query
    _active_license(session, app_name="plm.collab", tenant_id="acme", key="K-ACME")
    assert (
        session.query(AppLicense)
        .filter_by(app_name="plm.collab", status="Active", tenant_id="acme")
        .count()
        == 1
    )
    assert (
        session.query(AppLicense)
        .filter_by(app_name="plm.collab", status="Active", tenant_id="other")
        .count()
        == 1
    )


# 3b. positive: the resolved tenant's license unlocks install end-to-end
def test_resolved_tenant_license_unlocks_install(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    listing = _paid_listing(session, name="plm.collab")
    tenant_id_var.set("acme")
    svc = AppStoreService(session)
    svc.purchase_app(listing.id, plan_type="Pro")
    session.commit()
    result = svc.install_from_store(listing.id, user_id=1)
    assert result["status"] == "Installed"


# 4. a legacy NULL-tenant active license never unlocks
def test_legacy_null_tenant_license_never_unlocks(session, monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    listing = _paid_listing(session, name="plm.collab")
    _active_license(session, app_name="plm.collab", tenant_id=None, key="K-LEGACY")
    svc = AppStoreService(session)

    # explicit tenant -> NULL license must not match
    tenant_id_var.set("acme")
    with pytest.raises(ValueError, match="No active license"):
        svc.install_from_store(listing.id, user_id=1)

    # even single-mode "default" fallback -> NULL still never matches
    tenant_id_var.set(None)
    with pytest.raises(ValueError, match="No active license"):
        svc.install_from_store(listing.id, user_id=1)
