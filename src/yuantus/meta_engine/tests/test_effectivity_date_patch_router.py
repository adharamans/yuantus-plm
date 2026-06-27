"""Effectivity date PATCH route (L3-1): PATCH /api/v1/effectivities/{id}.

Real EffectivityService over in-memory SQLite (create_app + get_db override). The
latest-released / suspended **target guards** have their own tests, so they are
monkeypatched to no-op here — what's under test is the v1-narrow window-edit logic:
Date-only, elapsed-window 409 guard, start>=end 400, 404, no-fields 400.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.models.base import Base

_USER = SimpleNamespace(id=1, roles=["user"], is_superuser=False)
_URL = "/api/v1/effectivities/{}"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture(autouse=True)
def _skip_target_guards(monkeypatch):
    # latest-released / suspended guards have dedicated tests; no-op them so this test
    # exercises the window-edit + elapsed-window logic without full Item lifecycle setup.
    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_latest_released",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_not_suspended",
        lambda *a, **k: None,
    )
    yield


@pytest.fixture()
def Session():
    from yuantus.meta_engine.bootstrap import import_all_models

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


def _eff(db, *, effectivity_type="Date", start_date=None, end_date=None, item_id="it-1"):
    e = Effectivity(
        id=str(uuid.uuid4()),
        item_id=item_id,
        version_id=None,
        effectivity_type=effectivity_type,
        start_date=start_date,
        end_date=end_date,
        payload={},
    )
    db.add(e)
    db.commit()
    return e


def test_patch_future_window_updates_end_date(client, db):
    new_end = datetime.utcnow() + timedelta(days=20)
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.patch(_URL.format(e.id), json={"end_date": new_end.isoformat()})
    assert r.status_code == 200, r.text
    assert r.json()["end_date"][:16] == new_end.isoformat()[:16]


def test_patch_open_ended_window_allows_edit(client, db):
    # end_date IS NULL (open-ended) -> not elapsed -> editable
    e = _eff(db, start_date=datetime.utcnow(), end_date=None)
    new_end = (datetime.utcnow() + timedelta(days=5)).isoformat()
    assert client.patch(_URL.format(e.id), json={"end_date": new_end}).status_code == 200


def test_patch_elapsed_window_is_409(client, db):
    # existing end_date already in the past -> may have been swept -> 409, no un-expire
    e = _eff(
        db,
        start_date=datetime.utcnow() - timedelta(days=10),
        end_date=datetime.utcnow() - timedelta(days=3),
    )
    r = client.patch(
        _URL.format(e.id),
        json={"end_date": (datetime.utcnow() + timedelta(days=5)).isoformat()},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "effectivity_window_elapsed"


def test_patch_non_date_effectivity_is_400(client, db):
    e = _eff(db, effectivity_type="Lot", start_date=None, end_date=None)
    r = client.patch(
        _URL.format(e.id),
        json={"end_date": (datetime.utcnow() + timedelta(days=5)).isoformat()},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "effectivity_not_date"


def test_patch_start_after_end_is_400(client, db):
    fut = datetime.utcnow() + timedelta(days=10)
    e = _eff(db, start_date=datetime.utcnow(), end_date=fut)
    # move start_date PAST the existing future end_date -> start >= end
    r = client.patch(_URL.format(e.id), json={"start_date": (fut + timedelta(days=1)).isoformat()})
    assert r.status_code == 400


def test_patch_unknown_effectivity_is_404(client, db):
    r = client.patch(
        _URL.format("does-not-exist"),
        json={"end_date": (datetime.utcnow() + timedelta(days=5)).isoformat()},
    )
    assert r.status_code == 404


def test_patch_no_fields_is_400(client, db):
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    assert client.patch(_URL.format(e.id), json={}).status_code == 400


def test_patch_route_registered_and_owned():
    import yuantus.meta_engine.web.effectivity_router as mod

    app = create_app()
    route = next(
        r
        for r in app.routes
        if getattr(r, "path", "") == "/api/v1/effectivities/{effectivity_id}"
        and "PATCH" in getattr(r, "methods", set())
    )
    assert route.endpoint.__module__ == mod.__name__


def test_patch_target_not_latest_released_is_409(client, db, monkeypatch):
    # create-time protection is preserved on update: a non-latest-released target -> 409.
    from yuantus.meta_engine.services.latest_released_guard import NotLatestReleasedError

    def _raise(*a, **k):
        raise NotLatestReleasedError(reason="not latest-released", target_id="it-1")

    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_latest_released", _raise
    )
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.patch(
        _URL.format(e.id),
        json={"end_date": (datetime.utcnow() + timedelta(days=20)).isoformat()},
    )
    assert r.status_code == 409


def test_patch_target_suspended_is_409(client, db, monkeypatch):
    # create-time protection is preserved on update: a suspended target -> 409.
    from yuantus.meta_engine.services.suspended_guard import SuspendedStateError

    def _raise(*a, **k):
        raise SuspendedStateError(reason="suspended", target_id="it-1")

    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_not_suspended", _raise
    )
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.patch(
        _URL.format(e.id),
        json={"end_date": (datetime.utcnow() + timedelta(days=20)).isoformat()},
    )
    assert r.status_code == 409


# --- DELETE guards (create-time protection mirrored onto delete) -------------


def test_delete_happy_path_is_ok(client, db):
    # guards are no-op'd by the autouse fixture -> delete succeeds.
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.delete(_URL.format(e.id))
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    # gone afterwards -> a second delete is 404
    assert client.delete(_URL.format(e.id)).status_code == 404


def test_delete_unknown_effectivity_is_404(client):
    assert client.delete(_URL.format("does-not-exist")).status_code == 404


def test_delete_target_not_latest_released_is_409(client, db, monkeypatch):
    # create-time protection is mirrored on delete: a non-latest-released target -> 409.
    from yuantus.meta_engine.services.latest_released_guard import NotLatestReleasedError

    def _raise(*a, **k):
        raise NotLatestReleasedError(reason="not latest-released", target_id="it-1")

    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_latest_released", _raise
    )
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.delete(_URL.format(e.id))
    assert r.status_code == 409
    assert r.json()["detail"]["target_id"] == "it-1"


def test_delete_target_suspended_is_409(client, db, monkeypatch):
    # create-time protection is mirrored on delete: a suspended target -> 409.
    from yuantus.meta_engine.services.suspended_guard import SuspendedStateError

    def _raise(*a, **k):
        raise SuspendedStateError(reason="suspended", target_id="it-1")

    monkeypatch.setattr(
        "yuantus.meta_engine.services.effectivity_service.assert_not_suspended", _raise
    )
    e = _eff(db, start_date=datetime.utcnow(), end_date=datetime.utcnow() + timedelta(days=10))
    r = client.delete(_URL.format(e.id))
    assert r.status_code == 409
