"""Lifecycle transition-history read route (Slice 2):
GET /api/v1/items/{item_id}/transition-history.

Reads the audit rows written by promote() (Slice 1). Tests insert rows directly with explicit
distinct created_at (not via promote(), to keep this slice's tests decoupled from the write
path) and assert the most-recent-first order, item isolation, the empty-list vs 404 split, the
auth gate, and route ownership.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.lifecycle.models import LifecycleTransitionHistory
from yuantus.meta_engine.models.item import Item
from yuantus.models.base import Base

_USER = SimpleNamespace(id=1, roles=["user"], is_superuser=False)
_URL = "/api/v1/items/{}/transition-history"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # the auth-enforce middleware reads AUTH_MODE per request; "optional" lets the TestClient
    # through so the get_current_user override is what supplies the (authenticated) user.
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture()
def Session():
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.models import user as _user  # noqa: F401  - registers the 'users' FK target

    import_all_models()
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


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
    app.dependency_overrides[get_current_user] = lambda: _USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _item(db, iid):
    db.add(Item(id=iid, config_id=iid, item_type_id="t", state="Released", is_current=True, properties={}))
    db.commit()


def _hist(db, *, item_id, created_at, to_state_name="Released", **kw):
    row = LifecycleTransitionHistory(
        id=str(uuid.uuid4()), item_id=item_id, created_at=created_at,
        to_state_name=to_state_name, outcome="success", **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_returns_history_most_recent_first(client, db):
    _item(db, "I")
    # explicit, distinct created_at — order must follow created_at desc, NOT insertion order.
    _hist(db, item_id="I", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 3), to_state_name="C")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 2), to_state_name="B")
    body = client.get(_URL.format("I")).json()
    assert body["count"] == 3
    assert [r["to_state_name"] for r in body["items"]] == ["C", "B", "A"]


def test_item_isolation(client, db):
    _item(db, "I")
    _item(db, "J")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 1))
    _hist(db, item_id="J", created_at=datetime(2026, 6, 2))
    body = client.get(_URL.format("I")).json()
    assert body["count"] == 1 and all(r["item_id"] == "I" for r in body["items"])


def test_empty_list_for_existing_item_with_no_history(client, db):
    _item(db, "I")  # exists, but no history
    r = client.get(_URL.format("I"))
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_404_for_missing_item(client, db):
    assert client.get(_URL.format("ghost")).status_code == 404


def test_limit_caps_to_most_recent(client, db):
    _item(db, "I")
    for d in (1, 2, 3, 4):
        _hist(db, item_id="I", created_at=datetime(2026, 6, d), to_state_name=f"s{d}")
    body = client.get(_URL.format("I") + "?limit=2").json()
    assert body["count"] == 2
    assert [r["to_state_name"] for r in body["items"]] == ["s4", "s3"]  # most recent 2


def test_serializes_permission_actor_and_comment(client, db):
    _item(db, "I")
    _hist(
        db, item_id="I", created_at=datetime(2026, 6, 1), from_state_name="Draft",
        from_permission_id="p_draft", to_permission_id="p_rel", actor_user_id=7, comment="go",
    )
    row = client.get(_URL.format("I")).json()["items"][0]
    assert row["from_permission_id"] == "p_draft" and row["to_permission_id"] == "p_rel"
    assert row["actor_user_id"] == 7 and row["comment"] == "go" and row["outcome"] == "success"


# -- route owner / auth contracts ---------------------------------------------
def _the_route():
    app = create_app()
    return next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/items/{item_id}/transition-history"
    )


def test_route_is_registered_and_owned():
    import yuantus.meta_engine.web.lifecycle_transition_history_router as mod

    route = _the_route()
    assert "GET" in route.methods
    assert route.endpoint.__module__ == mod.__name__  # owned by our router module


def test_route_is_auth_gated():
    # the get_current_user dependency must be wired (item-read auth pattern).
    deps = [d.call for d in _the_route().dependant.dependencies]
    assert get_current_user in deps
