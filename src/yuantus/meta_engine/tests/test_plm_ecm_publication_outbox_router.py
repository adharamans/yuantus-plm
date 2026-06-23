"""Route-level tests for the PLM->ECM publication OUTBOX ops API (ECM-P1C).

The 3 routes (list / get / replay) drive the REAL outbox service against an
in-memory SQLite session. Gate order is admin -> is_entitled (both 403 before any
row read). replay is a PURE failed->pending reset (no adapter resend). Most tests
call the route functions directly; one TestClient test covers HTTP wiring + the
AUTH_MODE=optional path.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.ecm_publication.models import EcmPublicationOutbox
from yuantus.meta_engine.web.plm_ecm_publication_outbox_router import (
    get_publication_outbox,
    list_publication_outbox,
    replay_publication_outbox,
)
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_ADMIN = SimpleNamespace(id=1, roles=["admin"], is_superuser=False)
_VIEWER = SimpleNamespace(id=2, roles=["viewer"], is_superuser=False)


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture(autouse=True)
def _entitled(monkeypatch):
    # Default: entitled. Individual tests override to False.
    monkeypatch.setattr(EntitlementService, "is_entitled", lambda self, key: True)
    yield


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[RBACUser.__table__, EcmPublicationOutbox.__table__],
    )
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


def _row(db, *, state="pending", reason=None, attempt_count=0, file_role="native_cad"):
    r = EcmPublicationOutbox(
        id=uuid.uuid4().hex, item_id="P1", version_id="v1",
        file_id=f"f-{uuid.uuid4().hex[:8]}", file_role=file_role,
        target_system="athena", state=state, reason=reason, payload_fingerprint="fp",
        attempt_count=attempt_count, max_attempts=3,
        next_attempt_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    db.add(r)
    db.flush()
    return r


# --- gate --------------------------------------------------------------------
def test_list_denies_non_admin(db):
    with pytest.raises(HTTPException) as ei:
        list_publication_outbox(state=None, user=_VIEWER, db=db)
    assert ei.value.status_code == 403


def test_list_denies_when_not_entitled(db, monkeypatch):
    monkeypatch.setattr(EntitlementService, "is_entitled", lambda self, key: False)
    with pytest.raises(HTTPException) as ei:
        list_publication_outbox(state=None, user=_ADMIN, db=db)
    assert ei.value.status_code == 403
    assert "ecm_publish" in ei.value.detail


def test_get_404_when_missing(db):
    with pytest.raises(HTTPException) as ei:
        get_publication_outbox("missing", user=_ADMIN, db=db)
    assert ei.value.status_code == 404


# --- list / get --------------------------------------------------------------
def test_list_returns_all_then_filters_by_state(db):
    _row(db, state="pending", file_role="native_cad")
    _row(db, state="sent", file_role="drawing")
    _row(db, state="failed", reason="remote_error", file_role="geometry")
    db.commit()

    allr = list_publication_outbox(state=None, limit=200, user=_ADMIN, db=db)
    assert allr.count == 3

    pend = list_publication_outbox(state="pending", limit=200, user=_ADMIN, db=db)
    assert pend.count == 1 and pend.rows[0].state == "pending"


def test_list_filters_by_conflict_after_sent(db):
    _row(db, state="sent", file_role="native_cad")  # no conflict flag
    c = _row(db, state="sent", file_role="drawing")
    c.properties = {"conflict_after_sent": True, "conflict_fingerprint": "x"}
    db.flush()
    db.commit()

    allr = list_publication_outbox(state=None, conflict=None, limit=200, user=_ADMIN, db=db)
    assert allr.count == 2

    only = list_publication_outbox(state=None, conflict=True, limit=200, user=_ADMIN, db=db)
    assert only.count == 1
    assert (only.rows[0].properties or {}).get("conflict_after_sent") is True

    non = list_publication_outbox(state=None, conflict=False, limit=200, user=_ADMIN, db=db)
    assert non.count == 1
    assert (non.rows[0].properties or {}).get("conflict_after_sent") is None


def test_list_rejects_invalid_state_422(db):
    with pytest.raises(HTTPException) as ei:
        list_publication_outbox(state="bogus", limit=200, user=_ADMIN, db=db)
    assert ei.value.status_code == 422


def test_get_returns_row_with_per_file_identity(db):
    row = _row(db, state="sent")
    db.commit()
    resp = get_publication_outbox(row.id, user=_ADMIN, db=db)
    assert resp.id == row.id
    assert resp.file_id == row.file_id and resp.file_role == "native_cad"


# --- replay ------------------------------------------------------------------
def test_replay_failed_remote_error_resets_to_pending(db):
    row = _row(db, state="failed", reason="remote_error", attempt_count=3)
    db.commit()
    resp = replay_publication_outbox(row.id, user=_ADMIN, db=db)
    assert resp.state == "pending"
    assert resp.reason is None
    assert resp.attempt_count == 0  # reset for fresh retries
    db.refresh(row)
    assert row.state == "pending" and row.properties.get("replayed") is True


def test_replay_non_retryable_reason_409(db):
    row = _row(db, state="failed", reason="validation_error")
    db.commit()
    with pytest.raises(HTTPException) as ei:
        replay_publication_outbox(row.id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409


def test_replay_non_failed_state_409(db):
    row = _row(db, state="sent")
    db.commit()
    with pytest.raises(HTTPException) as ei:
        replay_publication_outbox(row.id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409


# --- HTTP wiring -------------------------------------------------------------
def test_http_list_wiring(db):
    _row(db, state="pending")
    db.commit()

    def override_get_db():
        yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    client = TestClient(app)
    resp = client.get("/api/v1/plm-ecm/publication-outbox")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
