"""Tests for the MES async wiring (Consumption R2.5b): route async-mode (default
off), the inbox ops routes, and the worker drain. The inbox accept/process
mechanism itself is covered by test_consumption_mes_inbox_service."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.api.dependencies.mes_ingest_auth import require_mes_ingest_credential
from yuantus.database import get_db
from yuantus.meta_engine.models.item import Item  # noqa: F401  (mapper registration)
from yuantus.meta_engine.models.parallel_tasks import (
    ConsumptionPlan,
    ConsumptionRecord,
    MesConsumptionInbox,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_ADMIN = SimpleNamespace(id=1, roles=["admin"], is_superuser=True)


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=eng,
        tables=[
            RBACUser.__table__,
            ConsumptionPlan.__table__,
            ConsumptionRecord.__table__,
            MesConsumptionInbox.__table__,
        ],
    )
    return eng


@pytest.fixture()
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(Session):
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def client(Session):
    app = create_app()

    def _override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    app.dependency_overrides[require_mes_ingest_credential] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _plan(db):
    p = ConsumptionPlanService(db).create_plan(name="P", item_id="i1", planned_quantity=10.0)
    db.commit()
    return p


def _url(pid):
    return f"/api/v1/consumption/plans/{pid}/mes-actuals"


def _body(pid):
    return {"plan_id": pid, "mes_event_id": "evt-1", "actual_quantity": 4.0}


def _async_on(monkeypatch, on=True):
    monkeypatch.setattr(
        "yuantus.meta_engine.web.parallel_tasks_consumption_router.get_settings",
        lambda: SimpleNamespace(MES_INGEST_ASYNC=on),
    )


# --- route async-mode -------------------------------------------------------
def test_async_off_is_synchronous_200(client, db, monkeypatch):
    _async_on(monkeypatch, on=False)
    plan = _plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id))
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "CREATED"
    assert db.query(MesConsumptionInbox).count() == 0  # sync path, no inbox


def test_async_on_returns_202_and_enqueues(client, db, monkeypatch):
    _async_on(monkeypatch, on=True)
    plan = _plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id))
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["disposition"] == "ACCEPTED" and body["state"] == "pending"
    assert db.query(MesConsumptionInbox).count() == 1
    assert db.query(ConsumptionRecord).count() == 0  # not processed yet


def test_async_accept_is_idempotent_202(client, db, monkeypatch):
    _async_on(monkeypatch, on=True)
    plan = _plan(db)
    client.post(_url(plan.id), json=_body(plan.id))
    resp = client.post(_url(plan.id), json=_body(plan.id))  # replay
    assert resp.status_code == 202 and resp.json()["disposition"] == "DUPLICATE"
    assert db.query(MesConsumptionInbox).count() == 1


# --- ops routes -------------------------------------------------------------
def _seed_inbox(db, state="pending"):
    row = MesConsumptionInbox(
        idempotency_key=f"k-{state}", plan_id="p1", mes_event_id="e",
        source_type="mes", actual_quantity=1.0, state=state,
    )
    db.add(row); db.commit()
    return row


def test_ops_list_and_filter(client, db):
    _seed_inbox(db, "pending")
    _seed_inbox(db, "failed")
    allr = client.get("/api/v1/consumption/mes-inbox")
    assert allr.status_code == 200 and allr.json()["count"] == 2
    failed = client.get("/api/v1/consumption/mes-inbox?state=failed")
    assert failed.json()["count"] == 1 and failed.json()["rows"][0]["state"] == "failed"


def test_ops_list_invalid_state_422(client, db):
    assert client.get("/api/v1/consumption/mes-inbox?state=bogus").status_code == 422


def test_ops_get_404(client, db):
    assert client.get("/api/v1/consumption/mes-inbox/ghost").status_code == 404


def test_ops_replay_failed_resets_to_pending(client, db):
    row = _seed_inbox(db, "failed")
    row.attempt_count = 3; db.commit()
    resp = client.post(f"/api/v1/consumption/mes-inbox/{row.id}/replay")
    assert resp.status_code == 200
    assert resp.json()["state"] == "pending" and resp.json()["attempt_count"] == 0


def test_ops_replay_non_failed_409(client, db):
    row = _seed_inbox(db, "processed")
    assert client.post(f"/api/v1/consumption/mes-inbox/{row.id}/replay").status_code == 409


# --- worker drain -----------------------------------------------------------
def test_drain_once_processes_pending(client, db, monkeypatch):
    from yuantus.meta_engine.services.consumption_mes_inbox_service import (
        MesConsumptionInboxService,
    )
    _async_on(monkeypatch, on=True)
    plan = _plan(db)
    client.post(_url(plan.id), json=_body(plan.id))  # enqueue
    n = MesConsumptionInboxService(db).drain_once()
    assert n == 1
    assert db.query(ConsumptionRecord).count() == 1  # drained -> record
    row = db.query(MesConsumptionInbox).one()
    assert row.state == "processed" and row.record_id is not None


def test_async_reserved_key_is_400_not_accepted(client, db, monkeypatch):
    # symmetry with the sync route: a reserved-key payload is 400, NOT durably
    # accepted into the inbox.
    _async_on(monkeypatch, on=True)
    plan = _plan(db)
    body = _body(plan.id)
    body["attributes"] = {"_ingestion": {"spoof": True}}
    resp = client.post(_url(plan.id), json=body)
    assert resp.status_code == 400
    assert db.query(MesConsumptionInbox).count() == 0


def test_async_uom_mismatch_drains_to_conflict_not_silent(client, db, monkeypatch):
    # the async path must NOT silently ingest a wrong-unit event (R2.1 parity):
    # accept 202, then the worker surfaces it as a conflict, no record.
    from yuantus.meta_engine.services.consumption_mes_inbox_service import (
        MesConsumptionInboxService,
    )
    _async_on(monkeypatch, on=True)
    plan = ConsumptionPlanService(db).create_plan(
        name="P", item_id="i1", planned_quantity=10.0, uom="KG"
    )
    db.commit()
    body = _body(plan.id)
    body["uom"] = "EA"  # disagrees with the KG plan
    assert client.post(_url(plan.id), json=body).status_code == 202
    MesConsumptionInboxService(db).drain_once()
    row = db.query(MesConsumptionInbox).one()
    assert row.state == "conflict"
    assert db.query(ConsumptionRecord).count() == 0
