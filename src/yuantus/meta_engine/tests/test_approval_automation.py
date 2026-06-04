"""PLM-COLLAB-P2-A/P2-B: approval-automation feature gate + template provisioning.

A minimal FastAPI app mounts only ``approval_automation_router`` with ``get_db``
overridden to an in-memory SQLite session. The admin gate is driven by overriding
``get_current_user`` (so the REAL ``require_admin_user`` role logic runs). Tenancy is
single-mode "default".

Pins (incl. the owner-stated hard constraints):
- P2-A independent SKU: approval_automation is lit -> {"plm.approval_automation"};
  a plm.collab license does NOT unlock it (not bundled, not reusing collaboration_pro).
- GET /templates: never gated -- works even unauthenticated; unentitled still sees the
  3 definitions + upgrade.available True; entitled -> upgrade.available False.
- POST /provision identity gate (runs BEFORE entitlement): unauth -> 401,
  non-admin -> 403 "Admin role required", admin + unentitled -> 403 upgrade affordance.
- HARD #1 unentitled cannot provision: admin + unentitled -> 403, zero rows written;
  the service-level call raises EntitlementRequiredError (cannot bypass).
- HARD #2 provision idempotent: re-provision never duplicates -- exactly 3 drafts.
- P2-B provisions only DRAFT rows (never enabled/executed).
- the router exposes exactly the 2 routes (693 -> 695 at app level pinned elsewhere).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.models.approval_automation import ApprovalAutomationTemplate
from yuantus.meta_engine.services.approval_automation_service import (
    ApprovalAutomationService,
    EntitlementRequiredError,
)
from yuantus.meta_engine.web.approval_automation_router import approval_automation_router
from yuantus.security.rbac.models import RBACUser

FEATURE = "approval_automation"
SKU_APP = "plm.approval_automation"  # the independent SKU app_name
TENANT = "default"  # single-mode resolved tenant when no request context is set

# Duck-typed users -- require_admin_user only reads .roles and .is_superuser.
_ADMIN = type("_AdminUser", (), {"roles": ["admin"], "is_superuser": True})()
_NONADMIN = type("_PlainUser", (), {"roles": [], "is_superuser": False})()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            RBACUser.__table__,
            AppRegistry.__table__,
            AppLicense.__table__,
            ApprovalAutomationTemplate.__table__,
        ],
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


def _client(db_session, *, user="admin"):
    app = FastAPI()
    app.include_router(approval_automation_router, prefix="/api/v1")
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
            id=uuid.uuid4().hex,
            app_name=app_name,
            license_key=uuid.uuid4().hex,
            status="Active",
            tenant_id=TENANT,
        )
    )
    db_session.commit()


# --- P2-A independent SKU + GET affordance surface (ungated) -------------------

def test_templates_unentitled_offers_upgrade_and_lists_definitions(db_session):
    body = _client(db_session).get("/api/v1/approvals/automation/templates").json()
    assert body["feature_key"] == FEATURE
    assert body["entitled"] is False
    assert body["upgrade"]["available"] is True
    keys = {t["template_key"] for t in body["templates"]}
    assert keys == {"eco_approval", "bom_change_approval", "document_release_approval"}
    assert body["provisioned"] == []


def test_get_templates_is_ungated_even_unauthenticated(db_session):
    # GET is the upgrade-affordance surface -- no admin/identity gate.
    r = _client(db_session, user="unauth").get("/api/v1/approvals/automation/templates")
    assert r.status_code == 200
    assert r.json()["upgrade"]["available"] is True


def test_entitled_via_independent_sku(db_session):
    _add_license(db_session, app_name=SKU_APP)
    body = _client(db_session).get("/api/v1/approvals/automation/templates").json()
    assert body["entitled"] is True
    assert body["upgrade"]["available"] is False


def test_collab_license_does_not_unlock_approval_automation(db_session):
    # Independent SKU: a Collaboration Pro license (plm.collab) must NOT grant
    # approval automation -- it is separately sellable.
    _add_license(db_session, app_name="plm.collab")
    body = _client(db_session).get("/api/v1/approvals/automation/templates").json()
    assert body["entitled"] is False


# --- POST identity gate (runs BEFORE entitlement) -----------------------------

def test_provision_unauthenticated_is_401(db_session):
    # Even with a valid tenant license, an unauthenticated caller is rejected at the
    # identity gate -- 401, NOT a 403 upgrade -- proving the admin gate runs first.
    _add_license(db_session)
    r = _client(db_session, user="unauth").post("/api/v1/approvals/automation/provision")
    assert r.status_code == 401
    assert db_session.query(ApprovalAutomationTemplate).count() == 0


def test_provision_non_admin_is_403(db_session):
    _add_license(db_session)
    r = _client(db_session, user="nonadmin").post("/api/v1/approvals/automation/provision")
    assert r.status_code == 403
    assert r.json()["detail"] == "Admin role required"
    assert db_session.query(ApprovalAutomationTemplate).count() == 0


# --- HARD #1: entitlement gate (admin caller, unentitled tenant) ---------------

def test_admin_unentitled_provision_is_403_upgrade_and_writes_nothing(db_session):
    r = _client(db_session, user="admin").post("/api/v1/approvals/automation/provision")
    assert r.status_code == 403
    assert r.json()["detail"]["upgrade"]["available"] is True
    assert db_session.query(ApprovalAutomationTemplate).count() == 0


def test_service_provision_without_entitlement_raises_and_writes_nothing(db_session):
    # The write path itself is gated (defense-in-depth) -- a non-router caller
    # cannot bypass is_entitled.
    with pytest.raises(EntitlementRequiredError):
        ApprovalAutomationService(db_session).provision()
    assert db_session.query(ApprovalAutomationTemplate).count() == 0


# --- HARD #2: provision idempotent + P2-B draft-only --------------------------

def test_admin_entitled_provisions_three_drafts(db_session):
    _add_license(db_session)
    r = _client(db_session, user="admin").post("/api/v1/approvals/automation/provision")
    assert r.status_code == 200
    provisioned = r.json()["provisioned"]
    assert len(provisioned) == 3
    assert all(p["state"] == "draft" for p in provisioned)
    assert db_session.query(ApprovalAutomationTemplate).count() == 3


def test_provision_is_idempotent_no_duplicates(db_session):
    _add_license(db_session)
    client = _client(db_session, user="admin")
    client.post("/api/v1/approvals/automation/provision")
    client.post("/api/v1/approvals/automation/provision")
    # exactly 3 drafts despite re-provision -- the (tenant_id, template_key) unique
    # scope guarantees get-or-create, never duplicate.
    assert db_session.query(ApprovalAutomationTemplate).count() == 3


def test_provisioned_drafts_appear_in_templates_listing(db_session):
    _add_license(db_session)
    client = _client(db_session, user="admin")
    client.post("/api/v1/approvals/automation/provision")
    body = client.get("/api/v1/approvals/automation/templates").json()
    assert len(body["provisioned"]) == 3
    assert {p["template_key"] for p in body["provisioned"]} == {
        "eco_approval",
        "bom_change_approval",
        "document_release_approval",
    }


def test_router_exposes_exactly_two_routes():
    assert len(approval_automation_router.routes) == 2
