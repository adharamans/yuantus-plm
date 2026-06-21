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
from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.api.dependencies.auth import get_current_identity, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.lifecycle.models import LifecycleTransitionHistory
from yuantus.meta_engine.models.item import Item
from yuantus.models.base import Base

_USER = SimpleNamespace(id=1, roles=["user"], is_superuser=False)
_ADMIN = SimpleNamespace(id=9, roles=["superuser"], is_superuser=True)
_URL = "/api/v1/items/{}/transition-history"
_FORENSIC = "/api/v1/transition-history/forensic/{}"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # the auth-enforce middleware reads AUTH_MODE per request; "optional" lets the TestClient
    # through so the get_current_user override is what supplies the (authenticated) user.
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture(autouse=True)
def _perm_allow(monkeypatch):
    # The item-scoped read now does a per-item ACL (check_permission(item_type_id, get) -> 403,
    # matching bom_where_used/impact). Default-allow here so the existing read tests exercise the
    # 200 path; the deny -> 403 path has its own test that re-patches to False.
    monkeypatch.setattr(
        "yuantus.meta_engine.web.lifecycle_transition_history_router.MetaPermissionService",
        lambda db: SimpleNamespace(check_permission=lambda *a, **k: True),
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
    app.dependency_overrides[get_current_identity] = lambda: _ADMIN
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


def test_item_route_403_without_read_permission(client, db, monkeypatch):
    # per-item ACL (2a): a caller without get-permission on the item's type gets 403, not data.
    _item(db, "I")
    monkeypatch.setattr(
        "yuantus.meta_engine.web.lifecycle_transition_history_router.MetaPermissionService",
        lambda db: SimpleNamespace(check_permission=lambda *a, **k: False),
    )
    assert client.get(_URL.format("I")).status_code == 403


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


# -- forensic admin route (GET /api/v1/transition-history/forensic/{item_id}) --
def test_forensic_returns_deleted_item_history(client, db):
    # No _item(): the item never exists in the table (a *deleted* item). Its FK-free history rows
    # are still present and MUST be returned — the whole point of the forensic route.
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), to_state_name="B")
    r = client.get(_FORENSIC.format("GONE"))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [x["to_state_name"] for x in body["items"]] == ["B", "A"]  # created_at desc


def test_forensic_never_existed_id_is_empty_200_not_404(client, db):
    # Behavioral inverse of the item-scoped route's 404: a forensic lookup of an id with no
    # history is an empty 200, never 404 — this route deliberately does not resolve the item.
    r = client.get(_FORENSIC.format("neverwas"))
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_forensic_requires_superuser(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1))
    # a non-superuser identity must be rejected (require_superuser -> 403)
    client.app.dependency_overrides[get_current_identity] = lambda: SimpleNamespace(is_superuser=False)
    assert client.get(_FORENSIC.format("GONE")).status_code == 403


def _forensic_route():
    app = create_app()
    return next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/transition-history/forensic/{item_id}"
    )


def test_forensic_route_is_registered_and_owned():
    import yuantus.meta_engine.web.lifecycle_transition_history_router as mod

    route = _forensic_route()
    assert "GET" in route.methods
    assert route.endpoint.__module__ == mod.__name__  # owned by our router module


def test_forensic_route_is_admin_gated():
    # require_superuser must be wired; the bare get_current_user item-read pattern must NOT be.
    deps = [d.call for d in _forensic_route().dependant.dependencies]
    assert require_superuser in deps
    assert get_current_user not in deps
