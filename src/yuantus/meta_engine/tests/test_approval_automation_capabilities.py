"""PLM-COLLAB-P2-D: ECO-scenario capability/upgrade entry.

A minimal FastAPI app mounts only ``approval_automation_capabilities_router`` with
``get_db`` overridden to an in-memory SQLite session. The route is UNGATED (no auth) --
it returns only the product capability / upgrade affordance, no PLM/ECO data, like the
P1-D feature status + the P2-B GET /templates affordance surfaces. Tenancy is
single-mode "default".

Pins (the owner-listed obligations):
- unentitled -> capability null + upgrade.available true + read-only upgrade hints.
- entitled -> capability with actions [notify] + eco_approval + endpoints + the
  action_status:"stubbed" marker.
- the router exposes exactly ONE route; it is registered in the decomposition contract.
- SOURCE-LEVEL boundary: the P2-D service + router contain NO ECO lookup (get_eco /
  ECOService / ECOApproval) and NO write (.add / .commit / AuditLog).
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.web import approval_automation_capabilities_router as router_mod
from yuantus.meta_engine.services import approval_automation_capabilities_service as svc_mod
from yuantus.meta_engine.web.approval_automation_capabilities_router import (
    approval_automation_capabilities_router,
)
from yuantus.security.rbac.models import RBACUser

SKU_APP = "plm.approval_automation"
TENANT = "default"
CAPS = "/api/v1/approvals/automation/eco/capabilities"


@pytest.fixture
def db_session():
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


def _client(db_session):
    app = FastAPI()
    app.include_router(approval_automation_capabilities_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
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


def test_unentitled_returns_null_capability_with_upgrade_hints(db_session):
    body = _client(db_session).get(CAPS).json()
    assert body["feature_key"] == "approval_automation"
    assert body["entitled"] is False
    assert body["capability"] is None
    assert body["upgrade"]["available"] is True
    # read-only upgrade hints; production stays on P1-C signed license
    assert body["upgrade"]["license_mode"] == "offline_signed"
    assert body["upgrade"]["mock_activation"] == "demo_only"


def test_entitled_returns_capability_descriptor(db_session):
    _add_license(db_session)
    body = _client(db_session).get(CAPS).json()
    assert body["entitled"] is True
    assert body["upgrade"]["available"] is False
    cap = body["capability"]
    assert cap["scenario"] == "eco"
    assert cap["actions"] == ["notify"]
    assert cap["action_status"] == "stubbed"  # notify is a placeholder, not live DingTalk
    assert cap["template_key"] == "eco_approval"
    assert cap["endpoints"]["context"] == "/api/v1/approvals/automation/eco/{eco_id}/context"
    assert cap["endpoints"]["actions"] == "/api/v1/approvals/automation/eco/{eco_id}/actions"


def test_router_exposes_exactly_one_route():
    assert len(approval_automation_capabilities_router.routes) == 1


def test_route_is_registered_in_decomposition_contract():
    from yuantus.meta_engine.tests.test_approvals_router_decomposition_closeout_contracts import (
        _EXPECTED_APPROVAL_ROUTE_OWNERS,
    )

    assert (
        _EXPECTED_APPROVAL_ROUTE_OWNERS.get(("GET", CAPS))
        == "yuantus.meta_engine.web.approval_automation_capabilities_router"
    )


def test_p2d_code_has_no_eco_lookup_or_write():
    # The scenario entry is pure affordance: it must not look up an ECO or write.
    forbidden = ("get_eco", "ECOService", "ECOApproval", "AuditLog", ".add(", ".commit(")
    for mod in (svc_mod, router_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{token!r} must not appear in {mod.__file__}"
