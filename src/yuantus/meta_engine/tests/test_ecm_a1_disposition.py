"""ECM A1 (disposition): stable-across-versions Athena source identity.

Covers the three A1 code changes:
(A) ``build_transfer_source_node_id`` folds ONLY ``(item_id, file_role)`` -> the id is
    STABLE across versions (same item+role, different version/file -> SAME id; a different
    role -> DIFFERENT id), so version N+1 revisions version N's Athena doc in place.
(B) ``_local_datetime`` keeps MICROSECONDS (not truncated to seconds), so two releases in
    the same second do not collapse to the same watermark under the now-stable identity.
(C) ``enqueue_release`` same-role fail-closed guard: >1 controlled file of one role would
    fold into a single Athena doc under the stable identity, so such files are SKIPPED;
    a version with one file per role still enqueues that file.

Fixture approach: cases A and B are pure-function (plain dicts / a naive datetime, no
session, no models). Case C uses a lightweight ``SimpleNamespace`` stand-in for the
version + its version_files (spec-endorsed; it exposes exactly what the guard reads:
``version_files`` items with ``file_role``/``file_id``/``file``, plus ``item_id``/``id``)
together with a REAL sqlite session (the enqueue sub-case runs ``_find`` -> ``session.add``
-> ``session.flush`` against the real ``EcmPublicationOutbox`` table). The session fixture
and the ``_duck_version``/``_file`` helpers mirror ``test_ecm_publication_enqueue.py``.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService
from yuantus.meta_engine.ecm_publication.transfer_receiver_adapter import (
    _local_datetime,
    build_transfer_source_node_id,
)
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ecm-a1.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


def _file(file_id, role_unused, *, checksum, ext="step"):
    return SimpleNamespace(
        id=file_id,
        checksum=checksum,
        filename=f"{file_id}.{ext}",
        mime_type="model/step",
        file_size=100,
        cad_format="STEP",
        system_path=f"/v/{file_id}",
    )


def _duck_version(*, item_id="P", version_id="v1", files):
    # files: list of (file_role, file_id, checksum)
    vfs = [
        SimpleNamespace(file_role=role, file_id=fid, file=_file(fid, role, checksum=cs))
        for (role, fid, cs) in files
    ]
    return SimpleNamespace(
        item_id=item_id,
        id=version_id,
        version_label="1.A",
        generation=1,
        revision="A",
        released_at=None,
        released_by_id=3,
        version_files=vfs,
    )


def _count(session):
    return session.query(EcmPublicationOutbox).count()


# ---------- (A) stable sourceNodeId across versions ---------------------------
def test_source_node_id_is_stable_across_versions():
    # Same item + role, but version N+1 carries a different version_id AND file_id.
    snap_v1 = {
        "item_id": "ITEM-1",
        "version_id": "ver-1",
        "file_id": "file-1",
        "file_role": "native_cad",
    }
    snap_v2 = {
        "item_id": "ITEM-1",
        "version_id": "ver-2",  # different version
        "file_id": "file-2",  # different file
        "file_role": "native_cad",  # SAME logical controlled file
    }
    assert build_transfer_source_node_id(snap_v1) == build_transfer_source_node_id(
        snap_v2
    ), "id must be stable across versions for the same (item_id, file_role)"


def test_source_node_id_differs_by_file_role():
    snap_native = {
        "item_id": "ITEM-1",
        "version_id": "ver-1",
        "file_id": "file-1",
        "file_role": "native_cad",
    }
    snap_drawing = {
        "item_id": "ITEM-1",
        "version_id": "ver-1",
        "file_id": "file-1",
        "file_role": "drawing",  # different role -> distinct logical file
    }
    assert build_transfer_source_node_id(snap_native) != build_transfer_source_node_id(
        snap_drawing
    ), "a different file_role must yield a different id"


# ---------- (B) microsecond watermark -----------------------------------------
def test_local_datetime_keeps_microseconds():
    result = _local_datetime(datetime(2026, 6, 22, 1, 2, 3, 456789))
    # naive input -> no tz branch -> "2026-06-22T01:02:03.456789"
    assert ".456789" in result, (
        "microseconds must be preserved (not truncated to seconds) so two releases in "
        f"the same second get distinct watermarks; got {result!r}"
    )


# ---------- (C) same-role fail-closed guard in enqueue_release -----------------
def test_enqueue_skips_when_two_files_share_a_controlled_role(session):
    # Two native_cad controlled files would fold into ONE Athena doc under the stable
    # (item, role) identity -> both are fail-closed SKIPPED -> zero rows enqueued.
    ver = _duck_version(
        files=[
            ("native_cad", "f1", "c1"),
            ("native_cad", "f2", "c2"),  # second file of the SAME controlled role
        ]
    )
    rows = EcmPublicationOutboxService(session).enqueue_release(ver, user_id=3)
    assert rows == []
    assert _count(session) == 0


def test_enqueue_keeps_single_file_per_role(session):
    # One file per role (no collision) -> each controlled file IS enqueued.
    ver = _duck_version(
        files=[
            ("native_cad", "f1", "c1"),
            ("drawing", "f2", "c2"),
            ("preview", "f3", "c3"),  # NOT controlled -> skipped (role gate, not guard)
        ]
    )
    rows = EcmPublicationOutboxService(session).enqueue_release(ver, user_id=3)
    assert {r.file_role for r in rows} == {"native_cad", "drawing"}
    assert all(r.state == EcmPublicationState.PENDING.value for r in rows)
    assert _count(session) == 2
