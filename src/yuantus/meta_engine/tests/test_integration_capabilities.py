"""PLM-COLLAB-P2.5 (Integration Handshake): the integration capability manifest.

A minimal FastAPI app mounts only ``integration_capabilities_router`` with ``get_db``
overridden to an in-memory SQLite session. UNGATED advisory surface (no auth). Tenancy
is single-mode "default".

Pins (the owner-ratified shape + invariants):
- top-level: schema_version "v1", provider "yuantus-plm", advisory true.
- approval_automation (lit): supported true; unentitled -> entitled false; entitled ->
  the rich descriptor (api_version "v1", scenarios [eco], actions [notify],
  action_status "stubbed").
- bom_multitable (lit, P3-B): supported true, api_version v1, scenarios [bom_review], NO
  actions; unentitled -> entitled false; entitled ONLY by plm.bom_multitable (not plm.collab).
- bom_multitable_writeback (lit, Phase-7): supported true, api_version v1,
  scenarios [bom_review], actions [line_patch], action_status governed; entitled ONLY by
  plm.bom_multitable_writeback (not the read SKU).
- every feature carries cache_scope {supported:"global", entitled:"tenant"}.
- ``supported`` is DERIVED from FEATURE_APP_NAMES lit-ness (not hardcoded).
- descriptor keys are a subset of FEATURE_APP_NAMES (so is_entitled never sees an
  unknown key).
- the router exposes exactly ONE route; the manifest code performs NO write.
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
from yuantus.meta_engine.app_framework.entitlement_service import FEATURE_APP_NAMES
from yuantus.meta_engine.app_framework.models import AppRegistry
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.services import integration_capabilities_service as svc_mod
from yuantus.meta_engine.services.integration_capabilities_service import _FEATURE_DESCRIPTORS
from yuantus.meta_engine.web import integration_capabilities_router as router_mod
from yuantus.meta_engine.web.integration_capabilities_router import (
    integration_capabilities_router,
)
from yuantus.security.rbac.models import RBACUser

SKU_APP = "plm.approval_automation"
TENANT = "default"
CAPS = "/api/v1/integrations/capabilities"


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
    app.include_router(integration_capabilities_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    return TestClient(app)


def _add_license(db_session, *, app_name=SKU_APP):
    db_session.add(
        AppLicense(id="lic1", app_name=app_name, license_key="key1", status="Active", tenant_id=TENANT)
    )
    db_session.commit()


def test_manifest_envelope_and_advisory_marker(db_session):
    body = _client(db_session).get(CAPS).json()
    assert body["schema_version"] == "v1"
    assert body["provider"] == "yuantus-plm"
    assert body["advisory"] is True  # NOT an authorization source -- a hint only


def test_unentitled_supported_but_not_entitled(db_session):
    feats = _client(db_session).get(CAPS).json()["features"]
    # approval_automation is lit -> supported; no license -> not entitled
    assert feats["approval_automation"]["supported"] is True
    assert feats["approval_automation"]["entitled"] is False
    # bom_multitable is lit (P3-B) -> supported with its read-only descriptor; no license
    # here -> not entitled. It is a READ surface, so it advertises NO actions.
    bm = feats["bom_multitable"]
    assert bm["supported"] is True
    assert bm["api_version"] == "v1"
    assert bm["scenarios"] == ["bom_review"]
    assert bm["entitled"] is False
    assert "actions" not in bm and "action_status" not in bm
    wb = feats["bom_multitable_writeback"]
    assert wb["supported"] is True
    assert wb["api_version"] == "v1"
    assert wb["scenarios"] == ["bom_review"]
    assert wb["actions"] == ["line_patch"]
    assert wb["action_status"] == "governed"
    assert wb["entitled"] is False


def test_entitled_emits_rich_descriptor(db_session):
    _add_license(db_session)
    feat = _client(db_session).get(CAPS).json()["features"]["approval_automation"]
    assert feat["entitled"] is True
    assert feat["api_version"] == "v1"
    assert feat["scenarios"] == ["eco"]
    assert feat["actions"] == ["notify"]
    assert feat["action_status"] == "stubbed"


def test_bom_multitable_entitled_by_its_own_sku_only(db_session):
    # P3-B: the bom_multitable SKU (plm.bom_multitable) entitles it; the read descriptor
    # carries scenarios [bom_review] and -- as a read surface -- no actions/action_status.
    _add_license(db_session, app_name="plm.bom_multitable")
    feats = _client(db_session).get(CAPS).json()["features"]
    bm = feats["bom_multitable"]
    assert bm["supported"] is True and bm["entitled"] is True
    assert bm["api_version"] == "v1"
    assert bm["scenarios"] == ["bom_review"]
    assert "actions" not in bm and "action_status" not in bm
    # independence: lighting/entitling bom_multitable did NOT entitle approval_automation
    assert feats["approval_automation"]["entitled"] is False
    # independence: the read SKU does NOT unlock the Phase-7 governed write feature
    assert feats["bom_multitable_writeback"]["entitled"] is False


def test_bom_multitable_writeback_entitled_by_its_own_sku_only(db_session):
    # Phase-7: the governed write feature is independent from the read projection.
    _add_license(db_session, app_name="plm.bom_multitable_writeback")
    feats = _client(db_session).get(CAPS).json()["features"]
    wb = feats["bom_multitable_writeback"]
    assert wb["supported"] is True and wb["entitled"] is True
    assert wb["api_version"] == "v1"
    assert wb["scenarios"] == ["bom_review"]
    assert wb["actions"] == ["line_patch"]
    assert wb["action_status"] == "governed"
    assert feats["bom_multitable"]["entitled"] is False
    assert feats["approval_automation"]["entitled"] is False


def test_bom_multitable_not_entitled_by_collab_license(db_session):
    # the plm.collab SKU must NOT entitle bom_multitable (independent SKU, not bundled).
    _add_license(db_session, app_name="plm.collab")
    bm = _client(db_session).get(CAPS).json()["features"]["bom_multitable"]
    assert bm["supported"] is True  # supported = lit-ness (tenant-independent)
    assert bm["entitled"] is False  # but a different SKU's license does not entitle it
    assert _client(db_session).get(CAPS).json()["features"]["bom_multitable_writeback"][
        "entitled"
    ] is False


def test_every_feature_declares_cache_scope(db_session):
    feats = _client(db_session).get(CAPS).json()["features"]
    for entry in feats.values():
        assert entry["cache_scope"] == {"supported": "global", "entitled": "tenant"}


def test_response_headers_prevent_cross_tenant_caching(db_session):
    # The body's cache_scope is metadata only -- enforcement is the HTTP headers, so an
    # intermediary never serves one tenant's `entitled` to another.
    r = _client(db_session).get(CAPS)
    assert r.headers["cache-control"] == "no-store"
    vary = r.headers["vary"]
    assert "Authorization" in vary
    assert "x-tenant-id" in vary  # settings.TENANT_HEADER (default)
    assert "x-org-id" in vary  # settings.ORG_HEADER (default)


def test_supported_is_derived_from_registry_not_hardcoded(db_session):
    feats = _client(db_session).get(CAPS).json()["features"]
    for key, entry in feats.items():
        assert entry["supported"] is bool(FEATURE_APP_NAMES.get(key))


def test_descriptor_keys_are_known_features(db_session):
    # Guards against a typo'd feature key that would make is_entitled raise ValueError.
    assert set(_FEATURE_DESCRIPTORS) <= set(FEATURE_APP_NAMES)


def test_router_exposes_exactly_one_route():
    assert len(integration_capabilities_router.routes) == 1


def test_manifest_code_performs_no_write():
    # Advisory + read-only: the manifest must never write or audit.
    forbidden = (".add(", ".commit(", "AuditLog")
    for mod in (svc_mod, router_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in src, f"{token!r} must not appear in {mod.__file__}"
