"""ECM-A1 latest-wins ordering guard (worker-side).

ECM-publish A1 made the Athena source identity STABLE per ``(item_id, file_role)``,
so version N+1 revisions version N's SAME Athena doc. Athena revisions on ANY
watermark mismatch (equality, not ordering), so an OUT-OF-ORDER same-lineage publish
(an older version's outbox row draining AFTER a newer version's -- e.g. via retry)
could overwrite the Athena doc with OLDER content. The worker's
``_superseded_by_newer_sent`` guard prevents this: an older row is never dispatched
once a NEWER one for the same ``(item_id, file_role, target_system)`` lineage is
already SENT.

Fixtures mirror ``test_ecm_publication_worker.py`` (real sqlite session, real backing
Item/ItemVersion/VersionFile/FileContainer, the worker driven via
``run_once_with_session``). The released_at strings are compared lexicographically,
which == chronological because build_snapshot writes a naive ISO string.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication.adapter import NullEcmPublicationAdapter
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationReason,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService
from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker
from yuantus.meta_engine.models.file import FileContainer
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.models import user as _user  # noqa: F401
from yuantus.models.base import Base

import_all_models()

_PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)

# Two release instants on the SAME lineage. EARLIER < LATER both as datetimes and,
# crucially, as the naive ISO strings build_snapshot emits (lexicographic == chrono).
_EARLIER = datetime(2026, 6, 16, 9, 0, 0)
_LATER = datetime(2026, 6, 18, 9, 0, 0)


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ecm-a1-guard.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


# ---- adapter spy: records dispatch so we can assert the guard short-circuits -----
class _SpyAdapter(NullEcmPublicationAdapter):
    """Null adapter that records whether the worker ever reached dispatch
    (build_payload / send). The guard must short-circuit BEFORE any of these."""

    def __init__(self):
        self.build_calls = 0
        self.send_calls = 0

    def build_payload(self, snapshot):
        self.build_calls += 1
        return super().build_payload(snapshot)

    def send(self, payload):
        self.send_calls += 1
        return super().send(payload)


# ---- seeding (mirrors test_ecm_publication_worker._seed_released_file) ------------
def _seed_released_file(session, *, checksum="c1", role="native_cad", released_at=None):
    iid = f"P-{uuid.uuid4().hex[:8]}"
    item = Item(id=iid, item_type_id="Part", config_id=f"c-{iid}",
                generation=1, is_current=True)
    session.add(item)
    session.flush()
    v = ItemVersion(id=f"v-{uuid.uuid4().hex}", item_id=iid, generation=1, revision="A",
                    version_label="1.A", state="Released", is_current=True,
                    is_released=True, released_at=released_at)
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


def _enqueue_one(session, *, released_at, **kw):
    """Seed a released version+file with ``released_at`` set BEFORE enqueue (so the
    snapshot carries it) and enqueue its single controlled-file row, made due."""
    v, fc = _seed_released_file(session, released_at=released_at, **kw)
    (row,) = EcmPublicationOutboxService(session).enqueue_release(v, user_id=1)
    row.next_attempt_at = _PAST  # unambiguously due
    session.commit()
    # build_snapshot must have captured released_at as the ISO string we compare on.
    assert row.snapshot.get("released_at") == released_at.isoformat()
    return v, fc, row


def _sent_sibling(session, pending_row, *, released_at):
    """A SENT row on the SAME (item_id, file_role, target_system) lineage as
    ``pending_row`` but a DIFFERENT id / version_id / file_id (a different version's
    drained outbox row). Only its snapshot.released_at matters to the guard, so it
    needs no backing version."""
    sib = EcmPublicationOutbox(
        id=uuid.uuid4().hex,
        item_id=pending_row.item_id,
        version_id=f"v-other-{uuid.uuid4().hex}",
        file_id=f"f-other-{uuid.uuid4().hex[:8]}",
        file_role=pending_row.file_role,
        target_system=pending_row.target_system,
        state=EcmPublicationState.SENT.value,
        reason=None,
        snapshot={"released_at": released_at.isoformat()},
        payload_fingerprint="fp-sibling",
        attempt_count=1,
        max_attempts=3,
        next_attempt_at=_PAST,
        dispatched_at=datetime.now(timezone.utc),
    )
    session.add(sib)
    session.commit()
    return sib


def _worker(adapter=None, **kw):
    kw.setdefault("backoff_seconds", 0)
    return EcmPublicationOutboxWorker("w1", adapter=adapter, **kw)


# ===== (A) superseded -> SKIPPED, never dispatched ===============================
def test_superseded_by_newer_sent_skips_without_dispatch(session):
    # PENDING row at the EARLIER release; a SENT sibling on the same lineage at the
    # LATER release. The older row must be skipped, not sent (out-of-order drain).
    _, _, row = _enqueue_one(session, released_at=_EARLIER)
    _sent_sibling(session, row, released_at=_LATER)

    spy = _SpyAdapter()
    n = _worker(spy).run_once_with_session(session)
    assert n == 1  # claimed + processed (without raising)

    session.refresh(row)
    assert row.state == EcmPublicationState.SKIPPED.value
    assert row.reason == EcmPublicationReason.NOT_ELIGIBLE.value
    assert row.properties.get("superseded_by_newer_sent") is True
    # the guard short-circuited BEFORE revalidate / build_payload / send
    assert spy.build_calls == 0 and spy.send_calls == 0
    assert row.dispatched_at is None  # never dispatched
    # claim released
    assert row.worker_id is None and row.claimed_at is None


def test_superseded_guard_predicate_true_at_unit_level(session):
    # Direct _superseded_by_newer_sent check (newer SENT sibling present).
    _, _, row = _enqueue_one(session, released_at=_EARLIER)
    _sent_sibling(session, row, released_at=_LATER)
    assert _worker()._superseded_by_newer_sent(session, row) is True


# ===== (B) NOT superseded -> dispatched normally ================================
def test_not_superseded_dispatches_via_null_adapter(session):
    # Same shape, but the only SENT sibling is at an EARLIER release than this row
    # (this row IS the newest) -> the guard must NOT fire and the row drains to SENT
    # through the worker's normal revalidate + Null-adapter dispatch path.
    _, _, row = _enqueue_one(session, released_at=_LATER)
    _sent_sibling(session, row, released_at=_EARLIER)

    # guard predicate is False ...
    assert _worker()._superseded_by_newer_sent(session, row) is False

    # ... and the full worker path dispatches (revalidation passes: released_at is a
    # volatile key excluded from the fingerprint, so no spurious drift).
    spy = _SpyAdapter()
    n = _worker(spy).run_once_with_session(session)
    assert n == 1
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value
    assert row.reason is None
    assert row.dispatched_at is not None
    assert "superseded_by_newer_sent" not in (row.properties or {})
    assert spy.send_calls == 1  # the guard did not short-circuit


def test_no_sent_sibling_dispatches(session):
    # No SENT sibling at all on the lineage -> guard False -> normal dispatch.
    _, _, row = _enqueue_one(session, released_at=_LATER)
    assert _worker()._superseded_by_newer_sent(session, row) is False
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value


def test_equal_released_at_sent_sibling_does_not_supersede(session):
    # A SENT sibling at the SAME released_at is NOT strictly newer -> not superseded
    # (strict >, so a same-instant re-publish never blocks). Dispatches normally.
    _, _, row = _enqueue_one(session, released_at=_LATER)
    _sent_sibling(session, row, released_at=_LATER)
    assert _worker()._superseded_by_newer_sent(session, row) is False
    _worker(NullEcmPublicationAdapter()).run_once_with_session(session)
    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value


def test_newer_sent_sibling_on_other_lineage_is_ignored(session):
    # A newer SENT sibling for a DIFFERENT file_role (or item) must NOT supersede --
    # the guard is scoped to the same (item_id, file_role, target_system) lineage.
    _, _, row = _enqueue_one(session, released_at=_EARLIER)
    other_lineage = _sent_sibling(session, row, released_at=_LATER)
    other_lineage.file_role = "drawing"  # different lineage, even though newer
    session.commit()
    assert _worker()._superseded_by_newer_sent(session, row) is False
