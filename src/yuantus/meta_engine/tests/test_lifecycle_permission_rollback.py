"""Lifecycle promote() restores item.permission_id when a transition is rolled back.

promote() sets state-driven permission (item.permission_id = target_state.default_permission_id)
when entering a state, but the 3 failure rollback paths previously restored only state/
current_state — leaving a STALE permission that a caller could commit. This drives the real
promote() to the on_enter_state-abort rollback and asserts permission_id is restored to its
pre-transition value, plus a no-regression success case, plus a source check that all 3
rollback paths restore it.
"""
from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import yuantus.meta_engine.lifecycle.service as _svc_mod
from yuantus.meta_engine.lifecycle.hooks import HookType, hook_registry
from yuantus.meta_engine.lifecycle.models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
)
from yuantus.meta_engine.lifecycle.service import LifecycleService
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.models import user as _user  # noqa: F401  - registers the 'users' table (FK target)
from yuantus.models.base import Base

_ITEM_TYPE = "PR-Part"  # unique to this test so the abort hook can't touch other items
_SVC_TEXT = pathlib.Path(_svc_mod.__file__).read_text(encoding="utf-8")


@pytest.fixture()
def session():
    from yuantus.meta_engine.bootstrap import import_all_models

    import_all_models()
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    s.add(LifecycleMap(id="m", name="PR Lifecycle"))
    s.add(LifecycleState(id="s_draft", name="Draft", lifecycle_map_id="m", is_start_state=True))
    # Released carries a state-driven default permission, so entering it MUTATES item.permission_id.
    s.add(
        LifecycleState(
            id="s_rel", name="Released", lifecycle_map_id="m", is_released=True,
            default_permission_id="perm_released",
        )
    )
    s.add(LifecycleTransition(id="t", lifecycle_map_id="m", from_state_id="s_draft", to_state_id="s_rel"))
    s.add(ItemType(id=_ITEM_TYPE, label="PR Part", is_versionable=False, lifecycle_map_id="m"))
    s.commit()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def abort_on_enter():
    # Register an on_enter_state hook that aborts, then restore the registry (singleton) so
    # nothing leaks into other tests in the same process.
    key = f"{_ITEM_TYPE}:{HookType.ON_ENTER_STATE.value}"
    before = list(hook_registry._hooks.get(key, []))

    def _abort(ctx):
        ctx.abort = True
        ctx.abort_reason = "test: abort on enter"

    hook_registry.register(_ITEM_TYPE, HookType.ON_ENTER_STATE, _abort)
    try:
        yield
    finally:
        hook_registry._hooks[key] = before


def _draft_item(session, iid="i1"):
    item = Item(
        id=iid, config_id=iid, item_type_id=_ITEM_TYPE, state="Draft", current_state="s_draft",
        permission_id="perm_draft", is_current=True, properties={},
    )
    session.add(item)
    session.commit()
    return item


def test_permission_id_restored_on_rollback(session, abort_on_enter):
    item = _draft_item(session)
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    # the on_enter_state hook aborts AFTER the state-driven permission was set, so the
    # rollback must undo BOTH state and permission.
    assert res.success is False
    assert item.state == "Draft" and item.current_state == "s_draft"
    assert item.permission_id == "perm_draft"  # the fix: NOT the stale "perm_released"


def test_permission_id_updated_on_success(session):
    # no abort hook -> promote succeeds -> the state-driven permission is applied (proves the
    # fix only restores on FAILURE and does not disturb the normal set).
    item = _draft_item(session, "i2")
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    assert res.success is True
    assert item.state == "Released"
    assert item.permission_id == "perm_released"


def test_permission_id_restored_on_workflow_start_failure(session, monkeypatch):
    # path 2: a state whose linked workflow fails to start rolls back -> permission restored.
    from yuantus.meta_engine.workflow.models import WorkflowMap
    from yuantus.meta_engine.workflow.service import WorkflowService

    rel = session.get(LifecycleState, "s_rel")
    rel.workflow_map_id = "wfm"
    session.add(WorkflowMap(id="wfm", name="WF"))
    session.commit()

    def _boom(self, *a, **k):
        raise RuntimeError("workflow boom")

    monkeypatch.setattr(WorkflowService, "start_workflow", _boom)
    item = _draft_item(session, "i_wf")
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    assert res.success is False and "workflow" in res.error.lower()
    assert item.state == "Draft" and item.permission_id == "perm_draft"


def test_permission_id_restored_on_version_release_failure(session, monkeypatch):
    # path 3: entering Released triggers version release; if it fails, rollback -> restored.
    from yuantus.meta_engine.version.service import VersionService

    def _boom(self, *a, **k):
        raise RuntimeError("release boom")

    monkeypatch.setattr(VersionService, "release", _boom)
    item = _draft_item(session, "i_ver")
    item.current_version_id = "v1"  # skip create_initial_version; go straight to release
    session.commit()
    res = LifecycleService(session).promote(item, "Released", user_id=1)
    assert res.success is False and "version release" in res.error.lower()
    assert item.state == "Draft" and item.permission_id == "perm_draft"


def test_placeholder_comment_removed():
    # the old "restore old permission if necessary" placeholder must be gone (not brittle to
    # legitimate refactors, unlike a fixed restore-count).
    assert "restore old permission if necessary" not in _SVC_TEXT
