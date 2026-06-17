"""Runtime tests for MES -> ConsumptionRecord ingestion (Consumption R2).

Two layers:

* service layer -- ``ConsumptionPlanService.ingest_mes_consumption`` drives the
  real idempotency enforcement against an in-memory SQLite session: CREATED,
  DUPLICATE (replay), CONFLICT (divergent ``actual_quantity`` / ``source_id``),
  the ``variance`` counts-once invariant, the persisted unique column, and the
  manual ``/actuals`` path staying NULL-keyed and never deduped.
* route layer -- the live ``POST /api/v1/consumption/plans/{plan_id}/mes-actuals``
  via ``TestClient`` for the HTTP contract: 200 CREATED/DUPLICATE, 409 conflict,
  404 missing plan, 400 plan_id mismatch / reserved-key, 422 invalid event.

The idempotency key is the R1 sha256(plan_id|source_type|mes_event_id); the
divergence comparison set is actual_quantity + source_id (source_id is NOT in
the key, so an attribution change must surface as a conflict, not be swallowed).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
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
)
from yuantus.meta_engine.services.consumption_mes_contract import (
    MesConsumptionEvent,
    derive_consumption_idempotency_key,
    map_mes_event_to_consumption_record_inputs,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_USER = SimpleNamespace(id=1, roles=["admin"], is_superuser=True)


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # The auth-enforce middleware runs before route deps; dependency_overrides
    # alone won't bypass it. AUTH_MODE=optional lets the overridden
    # get_current_user supply the principal (mirrors the ECM router tests).
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


# ---------------------------------------------------------------------------
# Shared in-memory engine (one DB shared by the direct session + TestClient).
# ---------------------------------------------------------------------------
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
        ],
    )
    return eng


@pytest.fixture()
def Session(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture()
def db(Session):
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(engine, Session):
    app = create_app()

    def _override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _USER
    # The mes-actuals route now authenticates via require_mes_ingest_credential
    # (which yields its own tenant-scoped session). These R2/R2.1 behavior tests
    # are not testing auth, so override it to yield the in-memory test session;
    # the dedicated credential auth is covered by test_consumption_mes_ingest_credential.
    app.dependency_overrides[require_mes_ingest_credential] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_plan(db, *, planned=10.0):
    service = ConsumptionPlanService(db)
    plan = service.create_plan(name="P", item_id="item-1", planned_quantity=planned)
    db.commit()
    return plan


def _event(plan_id, **overrides):
    base = dict(plan_id=plan_id, mes_event_id="evt-1", actual_quantity=4.0)
    base.update(overrides)
    return MesConsumptionEvent(**base)


def _ingest(db, event):
    service = ConsumptionPlanService(db)
    inputs = map_mes_event_to_consumption_record_inputs(event)
    record, disposition = service.ingest_mes_consumption(inputs)
    db.commit()
    return record, disposition


# ===========================================================================
# Service layer
# ===========================================================================
def test_first_ingest_is_created_and_persists_key(db):
    plan = _seed_plan(db)
    event = _event(plan.id)
    record, disposition = _ingest(db, event)
    assert disposition == "CREATED"
    assert record.idempotency_key == derive_consumption_idempotency_key(event)
    assert record.actual_quantity == 4.0
    # exactly one row
    assert db.query(ConsumptionRecord).count() == 1


def test_replay_same_event_is_duplicate_single_row(db):
    plan = _seed_plan(db)
    event = _event(plan.id)
    r1, d1 = _ingest(db, event)
    r2, d2 = _ingest(db, event)  # identical replay
    assert d1 == "CREATED" and d2 == "DUPLICATE"
    assert r2.id == r1.id  # the existing row, not a new one
    assert db.query(ConsumptionRecord).count() == 1


def test_replay_does_not_double_count_variance(db):
    plan = _seed_plan(db, planned=10.0)
    event = _event(plan.id, actual_quantity=4.0)
    _ingest(db, event)
    _ingest(db, event)  # at-least-once replay
    variance = ConsumptionPlanService(db).variance(plan.id)
    assert variance["records"] == 1
    assert variance["actual_quantity"] == 4.0  # counted once, not 8.0


def test_distinct_event_ids_create_distinct_rows(db):
    plan = _seed_plan(db)
    _ingest(db, _event(plan.id, mes_event_id="evt-1", actual_quantity=4.0))
    _ingest(db, _event(plan.id, mes_event_id="evt-2", actual_quantity=3.0))
    assert db.query(ConsumptionRecord).count() == 2
    assert ConsumptionPlanService(db).variance(plan.id)["actual_quantity"] == 7.0


def test_same_key_divergent_quantity_is_conflict_no_write(db):
    plan = _seed_plan(db)
    _ingest(db, _event(plan.id, mes_event_id="evt-1", actual_quantity=4.0))
    # same key (same plan/source_type/mes_event_id) but a different quantity
    service = ConsumptionPlanService(db)
    inputs = map_mes_event_to_consumption_record_inputs(
        _event(plan.id, mes_event_id="evt-1", actual_quantity=9.0)
    )
    record, disposition = service.ingest_mes_consumption(inputs)
    assert disposition == "CONFLICT"
    db.rollback()
    assert db.query(ConsumptionRecord).count() == 1
    assert db.query(ConsumptionRecord).one().actual_quantity == 4.0  # original kept


def test_same_key_divergent_source_id_is_conflict(db):
    plan = _seed_plan(db)
    _ingest(db, _event(plan.id, mes_event_id="evt-1", actual_quantity=4.0, source_id="wo-1"))
    service = ConsumptionPlanService(db)
    # identical quantity, but a different workorder attribution under the same key
    inputs = map_mes_event_to_consumption_record_inputs(
        _event(plan.id, mes_event_id="evt-1", actual_quantity=4.0, source_id="wo-2")
    )
    record, disposition = service.ingest_mes_consumption(inputs)
    assert disposition == "CONFLICT"
    db.rollback()
    assert db.query(ConsumptionRecord).one().source_id == "wo-1"  # not overwritten


def test_manual_actuals_path_is_never_deduped(db):
    plan = _seed_plan(db)
    service = ConsumptionPlanService(db)
    service.add_actual(plan_id=plan.id, actual_quantity=2.0, source_type="workorder")
    service.add_actual(plan_id=plan.id, actual_quantity=2.0, source_type="workorder")
    db.commit()
    rows = db.query(ConsumptionRecord).all()
    assert len(rows) == 2  # two identical manual entries both persisted
    assert all(r.idempotency_key is None for r in rows)  # NULL key on the manual path


def test_duplicate_preserves_caller_uncommitted_writes(db):
    # ingest_mes_consumption is SAVEPOINT-scoped: a DUPLICATE (or CONFLICT) must
    # roll back ONLY the failed insert, never the caller's outer transaction.
    # Probe: create plan + first ingest + a duplicate, all in ONE uncommitted tx.
    service = ConsumptionPlanService(db)
    plan = service.create_plan(name="P", item_id="item-1", planned_quantity=10.0)
    db.flush()  # pending in the outer tx, deliberately NOT committed
    event = _event(plan.id)
    _, d1 = service.ingest_mes_consumption(
        map_mes_event_to_consumption_record_inputs(event)
    )
    _, d2 = service.ingest_mes_consumption(
        map_mes_event_to_consumption_record_inputs(event)
    )
    assert d1 == "CREATED" and d2 == "DUPLICATE"
    # the uncommitted plan and the first record must survive the duplicate
    assert db.query(ConsumptionPlan).filter_by(id=plan.id).one_or_none() is not None
    assert db.query(ConsumptionRecord).count() == 1
    db.commit()
    assert db.query(ConsumptionRecord).count() == 1


def test_conflict_preserves_caller_uncommitted_writes(db):
    # Same SAVEPOINT guarantee on the CONFLICT branch.
    service = ConsumptionPlanService(db)
    plan = service.create_plan(name="P", item_id="item-1", planned_quantity=10.0)
    db.flush()  # uncommitted
    base = _event(plan.id, mes_event_id="evt-1", actual_quantity=4.0)
    _, d1 = service.ingest_mes_consumption(
        map_mes_event_to_consumption_record_inputs(base)
    )
    _, d2 = service.ingest_mes_consumption(
        map_mes_event_to_consumption_record_inputs(
            _event(plan.id, mes_event_id="evt-1", actual_quantity=9.0)
        )
    )
    assert d1 == "CREATED" and d2 == "CONFLICT"
    # the plan + original record survive; the divergent insert wrote nothing
    assert db.query(ConsumptionPlan).filter_by(id=plan.id).one_or_none() is not None
    assert db.query(ConsumptionRecord).count() == 1
    assert db.query(ConsumptionRecord).one().actual_quantity == 4.0


def test_missing_plan_raises_before_insert(db):
    service = ConsumptionPlanService(db)
    inputs = map_mes_event_to_consumption_record_inputs(_event("nope"))
    with pytest.raises(ValueError, match="not found"):
        service.ingest_mes_consumption(inputs)
    assert db.query(ConsumptionRecord).count() == 0


# ===========================================================================
# Route layer (HTTP contract)
# ===========================================================================
def _url(plan_id):
    return f"/api/v1/consumption/plans/{plan_id}/mes-actuals"


def _body(plan_id, **overrides):
    body = {"plan_id": plan_id, "mes_event_id": "evt-1", "actual_quantity": 4.0}
    body.update(overrides)
    return body


def test_route_created_then_duplicate(client, db):
    plan = _seed_plan(db)
    r1 = client.post(_url(plan.id), json=_body(plan.id))
    assert r1.status_code == 200, r1.text
    assert r1.json()["disposition"] == "CREATED"
    assert r1.json()["idempotency_key"]
    r2 = client.post(_url(plan.id), json=_body(plan.id))
    assert r2.status_code == 200
    assert r2.json()["disposition"] == "DUPLICATE"
    assert r2.json()["id"] == r1.json()["id"]


def test_route_conflict_returns_409(client, db):
    plan = _seed_plan(db)
    r1 = client.post(_url(plan.id), json=_body(plan.id, actual_quantity=4.0))
    resp = client.post(_url(plan.id), json=_body(plan.id, actual_quantity=9.0))
    assert resp.status_code == 409, resp.text
    # the 409 body must carry an actionable, stable shape for MES/operator tooling
    detail = resp.json()["detail"]
    assert detail["code"] == "consumption_mes_idempotency_conflict"
    ctx = detail["context"]
    assert ctx["idempotency_key"]
    assert ctx["existing_record_id"] == r1.json()["id"]
    assert ctx["plan_id"] == plan.id


def test_route_transient_db_error_is_retryable_503(client, db, monkeypatch):
    # A transient DB failure (deadlock/serialization) is NOT an IntegrityError,
    # so it reaches the route catch-all. It must be a retryable 5xx, never a 4xx
    # that would make an at-least-once producer drop the event (variance undercount).
    plan = _seed_plan(db)

    def _boom(self, inputs):
        raise OperationalError("INSERT ...", {}, Exception("deadlock detected"))

    monkeypatch.setattr(ConsumptionPlanService, "ingest_mes_consumption", _boom)
    resp = client.post(_url(plan.id), json=_body(plan.id))
    assert resp.status_code == 503, resp.text


def test_route_unexpected_error_is_500_not_400(client, db, monkeypatch):
    plan = _seed_plan(db)

    def _boom(self, inputs):
        raise RuntimeError("unexpected server-side failure")

    monkeypatch.setattr(ConsumptionPlanService, "ingest_mes_consumption", _boom)
    resp = client.post(_url(plan.id), json=_body(plan.id))
    assert resp.status_code == 500, resp.text


def test_route_plan_id_mismatch_400(client, db):
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body("other-plan"))
    assert resp.status_code == 400


def test_route_missing_plan_404(client):
    resp = client.post(_url("ghost"), json=_body("ghost"))
    assert resp.status_code == 404


def test_route_invalid_source_type_422(client, db):
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id, source_type="erp"))
    assert resp.status_code == 422


def test_route_negative_quantity_422(client, db):
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id, actual_quantity=-1.0))
    assert resp.status_code == 422


def test_route_reserved_attributes_key_400(client, db):
    plan = _seed_plan(db)
    resp = client.post(
        _url(plan.id), json=_body(plan.id, attributes={"_ingestion": {"x": 1}})
    )
    assert resp.status_code == 400


# --- uom reconciliation (R2.1) ---------------------------------------------
def test_route_declared_uom_mismatch_is_422(client, db):
    # plan default uom is "EA"; a declared divergent unit must be rejected, not
    # silently summed into variance.
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id, uom="kg"))
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "consumption_mes_uom_mismatch"
    assert detail["context"]["plan_uom"] == "EA"
    assert detail["context"]["event_uom"] == "kg"
    assert db.query(ConsumptionRecord).count() == 0  # nothing written


def test_route_matching_uom_is_accepted(client, db):
    # same unit (case-insensitive) is fine.
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id, uom="ea"))
    assert resp.status_code == 200, resp.text
    assert resp.json()["disposition"] == "CREATED"


def test_route_omitted_uom_is_lenient(client, db):
    # no declared uom -> implicitly the plan's unit; never rejected.
    plan = _seed_plan(db)
    resp = client.post(_url(plan.id), json=_body(plan.id))
    assert resp.status_code == 200, resp.text


def test_route_uom_mismatch_on_missing_plan_is_404_not_422(client):
    # a uom is declared but the plan does not exist -> 404 wins (no false 422).
    resp = client.post(_url("ghost"), json=_body("ghost", uom="kg"))
    assert resp.status_code == 404
