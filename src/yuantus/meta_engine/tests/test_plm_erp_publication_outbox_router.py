"""Route-level tests for the PLM->ERP publication OUTBOX API (G2 R2 HTTP routes).

The routes drive the REAL outbox service against an in-memory SQLite session, so
the state machine + error->HTTP mapping are exercised end to end; only
`build_publication_readiness` (the readiness verdict) and, where a failure is
needed, the adapter are patched. Most tests call the route functions directly
(no TestClient/middleware needed); one TestClient test covers HTTP wiring + the
AUTH_MODE=optional path (the recurring 401 trap).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.erp_publication.adapter import (
    NullErpPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.erp_publication.models import ErpPublicationOutbox
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.web import plm_erp_publication_outbox_router as outbox_mod
from yuantus.meta_engine.web.plm_erp_publication_outbox_router import (
    OutboxEnqueueRequest,
    dry_run_publication,
    enqueue_publication,
    get_publication_outbox,
    process_publication,
    replay_publication,
)
from yuantus.meta_engine.web.plm_erp_publication_router import (
    BlockingReason,
    EsignBlock,
    FileRef,
    ItemBlock,
    Limits,
    PublicationReadinessResponse,
    VersionBlock,
)
from yuantus.meta_engine.web.release_readiness_router import ReadinessSummary
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_MODULE = "yuantus.meta_engine.web.plm_erp_publication_outbox_router"
_ADMIN = SimpleNamespace(id=1, roles=["admin"], is_superuser=False)
_VIEWER = SimpleNamespace(id=2, roles=["viewer"], is_superuser=False)
_ITEM_ID = "ITEM-1"
TARGET = "erp-test"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # Disable AuthEnforcementMiddleware (TestClient path) — patch ONLY the
    # middleware's get_settings, never the global lru_cache (R1-B pattern).
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture()
def db():
    # StaticPool + check_same_thread=False: one shared connection so the
    # in-memory DB survives across the TestClient's request thread.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[RBACUser.__table__, Item.__table__, ErpPublicationOutbox.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    session.add(Item(id=_ITEM_ID, config_id="cfg-1", state="Released"))
    session.flush()
    try:
        yield session
    finally:
        session.close()


def _readiness(*, eligible=True, with_version=True, version_id="VER-1", file_role="native"):
    version = None
    file_refs = []
    if with_version:
        version = VersionBlock(
            version_id=version_id, generation=2, revision="B", version_label="B",
            state="Released", is_current=True, is_released=True,
            released_at="2026-05-28T00:00:00", primary_file_id="F1",
        )
        file_refs = [FileRef(file_id="F1", file_role=file_role, is_primary=True, sequence=1, snapshot_path="/p")]
    return PublicationReadinessResponse(
        item=ItemBlock(item_id=_ITEM_ID, lifecycle_state="Released"),
        version=version,
        eligible=eligible,
        generated_at=None,
        ruleset_id="readiness",
        limits=Limits(mbom_limit=20, routing_limit=20, baseline_limit=20),
        summary=ReadinessSummary(ok=eligible),
        resources=[],
        esign=EsignBlock(present=False, is_complete=None, completed_at=None),
        file_refs=file_refs,
        blocking_reasons=[] if eligible else [BlockingReason(reason="mbom_release", detail=None)],
    )


def _body(**kw):
    return OutboxEnqueueRequest(target_system=TARGET, **kw)


def _patch_readiness(*returns):
    """Patch build_publication_readiness in the outbox-router namespace."""
    m = MagicMock(side_effect=list(returns) if len(returns) > 1 else None)
    if len(returns) == 1:
        m.return_value = returns[0]
    return patch(f"{_MODULE}.build_publication_readiness", m), m


def _enqueue(db, readiness, **body_kw):
    p, _ = _patch_readiness(readiness)
    with p:
        return enqueue_publication(_ITEM_ID, _body(**body_kw), user=_ADMIN, db=db)


# --- auth + not-found --------------------------------------------------------


def test_enqueue_denies_non_admin(db):
    with pytest.raises(HTTPException) as ei:
        with _patch_readiness(_readiness())[0]:
            enqueue_publication(_ITEM_ID, _body(), user=_VIEWER, db=db)
    assert ei.value.status_code == 403


def test_enqueue_404_when_item_missing(db):
    with pytest.raises(HTTPException) as ei:
        enqueue_publication("nope", _body(), user=_ADMIN, db=db)
    assert ei.value.status_code == 404


def test_status_404_when_outbox_missing(db):
    with pytest.raises(HTTPException) as ei:
        get_publication_outbox("missing", user=_ADMIN, db=db)
    assert ei.value.status_code == 404


# --- enqueue -----------------------------------------------------------------


def test_enqueue_eligible_creates_pending_row(db):
    resp = _enqueue(db, _readiness(eligible=True))
    assert resp.persisted is True
    assert resp.state == "pending"
    assert resp.outbox is not None and resp.outbox.state == "pending"


def test_http_enqueue_rejects_empty_target_system(db):
    def override_get_db():
        yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    client = TestClient(app)
    with _patch_readiness(_readiness())[0]:
        resp = client.post(
            f"/api/v1/plm-erp/items/{_ITEM_ID}/publication-outbox/enqueue",
            json={"target_system": ""},
        )
    assert resp.status_code == 422


def test_enqueue_ineligible_is_skipped(db):
    resp = _enqueue(db, _readiness(eligible=False))
    assert resp.persisted is True
    assert resp.state == "skipped"
    assert resp.outbox.reason == "not_eligible"


def test_enqueue_versionless_200_persisted_false(db):
    resp = _enqueue(db, _readiness(with_version=False, eligible=False))
    assert resp.persisted is False
    assert resp.outbox is None
    assert resp.state == "skipped" and resp.reason == "not_eligible"


def test_enqueue_idempotent_reuse(db):
    r1 = _enqueue(db, _readiness())
    r2 = _enqueue(db, _readiness())
    assert r1.outbox.id == r2.outbox.id
    assert db.query(ErpPublicationOutbox).count() == 1


def test_enqueue_unknown_ruleset_chained_400(db):
    m = MagicMock(side_effect=ValueError("unknown ruleset"))
    with patch(f"{_MODULE}.build_publication_readiness", m):
        with pytest.raises(HTTPException) as ei:
            enqueue_publication(_ITEM_ID, _body(ruleset_id="bogus"), user=_ADMIN, db=db)
    assert ei.value.status_code == 400
    assert isinstance(ei.value.__cause__, ValueError)


def test_enqueue_integrityerror_race_reuses_row(db):
    # Simulate the concurrent-first-enqueue race: service.enqueue raises
    # IntegrityError once, then (after rollback) returns the committed row.
    from sqlalchemy.exc import IntegrityError

    existing = ErpPublicationOutbox(
        id="x", item_id=_ITEM_ID, version_id="VER-1", target_system=TARGET,
        publication_kind="readiness", state="pending",
    )
    svc = MagicMock()
    svc.enqueue.side_effect = [IntegrityError("dup", None, Exception()), existing]
    with _patch_readiness(_readiness())[0]:
        with patch(f"{_MODULE}.ErpPublicationOutboxService", return_value=svc):
            with patch.object(db, "rollback") as rb:
                resp = enqueue_publication(_ITEM_ID, _body(), user=_ADMIN, db=db)
    assert rb.called
    assert resp.persisted is True and resp.outbox.id == "x"


# --- dry-run -----------------------------------------------------------------


def test_dry_run_reaches_dry_run_ready(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    resp = dry_run_publication(row_id, user=_ADMIN, db=db)
    assert resp.state == "dry_run_ready"
    assert resp.dispatched_at is None


# --- process (the two #668 guards at the route level) ------------------------


def test_process_happy_sends_via_null_adapter(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    with _patch_readiness(_readiness())[0]:  # revalidate -> eligible
        resp = process_publication(row_id, user=_ADMIN, db=db)
    assert resp.state == "sent"
    assert (resp.properties or {}).get("remote_id", "").startswith("null:")


def test_GUARD1_process_on_skipped_is_409_no_resend(db):
    row = _enqueue(db, _readiness(eligible=False))  # skipped
    row_id = row.outbox.id
    with _patch_readiness(_readiness(eligible=False))[0]:
        with pytest.raises(HTTPException) as ei:
            process_publication(row_id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409
    # not mutated to sent
    assert db.get(ErpPublicationOutbox, row_id).state == "skipped"


def test_GUARD1_process_on_sent_is_409(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    with _patch_readiness(_readiness())[0]:
        process_publication(row_id, user=_ADMIN, db=db)  # -> sent
    with _patch_readiness(_readiness())[0]:
        with pytest.raises(HTTPException) as ei:
            process_publication(row_id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409
    assert db.get(ErpPublicationOutbox, row_id).state == "sent"


class _RaiseAdapter(NullErpPublicationAdapter):
    def send(self, payload):
        raise RuntimeError("kaboom")


def test_GUARD2_adapter_exception_folds_to_failed_not_500(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    with _patch_readiness(_readiness())[0]:
        with patch(f"{_MODULE}.NullErpPublicationAdapter", _RaiseAdapter):
            resp = process_publication(row_id, user=_ADMIN, db=db)  # must NOT raise
    assert resp.state == "failed"
    assert resp.reason == "adapter_error"
    assert "kaboom" in (resp.error_message or "")


def test_process_revalidate_flip_ineligible_skips(db):
    row_id = _enqueue(db, _readiness(eligible=True)).outbox.id
    # enqueue saw eligible; the process revalidate now returns ineligible.
    with _patch_readiness(_readiness(eligible=False))[0]:
        resp = process_publication(row_id, user=_ADMIN, db=db)
    assert resp.state == "skipped"
    assert resp.reason == "not_eligible"


def test_process_revalidate_version_mismatch_skips(db):
    row_id = _enqueue(db, _readiness(version_id="VER-1")).outbox.id
    with _patch_readiness(_readiness(eligible=True, version_id="VER-2"))[0]:
        resp = process_publication(row_id, user=_ADMIN, db=db)
    assert resp.state == "skipped"
    assert resp.reason == "not_eligible"
    assert (resp.properties or {}).get("revalidated_version_mismatch") is True


def test_process_revalidate_reuses_build_publication_readiness(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    p, m = _patch_readiness(_readiness())
    with p:
        process_publication(row_id, user=_ADMIN, db=db)
    assert m.called  # the route's revalidate went through build_publication_readiness


def test_process_backing_item_gone_is_409(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    # Raw delete + expunge to drop the backing item without triggering ORM
    # relationship cascades into tables absent from the minimal SQLite set.
    db.execute(text("DELETE FROM meta_items WHERE id = :id"), {"id": _ITEM_ID})
    db.expunge_all()
    with pytest.raises(HTTPException) as ei:
        process_publication(row_id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409


# --- replay ------------------------------------------------------------------


class _RemoteErrorAdapter(NullErpPublicationAdapter):
    def send(self, payload):
        return SendResult(ok=False, error="remote down", error_kind="remote_error")


class _FailValidateAdapter(NullErpPublicationAdapter):
    def validate_contract(self, payload):
        return ValidationResult(ok=False, errors=["bad"])


def test_replay_failed_remote_error_retries_to_sent(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    with _patch_readiness(_readiness())[0]:
        with patch(f"{_MODULE}.NullErpPublicationAdapter", _RemoteErrorAdapter):
            process_publication(row_id, user=_ADMIN, db=db)  # -> failed/remote_error
    assert db.get(ErpPublicationOutbox, row_id).reason == "remote_error"
    with _patch_readiness(_readiness())[0]:
        resp = replay_publication(row_id, user=_ADMIN, db=db)  # Null adapter -> sent
    assert resp.state == "sent"


def test_replay_validation_error_not_retryable_409(db):
    row_id = _enqueue(db, _readiness()).outbox.id
    with patch(f"{_MODULE}.NullErpPublicationAdapter", _FailValidateAdapter):
        dry_run_publication(row_id, user=_ADMIN, db=db)  # -> failed/validation_error
    assert db.get(ErpPublicationOutbox, row_id).reason == "validation_error"
    with _patch_readiness(_readiness())[0]:
        with pytest.raises(HTTPException) as ei:
            replay_publication(row_id, user=_ADMIN, db=db)
    assert ei.value.status_code == 409


# --- HTTP wiring (TestClient) ------------------------------------------------


def test_http_enqueue_200_via_testclient(db):
    def override_get_db():
        yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    client = TestClient(app)
    with _patch_readiness(_readiness())[0]:
        resp = client.post(
            f"/api/v1/plm-erp/items/{_ITEM_ID}/publication-outbox/enqueue",
            json={"target_system": TARGET},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["persisted"] is True
    assert data["outbox"]["state"] == "pending"
