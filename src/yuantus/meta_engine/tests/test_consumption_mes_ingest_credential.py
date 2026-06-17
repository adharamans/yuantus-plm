"""Auth-boundary tests for the dedicated MES ingest credential (Consumption R2.2).

Covers the owner-ratified guarantees + the three implementation watch-points:
fail-closed 503, 401 on bad/missing credential, tenant pinned from CONFIG (not the
x-tenant-id header) and set BEFORE the session is created, and auth resolved before
any plan read (no existence-probing). Cross-tenant *schema* isolation is a Postgres
runtime property and is NOT asserted here (SQLite single-mode does not switch schemas).
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies import mes_ingest_auth
from yuantus.context import org_id_var, tenant_id_var
from yuantus.meta_engine.models.item import Item  # noqa: F401  (mapper registration)
from yuantus.meta_engine.models.parallel_tasks import (
    ConsumptionPlan,
    ConsumptionRecord,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_USER = "mes-user"
_SECRET = "mes-secret"
_TENANT = "tenant-X"


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=eng,
        tables=[RBACUser.__table__, ConsumptionPlan.__table__, ConsumptionRecord.__table__],
    )
    return eng


@pytest.fixture()
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def seed_plan(Session):
    s = Session()
    plan = ConsumptionPlanService(s).create_plan(
        name="P", item_id="item-1", planned_quantity=10.0
    )
    s.commit()
    pid = plan.id
    s.close()
    return pid


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _configure(monkeypatch, Session, *, user=_USER, secret=_SECRET, tenant=_TENANT, org=""):
    monkeypatch.setattr(
        mes_ingest_auth,
        "get_settings",
        lambda: SimpleNamespace(
            MES_INGEST_USER=user,
            MES_INGEST_SECRET=secret,
            MES_INGEST_TENANT_ID=tenant,
            MES_INGEST_ORG_ID=org,
        ),
    )

    @contextlib.contextmanager
    def _fake_session():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(mes_ingest_auth, "get_db_session", _fake_session)


def _url(plan_id):
    return f"/api/v1/consumption/plans/{plan_id}/mes-actuals"


def _body(plan_id):
    return {"plan_id": plan_id, "mes_event_id": "evt-1", "actual_quantity": 4.0}


def _hdr(user=_USER, secret=_SECRET):
    return {"X-MES-Ingest-User": user, "X-MES-Ingest-Secret": secret}


# --- fail-closed (503) ------------------------------------------------------
def test_unconfigured_secret_is_503(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session, secret="")
    resp = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr())
    assert resp.status_code == 503


def test_unconfigured_tenant_is_503(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session, tenant="")
    resp = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr())
    assert resp.status_code == 503


# --- auth (401) -------------------------------------------------------------
def test_missing_headers_is_401(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    resp = client.post(_url(seed_plan), json=_body(seed_plan))
    assert resp.status_code == 401


def test_wrong_secret_is_401(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    resp = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr(secret="nope"))
    assert resp.status_code == 401


def test_wrong_user_is_401(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    resp = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr(user="nope"))
    assert resp.status_code == 401


def test_no_mes_headers_not_bypassed_by_bearer(client, monkeypatch, Session, seed_plan):
    # the route is machine-only now: a normal bearer/user session never satisfies it.
    _configure(monkeypatch, Session)
    resp = client.post(
        _url(seed_plan), json=_body(seed_plan), headers={"Authorization": "Bearer x"}
    )
    assert resp.status_code == 401


# --- success ----------------------------------------------------------------
def test_valid_credential_ingests(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    resp = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr())
    assert resp.status_code == 200, resp.text
    assert resp.json()["disposition"] == "CREATED"


# --- watch-point 3: no existence-probing ------------------------------------
def test_bad_cred_same_401_for_real_and_fake_plan(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    real = client.post(_url(seed_plan), json=_body(seed_plan), headers=_hdr(secret="x"))
    fake = client.post(_url("ghost-plan"), json=_body("ghost-plan"), headers=_hdr(secret="x"))
    assert real.status_code == 401 and fake.status_code == 401  # no existence leak


# --- secret hygiene ---------------------------------------------------------
def test_secret_never_echoed(client, monkeypatch, Session, seed_plan):
    _configure(monkeypatch, Session)
    resp = client.post(
        _url(seed_plan), json=_body(seed_plan), headers=_hdr(secret="supersecret-xyz")
    )
    assert resp.status_code == 401
    assert "supersecret-xyz" not in resp.text
    assert _SECRET not in resp.text  # configured secret never leaked either


# --- watch-point 2: tenant from config, set BEFORE the session --------------
def test_dependency_pins_config_tenant_before_session(monkeypatch, Session):
    _configure(monkeypatch, Session, org="org-Y")
    seen = {}

    @contextlib.contextmanager
    def _spy_session():
        seen["tenant_at_creation"] = tenant_id_var.get()
        seen["org_at_creation"] = org_id_var.get()
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(mes_ingest_auth, "get_db_session", _spy_session)
    prior = tenant_id_var.get()
    gen = mes_ingest_auth.require_mes_ingest_credential(
        x_mes_ingest_user=_USER, x_mes_ingest_secret=_SECRET
    )
    next(gen)  # advance to the yield
    # watch-point 2: the contextvar was set to CONFIG *before* the session was
    # created (the spy captured it at creation) -- so the session's schema is the
    # bound tenant's. The contextvar is then reset (it must not cross the yield).
    assert seen["tenant_at_creation"] == _TENANT
    assert seen["org_at_creation"] == "org-Y"
    assert tenant_id_var.get() == prior  # restored, no leak across the yield
    with pytest.raises(StopIteration):
        next(gen)  # close the session
    assert tenant_id_var.get() == prior


def test_non_ascii_credential_is_clean_reject_not_typeerror(monkeypatch, Session):
    # Attacker-controlled header bytes decode (latin-1) to a non-ASCII str.
    # secrets.compare_digest(str, str) raises TypeError on non-ASCII; the bytes
    # compare must instead reject cleanly (-> 401), never raise an unhandled 500.
    assert mes_ingest_auth._credential_ok("éwrong", "mes-secret") is False  # no raise
    _configure(monkeypatch, Session)
    with pytest.raises(HTTPException) as ei:
        gen = mes_ingest_auth.require_mes_ingest_credential(
            x_mes_ingest_user=_USER, x_mes_ingest_secret="éwrong"
        )
        next(gen)
    assert ei.value.status_code == 401


def test_header_tenant_is_overridden_by_config(monkeypatch, Session):
    # even if the middleware pre-set tenant_id_var from x-tenant-id, the bound
    # CONFIG tenant wins at session creation (and the prior value is restored).
    _configure(monkeypatch, Session, tenant="tenant-CONFIG")
    seen = {}

    @contextlib.contextmanager
    def _spy_session():
        seen["tenant_at_creation"] = tenant_id_var.get()
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(mes_ingest_auth, "get_db_session", _spy_session)
    token = tenant_id_var.set("tenant-EVIL-HEADER")
    try:
        gen = mes_ingest_auth.require_mes_ingest_credential(
            x_mes_ingest_user=_USER, x_mes_ingest_secret=_SECRET
        )
        next(gen)
        assert seen["tenant_at_creation"] == "tenant-CONFIG"  # not the spoofed header
        assert tenant_id_var.get() == "tenant-EVIL-HEADER"  # prior restored
        with pytest.raises(StopIteration):
            next(gen)
    finally:
        tenant_id_var.reset(token)
