"""License revocation route (L4 Fork C): POST /api/v1/admin/licenses/{key}/revoke.

In-memory SQLite, override get_db + require_superuser (mirrors test_license_status_router).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.web.license_revoke_router import license_revoke_router
from yuantus.models.audit import AuditLog
from yuantus.models.base import Base

TENANT = "tenant-1"
URL = "/api/v1/admin/licenses/{}/revoke"


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine, tables=[AppLicense.__table__, AuditLog.__table__])
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _client(db_session, *, superuser=True):
    app = FastAPI()
    app.include_router(license_revoke_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    if superuser:
        app.dependency_overrides[require_superuser] = lambda: object()
    else:
        def _deny():
            raise HTTPException(status_code=403, detail="superuser required")

        app.dependency_overrides[require_superuser] = _deny
    return TestClient(app)


def _license(db_session, *, key, app_name="plm.collab", status="Active"):
    db_session.add(
        AppLicense(
            id=uuid.uuid4().hex,
            app_name=app_name,
            license_key=key,
            status=status,
            tenant_id=TENANT,
        )
    )
    db_session.commit()


def test_revoke_sets_status_revoked_and_audits(db_session):
    _license(db_session, key="K1")
    r = _client(db_session).post(URL.format("K1"), json={"reason": "compromised"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1 and body["revoked"][0]["status"] == "Revoked"
    # status persisted
    assert db_session.query(AppLicense).filter_by(license_key="K1").one().status == "Revoked"
    # append-only audit row written
    audit = db_session.query(AuditLog).filter(AuditLog.path.like("admin:license/revoke%")).all()
    assert len(audit) == 1 and audit[0].tenant_id == TENANT


def test_revoke_unknown_key_is_404(db_session):
    r = _client(db_session).post(URL.format("nope"), json={"reason": "x"})
    assert r.status_code == 404


def test_revoke_requires_superuser(db_session):
    _license(db_session, key="K2")
    r = _client(db_session, superuser=False).post(URL.format("K2"), json={"reason": "x"})
    assert r.status_code == 403
    # not revoked
    assert db_session.query(AppLicense).filter_by(license_key="K2").one().status == "Active"


def test_revoke_requires_reason(db_session):
    _license(db_session, key="K3")
    r = _client(db_session).post(URL.format("K3"), json={})  # missing reason -> 422
    assert r.status_code == 422


def test_revoke_is_idempotent(db_session):
    _license(db_session, key="K4", status="Revoked")
    r = _client(db_session).post(URL.format("K4"), json={"reason": "again"})
    assert r.status_code == 200 and r.json()["revoked"][0]["status"] == "Revoked"


def test_revoke_does_not_create_a_quota_or_clear_cap(db_session):
    # append-only: revoke touches AppLicense.status + an audit row only — no seat-cap write
    # (TenantQuota lives in the identity DB and is intentionally untouched here).
    _license(db_session, key="K5")
    _client(db_session).post(URL.format("K5"), json={"reason": "x"})
    # the only side effects are the status flip + one audit row
    assert db_session.query(AuditLog).count() == 1


def test_router_exposes_exactly_one_route():
    assert len(license_revoke_router.routes) == 1
