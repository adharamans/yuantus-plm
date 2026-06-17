"""Tests for the async MES inbox mechanism (Consumption R2.5, unwired)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.models.item import Item  # noqa: F401  (mapper registration)
from yuantus.meta_engine.models.parallel_tasks import (
    ConsumptionPlan,
    ConsumptionRecord,
    MesConsumptionInbox,
)
from yuantus.meta_engine.services.consumption_mes_contract import MesConsumptionEvent
from yuantus.meta_engine.services.consumption_mes_inbox_service import (
    MesConsumptionInboxService,
)
from yuantus.meta_engine.services.parallel_tasks_service import ConsumptionPlanService
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser


@pytest.fixture()
def db():
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
    session = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _plan(db, uom="EA"):
    p = ConsumptionPlanService(db).create_plan(
        name="P", item_id="item-1", planned_quantity=10.0, uom=uom
    )
    db.commit()
    return p


def _event(plan_id, **ov):
    base = dict(plan_id=plan_id, mes_event_id="evt-1", actual_quantity=4.0)
    base.update(ov)
    return MesConsumptionEvent(**base)


def test_accept_is_idempotent(db):
    plan = _plan(db)
    svc = MesConsumptionInboxService(db)
    r1, d1 = svc.accept_event(_event(plan.id))
    r2, d2 = svc.accept_event(_event(plan.id))  # replay
    db.commit()
    assert d1 == "ACCEPTED" and d2 == "DUPLICATE"
    assert r1.id == r2.id
    assert db.query(MesConsumptionInbox).count() == 1


def test_accept_stores_raw_event(db):
    plan = _plan(db, uom="KG")
    svc = MesConsumptionInboxService(db)
    row, _ = svc.accept_event(_event(plan.id, uom="g", actual_quantity=1000.0))
    db.commit()
    assert row.uom == "g" and row.actual_quantity == 1000.0  # raw, not converted
    assert row.state == "pending"


def test_process_created_then_processed(db):
    plan = _plan(db)
    svc = MesConsumptionInboxService(db)
    row, _ = svc.accept_event(_event(plan.id))
    db.commit()
    state = svc.process_row(row)
    db.commit()
    assert state == "processed"
    assert row.record_id is not None
    assert db.query(ConsumptionRecord).count() == 1


def test_process_replay_does_not_double_count(db):
    # the same event drained twice -> one ConsumptionRecord (record-level idempotency)
    plan = _plan(db)
    svc = MesConsumptionInboxService(db)
    row, _ = svc.accept_event(_event(plan.id, actual_quantity=4.0))
    db.commit()
    svc.process_row(row)
    db.commit()
    # simulate a re-drain of the same logical event via a fresh inbox row
    row2 = MesConsumptionInbox(
        idempotency_key="other-key", plan_id=plan.id, mes_event_id="evt-1",
        source_type="mes", actual_quantity=4.0, state="pending",
    )
    db.add(row2); db.commit()
    state2 = svc.process_row(row2)
    db.commit()
    # same (plan, source_type, mes_event_id) -> record dedup -> processed (DUPLICATE), one row
    assert state2 == "processed"
    assert db.query(ConsumptionRecord).count() == 1
    assert ConsumptionPlanService(db).variance(plan.id)["actual_quantity"] == 4.0


def test_process_conflict_marks_conflict(db):
    plan = _plan(db)
    svc = MesConsumptionInboxService(db)
    r1, _ = svc.accept_event(_event(plan.id, mes_event_id="evt-1", actual_quantity=4.0))
    db.commit()
    svc.process_row(r1)
    db.commit()
    # a divergent same-key event (different qty)
    r2 = MesConsumptionInbox(
        idempotency_key="k2", plan_id=plan.id, mes_event_id="evt-1",
        source_type="mes", actual_quantity=9.0, state="pending",
    )
    db.add(r2); db.commit()
    state = svc.process_row(r2)
    db.commit()
    assert state == "conflict"
    assert db.query(ConsumptionRecord).count() == 1  # no second record


def test_process_missing_plan_retries_then_fails(db):
    svc = MesConsumptionInboxService(db)
    row = MesConsumptionInbox(
        idempotency_key="k", plan_id="ghost", mes_event_id="e",
        source_type="mes", actual_quantity=1.0, state="pending", max_attempts=2,
    )
    db.add(row); db.commit()
    assert svc.process_row(row) == "pending"  # attempt 1 -> retry
    db.commit()
    assert row.attempt_count == 1
    assert svc.process_row(row) == "failed"   # attempt 2 -> terminal
    db.commit()
    assert db.query(ConsumptionRecord).count() == 0


def test_claim_due_returns_pending(db):
    plan = _plan(db)
    svc = MesConsumptionInboxService(db)
    svc.accept_event(_event(plan.id))
    db.commit()
    due = svc.claim_due()
    assert len(due) == 1 and due[0].state == "pending"


def test_accept_rejects_reserved_key(db):
    # sync/async symmetry: a payload the sync route 400s (reserved _ingestion key)
    # must NOT be durably accepted -> accept raises (route maps to 400).
    plan = _plan(db)
    with pytest.raises(ValueError, match="reserved key"):
        MesConsumptionInboxService(db).accept_event(
            _event(plan.id, attributes={"_ingestion": {"x": 1}})
        )


def test_process_poison_row_fails_terminally(db):
    # a directly-inserted poison row (reserved key) must reach a TERMINAL state on
    # the first drain, never crash the worker / loop forever.
    row = MesConsumptionInbox(
        idempotency_key="poison", plan_id="p", mes_event_id="e", source_type="mes",
        actual_quantity=1.0, attributes={"_ingestion": {"x": 1}}, state="pending",
        max_attempts=5,
    )
    db.add(row); db.commit()
    assert MesConsumptionInboxService(db).process_row(row) == "failed"  # terminal


def test_process_uom_mismatch_is_conflict_not_silent(db):
    # async parity with the sync R2.1 guard: a declared uom that disagrees with
    # plan.uom must NOT be ingested with the wrong unit -> conflict, no record.
    plan = _plan(db, uom="KG")
    svc = MesConsumptionInboxService(db)
    row, _ = svc.accept_event(_event(plan.id, uom="EA", actual_quantity=1.0))
    db.commit()
    assert svc.process_row(row) == "conflict"
    db.commit()
    assert db.query(ConsumptionRecord).count() == 0  # not silently miscounted
