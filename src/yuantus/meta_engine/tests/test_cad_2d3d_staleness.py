"""WP1.3 CAD 2D/3D staleness contracts.

Provenance model: a drawing pins the model batch it was last co-saved with
(source_batch_id); stale only when the model moved past that pin. Batch-id
inequality alone never means stale (the命门 anti-false-positive, T2b).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.file import (
    DocumentType,
    FileContainer,
    FileRole,
    ItemFile,
)
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.cad_consistency_service import (
    REASON_AMBIGUOUS,
    REASON_MODEL_MOVED_ON,
    REASON_NO_MODEL,
    REASON_UNKNOWN,
    REASON_UP_TO_DATE,
    CadConsistencyService,
)
from yuantus.meta_engine.services.cad_import_service import _get_document_type
from yuantus.meta_engine.version.file_service import VersionFileService
from yuantus.meta_engine.version.models import ItemVersion, VersionFile
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_3D = DocumentType.CAD_3D.value  # "3d"
_2D = DocumentType.CAD_2D.value  # "2d"
_NATIVE = FileRole.NATIVE_CAD.value
_DRAWING = FileRole.DRAWING.value


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cad-staleness.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    yield db
    db.close()


def _item(session, item_id: str, current_version_id: str = None) -> Item:
    item = Item(
        id=item_id,
        item_type_id="Part",
        config_id=f"cfg-{item_id}-{uuid.uuid4()}",
        generation=1,
        is_current=True,
        state="Active",
        properties={"item_number": item_id},
        current_version_id=current_version_id,
    )
    session.add(item)
    return item


def _file(session, file_id: str, document_type: str) -> FileContainer:
    fc = FileContainer(
        id=file_id,
        filename=f"{file_id}.bin",
        system_path=f"/vault/{file_id}.bin",
        document_type=document_type,
    )
    session.add(fc)
    return fc


def _link(
    session,
    item_id: str,
    file_id: str,
    role: str,
    *,
    import_batch_id: str = None,
    source_batch_id: str = None,
) -> ItemFile:
    lf = ItemFile(
        item_id=item_id,
        file_id=file_id,
        file_role=role,
        import_batch_id=import_batch_id,
        source_batch_id=source_batch_id,
    )
    session.add(lf)
    return lf


def _model(session, item_id: str, *, batch: str) -> None:
    """A 3D native model (document_type=3d, role=native_cad)."""
    _file(session, f"{item_id}-model", _3D)
    _link(session, item_id, f"{item_id}-model", _NATIVE, import_batch_id=batch)


def _drawing(
    session, item_id: str, *, import_batch: str = None, source_batch: str = None
) -> ItemFile:
    _file(session, f"{item_id}-dwg", _2D)
    return _link(
        session,
        item_id,
        f"{item_id}-dwg",
        _DRAWING,
        import_batch_id=import_batch,
        source_batch_id=source_batch,
    )


def _verdict(session, item_id: str):
    """Run recompute and return the single drawing's (reason, needs_update)."""
    res = CadConsistencyService(session).recompute(item_id)
    assert res["drawing_count"] == 1, res
    d = res["drawings"][0]
    return d["staleness_reason"], d["needs_update"]


def test_t1_save_all_same_batch_is_up_to_date(session):
    _item(session, "P1")
    _model(session, "P1", batch="B")
    _drawing(session, "P1", import_batch="B")  # co-saved, source_batch not yet pinned
    session.commit()

    reason, needs = _verdict(session, "P1")
    assert (reason, needs) == (REASON_UP_TO_DATE, False)
    dwg = session.query(ItemFile).filter_by(file_role=_DRAWING).one()
    assert dwg.source_batch_id == "B"  # pinned to model batch


def test_t2_model_moved_on_is_stale(session):
    _item(session, "P2")
    _model(session, "P2", batch="C")
    _drawing(session, "P2", import_batch="B", source_batch="B")  # pinned to old model B
    session.commit()

    assert _verdict(session, "P2") == (REASON_MODEL_MOVED_ON, True)


def test_t2b_reexport_2d_only_model_untouched_is_not_stale(session):
    """命门: after a save-all (pin=B), re-export ONLY the 2D in a new batch C while
    the model stays at B. Must be up_to_date, never falsely stale."""
    _item(session, "P2B")
    _model(session, "P2B", batch="B")
    # drawing's own latest import is C, but its provenance is still pinned to B.
    _drawing(session, "P2B", import_batch="C", source_batch="B")
    session.commit()

    assert _verdict(session, "P2B") == (REASON_UP_TO_DATE, False)


def test_t3_null_provenance_is_unknown_fail_open(session):
    _item(session, "P3")
    _model(session, "P3", batch="B")
    _drawing(session, "P3", import_batch="X")  # never co-saved -> source_batch None
    session.commit()

    assert _verdict(session, "P3") == (REASON_UNKNOWN, False)


def test_t5_no_model_is_no_model(session):
    _item(session, "P5")
    _drawing(session, "P5", import_batch="B")
    session.commit()

    assert _verdict(session, "P5") == (REASON_NO_MODEL, False)


def test_t12_multiple_native_cad_is_ambiguous(session):
    _item(session, "P12")
    _file(session, "P12-m1", _3D)
    _link(session, "P12", "P12-m1", _NATIVE, import_batch_id="B")
    _file(session, "P12-m2", _3D)
    _link(session, "P12", "P12-m2", _NATIVE, import_batch_id="B")
    _drawing(session, "P12", import_batch="B", source_batch="B")
    session.commit()

    assert _verdict(session, "P12") == (REASON_AMBIGUOUS, False)


def test_t13_mislabeled_2d_dwg_is_drawing_not_model(session):
    """A 2D DWG mislabeled file_role=native_cad: document_type=2d is authoritative
    -> it is a drawing D, not the model M."""
    _item(session, "P13")
    _model(session, "P13", batch="B")  # real 3D model
    _file(session, "P13-dwg", _2D)
    _link(session, "P13", "P13-dwg", _NATIVE, import_batch_id="B")  # 2D + native_cad
    session.commit()

    view = CadConsistencyService(session).get_staleness("P13")
    assert view["model_status"] == "ok"
    assert view["model"]["file_id"] == "P13-model"  # the real 3D, not the DWG
    drawing_ids = {d["file_id"] for d in view["drawings"]}
    assert "P13-dwg" in drawing_ids  # mislabeled DWG resolved as drawing


def test_t14_model_moved_on_then_reexport_2d_only_stays_stale(session):
    """After model B->C, re-importing only the 2D (batch D) does NOT repin -> still
    stale. (Guards against assuming T2b covers all drawing-only cases.)"""
    _item(session, "P14")
    _model(session, "P14", batch="C")  # model already moved on to C
    _drawing(session, "P14", import_batch="D", source_batch="B")  # 2D re-imported alone
    session.commit()

    assert _verdict(session, "P14") == (REASON_MODEL_MOVED_ON, True)


def test_t15_recompute_does_not_rewrite_historical_version_snapshots(session):
    v_hist = "P15-V1"
    v_curr = "P15-V2"
    session.add(
        ItemVersion(
            id=v_hist, item_id="P15", generation=1, revision="A", state="Released",
            is_current=False, is_released=True,  # B1: historical, non-current released
        )
    )
    session.add(
        ItemVersion(id=v_curr, item_id="P15", generation=1, revision="B", state="Draft")
    )
    _item(session, "P15", current_version_id=v_curr)
    _model(session, "P15", batch="C")
    _drawing(session, "P15", import_batch="B", source_batch="B")  # will be stale (B!=C)
    # Frozen snapshots of the drawing on both versions (historical NOT stale).
    for vid in (v_hist, v_curr):
        session.add(
            VersionFile(
                id=f"{vid}-vf",
                version_id=vid,
                file_id="P15-dwg",
                file_role=_DRAWING,
                needs_update=False,
                source_batch_id="B",
            )
        )
    session.commit()

    reason, needs = _verdict(session, "P15")
    assert (reason, needs) == (REASON_MODEL_MOVED_ON, True)

    hist_vf = session.query(VersionFile).filter_by(version_id=v_hist).one()
    curr_vf = session.query(VersionFile).filter_by(version_id=v_curr).one()
    assert hist_vf.needs_update is False  # historical snapshot untouched
    assert curr_vf.needs_update is True  # current snapshot mirrors the verdict


def test_t16_checkin_native_is_selectable_as_model(session):
    # (a) derivation: a checked-in .step native derives document_type "3d".
    assert _get_document_type("step") == _3D
    # (b) selection: the native_cad ItemFile a checkin materializes (document_type
    # 3d) is selected as model M and drives the drawing verdict.
    _item(session, "P16")
    _model(session, "P16", batch="B")  # mirrors post-checkin native_cad + document_type 3d
    _drawing(session, "P16", import_batch="B")
    session.commit()

    view = CadConsistencyService(session).get_staleness("P16")
    assert view["model_status"] == "ok"
    assert view["model_import_batch_id"] == "B"


def test_t17_pdf_printout_and_png_preview_are_not_drawings(session):
    _item(session, "P17")
    _model(session, "P17", batch="B")
    _drawing(session, "P17", import_batch="B", source_batch="B")  # the real drawing
    # 2D-classified printout/preview/geometry must be excluded as drawings.
    for role, suffix in (
        (FileRole.PRINTOUT.value, "pdf"),
        (FileRole.PREVIEW.value, "png"),
        (FileRole.GEOMETRY.value, "geo"),
    ):
        _file(session, f"P17-{suffix}", _2D)
        _link(session, "P17", f"P17-{suffix}", role, import_batch_id="B")
    session.commit()

    view = CadConsistencyService(session).get_staleness("P17")
    drawing_ids = {d["file_id"] for d in view["drawings"]}
    assert drawing_ids == {"P17-dwg"}  # only the real drawing


def test_t18_same_file_two_roles_two_rows_no_collapse(session):
    """Same FileContainer on the same Part as native_cad AND drawing -> two distinct
    ItemFile rows; the (item,file,role) unique index blocks a true duplicate."""
    _item(session, "P18")
    _file(session, "P18-shared", _3D)
    _link(session, "P18", "P18-shared", _NATIVE, import_batch_id="B")
    _link(session, "P18", "P18-shared", _DRAWING, import_batch_id="B")
    session.commit()

    rows = session.query(ItemFile).filter_by(item_id="P18", file_id="P18-shared").all()
    assert {r.file_role for r in rows} == {_NATIVE, _DRAWING}

    # A true duplicate triple is rejected by uq_item_file_role.
    session.add(ItemFile(item_id="P18", file_id="P18-shared", file_role=_NATIVE))
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_t16_real_checkin_materializes_selectable_model(session, tmp_path):
    """Integration: a REAL CheckinManager.checkin of a .step native must produce a
    native_cad ItemFile with document_type='3d' that CadConsistencyService selects
    as model M (the ratified H1 gate, end-to-end through _resolve_cad_metadata ->
    _upsert_native_role_row -> VersionService.checkin sync -> recompute)."""
    from types import SimpleNamespace

    from yuantus.meta_engine.services.checkin_service import CheckinManager
    from yuantus.meta_engine.services.file_service import FileService
    from yuantus.meta_engine.storage.local_storage import LocalStorageProvider

    user_id = 1
    vid = "P16I-V1"
    session.add(
        ItemVersion(
            id=vid,
            item_id="P16I",
            generation=1,
            revision="A",
            state="Draft",
            is_current=True,
            checked_out_by_id=user_id,
        )
    )
    _item(session, "P16I", current_version_id=vid)
    session.commit()

    mgr = CheckinManager(session, user_id=user_id)
    # Local on-disk storage under tmp_path (no S3/minio needed).
    mgr.file_service.storage = FileService(
        storage_provider=LocalStorageProvider(
            SimpleNamespace(
                LOCAL_STORAGE_PATH=str(tmp_path),
                LOCAL_STORAGE_PUBLIC_URL_PREFIX="",
            )
        )
    )
    mgr.checkin("P16I", b"ISO-10303-21;\nHEADER;", "model.step", import_batch_id="B")

    view = CadConsistencyService(session).get_staleness("P16I")
    assert view["model_status"] == "ok"  # native checkin is selectable as M
    assert view["model_import_batch_id"] == "B"
    model_fc = session.get(FileContainer, view["model"]["file_id"])
    assert model_fc.document_type == _3D  # derived, not hard-coded OTHER

    native_links = (
        session.query(ItemFile)
        .filter_by(item_id="P16I", file_role=_NATIVE)
        .all()
    )
    assert len(native_links) == 1
    assert native_links[0].import_batch_id == "B"


def test_t11_import_batch_id_syncs_from_item_file_to_version_file(session):
    v_curr = "P11-V1"
    session.add(
        ItemVersion(id=v_curr, item_id="P11", generation=1, revision="A", state="Draft")
    )
    _item(session, "P11", current_version_id=v_curr)
    _file(session, "P11-model", _3D)
    _link(session, "P11", "P11-model", _NATIVE, import_batch_id="B", source_batch_id="B")
    session.commit()

    VersionFileService(session).sync_item_files_to_version(
        item_id="P11", version_id=v_curr
    )
    session.commit()

    vf = session.query(VersionFile).filter_by(version_id=v_curr, file_id="P11-model").one()
    assert vf.import_batch_id == "B"
    assert vf.source_batch_id == "B"


def test_t18a_cad_import_attach_different_roles_creates_two_rows(session):
    """CAD-import writer path (_attach_to_item): the same FileContainer attached to
    one Part as native_cad then drawing -> two rows; re-attaching the same triple
    updates the batch and never mutates the role."""
    from unittest.mock import MagicMock

    from yuantus.meta_engine.services.cad_import_service import CadImportService

    _item(session, "P18A")
    fc = _file(session, "P18A-shared", _3D)
    session.commit()

    svc = CadImportService(session, MagicMock())
    id1 = svc._attach_to_item(
        item_id="P18A", file_container=fc, file_role=_NATIVE, user_id=1, import_batch_id="B"
    )
    id2 = svc._attach_to_item(
        item_id="P18A", file_container=fc, file_role=_DRAWING, user_id=1, import_batch_id="B"
    )
    assert id1 != id2
    rows = session.query(ItemFile).filter_by(item_id="P18A", file_id="P18A-shared").all()
    assert {r.file_role for r in rows} == {_NATIVE, _DRAWING}

    # Re-attach the same (item, file, native_cad) triple -> update, not a new row.
    id1b = svc._attach_to_item(
        item_id="P18A", file_container=fc, file_role=_NATIVE, user_id=1, import_batch_id="C"
    )
    assert id1b == id1
    native = (
        session.query(ItemFile)
        .filter_by(item_id="P18A", file_id="P18A-shared", file_role=_NATIVE)
        .one()
    )
    assert native.import_batch_id == "C"  # batch updated; role unchanged


def test_t18b_file_attach_router_different_roles_creates_two_rows(session):
    """file-attachment writer path: attach the same file as native_cad then drawing
    -> two rows; re-attaching a role updates the same row, never its role."""
    import asyncio

    from yuantus.meta_engine.models.meta_schema import ItemType
    from yuantus.meta_engine.web.file_attachment_router import (
        AttachFileRequest,
        attach_file_to_item,
    )

    session.add(ItemType(id="Part", label="Part", is_versionable=True))
    _item(session, "P18B")
    _file(session, "P18B-shared", _3D)
    session.commit()

    def _attach(role):
        return asyncio.run(
            attach_file_to_item(
                AttachFileRequest(item_id="P18B", file_id="P18B-shared", file_role=role),
                user_id=1,
                db=session,
            )
        )

    r1 = _attach(_NATIVE)
    r2 = _attach(_DRAWING)
    assert r1["status"] == "created" and r2["status"] == "created"
    rows = session.query(ItemFile).filter_by(item_id="P18B", file_id="P18B-shared").all()
    assert {x.file_role for x in rows} == {_NATIVE, _DRAWING}

    # Re-attach native_cad -> updates the same row, no third row, role unchanged.
    r1b = _attach(_NATIVE)
    assert r1b["status"] == "updated"
    rows2 = session.query(ItemFile).filter_by(item_id="P18B", file_id="P18B-shared").all()
    assert len(rows2) == 2
    assert {x.file_role for x in rows2} == {_NATIVE, _DRAWING}


def test_sync_version_files_to_item_refuses_non_current_version(session):
    """WP1.3 service guard: syncing a NON-current version back to the item must
    raise -- a historical snapshot can never overwrite current provenance."""
    from yuantus.meta_engine.version.file_service import (
        VersionFileError,
        VersionFileService,
    )

    v_hist = "PNC-V1"
    v_curr = "PNC-V2"
    session.add(
        ItemVersion(
            id=v_hist, item_id="PNC", generation=1, revision="A", state="Released",
            is_current=False, is_released=True,  # B1: historical, non-current released
        )
    )
    session.add(
        ItemVersion(id=v_curr, item_id="PNC", generation=1, revision="B", state="Draft")
    )
    _item(session, "PNC", current_version_id=v_curr)
    session.commit()

    with pytest.raises(VersionFileError, match="not the item's current version"):
        VersionFileService(session).sync_version_files_to_item(
            version_id=v_hist, item_id="PNC"
        )
