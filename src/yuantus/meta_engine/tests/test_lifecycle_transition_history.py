"""Lifecycle promote() records a durable transition-history row (Slice 1).

A successful promote() writes exactly one meta_lifecycle_transition_history row (actor /
from+to state / from+to permission / comment). Best-effort: a write failure — including a
flush-level DB error, isolated by a SAVEPOINT — is swallowed and never fails the transition.
Gated off by LIFECYCLE_TRANSITION_HISTORY_ENABLED=False; a rolled-back transition writes no
row. DB-free sqlite driving the real promote().
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import yuantus.meta_engine.lifecycle.models as _models
import yuantus.meta_engine.lifecycle.service as _service
from yuantus.config import get_settings
from yuantus.meta_engine.lifecycle.hooks import HookType, hook_registry
from yuantus.meta_engine.lifecycle.models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
    LifecycleTransitionHistory,
)
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.models import user as _user  # noqa: F401  - registers the 'users' table (FK target)
from yuantus.models.base import Base

_ITEM_TYPE = "TH-Part"


@pytest.fixture()
def session():
    from yuantus.meta_engine.bootstrap import import_all_models

    import_all_models()
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    s.add(LifecycleMap(id="m", name="TH Lifecycle"))
    s.add(LifecycleState(id="s_draft", name="Draft", lifecycle_map_id="m", is_start_state=True))
    s.add(
        LifecycleState(
            id="s_rel", name="Released", lifecycle_map_id="m", is_released=True,
            default_permission_id="perm_released",
        )
    )
    s.add(LifecycleTransition(id="t", lifecycle_map_id="m", from_state_id="s_draft", to_state_id="s_rel"))
    s.add(ItemType(id=_ITEM_TYPE, label="TH Part", is_versionable=False, lifecycle_map_id="m"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


def _draft_item(session, iid="i1"):
    # is_versionable=False so promote(Released) skips the version-release path and reaches the
    # history write directly (keeps these tests focused on the audit row).
    item = Item(
        id=iid, config_id=iid, item_type_id=_ITEM_TYPE, state="Draft", current_state="s_draft",
        permission_id="perm_draft", is_versionable=False, is_current=True, properties={},
    )
    session.add(item)
    session.commit()
    return item


def test_success_records_one_row(session):
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=7, comment="ship it")
    session.commit()
    assert res.success is True
    row = session.query(LifecycleTransitionHistory).one()
    assert row.item_id == item.id
    assert row.from_state_id == "s_draft" and row.from_state_name == "Draft"
    assert row.to_state_id == "s_rel" and row.to_state_name == "Released"
    assert row.from_permission_id == "perm_draft" and row.to_permission_id == "perm_released"
    assert row.transition_id == "t" and row.lifecycle_map_id == "m"
    assert row.actor_user_id == 7 and row.comment == "ship it" and row.outcome == "success"


def test_unvalidated_system_user_still_records(session):
    # actor_user_id is FK-free, so a system promote (user_id=0 / no such user) still records.
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=0)
    session.commit()
    assert res.success is True
    assert session.query(LifecycleTransitionHistory).one().actor_user_id == 0


def test_disabled_flag_writes_no_row(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "LIFECYCLE_TRANSITION_HISTORY_ENABLED", False)
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    session.commit()
    assert res.success is True
    assert session.query(LifecycleTransitionHistory).count() == 0


def test_construction_failure_is_best_effort(session, monkeypatch):
    # a history-write failure must never break the transition or poison the session.
    def _boom(*a, **k):
        raise RuntimeError("history boom")

    monkeypatch.setattr(_service, "LifecycleTransitionHistory", _boom)
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    session.commit()  # session not poisoned
    assert res.success is True
    assert session.get(Item, item.id).state == "Released"  # the state change persisted
    assert session.query(LifecycleTransitionHistory).count() == 0


def test_flush_failure_isolated_by_savepoint(session, monkeypatch):
    # a flush-level DB error (here a duplicate PK) is rolled back to the SAVEPOINT, so the
    # transition still commits and the session stays usable.
    monkeypatch.setattr(_models.uuid, "uuid4", lambda: "dup-id")
    session.add(LifecycleTransitionHistory(id="dup-id", item_id="pre", outcome="success"))
    session.commit()
    item = _draft_item(session, "i2")
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    session.commit()  # MUST succeed despite the duplicate-PK history insert
    assert res.success is True
    assert session.get(Item, item.id).state == "Released"
    assert session.query(LifecycleTransitionHistory).count() == 1  # only the pre-existing row


@pytest.fixture()
def abort_on_enter():
    key = f"{_ITEM_TYPE}:{HookType.ON_ENTER_STATE.value}"
    before = list(hook_registry._hooks.get(key, []))

    def _abort(ctx):
        ctx.abort = True
        ctx.abort_reason = "test abort"

    hook_registry.register(_ITEM_TYPE, HookType.ON_ENTER_STATE, _abort)
    try:
        yield
    finally:
        hook_registry._hooks[key] = before


def test_rolled_back_transition_writes_no_row(session, abort_on_enter, monkeypatch):
    # The SUCCESS write is skipped on a failed transition. The all-attempts FAILURE write (T2) goes
    # via a SEPARATE audit session (covered in test_lifecycle_transition_attempts.py); make it a
    # no-op here so this Slice-1 test stays focused on "the caller's session holds no row for a
    # rolled-back transition" and never touches the global session.
    import contextlib

    import yuantus.database as ydb

    @contextlib.contextmanager
    def _no_audit_session():
        raise RuntimeError("no audit session in this slice-1 test")
        yield  # pragma: no cover

    monkeypatch.setattr(ydb, "get_db_session", _no_audit_session)
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    session.commit()
    assert res.success is False
    assert session.query(LifecycleTransitionHistory).count() == 0
    assert session.get(Item, item.id).state == "Draft"


def test_business_flush_error_is_not_swallowed_by_audit_best_effort(session, monkeypatch):
    # P1 regression: the audit best-effort must scope only to the history row. A flush error
    # from the PRE-EXISTING business state (the state change) must propagate — NOT be mislabeled
    # a history-write failure and swallowed (which would return success on a rollback-pending
    # session and blow up the caller's commit). With the fix the business state is flushed
    # OUTSIDE the audit guard, so this error bubbles up.
    item = _draft_item(session)
    session.autoflush = False  # so the first flush reached is the explicit business flush
    monkeypatch.setattr(
        session, "flush", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("business flush boom"))
    )
    with pytest.raises(RuntimeError, match="business flush boom"):
        LifecycleService(session).promote(item, "Released", user_id=1)
