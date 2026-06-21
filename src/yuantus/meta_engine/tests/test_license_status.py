"""PLM-Collab Phase 4: read-only license/entitlement status (support bundle).

Pins ``license_status.collect_license_status``: entitlement is decided via the
centralized ``is_entitled`` (tenant-scoped, so another tenant's license never counts),
the license summary is a whitelist of operator-safe fields, and the raw ``license_data``
blob is never surfaced.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.context import org_id_var, tenant_id_var
from yuantus.meta_engine.app_framework.license_status import collect_license_status
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser


@pytest.fixture(autouse=True)
def _single_mode(monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    t = tenant_id_var.set(None)
    o = org_id_var.set(None)
    yield
    tenant_id_var.reset(t)
    org_id_var.reset(o)
    get_settings.cache_clear()


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[RBACUser.__table__, AppRegistry.__table__, AppLicense.__table__],
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _add_license(session, *, tenant_id, app_name, status="Active", expires_at=None,
                 plan_type="Pilot", key=None):
    session.add(AppLicense(
        id=uuid.uuid4().hex, app_name=app_name, tenant_id=tenant_id, status=status,
        expires_at=expires_at, plan_type=plan_type, license_key=key or uuid.uuid4().hex,
        license_data={"kid": "k1", "subject": "ACME", "payload_hash": "deadbeef"},
    ))
    session.commit()


def test_status_reports_entitled_feature_and_license_summary(session):
    _add_license(session, tenant_id="acme", app_name="plm.bom_multitable", key="K-BOM")
    st = collect_license_status(session, "acme")
    assert st.tenant_id == "acme"
    assert st.features["bom_multitable"] is True
    assert st.features["plm_collaboration_pro"] is False  # not licensed
    assert len(st.licenses) == 1
    row = st.licenses[0]
    assert row.app_name == "plm.bom_multitable" and row.status == "Active"
    assert row.expires_at is None and row.license_key == "K-BOM"  # perpetual


def test_status_is_tenant_scoped(session):
    _add_license(session, tenant_id="other", app_name="plm.bom_multitable")
    st = collect_license_status(session, "acme")
    assert st.features["bom_multitable"] is False  # another tenant's license never counts
    assert st.licenses == []                        # and acme holds none


def test_status_never_exposes_license_data(session):
    _add_license(session, tenant_id="acme", app_name="plm.collab")
    row = collect_license_status(session, "acme").licenses[0]
    assert not hasattr(row, "license_data")
    # the row is exactly the operator-safe whitelist -- nothing else leaks
    assert set(vars(row)) == {"app_name", "status", "plan_type", "expires_at", "license_key"}


def test_status_normalizes_tenant(session):
    _add_license(session, tenant_id="acme", app_name="plm.bom_multitable")
    st = collect_license_status(session, "  acme  ")  # padded -> stripped
    assert st.tenant_id == "acme" and st.features["bom_multitable"] is True


def test_status_rejects_blank_tenant(session):
    # a blank/whitespace tenant must be refused, not reported as the single-mode "default"
    # tenant against an empty (tenant_id == "") license summary.
    for blank in ("", "   ", None):
        with pytest.raises(ValueError):
            collect_license_status(session, blank)
