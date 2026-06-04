"""PLM-COLLAB-P2-C: ECO approval-automation governed projection + notify action.

A minimal FastAPI app mounts only ``approval_automation_eco_router`` with ``get_db``
overridden to an in-memory SQLite session and ``get_current_user`` overridden to drive
the auth/admin gates (so the REAL require_admin_user role logic runs). The full schema
is created (the ECO FK web) by importing the app to register all models, then
``create_all``. Tenancy is single-mode "default".

Pins (the owner-listed test obligations):
- GET unauthenticated -> 401.
- GET unentitled -> does NOT leak ECO existence (context: null, identical for an
  existing and a non-existent ECO; the ECO is never queried).
- GET entitled -> read-only snapshot (and writable authoritative fields are absent).
- POST unauthenticated -> 401; non-admin -> 403; admin + unentitled -> 403 AND zero
  audit; illegal action -> 400; non-existent ECO -> 404 + zero audit.
- POST notify -> stubbed result, writes ONE AuditLog row, mutates neither ECO state
  nor approval.
- the router exposes exactly the 2 routes (app-level 697 pinned elsewhere).
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the app registers ALL ORM models on Base.metadata (the ECO FK web), so
# create_all below builds the full schema -- the ECO has FKs to items/versions/stages.
from yuantus.api.app import create_app  # noqa: F401  (import side-effect: model registration)
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.audit import AuditLog
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.models.eco import ECO, ECOApproval
from yuantus.meta_engine.web.approval_automation_eco_router import approval_automation_eco_router

SKU_APP = "plm.approval_automation"
TENANT = "default"

_ADMIN = type("_AdminUser", (), {"id": 1, "roles": ["admin"], "is_superuser": True})()
_NONADMIN = type("_PlainUser", (), {"id": 2, "roles": [], "is_superuser": False})()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)  # full schema (ECO FK web is satisfied)
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


def _client(db_session, *, user="admin"):
    app = FastAPI()
    app.include_router(approval_automation_eco_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    if user == "admin":
        app.dependency_overrides[get_current_user] = lambda: _ADMIN
    elif user == "nonadmin":
        app.dependency_overrides[get_current_user] = lambda: _NONADMIN
    elif user == "unauth":
        def _unauth():
            raise HTTPException(status_code=401, detail="Unauthorized")

        app.dependency_overrides[get_current_user] = _unauth
    return TestClient(app)


def _add_license(db_session, *, app_name=SKU_APP):
    db_session.add(
        AppLicense(
            id="lic1",
            app_name=app_name,
            license_key="key1",
            status="Active",
            tenant_id=TENANT,
        )
    )
    db_session.commit()


def _make_eco(db_session, *, eco_id="E1", state="review"):
    db_session.add(
        ECO(id=eco_id, name="Test ECO", eco_type="bom", state=state, priority="high")
    )
    db_session.add(
        ECOApproval(
            id="A1", eco_id=eco_id, stage_id="S1", status="pending", approval_type="mandatory"
        )
    )
    db_session.commit()


# --- GET projection -----------------------------------------------------------

def test_get_unauthenticated_is_401(db_session):
    _add_license(db_session)
    _make_eco(db_session)
    r = _client(db_session, user="unauth").get("/api/v1/approvals/automation/eco/E1/context")
    assert r.status_code == 401


def test_get_unentitled_does_not_leak_eco_existence(db_session):
    # No license. An EXISTING ECO and a NON-existent one must return the SAME null
    # affordance -- the ECO is never queried, so existence cannot be inferred.
    _make_eco(db_session, eco_id="E1")
    client = _client(db_session, user="admin")  # authenticated but unentitled
    existing = client.get("/api/v1/approvals/automation/eco/E1/context").json()
    missing = client.get("/api/v1/approvals/automation/eco/E999/context").json()
    assert existing == missing
    assert existing["entitled"] is False
    assert existing["upgrade"]["available"] is True
    assert existing["context"] is None


def test_get_entitled_returns_readonly_snapshot(db_session):
    _add_license(db_session)
    _make_eco(db_session, eco_id="E1", state="review")
    body = _client(db_session).get("/api/v1/approvals/automation/eco/E1/context").json()
    assert body["entitled"] is True
    ctx = body["context"]
    assert ctx["eco_id"] == "E1"
    assert ctx["state"] == "review"
    assert ctx["sync_status"] == "snapshot"
    assert ctx["template_key"] == "eco_approval"
    assert {a["status"] for a in ctx["approvals"]} == {"pending"}
    # writable PLM authoritative machinery must NOT be projected
    assert "source_version_id" not in ctx
    assert "target_version_id" not in ctx


def test_get_entitled_missing_eco_is_404(db_session):
    _add_license(db_session)
    r = _client(db_session).get("/api/v1/approvals/automation/eco/NOPE/context")
    assert r.status_code == 404


# --- POST notify action -------------------------------------------------------

def test_post_unauthenticated_is_401(db_session):
    _add_license(db_session)
    _make_eco(db_session)
    r = _client(db_session, user="unauth").post(
        "/api/v1/approvals/automation/eco/E1/actions", json={"action": "notify"}
    )
    assert r.status_code == 401


def test_post_non_admin_is_403(db_session):
    _add_license(db_session)
    _make_eco(db_session)
    r = _client(db_session, user="nonadmin").post(
        "/api/v1/approvals/automation/eco/E1/actions", json={"action": "notify"}
    )
    assert r.status_code == 403


def test_post_admin_unentitled_is_403_and_zero_audit(db_session):
    _make_eco(db_session)
    r = _client(db_session, user="admin").post(
        "/api/v1/approvals/automation/eco/E1/actions", json={"action": "notify"}
    )
    assert r.status_code == 403
    assert r.json()["detail"]["upgrade"]["available"] is True
    assert db_session.query(AuditLog).count() == 0


def test_post_illegal_action_is_400(db_session):
    _add_license(db_session)
    _make_eco(db_session)
    r = _client(db_session, user="admin").post(
        "/api/v1/approvals/automation/eco/E1/actions", json={"action": "frobnicate"}
    )
    assert r.status_code == 400
    assert db_session.query(AuditLog).count() == 0


def test_post_notify_nonexistent_eco_is_404_zero_audit(db_session):
    _add_license(db_session)
    r = _client(db_session, user="admin").post(
        "/api/v1/approvals/automation/eco/NOPE/actions", json={"action": "notify"}
    )
    assert r.status_code == 404
    assert db_session.query(AuditLog).count() == 0


def test_post_notify_stub_writes_audit_without_mutating_eco(db_session):
    _add_license(db_session)
    _make_eco(db_session, eco_id="E1", state="review")
    r = _client(db_session, user="admin").post(
        "/api/v1/approvals/automation/eco/E1/actions", json={"action": "notify"}
    )
    assert r.status_code == 200
    assert r.json() == {
        "accepted": True,
        "dispatch_status": "stubbed",
        "channel": "dingtalk",
        "stub": True,
    }
    # exactly one audit row, and the ECO + approval are untouched
    audits = db_session.query(AuditLog).all()
    assert len(audits) == 1 and audits[0].method == "NOTIFY"
    db_session.expire_all()
    assert db_session.get(ECO, "E1").state == "review"
    assert db_session.get(ECOApproval, "A1").status == "pending"


def test_router_exposes_exactly_two_routes():
    assert len(approval_automation_eco_router.routes) == 2
