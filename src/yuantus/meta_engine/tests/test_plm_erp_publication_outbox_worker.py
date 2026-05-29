"""Tests for the PLM->ERP publication outbox worker (G2 R2 worker daemon).

DB-independent: an in-memory SQLite session. The worker drives the REAL outbox
service; only the readiness verdict (`readiness_builder`) and, where a failure is
needed, the adapter are injected. Covers claim/locking, pending-only + due
gating, reason-based retry/backoff, guard #1 (pre-send adapter_error attempt
accounting), dead-letter, version-drift skip, stale reclaim, batch size,
idempotent re-entry — plus a SQLite migration-schema check (guard #2).
"""
from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.erp_publication.adapter import (
    NullErpPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.erp_publication.models import (
    ErpPublicationOutbox,
    ErpPublicationReason,
    ErpPublicationState,
)
from yuantus.meta_engine.erp_publication.service import ErpPublicationOutboxService
from yuantus.meta_engine.erp_publication.worker import PublicationOutboxWorker
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.web.plm_erp_publication_router import (
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

_ITEM_ID = "ITEM-1"
_VER = "VER-1"
TARGET = "erp-test"
PENDING = ErpPublicationState.PENDING.value
SENT = ErpPublicationState.SENT.value
FAILED = ErpPublicationState.FAILED.value
SKIPPED = ErpPublicationState.SKIPPED.value


@pytest.fixture()
def session():
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
    db = SessionLocal()
    db.add(Item(id=_ITEM_ID, config_id="cfg-1", state="Released"))
    db.flush()
    try:
        yield db
    finally:
        db.close()


def _readiness(*, eligible=True, version_id=_VER):
    return PublicationReadinessResponse(
        item=ItemBlock(item_id=_ITEM_ID, lifecycle_state="Released"),
        version=VersionBlock(
            version_id=version_id, generation=2, revision="B", version_label="B",
            state="Released", is_current=True, is_released=True,
            released_at="2026-05-29T00:00:00", primary_file_id="F1",
        ),
        eligible=eligible, generated_at=None, ruleset_id="readiness",
        limits=Limits(mbom_limit=20, routing_limit=20, baseline_limit=20),
        summary=ReadinessSummary(ok=eligible), resources=[],
        esign=EsignBlock(present=False, is_complete=None, completed_at=None),
        file_refs=[FileRef(file_id="F1", file_role="native", is_primary=True, sequence=1, snapshot_path="/p")],
        blocking_reasons=[],
    )


def _enqueue(session, *, eligible=True, version_id=_VER):
    svc = ErpPublicationOutboxService(session)
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=eligible, version_id=version_id))
    session.commit()
    return row


def _worker(*, builder=None, adapter=None, batch_size=10, backoff=30, stale=900):
    return PublicationOutboxWorker(
        "w1",
        adapter=adapter or NullErpPublicationAdapter(),
        readiness_builder=builder or (lambda *a, **k: _readiness()),
        batch_size=batch_size,
        backoff_seconds=backoff,
        stale_timeout_seconds=stale,
        poll_interval_seconds=10,
    )


class _RaiseBuildAdapter(NullErpPublicationAdapter):
    def build_payload(self, snapshot):  # pre-send adapter_error (attempt_count NOT incremented by process)
        raise RuntimeError("build broke")


class _RaiseSendAdapter(NullErpPublicationAdapter):
    def send(self, payload):  # send-raised adapter_error (process DID increment)
        raise RuntimeError("send broke")


class _RemoteErrorAdapter(NullErpPublicationAdapter):
    def send(self, payload):
        return SendResult(ok=False, error="remote down", error_kind="remote_error")


class _FailValidateAdapter(NullErpPublicationAdapter):
    def validate_contract(self, payload):
        return ValidationResult(ok=False, errors=["bad"])


# --- happy + claim ----------------------------------------------------------


def test_worker_drains_pending_to_sent(session):
    row = _enqueue(session)
    n = _worker().run_once_with_session(session)
    session.refresh(row)
    assert n == 1
    assert row.state == SENT
    assert (row.properties or {}).get("remote_id", "").startswith("null:")


def test_claim_sets_worker_id_and_claimed_at(session):
    row = _enqueue(session)
    claimed = _worker()._claim_batch(session)
    assert [r.id for r in claimed] == [row.id]
    session.refresh(row)
    assert row.worker_id == "w1" and row.claimed_at is not None


def test_recently_claimed_row_not_reclaimed_by_other_worker(session):
    _enqueue(session)
    _worker()._claim_batch(session)  # worker w1 claims (claimed_at = now)
    other = PublicationOutboxWorker(
        "w2", readiness_builder=lambda *a, **k: _readiness(),
        batch_size=10, backoff_seconds=30, stale_timeout_seconds=900, poll_interval_seconds=10,
    )
    assert other._claim_batch(session) == []  # claimed_at gate keeps w2 off


def test_idempotent_reentry_sent_row_not_reclaimed(session):
    _enqueue(session)
    w = _worker()
    assert w.run_once_with_session(session) == 1
    assert w.run_once_with_session(session) == 0


# --- gating -----------------------------------------------------------------


def test_only_pending_claimed_not_sent_or_skipped(session):
    sent_row = _enqueue(session)
    _worker().run_once_with_session(session)  # -> sent
    session.refresh(sent_row)
    assert sent_row.state == SENT
    skipped = _enqueue(session, version_id="VER-2", eligible=False)  # skipped/not_eligible
    assert skipped.state == SKIPPED
    # a fresh poll claims neither the sent nor the skipped row
    assert _worker()._claim_batch(session) == []


def test_future_next_attempt_at_not_due(session):
    row = _enqueue(session)
    row.next_attempt_at = datetime.now(timezone.utc) + timedelta(hours=1)
    session.commit()
    assert _worker()._claim_batch(session) == []


def test_batch_size_caps_claim(session):
    for i in range(3):
        _enqueue(session, version_id=f"V{i}")
    claimed = _worker(batch_size=2)._claim_batch(session)
    assert len(claimed) == 2


# --- retry / backoff / guard #1 ---------------------------------------------


def test_remote_error_reschedules_to_pending_with_backoff(session):
    row = _enqueue(session)
    _worker(adapter=_RemoteErrorAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == PENDING  # rescheduled
    assert row.reason is None
    assert row.attempt_count == 1  # process incremented (send path)
    # backoff pushed next_attempt_at into the future (normalize: SQLite stores
    # naive, PG stores tz-aware).
    nxt = row.next_attempt_at
    if nxt.tzinfo is not None:
        nxt = nxt.astimezone(timezone.utc).replace(tzinfo=None)
    assert nxt > datetime.utcnow()
    assert row.worker_id is None  # claim released on reschedule


def test_validation_error_is_terminal_not_rescheduled(session):
    row = _enqueue(session)
    _worker(adapter=_FailValidateAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == FAILED and row.reason == ErpPublicationReason.VALIDATION_ERROR.value


def test_guard1_presend_adapter_error_counts_attempt(session):
    # build_payload raises -> process() fails BEFORE its pre-send increment, so
    # attempt_count stays 0; the worker's reschedule_retry must count it once so
    # it cannot loop forever at attempt 0.
    row = _enqueue(session)
    _worker(adapter=_RaiseBuildAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == PENDING  # rescheduled, not stuck
    assert row.attempt_count == 1  # GUARD #1: counted despite pre-send failure


def test_guard1_progresses_to_dead_letter(session):
    # A pre-send adapter_error that never counts would loop forever; confirm the
    # attempt budget is consumed and the row dead-letters at max_attempts.
    row = _enqueue(session)
    row.max_attempts = 3
    session.commit()
    w = _worker(adapter=_RaiseBuildAdapter())
    seen = []
    for _ in range(5):
        # make the row due again each tick
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        row.claimed_at = None
        row.worker_id = None
        session.commit()
        w.run_once_with_session(session)
        session.refresh(row)
        seen.append((row.attempt_count, row.state))
        if row.state == FAILED and row.attempt_count >= row.max_attempts:
            break
    assert row.state == FAILED and row.attempt_count == 3, seen  # dead-letter, did not loop at 0


def test_send_error_not_double_counted_by_reschedule(session):
    # service-level guard: process() already incremented (send path) -> reschedule
    # must NOT increment again.
    svc = ErpPublicationOutboxService(session)
    row = _enqueue(session)
    row.state = FAILED
    row.reason = ErpPublicationReason.ADAPTER_ERROR.value
    row.attempt_count = 1  # process incremented before send
    session.commit()
    svc.reschedule_retry(row, attempt_count_before=0, backoff_seconds=30)  # before<current => no extra
    assert row.attempt_count == 1 and row.state == PENDING


# --- version drift / ineligible ---------------------------------------------


def test_version_drift_skips_without_send(session):
    row = _enqueue(session, version_id=_VER)
    # revalidate returns a DIFFERENT version -> stale snapshot must not be sent
    drift = _worker(builder=lambda *a, **k: _readiness(version_id="VER-9"))
    drift.run_once_with_session(session)
    session.refresh(row)
    assert row.state == SKIPPED
    assert row.reason == ErpPublicationReason.NOT_ELIGIBLE.value
    assert (row.properties or {}).get("revalidated_version_mismatch") is True


def test_revalidate_ineligible_skips(session):
    row = _enqueue(session)
    _worker(builder=lambda *a, **k: _readiness(eligible=False)).run_once_with_session(session)
    session.refresh(row)
    assert row.state == SKIPPED and row.reason == ErpPublicationReason.NOT_ELIGIBLE.value


def test_backing_item_gone_skips_without_crash(session):
    from sqlalchemy import text
    row = _enqueue(session)
    session.execute(text("DELETE FROM meta_items WHERE id = :i"), {"i": _ITEM_ID})
    session.expunge_all()
    n = _worker().run_once_with_session(session)
    row = session.get(ErpPublicationOutbox, row.id)
    assert n == 1
    assert row.state == SKIPPED  # never crashed the loop
    assert row.worker_id is None


# --- stale reclaim ----------------------------------------------------------


def test_stale_claim_reclaimed(session):
    row = _enqueue(session)
    row.worker_id = "dead-worker"
    row.claimed_at = datetime.now(timezone.utc) - timedelta(seconds=10_000)  # > stale
    session.commit()
    reclaimed = _worker(stale=900)._reclaim_stale(session)
    session.refresh(row)
    assert reclaimed == 1
    assert row.worker_id is None and row.claimed_at is None


# --- registry resolution (no adapter override) ------------------------------


def test_worker_resolves_adapter_via_registry_when_no_override(session):
    from unittest.mock import MagicMock, patch

    row = _enqueue(session)
    resolver = MagicMock(return_value=NullErpPublicationAdapter())
    w = PublicationOutboxWorker(
        "w1",
        adapter=None,  # no override -> resolve per-row by target_system
        readiness_builder=lambda *a, **k: _readiness(),
        batch_size=10, backoff_seconds=30, stale_timeout_seconds=900, poll_interval_seconds=10,
    )
    with patch("yuantus.meta_engine.erp_publication.worker.resolve_adapter", resolver):
        w.run_once_with_session(session)
    session.refresh(row)
    assert row.state == SENT
    resolver.assert_called_once_with(row.target_system)


def test_revalidate_raise_consumes_attempt_and_dead_letters(session):
    # #673 §10(a): a process() exception (here, the revalidate readiness build
    # raises) must CONSUME an attempt and dead-letter at max_attempts — not defer
    # forever at attempt 0.
    row = _enqueue(session)
    row.max_attempts = 2
    session.commit()

    def boom(*a, **k):
        raise ValueError("readiness build broke")

    w = _worker(builder=boom)
    for _ in range(5):
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        row.worker_id = None
        row.claimed_at = None
        session.commit()
        w.run_once_with_session(session)
        session.refresh(row)
        if row.state == FAILED:
            break
    assert row.state == FAILED and row.attempt_count == 2  # bounded, did not loop at 0
    assert row.reason == ErpPublicationReason.REMOTE_ERROR.value


# --- migration schema (guard #2) --------------------------------------------


def test_migration_adds_worker_columns_on_sqlite():
    """Live `alembic upgrade head` on fresh SQLite must add the worker columns
    with next_attempt_at NOT NULL (guard #2: SQLite-clean ADD COLUMN sequence)."""
    from alembic import command
    from alembic.config import Config

    repo = pathlib.Path(__file__).resolve()
    for _ in range(12):
        if (repo / "alembic.ini").is_file():
            break
        repo = repo.parent
    with tempfile.TemporaryDirectory() as tmp:
        db = pathlib.Path(tmp) / "wm.db"
        cfg = Config(str(repo / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
        command.upgrade(cfg, "head")
        insp = inspect(create_engine(f"sqlite:///{db}"))
        cols = {c["name"]: c for c in insp.get_columns("meta_erp_publication_outbox")}
    assert {"worker_id", "claimed_at", "next_attempt_at"} <= set(cols)
    assert cols["next_attempt_at"]["nullable"] is False
