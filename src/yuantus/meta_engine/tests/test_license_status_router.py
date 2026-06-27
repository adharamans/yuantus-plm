"""Admin license-status read route (L4-1): GET /api/v1/admin/license-status.

Mounts only ``license_status_router`` on a minimal FastAPI app with ``get_db``
overridden to in-memory SQLite and ``require_superuser`` overridden — exercises
the route without the full app lifespan (mirrors test_feature_router).

Pins: empty tenant -> 200 empty (no existence leak, not 404); active license ->
appears in licenses; raw license_data never exposed; blank tenant_id -> 400;
non-superuser -> 403; Cache-Control: no-store; exactly one route.
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
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.web.license_status_router import license_status_router
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

TENANT = "default"  # single-mode resolved tenant when no request context is set
URL = "/api/v1/admin/license-status"


@pytest.fixture
def db_session():
    # StaticPool + check_same_thread=False: FastAPI runs sync handlers in a
    # threadpool, so the in-memory SQLite session must be usable across threads.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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
def _single_mode(monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(db_session, *, superuser=True):
    app = FastAPI()
    app.include_router(license_status_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    if superuser:
        app.dependency_overrides[require_superuser] = lambda: object()
    else:
        def _deny():
            raise HTTPException(status_code=403, detail="superuser required")

        app.dependency_overrides[require_superuser] = _deny
    return TestClient(app)


def _add_license(db_session, *, app_name="plm.collab"):
    db_session.add(
        AppLicense(
            id=uuid.uuid4().hex,
            app_name=app_name,
            license_key=uuid.uuid4().hex,
            status="Active",
            tenant_id=TENANT,
        )
    )
    db_session.commit()


def test_tenant_without_licenses_is_empty_200_not_404(db_session):
    r = _client(db_session).get(URL, params={"tenant_id": TENANT})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == TENANT
    assert body["licenses"] == []
    # no existence leak: an unlicensed tenant is all-false features, not a 404
    assert body["features"] and all(v is False for v in body["features"].values())


def test_active_license_appears_in_licenses(db_session):
    _add_license(db_session)
    body = _client(db_session).get(URL, params={"tenant_id": TENANT}).json()
    assert any(row["app_name"] == "plm.collab" for row in body["licenses"])


def test_never_exposes_raw_license_data(db_session):
    _add_license(db_session)
    body = _client(db_session).get(URL, params={"tenant_id": TENANT}).json()
    assert body["licenses"]
    for row in body["licenses"]:
        assert "license_data" not in row  # field whitelist only


def test_blank_tenant_id_is_400(db_session):
    r = _client(db_session).get(URL, params={"tenant_id": "   "})
    assert r.status_code == 400


def test_requires_superuser(db_session):
    r = _client(db_session, superuser=False).get(URL, params={"tenant_id": TENANT})
    assert r.status_code == 403


def test_sets_cache_control_no_store(db_session):
    r = _client(db_session).get(URL, params={"tenant_id": TENANT})
    assert r.headers.get("Cache-Control") == "no-store"


def test_router_exposes_exactly_one_route():
    assert len(license_status_router.routes) == 1
