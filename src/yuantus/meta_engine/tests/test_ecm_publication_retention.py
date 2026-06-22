"""Item C: ECM publication outbox retention prune (default-off, SENT-only,
preserve conflict_after_sent)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_NOW = datetime(2026, 6, 21, tzinfo=timezone.utc)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ecm-ret.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


def _row(session, rid, state, dispatched_at, props=None):
    r = EcmPublicationOutbox(
        id=rid,
        item_id="itm",
        version_id="ver",
        file_id=rid,
        file_role="native_cad",
        target_system="athena",
        state=state,
        attempt_count=1,
        max_attempts=3,
        dispatched_at=dispatched_at,
        next_attempt_at=_NOW,
        created_at=_NOW,
        properties=props or {},
    )
    session.add(r)
    return r


def test_prune_disabled_is_noop(session):
    _row(session, "old", EcmPublicationState.SENT.value, _NOW - timedelta(days=200))
    session.flush()
    assert EcmPublicationOutboxService(session).prune_terminal(retention_days=0, now=_NOW) == 0
    assert session.query(EcmPublicationOutbox).count() == 1


def test_prune_deletes_old_sent_preserves_others(session):
    old = _NOW - timedelta(days=200)
    recent = _NOW - timedelta(days=1)
    _row(session, "old_sent", EcmPublicationState.SENT.value, old)
    _row(session, "recent_sent", EcmPublicationState.SENT.value, recent)
    _row(session, "old_conflict", EcmPublicationState.SENT.value, old, {"conflict_after_sent": True})
    _row(session, "old_failed", EcmPublicationState.FAILED.value, old)
    session.flush()
    deleted = EcmPublicationOutboxService(session).prune_terminal(retention_days=90, now=_NOW)
    assert deleted == 1
    remaining = {r.id for r in session.query(EcmPublicationOutbox).all()}
    assert remaining == {"recent_sent", "old_conflict", "old_failed"}


def test_prune_respects_limit(session):
    old = _NOW - timedelta(days=200)
    for i in range(5):
        _row(session, f"old_{i}", EcmPublicationState.SENT.value, old)
    session.flush()
    deleted = EcmPublicationOutboxService(session).prune_terminal(retention_days=90, now=_NOW, limit=2)
    assert deleted == 2
    assert session.query(EcmPublicationOutbox).count() == 3
