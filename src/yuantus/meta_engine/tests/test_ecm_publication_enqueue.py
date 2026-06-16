"""ECM-P1B: outbox enqueue + the release() hook (enqueue path only).

Covers: enqueue_release creates one PENDING row per CONTROLLED file (not previews) with
a content fingerprint; idempotent re-enqueue; conflict-as-audit (changed fingerprint vs
an already-SENT row is recorded, NOT raised); the release() hook is wired and is
exception-safe + non-blocking (global kill-switch off by default; entitlement-error and
enqueue-error never fail the release, via the gate + SAVEPOINT).

The service/hook read duck-typed version objects (the enqueue only reads attributes), so
no VersionFile/FileContainer seeding is needed; the wiring test goes through the real
`release()` with the hook stubbed.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import yuantus.config as _config
from yuantus.meta_engine.app_framework import entitlement_service as _ent_mod
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.ecm_publication import service as _ecm_service_mod
from yuantus.meta_engine.ecm_publication.models import (
    EcmPublicationOutbox,
    EcmPublicationState,
)
from yuantus.meta_engine.ecm_publication.service import EcmPublicationOutboxService
from yuantus.meta_engine.models.file import FileContainer
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.meta_engine.version.service import VersionService
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ecm-enqueue.db'}",
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


# ---------- service: enqueue_release ------------------------------------------
def test_enqueue_release_creates_one_pending_row_per_controlled_file(session):
    ver = _duck_version(
        files=[
            ("native_cad", "f1", "c1"),
            ("drawing", "f2", None),  # no checksum -> composed fingerprint basis
            ("preview", "f3", "c3"),  # NOT a controlled record -> skipped
            ("attachment", "f4", "c4"),  # NOT controlled
        ]
    )
    rows = EcmPublicationOutboxService(session).enqueue_release(ver, user_id=3)

    assert {r.file_role for r in rows} == {"native_cad", "drawing"}
    assert all(r.state == EcmPublicationState.PENDING.value for r in rows)
    assert all(r.reason is None and r.payload_fingerprint for r in rows)
    drawing = next(r for r in rows if r.file_role == "drawing")
    assert drawing.snapshot["content_fingerprint_basis"].startswith("composed:")
    native = next(r for r in rows if r.file_role == "native_cad")
    assert native.snapshot["content_fingerprint_basis"] == "checksum:c1"


def test_enqueue_release_is_idempotent(session):
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    svc = EcmPublicationOutboxService(session)
    svc.enqueue_release(ver, user_id=3)
    svc.enqueue_release(ver, user_id=3)  # same content
    assert _count(session) == 1  # no duplicate row


def test_conflict_after_sent_is_recorded_not_raised(session):
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    svc = EcmPublicationOutboxService(session)
    (row,) = svc.enqueue_release(ver, user_id=3)
    row.state = EcmPublicationState.SENT.value  # simulate the worker having published
    session.flush()

    # the file content changed (new checksum) -> a different fingerprint vs the SENT row
    ver.version_files[0].file.checksum = "c2"
    svc.enqueue_release(ver, user_id=3)  # must NOT raise

    session.refresh(row)
    assert row.state == EcmPublicationState.SENT.value  # still the published record
    assert row.properties.get("conflict_after_sent") is True
    assert _count(session) == 1  # no second row for the same key


def test_enqueue_resnapshots_nonterminal_row_on_content_change(session):
    # A NON-terminal row (e.g. FAILED) whose content changed is re-snapshotted in place
    # and reset to PENDING for a fresh attempt -- NOT a conflict (that is SENT-only).
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    svc = EcmPublicationOutboxService(session)
    (row,) = svc.enqueue_release(ver, user_id=3)
    row.state = EcmPublicationState.FAILED.value
    row.reason = "adapter_error"
    session.flush()

    ver.version_files[0].file.checksum = "c2"  # content changed
    (row2,) = svc.enqueue_release(ver, user_id=3)

    assert row2.id == row.id  # same row reused, not a new one
    assert row2.state == EcmPublicationState.PENDING.value  # reset for retry
    assert row2.reason is None
    assert row2.properties.get("re_snapshotted") is True
    assert row2.snapshot["content_fingerprint_basis"] == "checksum:c2"
    assert _count(session) == 1


# ---------- the release() hook: wiring + exception-safety ----------------------
def _item(session, iid="P"):
    it = Item(id=iid, item_type_id="Part", config_id=f"c-{iid}-{uuid.uuid4()}",
              generation=1, is_current=True)
    session.add(it)
    session.flush()
    return it


def _open_version(session, item):
    v = ItemVersion(id=f"v-{uuid.uuid4()}", item_id=item.id, generation=1, revision="A",
                    version_label="1.A", state="Draft", is_current=True, is_released=False)
    session.add(v)
    session.flush()
    item.current_version_id = v.id
    session.flush()
    return v


def test_release_invokes_the_ecm_enqueue_hook(session, monkeypatch):
    item = _item(session)
    v = _open_version(session, item)
    session.commit()
    seen = {}
    monkeypatch.setattr(
        VersionService,
        "_enqueue_ecm_publication",
        lambda self, version, user_id: seen.update(vid=version.id, uid=user_id),
    )
    VersionService(session).release(item.id, user_id=9)
    assert seen == {"vid": v.id, "uid": 9}


def _enable(monkeypatch, *, enabled=True, entitled=True, entitle_raises=False):
    monkeypatch.setattr(
        _config, "get_settings", lambda: SimpleNamespace(ECM_PUBLISH_ENABLED=enabled)
    )

    def _is_entitled(self, feature_key):
        if entitle_raises:
            raise ValueError(f"unknown feature_key: {feature_key!r}")
        return entitled

    monkeypatch.setattr(_ent_mod.EntitlementService, "is_entitled", _is_entitled)


def test_hook_enqueues_when_enabled_and_entitled(session, monkeypatch):
    _enable(monkeypatch, enabled=True, entitled=True)
    ver = _duck_version(files=[("native_cad", "f1", "c1"), ("drawing", "f2", "c2")])
    VersionService(session)._enqueue_ecm_publication(ver, user_id=3)
    assert _count(session) == 2


def test_hook_skips_when_disabled(session, monkeypatch):
    _enable(monkeypatch, enabled=False, entitled=True)
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    VersionService(session)._enqueue_ecm_publication(ver, user_id=3)
    assert _count(session) == 0


def test_hook_skips_when_not_entitled(session, monkeypatch):
    _enable(monkeypatch, enabled=True, entitled=False)
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    VersionService(session)._enqueue_ecm_publication(ver, user_id=3)
    assert _count(session) == 0


def test_hook_never_raises_on_entitlement_error(session, monkeypatch):
    # is_entitled raises (unregistered key / missing tenant) -> treated as not entitled.
    _enable(monkeypatch, enabled=True, entitle_raises=True)
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    VersionService(session)._enqueue_ecm_publication(ver, user_id=3)  # must not raise
    assert _count(session) == 0


def test_hook_never_raises_on_enqueue_error(session, monkeypatch):
    _enable(monkeypatch, enabled=True, entitled=True)

    def _boom(self, *a, **k):
        raise RuntimeError("enqueue blew up")

    monkeypatch.setattr(
        _ecm_service_mod.EcmPublicationOutboxService, "enqueue_release", _boom
    )
    ver = _duck_version(files=[("native_cad", "f1", "c1")])
    VersionService(session)._enqueue_ecm_publication(ver, user_id=3)  # SAVEPOINT swallows
    assert _count(session) == 0


# ---------- end-to-end: real release() over real ORM version_files -------------
def _file_container(session, fid, *, checksum):
    fc = FileContainer(
        id=fid,
        filename=f"{fid}.step",
        system_path=f"/v/{fid}",
        mime_type="model/step",
        file_size=10,
        cad_format="STEP",
        checksum=checksum,
    )
    session.add(fc)
    session.flush()
    return fc


def _version_file(session, version, fid, role):
    vf = VersionFile(id=f"vf-{uuid.uuid4()}", version_id=version.id, file_id=fid,
                     file_role=role)
    session.add(vf)
    session.flush()
    return vf


def test_real_release_enqueues_controlled_rows_end_to_end(session, monkeypatch):
    # Proves the REAL ORM VersionFile/FileContainer flow through enqueue (not just ducks):
    # the attribute contract (vf.file_id / vf.file_role / vf.file.checksum) is exercised
    # against the live models, and provenance stamped by release() lands in the snapshot.
    _enable(monkeypatch, enabled=True, entitled=True)
    item = _item(session)
    v = _open_version(session, item)
    _file_container(session, "fc1", checksum="cs1")
    _file_container(session, "fc2", checksum="cs2")
    _version_file(session, v, "fc1", "native_cad")  # controlled -> enqueued
    _version_file(session, v, "fc2", "preview")  # NOT controlled -> skipped
    session.commit()

    VersionService(session).release(item.id, user_id=7)

    rows = session.query(EcmPublicationOutbox).all()
    assert len(rows) == 1
    (row,) = rows
    assert row.file_id == "fc1" and row.file_role == "native_cad"
    assert row.item_id == item.id and row.version_id == v.id
    assert row.state == EcmPublicationState.PENDING.value
    assert row.snapshot["content_fingerprint_basis"] == "checksum:cs1"
    # release() stamps released_by_id BEFORE the enqueue snapshot (ordering proof).
    assert row.snapshot["released_by_id"] == 7
