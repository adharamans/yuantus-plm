"""WP1.2 PR 2/2: stale-drawings thin slice + bounded reachable-set.

The scan reuses WP1.3's materialized needs_update read-only (never recomputes),
and walks the assembly via the bounded O(V+E) reachable-set (no diamond explosion).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.file import (
    DocumentType,
    FileContainer,
    FileRole,
    ItemFile,
)
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.relationship.service import RelationshipService
from yuantus.meta_engine.services.cad_stale_drawings_service import (
    CadStaleDrawingsService,
)
from yuantus.meta_engine.web import cad_consistency_router as router_mod
from yuantus.meta_engine.web.cad_consistency_router import scan_stale_drawings
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_2D = DocumentType.CAD_2D.value
_3D = DocumentType.CAD_3D.value
_DRAWING = FileRole.DRAWING.value
_NATIVE = FileRole.NATIVE_CAD.value
_FIXED_TS = datetime(2026, 6, 5, tzinfo=timezone.utc)
_USER = SimpleNamespace(id=1, roles=[])


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stale-drawings.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(ItemType(id="Part", label="Part", is_versionable=True))
    db.add(
        ItemType(
            id="ASSEMBLY",
            label="Assembly",
            is_relationship=True,
            is_versionable=False,
            source_item_type_id="Part",
            related_item_type_id="Part",
        )
    )
    db.commit()
    yield db
    db.close()


def _part(session, pid: str) -> None:
    session.add(
        Item(
            id=pid,
            item_type_id="Part",
            config_id=f"cfg-{pid}-{uuid.uuid4()}",
            generation=1,
            is_current=True,
            state="Active",
            properties={"item_number": pid, "name": pid},
        )
    )


def _assembly(session, parent: str, child: str) -> None:
    RelationshipService(session).create_relationship(parent, child, "ASSEMBLY")
    session.commit()


def _drawing(session, part_id: str, *, stale: bool, doc_type: str = _2D, role: str = _DRAWING) -> None:
    fid = f"{part_id}-{role}-{doc_type}"
    session.add(
        FileContainer(id=fid, filename=f"{fid}.bin", system_path=f"/v/{fid}", document_type=doc_type)
    )
    session.add(
        ItemFile(
            item_id=part_id,
            file_id=fid,
            file_role=role,
            needs_update=stale,
            staleness_reason="model_moved_on" if stale else "up_to_date",
            staleness_checked_at=_FIXED_TS,
        )
    )
    session.commit()


def _run(coro):
    return asyncio.run(coro)


def _allow(monkeypatch):
    monkeypatch.setattr(
        router_mod.MetaPermissionService, "check_permission", lambda self, *a, **k: True
    )


# ---------- bounded reachable-set --------------------------------------------
def test_reachable_set_is_bounded_on_stacked_diamond(session):
    # A->{B1,B2}; B1->C, B2->C; C->{D1,D2}; D1->E, D2->E (shared C, E).
    for p in ["A", "B1", "B2", "C", "D1", "D2", "E"]:
        _part(session, p)
    session.commit()
    for a, b in [("A","B1"),("A","B2"),("B1","C"),("B2","C"),("C","D1"),("C","D2"),("D1","E"),("D2","E")]:
        _assembly(session, a, b)

    reachable = RelationshipService(session).get_reachable_items("A", max_depth=10)
    # Each shared part appears exactly once -- visited-set, no explosion.
    assert {r["item_id"] for r in reachable} == {"A","B1","B2","C","D1","D2","E"}
    assert len(reachable) == 7


def test_reachable_set_includes_root_and_survives_cycle(session):
    _part(session, "A"); _part(session, "B")
    session.commit()
    _assembly(session, "A", "B")
    _assembly(session, "B", "A")  # cycle

    reachable = RelationshipService(session).get_reachable_items("A")
    assert {r["item_id"] for r in reachable} == {"A", "B"}  # bounded, root included
    root = next(r for r in reachable if r["item_id"] == "A")
    assert root["min_depth"] == 0 and root["first_path"] == ["A"]


def test_reachable_set_expands_at_min_depth_not_first_arrival(session):
    # C is reachable at depth 1 (A->C) AND depth 2 (A->B->C); its child GC is at
    # depth 2 via the short path. With max_depth=2 a correct shortest-first BFS
    # expands C at its MIN depth (1) so GC (depth 2) is found. A visited-set keyed
    # on a deep first-arrival could expand C at depth 2 and drop GC as depth 3 >
    # cap -- a silent false negative. This asymmetric case locks the behavior.
    for p in ["A", "B", "C", "GC"]:
        _part(session, p)
    session.commit()
    _assembly(session, "A", "C")
    _assembly(session, "A", "B")
    _assembly(session, "B", "C")
    _assembly(session, "C", "GC")

    reachable = RelationshipService(session).get_reachable_items("A", max_depth=2)
    by_id = {r["item_id"]: r for r in reachable}
    assert "GC" in by_id  # min-depth expansion of C reaches GC within budget
    assert by_id["C"]["min_depth"] == 1  # shortest path, not the depth-2 one


def test_scan_finds_stale_under_min_depth_reachable_subassembly(session):
    # The false-negative the depth/visited-set edge would cause: a stale drawing on
    # a part that is ALSO reachable deep must still be found via the shallow path.
    for p in ["A", "B", "C", "GC"]:
        _part(session, p)
    session.commit()
    _assembly(session, "A", "C")
    _assembly(session, "A", "B")
    _assembly(session, "B", "C")
    _assembly(session, "C", "GC")
    _drawing(session, "GC", stale=True)

    res = CadStaleDrawingsService(session).scan("A", max_depth=2)
    assert "GC" in {d["part_id"] for d in res["drawings"]}  # not silently missed


# ---------- scan -------------------------------------------------------------
def test_scan_collects_stale_across_assembly_including_root(session):
    _part(session, "ASSY"); _part(session, "SUB"); _part(session, "LEAF")
    session.commit()
    _assembly(session, "ASSY", "SUB")
    _assembly(session, "SUB", "LEAF")
    _drawing(session, "ASSY", stale=True)   # root's own drawing is stale
    _drawing(session, "LEAF", stale=True)
    _drawing(session, "SUB", stale=False)   # fresh -> excluded

    res = CadStaleDrawingsService(session).scan("ASSY")
    assert res["scanned_parts"] == 3
    assert res["stale_count"] == 2
    stale_parts = {d["part_id"] for d in res["drawings"]}
    assert stale_parts == {"ASSY", "LEAF"}
    leaf = next(d for d in res["drawings"] if d["part_id"] == "LEAF")
    assert leaf["path"] == ["ASSY", "SUB", "LEAF"]
    assert leaf["staleness_reason"] == "model_moved_on"


def test_scan_is_read_only_does_not_recompute(session):
    _part(session, "P")
    session.commit()
    _drawing(session, "P", stale=True)

    before = (
        session.query(ItemFile).filter_by(item_id="P", file_role=_DRAWING).one()
    ).staleness_checked_at

    CadStaleDrawingsService(session).scan("P")

    after = (
        session.query(ItemFile).filter_by(item_id="P", file_role=_DRAWING).one()
    ).staleness_checked_at
    assert after == before  # staleness_checked_at untouched -> recompute never ran


def test_scan_excludes_models_and_non_2d(session):
    _part(session, "P")
    session.commit()
    _drawing(session, "P", stale=True, doc_type=_3D, role=_NATIVE)  # a 3D model
    res = CadStaleDrawingsService(session).scan("P")
    assert res["stale_count"] == 0  # only 2d drawings count


def test_scan_diamond_lists_shared_part_once(session):
    _part(session, "A"); _part(session, "B"); _part(session, "C"); _part(session, "SHARED")
    session.commit()
    _assembly(session, "A", "B"); _assembly(session, "A", "C")
    _assembly(session, "B", "SHARED"); _assembly(session, "C", "SHARED")
    _drawing(session, "SHARED", stale=True)

    res = CadStaleDrawingsService(session).scan("A")
    assert res["scanned_parts"] == 4  # A,B,C,SHARED -- each once
    assert [d["part_id"] for d in res["drawings"]] == ["SHARED"]  # not duplicated


# ---------- router -----------------------------------------------------------
def test_router_missing_root_is_404(session, monkeypatch):
    _allow(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _run(scan_stale_drawings("nope", max_depth=10, user=_USER, db=session))
    assert ei.value.status_code == 404


def test_router_non_part_is_400(session, monkeypatch):
    _allow(monkeypatch)
    session.add(
        Item(id="D1", item_type_id="Document", config_id=f"c-{uuid.uuid4()}",
             generation=1, is_current=True, state="Active", properties={})
    )
    session.commit()
    with pytest.raises(HTTPException) as ei:
        _run(scan_stale_drawings("D1", max_depth=10, user=_USER, db=session))
    assert ei.value.status_code == 400


def test_router_depth_over_cap_is_422(session, monkeypatch):
    _allow(monkeypatch)
    _part(session, "A")
    session.commit()
    with pytest.raises(HTTPException) as ei:
        _run(scan_stale_drawings("A", max_depth=99, user=_USER, db=session))
    assert ei.value.status_code == 422


def test_router_happy_path(session, monkeypatch):
    _allow(monkeypatch)
    _part(session, "A")
    session.commit()
    _drawing(session, "A", stale=True)
    out = _run(scan_stale_drawings("A", max_depth=10, user=_USER, db=session))
    assert out["root_id"] == "A" and out["stale_count"] == 1
