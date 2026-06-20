"""PLM-COLLAB-V2 seats (Option A): project_license_seats -> TenantQuota.max_users.

Pins the import-time seat-cap projection (``security/auth/seat_projection.py``): a valid
``seats`` lands on the identity-side ``TenantQuota.max_users`` (the cap the existing
``QuotaService`` provisioning gate enforces), invalid/absent seats are fail-open no-ops,
and re-import re-projects (the license is the source of truth). ``is_entitled()`` is
intentionally not exercised here -- seats live entirely outside the entitlement path.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.models.base import Base
from yuantus.security.auth.models import AuthUser, Organization, Tenant, TenantQuota
from yuantus.security.auth.quota_service import QuotaService
from yuantus.security.auth.seat_projection import project_license_seats


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def identity_session():
    engine = create_engine("sqlite:///:memory:")

    # SQLite ignores FK constraints unless this pragma is ON. Enable it so
    # TenantQuota.tenant_id -> auth_tenants is actually enforced and the helper's
    # ensure_tenant precondition is exercised the way production Postgres would --
    # without it a missing-tenant projection would silently pass here.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        bind=engine,
        tables=[
            Tenant.__table__,
            TenantQuota.__table__,
            AuthUser.__table__,
            Organization.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _payload(tenant_id="acme", **extra):
    p = {"tenant_id": tenant_id, "app_names": ["plm.bom_multitable"], "license_key": "K1"}
    p.update(extra)
    return p


def test_projects_valid_seats_to_max_users(identity_session):
    result = project_license_seats(identity_session, _payload(seats=20))
    identity_session.flush()
    assert result == 20
    quota = identity_session.get(TenantQuota, "acme")
    assert quota is not None and quota.max_users == 20
    assert identity_session.get(Tenant, "acme") is not None  # ensure_tenant ran (FK satisfied)


def test_absent_seats_projects_nothing(identity_session):
    assert project_license_seats(identity_session, _payload()) is None
    assert identity_session.get(TenantQuota, "acme") is None


@pytest.mark.parametrize("bad", [0, -1, -5, True, "20", 1.5])
def test_invalid_seats_is_fail_open_noop(identity_session, bad):
    # 0 / negative / bool / str / float -> skipped: no max_users written, no tenant lockout.
    assert project_license_seats(identity_session, _payload(seats=bad)) is None
    assert identity_session.get(TenantQuota, "acme") is None


def test_reimport_updates_cap_license_is_source_of_truth(identity_session):
    project_license_seats(identity_session, _payload(seats=20))
    identity_session.flush()
    project_license_seats(identity_session, _payload(seats=30))
    identity_session.flush()
    assert identity_session.get(TenantQuota, "acme").max_users == 30


def test_fk_is_actually_enforced_in_this_env(identity_session):
    # Control for the fixture pragma: a TenantQuota for a non-existent tenant MUST raise,
    # proving the helper's ensure_tenant precondition is load-bearing (not a SQLite no-op).
    identity_session.add(TenantQuota(tenant_id="ghost", max_users=5))
    with pytest.raises(IntegrityError):
        identity_session.flush()


def test_projected_cap_is_enforceable_at_provisioning(identity_session, monkeypatch):
    # End-to-end purpose: the projected cap is exactly what QuotaService enforces. With
    # seats=2 and two active users, a 3rd ({"users": 1}) would exceed the cap.
    monkeypatch.setenv("YUANTUS_QUOTA_MODE", "enforce")
    get_settings.cache_clear()
    project_license_seats(identity_session, _payload(seats=2))
    for username in ("u1", "u2"):
        identity_session.add(AuthUser(tenant_id="acme", username=username, is_active=True))
    identity_session.flush()
    decisions = QuotaService(identity_session).evaluate("acme", deltas={"users": 1})
    assert decisions, "projected cap should be enforced by QuotaService"
    assert decisions[0].resource == "users"
    assert decisions[0].limit == 2 and decisions[0].used == 2
