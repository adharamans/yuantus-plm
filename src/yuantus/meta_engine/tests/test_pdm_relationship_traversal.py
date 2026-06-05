"""WP1.2 PDM relationship traversal contracts.

Tree follows ASSEMBLY (containment) only; REFERENCE never folds into the tree.
Path-based cycle guard (ancestor reappears -> cycle, not a global visited). Root
is included in tree + flat. Response rows distinguish relationship_id (the edge,
which is itself an Item) from the counterpart item_id.
"""

from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.relationship import service as rel_service_mod
from yuantus.meta_engine.relationship.service import (
    RelationshipService,
    TraversalBudgetError,
)
from yuantus.meta_engine.web import pdm_relationship_router as router_mod
from yuantus.meta_engine.web.pdm_relationship_router import (
    get_item_relationship_tree,
    get_item_relationships,
)
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_USER = SimpleNamespace(id=1, roles=[])


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'pdm-traversal.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()
    _seed_types(db)
    yield db
    db.close()


def _seed_types(session) -> None:
    session.add(ItemType(id="Part", label="Part", is_versionable=True))
    for rel in ("ASSEMBLY", "REFERENCE"):
        session.add(
            ItemType(
                id=rel,
                label=rel.title(),
                is_relationship=True,
                is_versionable=False,
                source_item_type_id="Part",
                related_item_type_id="Part",
            )
        )
    session.commit()


def _parts(session, *ids: str) -> None:
    for i in ids:
        session.add(
            Item(
                id=i,
                item_type_id="Part",
                config_id=f"cfg-{i}-{uuid.uuid4()}",
                generation=1,
                is_current=True,
                state="Active",
                properties={"item_number": i, "name": f"name-{i}"},
            )
        )
    session.commit()


def _rel(session, parent: str, child: str, kind: str = "ASSEMBLY", **props):
    rel = RelationshipService(session).create_relationship(
        parent, child, kind, properties=props or None
    )
    session.commit()
    return rel


def _allow_permission(monkeypatch):
    monkeypatch.setattr(
        router_mod.MetaPermissionService,
        "check_permission",
        lambda self, *a, **k: True,
    )


# ---------- service: relationships (one-level) -------------------------------
def test_relationships_counterpart_distinguishes_edge_and_item(session):
    _parts(session, "A", "B")
    rel = _rel(session, "A", "B", quantity=2)

    rows = RelationshipService(session).get_item_relationships("A", direction="outgoing")
    assert len(rows) == 1
    r = rows[0]
    assert r["relationship_id"] == rel.id  # the edge (an Item)
    assert r["counterpart_item_id"] == "B"  # the counterpart item -- not confused
    assert r["relationship_id"] != r["counterpart_item_id"]
    assert r["relationship_kind"] == "ASSEMBLY"
    assert r["counterpart_direction"] == "outgoing"
    assert r["properties"] == {"quantity": 2}


def test_relationships_incoming_and_kind_filter(session):
    _parts(session, "A", "B")
    _rel(session, "A", "B", "ASSEMBLY")
    _rel(session, "A", "B", "REFERENCE")

    svc = RelationshipService(session)
    incoming = svc.get_item_relationships("B", direction="incoming")
    assert {r["relationship_kind"] for r in incoming} == {"ASSEMBLY", "REFERENCE"}
    assert all(r["counterpart_item_id"] == "A" for r in incoming)
    only_ref = svc.get_item_relationships("B", kind="REFERENCE", direction="incoming")
    assert [r["relationship_kind"] for r in only_ref] == ["REFERENCE"]


# ---------- service: tree ----------------------------------------------------
def test_tree_multilevel_with_via_relationship_and_path(session):
    _parts(session, "A", "B", "C", "D")
    _rel(session, "A", "B", quantity=2)
    _rel(session, "A", "C")
    rel_bd = _rel(session, "B", "D", quantity=5)

    res = RelationshipService(session).get_relationship_tree("A", max_depth=10)
    tree = res["tree"]
    assert tree["item_id"] == "A" and tree["depth"] == 0
    assert tree["via_relationship"] is None and tree["path"] == ["A"]
    child_ids = {c["item_id"] for c in tree["children"]}
    assert child_ids == {"B", "C"}
    b = next(c for c in tree["children"] if c["item_id"] == "B")
    assert b["via_relationship"]["quantity"] == 2
    d = b["children"][0]
    assert d["item_id"] == "D" and d["depth"] == 2
    assert d["path"] == ["A", "B", "D"]
    assert d["relationship_path"] == [b["via_relationship"]["relationship_id"], rel_bd.id]


def test_tree_only_follows_assembly_not_reference(session):
    _parts(session, "A", "B", "R")
    _rel(session, "A", "B", "ASSEMBLY")
    _rel(session, "A", "R", "REFERENCE")  # must NOT appear in the assembly tree

    tree = RelationshipService(session).get_relationship_tree("A")["tree"]
    assert {c["item_id"] for c in tree["children"]} == {"B"}


def test_tree_cycle_is_path_based_and_stops(session):
    _parts(session, "A", "B")
    _rel(session, "A", "B")
    _rel(session, "B", "A")  # B -> A closes a cycle

    tree = RelationshipService(session).get_relationship_tree("A")["tree"]
    b = tree["children"][0]
    a_again = b["children"][0]
    assert a_again["item_id"] == "A"
    assert a_again["cycle"] is True
    assert a_again["children"] == []  # stop descending


def test_diamond_tree_keeps_duplicates_flat_dedupes(session):
    # A -> B, A -> C, B -> D, C -> D  (D is a legitimate shared part)
    _parts(session, "A", "B", "C", "D")
    _rel(session, "A", "B")
    _rel(session, "A", "C")
    _rel(session, "B", "D")
    _rel(session, "C", "D")
    svc = RelationshipService(session)

    tree = svc.get_relationship_tree("A", projection="tree")["tree"]
    d_nodes = [
        gc
        for c in tree["children"]
        for gc in c["children"]
        if gc["item_id"] == "D"
    ]
    assert len(d_nodes) == 2  # kept under both B and C
    assert all(n["cycle"] is False for n in d_nodes)  # shared part != cycle

    flat = svc.get_relationship_tree("A", projection="flat")["items"]
    by_id = {e["item_id"]: e for e in flat}
    assert by_id["A"]["min_depth"] == 0  # root included
    assert by_id["D"]["occurrence_count"] == 2  # deduped, counted twice
    assert {"A", "B", "C", "D"} == set(by_id)


def test_tree_max_depth_truncates(session):
    _parts(session, "A", "B", "C", "D")
    _rel(session, "A", "B")
    _rel(session, "B", "C")
    _rel(session, "C", "D")

    tree = RelationshipService(session).get_relationship_tree("A", max_depth=2)["tree"]
    b = tree["children"][0]
    c = b["children"][0]
    assert c["item_id"] == "C" and c["depth"] == 2
    assert c["children"] == []  # depth==max_depth -> no further descent


def _stacked_diamond(session) -> None:
    # A->{B1,B2}; B1->C, B2->C; C->{D1,D2}; D1->E, D2->E  (shared C and E).
    # The structural tree (dups kept) grows multiplicatively past a small budget,
    # even though the underlying graph is tiny -- the explosion max_depth can't see.
    _parts(session, "A", "B1", "B2", "C", "D1", "D2", "E")
    for a, b in [
        ("A", "B1"), ("A", "B2"), ("B1", "C"), ("B2", "C"),
        ("C", "D1"), ("C", "D2"), ("D1", "E"), ("D2", "E"),
    ]:
        _rel(session, a, b)


def test_tree_node_budget_aborts_on_shared_part_explosion(session):
    _stacked_diamond(session)
    with pytest.raises(TraversalBudgetError):
        RelationshipService(session).get_relationship_tree(
            "A", max_depth=10, max_nodes=5
        )


def test_flat_also_bounded_by_budget(session):
    # flat builds the tree first, so it must hit the same bound (it is NOT a safe
    # escape from the shared-part explosion).
    _stacked_diamond(session)
    with pytest.raises(TraversalBudgetError):
        RelationshipService(session).get_relationship_tree(
            "A", projection="flat", max_nodes=5
        )


def test_router_tree_budget_exceeded_is_422(session, monkeypatch):
    _allow_permission(monkeypatch)
    monkeypatch.setattr(rel_service_mod, "MAX_TRAVERSAL_NODES", 4)
    _stacked_diamond(session)
    with pytest.raises(HTTPException) as ei:
        _run(
            get_item_relationship_tree(
                "A", kinds="ASSEMBLY", max_depth=10, projection="tree",
                user=_USER, db=session,
            )
        )
    assert ei.value.status_code == 422


# ---------- router: validation + errors --------------------------------------
def _run(coro):
    return asyncio.run(coro)


def test_router_tree_rejects_reference_with_422(session, monkeypatch):
    _allow_permission(monkeypatch)
    _parts(session, "A")
    with pytest.raises(HTTPException) as ei:
        _run(get_item_relationship_tree("A", kinds="REFERENCE", user=_USER, db=session))
    assert ei.value.status_code == 422


def test_router_tree_rejects_depth_over_cap(session, monkeypatch):
    _allow_permission(monkeypatch)
    _parts(session, "A")
    with pytest.raises(HTTPException) as ei:
        _run(
            get_item_relationship_tree(
                "A", kinds="ASSEMBLY", max_depth=51, projection="tree",
                user=_USER, db=session,
            )
        )
    assert ei.value.status_code == 422


def test_router_relationships_rejects_bad_direction(session, monkeypatch):
    _allow_permission(monkeypatch)
    _parts(session, "A")
    with pytest.raises(HTTPException) as ei:
        _run(get_item_relationships("A", direction="sideways", user=_USER, db=session))
    assert ei.value.status_code == 422


def test_router_missing_item_is_404(session, monkeypatch):
    _allow_permission(monkeypatch)
    with pytest.raises(HTTPException) as ei:
        _run(get_item_relationships("nope", user=_USER, db=session))
    assert ei.value.status_code == 404


def test_router_non_part_is_400(session, monkeypatch):
    _allow_permission(monkeypatch)
    session.add(
        Item(
            id="DOC1",
            item_type_id="Document",
            config_id=f"cfg-{uuid.uuid4()}",
            generation=1,
            is_current=True,
            state="Active",
            properties={},
        )
    )
    session.commit()
    with pytest.raises(HTTPException) as ei:
        _run(get_item_relationships("DOC1", user=_USER, db=session))
    assert ei.value.status_code == 400


def test_router_happy_path_returns_tree(session, monkeypatch):
    _allow_permission(monkeypatch)
    _parts(session, "A", "B")
    _rel(session, "A", "B")
    out = _run(
        get_item_relationship_tree(
            "A", kinds="ASSEMBLY", max_depth=10, projection="tree",
            user=_USER, db=session,
        )
    )
    assert out["tree"]["item_id"] == "A"
    assert out["tree"]["children"][0]["item_id"] == "B"


def test_router_permission_denied_is_403(session, monkeypatch):
    monkeypatch.setattr(
        router_mod.MetaPermissionService,
        "check_permission",
        lambda self, *a, **k: False,
    )
    _parts(session, "A")
    with pytest.raises(HTTPException) as ei:
        _run(get_item_relationships("A", user=_USER, db=session))
    assert ei.value.status_code == 403
