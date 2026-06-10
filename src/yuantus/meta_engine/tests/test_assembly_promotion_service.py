"""B2b CAD-PDM assembly promotion orchestration contracts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.lifecycle.models import (
    LifecycleMap,
    LifecycleState,
    LifecycleTransition,
)
from yuantus.meta_engine.lifecycle.service import LifecycleService, PromoteResult
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.relationship.service import RelationshipService
from yuantus.meta_engine.services.assembly_promotion_service import (
    AssemblyPromotionService,
)
from yuantus.meta_engine.web.pdm_assembly_promotion_router import (
    PromoteAssemblyRequest,
    promote_assembly,
)
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()

_USER = SimpleNamespace(id=1, roles=["admin"])


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'assembly-promotion.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    _seed_lifecycle(db)
    yield db
    db.close()


def _seed_lifecycle(session) -> None:
    session.add(LifecycleMap(id="map1", name="Part Lifecycle"))
    session.add(
        LifecycleState(
            id="state_draft",
            name="Draft",
            lifecycle_map_id="map1",
            is_start_state=True,
        )
    )
    session.add(
        LifecycleState(
            id="state_released",
            name="Released",
            lifecycle_map_id="map1",
            is_released=True,
        )
    )
    session.add(
        LifecycleState(
            id="state_suspended",
            name="Suspended",
            lifecycle_map_id="map1",
        )
    )
    session.add(
        LifecycleTransition(
            id="t1",
            lifecycle_map_id="map1",
            from_state_id="state_draft",
            to_state_id="state_released",
        )
    )
    session.add(
        ItemType(
            id="Part",
            label="Part",
            is_versionable=False,
            lifecycle_map_id="map1",
        )
    )
    session.add(
        ItemType(
            id="Document",
            label="Document",
            is_versionable=False,
        )
    )
    session.add(
        ItemType(
            id="ASSEMBLY",
            label="Assembly",
            is_relationship=True,
            is_versionable=False,
            source_item_type_id="Part",
            related_item_type_id="Part",
        )
    )
    session.commit()


def _item(session, iid: str, *, released: bool = False) -> Item:
    item = Item(
        id=iid,
        item_type_id="Part",
        config_id=f"cfg-{iid}-{uuid.uuid4()}",
        generation=1,
        is_current=True,
        is_versionable=False,
        state="Released" if released else "Draft",
        current_state="state_released" if released else "state_draft",
        properties={"item_number": iid},
    )
    session.add(item)
    return item


def _doc(session, iid: str) -> Item:
    item = Item(
        id=iid,
        item_type_id="Document",
        config_id=f"cfg-{iid}-{uuid.uuid4()}",
        generation=1,
        is_current=True,
        state="Draft",
        properties={},
    )
    session.add(item)
    return item


def _assembly(session, parent: str, child: str) -> str:
    rel = RelationshipService(session).create_relationship(parent, child, "ASSEMBLY")
    session.commit()
    return rel.id


def _dangling(session, parent: str, child: str = "GHOST") -> str:
    edge_id = f"edge-{parent}-{child}"
    session.add(
        Item(
            id=edge_id,
            item_type_id="ASSEMBLY",
            config_id=f"edge-{uuid.uuid4()}",
            generation=1,
            is_current=True,
            source_id=parent,
            related_id=child,
        )
    )
    session.commit()
    return edge_id


def _run(coro):
    return asyncio.run(coro)


def test_dry_run_orders_shared_child_before_every_parent_edge(session):
    # Shortcut + deep path: R -> X and R -> A -> B -> X.
    # min_depth would put X shallow; topological order still must promote X
    # before B, A, and R.
    for iid in ("R", "X", "A", "B"):
        _item(session, iid)
    session.commit()
    _assembly(session, "R", "X")
    _assembly(session, "R", "A")
    _assembly(session, "A", "B")
    _assembly(session, "B", "X")

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=True, user_id=1, user_roles=["admin"]
    )

    assert result["ok"] is True
    order = [row["item_id"] for row in result["plan"]]
    assert order.index("X") < order.index("B") < order.index("A") < order.index("R")
    assert result["plan"][-1]["item_id"] == "R"


def test_apply_promotes_leaves_before_parent_and_hard_gate_passes(session):
    for iid in ("R", "A", "B"):
        _item(session, iid)
    session.commit()
    _assembly(session, "R", "A")
    _assembly(session, "A", "B")

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=False, user_id=1, user_roles=["admin"]
    )

    assert result["ok"] is True
    assert [row["item_id"] for row in result["plan"]] == ["B", "A", "R"]
    assert {session.get(Item, iid).state for iid in ("R", "A", "B")} == {"Released"}


def test_already_released_descendant_is_skipped(session):
    _item(session, "R")
    _item(session, "C", released=True)
    session.commit()
    _assembly(session, "R", "C")

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=True, user_id=1, user_roles=["admin"]
    )

    by_id = {row["item_id"]: row for row in result["plan"]}
    assert by_id["C"]["action"] == "skip_already_released"
    assert by_id["R"]["action"] == "promote"


def test_unpromotable_child_blocks_before_apply(session):
    _item(session, "R")
    child = _item(session, "C")
    child.state = "Suspended"
    child.current_state = "state_suspended"
    session.commit()
    _assembly(session, "R", "C")

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=False, user_id=1, user_roles=["admin"]
    )

    assert result["ok"] is False
    by_id = {row["item_id"]: row for row in result["plan"]}
    assert by_id["C"]["action"] == "blocked"
    assert by_id["C"]["blocking_reason"] == "transition_missing"
    assert session.get(Item, "R").state == "Draft"


def test_apply_rolls_back_prior_promotions_on_midstream_failure(session, monkeypatch):
    for iid in ("R", "A", "B"):
        _item(session, iid)
    session.commit()
    _assembly(session, "R", "A")
    _assembly(session, "R", "B")

    def _fake_promote(self, item, target_state_name, user_id, comment="", force=False):
        if item.id == "B":
            return PromoteResult(success=False, error="boom")
        item.state = "Released"
        item.current_state = "state_released"
        return PromoteResult(success=True, from_state="Draft", to_state="Released")

    monkeypatch.setattr(LifecycleService, "promote", _fake_promote)

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=False, user_id=1, user_roles=["admin"]
    )

    assert result["ok"] is False
    assert {session.get(Item, iid).state for iid in ("R", "A", "B")} == {"Draft"}


def test_apply_rolls_back_and_returns_plan_when_promote_raises(session, monkeypatch):
    for iid in ("R", "A", "B"):
        _item(session, iid)
    session.commit()
    _assembly(session, "R", "A")
    _assembly(session, "R", "B")

    def _raising_promote(self, item, target_state_name, user_id, comment="", force=False):
        if item.id == "B":
            raise RuntimeError("hook exploded")
        item.state = "Released"
        item.current_state = "state_released"
        return PromoteResult(success=True, from_state="Draft", to_state="Released")

    monkeypatch.setattr(LifecycleService, "promote", _raising_promote)

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=False, user_id=1, user_roles=["admin"]
    )

    assert result["ok"] is False
    assert result["errors"] == [
        {"code": "promote_exception", "item_id": "B", "message": "hook exploded"}
    ]
    by_id = {row["item_id"]: row for row in result["plan"]}
    assert by_id["B"]["blocking_reason"] == "promote_exception"
    assert {session.get(Item, iid).state for iid in ("R", "A", "B")} == {"Draft"}


def test_dangling_cycle_and_max_depth_fail_closed(session):
    for iid in ("R", "A", "B", "C"):
        _item(session, iid)
    session.commit()
    _assembly(session, "R", "A")
    _assembly(session, "A", "R")  # cycle
    _assembly(session, "A", "B")
    _assembly(session, "B", "C")  # over max_depth=2
    _dangling(session, "R")

    result = AssemblyPromotionService(session).promote_assembly(
        "R", dry_run=False, max_depth=2, user_id=1, user_roles=["admin"]
    )

    codes = {err["code"] for err in result["errors"]}
    assert {"cycle_detected", "child_missing", "max_depth_exceeded"} <= codes
    assert result["ok"] is False
    assert {session.get(Item, iid).state for iid in ("R", "A", "B")} == {"Draft"}


def test_per_item_permission_denial_blocks_child(session):
    _item(session, "R")
    _item(session, "C")
    session.commit()
    _assembly(session, "R", "C")

    result = AssemblyPromotionService(session).promote_assembly(
        "R",
        dry_run=True,
        user_id=1,
        user_roles=[],
        permission_checker=lambda item: item.id != "C",
    )

    by_id = {row["item_id"]: row for row in result["plan"]}
    assert by_id["C"]["action"] == "blocked"
    assert by_id["C"]["blocking_reason"] == "permission_denied"
    assert result["ok"] is False


def test_router_happy_dry_run_and_apply_failure_mapping(session):
    _item(session, "R")
    child = _item(session, "C")
    child.state = "Suspended"
    child.current_state = "state_suspended"
    session.commit()
    _assembly(session, "R", "C")

    out = _run(
        promote_assembly(
            "R",
            PromoteAssemblyRequest(dry_run=True),
            user=_USER,
            db=session,
        )
    )
    assert out["dry_run"] is True
    assert out["ok"] is False

    out = _run(
        promote_assembly(
            "R",
            PromoteAssemblyRequest(dry_run=False),
            user=_USER,
            db=session,
        )
    )
    assert out["dry_run"] is False
    assert out["ok"] is False
    assert session.get(Item, "R").state == "Draft"


def test_router_rejects_non_part_and_request_model_rejects_non_released(session):
    _doc(session, "D")
    session.commit()
    with pytest.raises(Exception) as exc:
        _run(
            promote_assembly(
                "D",
                PromoteAssemblyRequest(),
                user=_USER,
                db=session,
            )
        )
    assert getattr(exc.value, "status_code", None) == 400

    with pytest.raises(ValidationError):
        PromoteAssemblyRequest(target_state="Obsolete")
