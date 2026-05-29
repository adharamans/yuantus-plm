"""Unit tests for the PLM->ERP publication outbox service (G2 R2).

DB-independent: an in-memory SQLite session (no Postgres). The R1-B verdict is
constructed directly as a PublicationReadinessResponse and passed in — the
service consumes the verdict, it does not re-derive eligibility (R2 build
taskbook §8).
"""
from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.erp_publication.adapter import (
    NullErpPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.erp_publication.models import ErpPublicationOutbox
from yuantus.meta_engine.erp_publication.service import (
    ErpPublicationOutboxService,
    PublicationConflictError,
    PublicationReplayError,
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

TARGET = "erp-test"


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[RBACUser.__table__, ErpPublicationOutbox.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def svc(session):
    return ErpPublicationOutboxService(session)


def _readiness(
    *,
    eligible=True,
    with_version=True,
    blocking=None,
    generated_at=None,
    item_id="ITEM-1",
    version_id="VER-1",
    released_at="2026-05-28T00:00:00",
    esign_complete=None,
    file_role="native",
):
    version = None
    file_refs = []
    if with_version:
        version = VersionBlock(
            version_id=version_id,
            generation=2,
            revision="B",
            version_label="B",
            state="Released",
            is_current=True,
            is_released=True,
            released_at=released_at,
            primary_file_id="F1",
        )
        file_refs = [
            FileRef(
                file_id="F1",
                file_role=file_role,
                is_primary=True,
                sequence=1,
                snapshot_path="/p",
            )
        ]
    return PublicationReadinessResponse(
        item=ItemBlock(item_id=item_id, lifecycle_state="Released"),
        version=version,
        eligible=eligible,
        generated_at=generated_at,
        ruleset_id="readiness",
        limits=Limits(mbom_limit=20, routing_limit=20, baseline_limit=20),
        summary=ReadinessSummary(ok=eligible),
        resources=[],
        esign=EsignBlock(
            present=esign_complete is not None,
            is_complete=esign_complete,
            completed_at=None,
        ),
        file_refs=file_refs,
        blocking_reasons=[BlockingReason(reason=r, detail=None) for r in (blocking or [])],
    )


def _count(session):
    return session.query(ErpPublicationOutbox).count()


# --- enqueue -----------------------------------------------------------------


def test_enqueue_eligible_creates_pending_row(svc, session):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=True))
    assert row is not None
    assert row.state == "pending"
    assert row.reason is None
    assert row.item_id == "ITEM-1" and row.version_id == "VER-1"
    assert _count(session) == 1


def test_enqueue_ineligible_with_version_is_skipped_not_eligible(svc, session):
    # eligible=False with an EMPTY blocking_reasons proves eligibility is NOT
    # derived from blocking_reasons (R2 build taskbook §7 note 4).
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=False, blocking=[]))
    assert row.state == "skipped"
    assert row.reason == "not_eligible"
    assert _count(session) == 1


def test_enqueue_versionless_persists_no_row(svc, session):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(with_version=False, eligible=False))
    assert row is None
    assert _count(session) == 0


# --- idempotency / duplicate behavior ---------------------------------------


def test_duplicate_same_content_reuses_row(svc, session):
    r1 = svc.enqueue(target_system=TARGET, readiness=_readiness())
    r2 = svc.enqueue(target_system=TARGET, readiness=_readiness())
    assert r1.id == r2.id
    assert _count(session) == 1


def test_duplicate_differing_only_in_generated_at_is_idempotent(svc, session):
    # generated_at is excluded from the fingerprint: same verdict, different
    # computation time -> still one row, no re-snapshot churn.
    r1 = svc.enqueue(
        target_system=TARGET,
        readiness=_readiness(generated_at=datetime(2026, 5, 28, 1, 0, 0)),
    )
    fp1 = r1.payload_fingerprint
    r2 = svc.enqueue(
        target_system=TARGET,
        readiness=_readiness(generated_at=datetime(2026, 5, 28, 9, 0, 0)),
    )
    assert r1.id == r2.id
    assert r2.payload_fingerprint == fp1
    assert _count(session) == 1


def test_duplicate_changed_content_resnapshots_non_terminal(svc, session):
    r1 = svc.enqueue(target_system=TARGET, readiness=_readiness(file_role="native"))
    fp1 = r1.payload_fingerprint
    r2 = svc.enqueue(target_system=TARGET, readiness=_readiness(file_role="drawing"))
    assert r1.id == r2.id  # same row
    assert r2.payload_fingerprint != fp1  # re-snapshotted
    assert (r2.properties or {}).get("re_snapshotted") is True
    assert _count(session) == 1


def test_duplicate_changed_content_against_sent_conflicts(svc, session):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    svc.process(row, NullErpPublicationAdapter())
    assert row.state == "sent"
    with pytest.raises(PublicationConflictError):
        svc.enqueue(target_system=TARGET, readiness=_readiness(file_role="changed"))


def test_db_unique_constraint_enforced(session):
    a = ErpPublicationOutbox(
        id="a", item_id="I", version_id="V", target_system=TARGET,
        publication_kind="readiness", state="pending",
    )
    b = ErpPublicationOutbox(
        id="b", item_id="I", version_id="V", target_system=TARGET,
        publication_kind="readiness", state="pending",
    )
    session.add(a)
    session.flush()
    session.add(b)
    with pytest.raises(IntegrityError):
        session.flush()


# --- dry-run (NO send) ------------------------------------------------------


def test_dry_run_reaches_dry_run_ready_without_sending(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.dry_run(row, NullErpPublicationAdapter())
    assert out.state == "dry_run_ready"
    assert out.dispatched_at is None
    assert "remote_id" not in (out.properties or {})


class _FailValidateAdapter(NullErpPublicationAdapter):
    def validate_contract(self, payload):
        return ValidationResult(ok=False, errors=["bad contract"])


class _RaiseBuildAdapter(NullErpPublicationAdapter):
    def build_payload(self, snapshot):
        raise RuntimeError("build broke")


def test_dry_run_validation_failure_is_validation_error(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.dry_run(row, _FailValidateAdapter())
    assert out.state == "failed"
    assert out.reason == "validation_error"


def test_dry_run_adapter_exception_is_adapter_error(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.dry_run(row, _RaiseBuildAdapter())
    assert out.state == "failed"
    assert out.reason == "adapter_error"
    assert "build broke" in (out.error_message or "")


# --- process (send) ---------------------------------------------------------


def test_process_sends_via_null_adapter(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.process(row, NullErpPublicationAdapter())
    assert out.state == "sent"
    assert out.reason is None
    assert out.dispatched_at is not None
    assert (out.properties or {}).get("remote_id", "").startswith("null:")
    assert out.attempt_count == 1


def test_process_revalidate_flip_ineligible_skips_without_send(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=True))
    out = svc.process(
        row,
        NullErpPublicationAdapter(),
        revalidate=lambda: _readiness(eligible=False),
    )
    assert out.state == "skipped"
    assert out.reason == "not_eligible"
    assert out.dispatched_at is None
    assert (out.properties or {}).get("revalidated_ineligible") is True


def test_process_revalidate_version_mismatch_skips_without_send(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(version_id="VER-1"))
    out = svc.process(
        row,
        NullErpPublicationAdapter(),
        revalidate=lambda: _readiness(version_id="VER-2"),
    )
    assert out.state == "skipped"
    assert out.reason == "not_eligible"
    assert out.dispatched_at is None
    assert (out.properties or {}).get("revalidated_version_mismatch") is True


class _RemoteErrorAdapter(NullErpPublicationAdapter):
    def send(self, payload):
        return SendResult(ok=False, error="remote down", error_kind="remote_error")


class _RaiseAdapter(NullErpPublicationAdapter):
    def send(self, payload):
        raise RuntimeError("kaboom")


def test_process_remote_error(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.process(row, _RemoteErrorAdapter())
    assert out.state == "failed"
    assert out.reason == "remote_error"


def test_process_adapter_exception_is_adapter_error(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.process(row, _RaiseAdapter())
    assert out.state == "failed"
    assert out.reason == "adapter_error"
    assert "kaboom" in (out.error_message or "")


def test_process_adapter_build_exception_is_adapter_error(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    out = svc.process(row, _RaiseBuildAdapter())
    assert out.state == "failed"
    assert out.reason == "adapter_error"
    assert "build broke" in (out.error_message or "")


def test_process_skipped_row_requires_replay(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=False))
    assert row.state == "skipped"
    with pytest.raises(PublicationReplayError):
        svc.process(row, NullErpPublicationAdapter())
    assert row.state == "skipped"
    assert row.dispatched_at is None


def test_process_sent_row_is_terminal(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    svc.process(row, NullErpPublicationAdapter())
    assert row.state == "sent"
    attempt_count = row.attempt_count
    with pytest.raises(PublicationReplayError):
        svc.process(row, NullErpPublicationAdapter())
    assert row.state == "sent"
    assert row.attempt_count == attempt_count


# --- replay -----------------------------------------------------------------


def test_replay_failed_remote_error_retries_to_sent(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    svc.process(row, _RemoteErrorAdapter())
    assert row.state == "failed" and row.reason == "remote_error"
    out = svc.replay(row, NullErpPublicationAdapter())
    assert out.state == "sent"
    assert out.attempt_count == 2


def test_replay_validation_error_is_not_retryable(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness())
    svc.dry_run(row, _FailValidateAdapter())
    assert row.state == "failed" and row.reason == "validation_error"
    with pytest.raises(PublicationReplayError):
        svc.replay(row, NullErpPublicationAdapter())


def test_replay_skipped_reopens_when_revalidate_now_eligible(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=False))
    assert row.state == "skipped"
    out = svc.replay(
        row,
        NullErpPublicationAdapter(),
        revalidate=lambda: _readiness(eligible=True),
    )
    assert out.state == "sent"


def test_replay_skipped_version_mismatch_stays_skipped(svc):
    row = svc.enqueue(
        target_system=TARGET,
        readiness=_readiness(eligible=False, version_id="VER-1"),
    )
    assert row.state == "skipped"
    out = svc.replay(
        row,
        NullErpPublicationAdapter(),
        revalidate=lambda: _readiness(eligible=True, version_id="VER-2"),
    )
    assert out.state == "skipped"
    assert out.reason == "not_eligible"
    assert (out.properties or {}).get("revalidated_version_mismatch") is True


def test_replay_skipped_without_revalidate_raises(svc):
    row = svc.enqueue(target_system=TARGET, readiness=_readiness(eligible=False))
    with pytest.raises(PublicationReplayError):
        svc.replay(row, NullErpPublicationAdapter())


# --- snapshot fidelity ------------------------------------------------------


def test_snapshot_maps_one_to_one(svc):
    row = svc.enqueue(
        target_system=TARGET,
        readiness=_readiness(eligible=True, esign_complete=None, released_at="2026-05-28T00:00:00"),
    )
    snap = row.snapshot
    assert snap["eligible"] is True
    assert snap["item"]["item_id"] == "ITEM-1"
    assert snap["version"]["version_id"] == "VER-1"
    assert snap["version"]["released_at"] == "2026-05-28T00:00:00"  # ISO string preserved
    assert snap["version"]["generation"] == 2
    assert snap["file_refs"][0]["file_id"] == "F1"
    assert snap["file_refs"][0]["is_primary"] is True
    assert snap["esign"]["present"] is False
    assert snap["esign"]["is_complete"] is None  # None != False (fidelity note 3)
    assert snap["limits"]["mbom_limit"] == 20
    assert snap["target_system"] == TARGET
    assert snap["publication_kind"] == "readiness"
