"""ECM-P1C: the publication outbox worker state machine.

Mirrors the erp worker test: in-memory-ish sqlite, real backing
Item/ItemVersion/VersionFile/FileContainer where the revalidation needs them,
and direct _claim_batch/_reclaim_stale unit checks for the claim gates. Drives
the failure paths with fake adapters injected into the worker.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication.adapter import (
    NullEcmPublicationAdapter,
    SendResult,
    ValidationResult,
)
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationReason,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import (
    EcmPublicationOutboxService,
    EcmPublicationReplayError,
)
from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker
from yuantus.meta_engine.models.file import FileContainer
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.models import user as _user  # noqa: F401
from yuantus.models.base import Base

import_all_models()

_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)
_FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ecm-worker.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


# ---- fake adapters (drive the send outcomes) --------------------------------
class _SendFails(NullEcmPublicationAdapter):
    def __init__(self, error_kind="remote_error"):
        self._kind = error_kind

    def send(self, payload):
        return SendResult(ok=False, error="boom", error_kind=self._kind)


class _SendRaises(NullEcmPublicationAdapter):
    def send(self, payload):
        raise RuntimeError("send broke")


class _BuildRaises(NullEcmPublicationAdapter):
    def build_payload(self, snapshot):
        raise RuntimeError("build broke")


class _ValidateFails(NullEcmPublicationAdapter):
    def validate_contract(self, payload):
        return ValidationResult(ok=False, errors=["bad"])


# ---- seeding ----------------------------------------------------------------
def _seed_released_file(session, *, checksum="c1", role="native_cad"):
    iid = f"P-{uuid.uuid4().hex[:8]}"
    item = Item(id=iid, item_type_id="Part", config_id=f"c-{iid}",
                generation=1, is_current=True)
    session.add(item)
    session.flush()
    v = ItemVersion(id=f"v-{uuid.uuid4().hex}", item_id=iid, generation=1, revision="A",
                    version_label="1.A", state="Released", is_current=True,
                    is_released=True)
    session.add(v)
    session.flush()
    fc = FileContainer(id=f"fc-{uuid.uuid4().hex}", filename="x.step",
                       system_path="/v/x", mime_type="model/step", file_size=10,
                       cad_format="STEP", checksum=checksum)
    session.add(fc)
    session.flush()
    vf = VersionFile(id=f"vf-{uuid.uuid4().hex}", version_id=v.id, file_id=fc.id,
                     file_role=role)
    session.add(vf)
    session.flush()
    return v, fc


def _enqueue_one(session, **kw):
    v, fc = _seed_released_file(session, **kw)
    (row,) = EcmPublicationOutboxService(session).enqueue_release(v, user_id=1)
    row.next_attempt_at = _PAST  # unambiguously due
    session.commit()
    return v, fc, row


def _bare_row(session, *, state="pending", reason=None, next_attempt_at=_PAST,
              worker_id=None, claimed_at=None, version_id="ghost", attempt_count=0,
              max_attempts=3):
    r = EcmPublicationOutbox(
        id=uuid.uuid4().hex, item_id="P", version_id=version_id,
        file_id=f"f-{uuid.uuid4().hex[:8]}",  # unique -> respects the per-file key
        file_role="native_cad", target_system="athena", state=state, reason=reason,
        payload_fingerprint="fp", attempt_count=attempt_count, max_attempts=max_attempts,
        next_attempt_at=next_attempt_at, worker_id=worker_id, claimed_at=claimed_at,
    )
    session.add(r)
    session.flush()
    return r


def _worker(adapter=None, **kw):
    kw.setdefault("backoff_seconds", 0)  # due immediately on reschedule
    return EcmPublicationOutboxWorker("w1", adapter=adapter, **kw)


# ===== happy path ============================================================
def test_pending_row_drains_to_sent_via_null_adapter(session):
    _, _, row = _enqueue_one(session)
    n = _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    assert n == 1
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value
    assert row.reason is None
    assert row.attempt_count == 1
    assert row.dispatched_at is not None
    assert row.properties.get("remote_id", "").startswith("null:")


def test_sent_row_is_not_reclaimed(session):
    _, _, row = _enqueue_one(session)
    w = _worker(NullEcmPublicationAdapter())
    w.run_once_with_session(session)
    n2 = w.run_once_with_session(session)  # idempotent re-entry
    assert n2 == 0
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value


# ===== claim gates (direct _claim_batch / _reclaim_stale) ====================
def test_claim_stamps_worker_id_and_claimed_at(session):
    row = _bare_row(session)
    session.commit()
    claimed = _worker()._claim_batch(session)
    assert [r.id for r in claimed] == [row.id]
    session.refresh(row)
    assert row.worker_id == "w1" and row.claimed_at is not None


def test_only_pending_rows_are_claimed(session):
    p = _bare_row(session, state="pending")
    _bare_row(session, state="sent")
    _bare_row(session, state="failed", reason="remote_error")
    _bare_row(session, state="skipped", reason="not_eligible")
    session.commit()
    claimed = _worker()._claim_batch(session)
    assert {r.id for r in claimed} == {p.id}


def test_future_next_attempt_at_is_not_due(session):
    _bare_row(session, next_attempt_at=_FUTURE)
    session.commit()
    assert _worker()._claim_batch(session) == []


def test_batch_size_caps_claim(session):
    for _ in range(5):
        _bare_row(session)
    session.commit()
    claimed = _worker(batch_size=2)._claim_batch(session)
    assert len(claimed) == 2


def test_recently_claimed_row_not_reclaimed_by_other_worker(session):
    # claimed by 'other' just now -> within stale window -> neither reclaimed nor stolen
    _bare_row(session, worker_id="other", claimed_at=datetime.now(timezone.utc))
    session.commit()
    w = _worker(stale_timeout_seconds=900)
    assert w._reclaim_stale(session) == 0
    assert w._claim_batch(session) == []


def test_stale_claim_is_reclaimed(session):
    row = _bare_row(session, worker_id="dead", claimed_at=_PAST)
    session.commit()
    w = _worker(stale_timeout_seconds=900)
    assert w._reclaim_stale(session) == 1
    session.refresh(row)
    assert row.worker_id is None and row.claimed_at is None


# ===== revalidation skips ====================================================
def test_version_drift_skips_without_send(session):
    v, fc, row = _enqueue_one(session, checksum="c1")
    fc.checksum = "c2"  # content changed after enqueue -> fingerprint drift
    session.commit()
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SKIPPED.value
    assert row.reason == EcmPublicationReason.NOT_ELIGIBLE.value
    # content drift on the same version -> the fingerprint-drift flag (NOT
    # version_mismatch), and the fresh version id is recorded for the audit trail.
    assert row.properties.get("revalidated_fingerprint_drift") is True
    assert row.properties.get("revalidated_version_id") == v.id
    assert row.dispatched_at is None  # never sent


def test_unreleased_version_skips_as_not_eligible(session):
    v, fc, row = _enqueue_one(session)
    v.is_released = False  # no longer publishable
    session.commit()
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SKIPPED.value
    assert row.reason == EcmPublicationReason.NOT_ELIGIBLE.value
    assert row.properties.get("revalidated_ineligible") is True


def test_backing_version_gone_skips_without_crash(session):
    row = _bare_row(session, version_id="ghost")  # no such ItemVersion
    session.commit()
    n = _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    assert n == 1  # claimed and processed without raising
    session.refresh(row)
    assert row.state == EcmPublicationState.SKIPPED.value
    assert row.worker_id is None and row.claimed_at is None


# ===== failure paths =========================================================
def test_remote_error_reschedules_and_releases_claim(session):
    _, _, row = _enqueue_one(session)
    _worker(_SendFails("remote_error")).run_once_with_session(session)
    session.refresh(row)
    # send incremented to 1, reschedule did NOT double-count, back to pending
    assert row.state == EcmPublicationState.PENDING.value
    assert row.attempt_count == 1
    assert row.worker_id is None and row.claimed_at is None


def test_validation_error_is_terminal(session):
    _, _, row = _enqueue_one(session)
    _worker(_ValidateFails()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.FAILED.value
    assert row.reason == EcmPublicationReason.VALIDATION_ERROR.value
    # terminal: a second run does not re-claim it
    assert _worker(_ValidateFails()).run_once_with_session(session) == 0


def test_presend_adapter_error_counts_attempt_and_dead_letters(session):
    # build_payload raises BEFORE process()'s pre-send increment (guard #1):
    # reschedule_retry must count it so it can't loop forever at attempt 0.
    _, _, row = _enqueue_one(session)
    row.max_attempts = 2
    session.commit()
    w = _worker(_BuildRaises())
    w.run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 1 and row.state == EcmPublicationState.PENDING.value
    w.run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 2
    assert row.state == EcmPublicationState.FAILED.value
    assert row.reason == EcmPublicationReason.ADAPTER_ERROR.value  # dead-letter


def test_send_raises_is_adapter_error_and_dead_letters_with_adapter_error_reason(session):
    # send raised AFTER the pre-send increment -> attempt counted, rescheduled;
    # with max_attempts=1 it dead-letters with reason=ADAPTER_ERROR (distinguishing
    # it from the remote_error path, which the PENDING-only assertion could not).
    _, _, row = _enqueue_one(session)
    row.max_attempts = 1
    session.commit()
    _worker(_SendRaises()).run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 1
    assert row.state == EcmPublicationState.FAILED.value
    assert row.reason == EcmPublicationReason.ADAPTER_ERROR.value
    assert row.error_message == "send broke"


def test_remote_error_dead_letters_without_double_count(session):
    # The pre-send-increment route to dead-letter: send fails remote_error each tick;
    # attempt_count advances EXACTLY +1 per tick (reschedule must not double-count),
    # then dead-letters FAILED at max_attempts with the claim cleared.
    _, _, row = _enqueue_one(session)
    row.max_attempts = 2
    session.commit()
    w = _worker(_SendFails("remote_error"))
    w.run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 1 and row.state == EcmPublicationState.PENDING.value
    w.run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 2
    assert row.state == EcmPublicationState.FAILED.value
    assert row.reason == EcmPublicationReason.REMOTE_ERROR.value
    assert row.worker_id is None and row.claimed_at is None
    assert w.run_once_with_session(session) == 0  # terminal: not re-claimed


def test_deleted_controlled_file_skips_as_not_eligible(session):
    # The 'controlled VersionFile gone' revalidation branch (distinct from
    # unreleased-version and fingerprint-drift).
    v, fc, row = _enqueue_one(session)
    session.query(VersionFile).filter(VersionFile.version_id == v.id).delete()
    session.commit()
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SKIPPED.value
    assert row.reason == EcmPublicationReason.NOT_ELIGIBLE.value
    assert row.properties.get("revalidated_ineligible") is True
    assert row.dispatched_at is None


class _RevalidateRaises(EcmPublicationOutboxWorker):
    def _revalidate(self, session, row, version):
        raise RuntimeError("revalidate broke")


def test_revalidate_exception_consumes_attempt_and_dead_letters(session):
    # process() raises only when revalidate() raises (build/validate/send are caught
    # inside process). The worker-level handler must: not crash, consume an attempt,
    # release the claim, back off; and dead-letter at max with reason=REMOTE_ERROR.
    _, _, row = _enqueue_one(session)
    row.max_attempts = 2
    session.commit()
    w = _RevalidateRaises("w1", adapter=NullEcmPublicationAdapter(), backoff_seconds=0)
    w.run_once_with_session(session)  # must not raise
    session.refresh(row)
    assert row.attempt_count == 1
    assert row.state == EcmPublicationState.PENDING.value
    assert row.worker_id is None and row.claimed_at is None
    assert row.error_message == "revalidate broke"
    # the exception path uses a FLAT max(backoff,1)=1s backoff -> force due again
    row.next_attempt_at = _PAST
    session.commit()
    w.run_once_with_session(session)
    session.refresh(row)
    assert row.attempt_count == 2
    assert row.state == EcmPublicationState.FAILED.value
    assert row.reason == EcmPublicationReason.REMOTE_ERROR.value


def test_no_adapter_override_resolves_null_and_sends(session):
    # worker with NO injected adapter -> registry resolves Null -> sends.
    # Pin the isolation invariant: if a stray YUANTUS_PUBLICATION_ECM_TARGET_SYSTEM
    # ever leaked into the env, resolve_adapter would return a LIVE CMIS adapter and
    # the next line would open a real socket -- assert Null FIRST so it fails loudly.
    from yuantus.meta_engine.ecm_publication.adapter import NullEcmPublicationAdapter
    from yuantus.meta_engine.ecm_publication.adapter_registry import resolve_adapter

    assert isinstance(resolve_adapter("athena"), NullEcmPublicationAdapter)
    _, _, row = _enqueue_one(session)
    _worker(adapter=None).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value


def test_run_forever_stops_and_survives_a_tick_error(session):
    # The daemon loop: a raising run_once is swallowed (loop stays alive), and
    # stop() ends it. We drive it by monkeypatching run_once to raise once then stop.
    w = _worker(NullEcmPublicationAdapter(), poll_interval_seconds=0)
    calls = {"n": 0}

    def _run_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("tick boom")  # must be swallowed
        w.stop()
        return 0

    w.run_once = _run_once
    w.run_forever()  # returns when stopped; must not propagate the tick error
    assert calls["n"] == 2 and w._running is False


def test_stop_flips_running_flag(session):
    w = _worker(NullEcmPublicationAdapter())
    w._running = True
    w.stop()
    assert w._running is False


def test_released_at_representation_change_does_not_spuriously_skip(session):
    # INVARIANT: the content fingerprint must not depend on a datetime's serialized
    # representation. released_at is currently a NAIVE column so this drift does not
    # occur in prod, but the worker recomputes the fingerprint in a separate session;
    # if released_at (or any reloaded datetime) round-tripped with a different
    # isoformat (naive vs tz-aware) the worker would see a spurious drift and SKIP.
    # We force the representation change (same instant, naive->aware) and assert SENT.
    v, fc = _seed_released_file(session)
    naive = datetime(2026, 6, 16, 0, 0, 0)  # as release() writes it
    v.released_at = naive
    session.flush()
    (row,) = EcmPublicationOutboxService(session).enqueue_release(v, user_id=1)
    row.next_attempt_at = _PAST
    session.commit()
    # the Postgres reload: identical instant, now tz-aware
    v.released_at = naive.replace(tzinfo=timezone.utc)
    session.commit()
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value  # NOT skipped


# ===== direct service unit checks (no worker) ================================
def test_process_rejects_terminal_state(session):
    _, _, row = _enqueue_one(session)
    row.state = EcmPublicationState.SENT.value
    session.flush()
    with pytest.raises(EcmPublicationReplayError):
        EcmPublicationOutboxService(session).process(row, NullEcmPublicationAdapter())


def test_process_accepts_dry_run_ready(session):
    _, _, row = _enqueue_one(session)
    row.state = EcmPublicationState.DRY_RUN_READY.value
    session.flush()
    EcmPublicationOutboxService(session).process(row, NullEcmPublicationAdapter())
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value


def test_reschedule_retry_linear_backoff_scales_with_attempt_count(session):
    svc = EcmPublicationOutboxService(session)
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # attempt_count 0, before 0 -> guard increments to 1 -> backoff = 10 * 1
    r1 = _bare_row(session, state="failed", reason="remote_error", attempt_count=0,
                   max_attempts=9)
    session.flush()
    svc.reschedule_retry(r1, attempt_count_before=0, backoff_seconds=10, now=base)
    assert r1.attempt_count == 1
    assert r1.next_attempt_at == base.replace(second=10)  # +10s
    # attempt_count 1, before 1 -> increments to 2 -> backoff = 10 * 2
    r2 = _bare_row(session, state="failed", reason="remote_error", attempt_count=1,
                   max_attempts=9)
    session.flush()
    svc.reschedule_retry(r2, attempt_count_before=1, backoff_seconds=10, now=base)
    assert r2.attempt_count == 2
    assert r2.next_attempt_at == base.replace(second=20)  # +20s (linear in attempt)
