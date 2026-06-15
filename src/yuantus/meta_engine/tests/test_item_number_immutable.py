"""WP3.2 (B3): item_number / number is immutable once assigned.

A normal ``update`` AML action may not change a non-empty existing
``item_number`` (or its ``number`` alias) to a different non-empty value. Only an
``admin`` / ``superuser`` role may override, and the override is audited (a WARNING
log). First assignment, no-op re-submits, and edits to unrelated fields are
unaffected. Both aliases stay in sync via ``ensure_item_number_aliases``.

The guard lives in ``operations/update_op.py`` (after permission + lock checks). The
test drives ``UpdateOperation.execute`` directly over an in-memory SQLite session with
an injected (``SimpleNamespace``) engine, monkeypatching the lifecycle-lock and
event-bus seams so the test pins the immutability behaviour, not lifecycle wiring.
"""

from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.exceptions.handlers import ValidationError
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.operations import update_op as update_op_mod
from yuantus.meta_engine.operations.update_op import UpdateOperation
from yuantus.meta_engine.schemas.aml import GenericItem
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'item-number-immutable.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(ItemType(id="Part", label="Part", is_versionable=True))
    db.commit()
    yield db
    db.close()


@pytest.fixture(autouse=True)
def _isolate_seams(monkeypatch):
    # The guard sits after the permission + lock checks and before the event emit;
    # neutralise the lifecycle-lock and event-bus seams so the test pins immutability.
    monkeypatch.setattr(update_op_mod, "is_item_locked", lambda *a, **k: (False, None))
    monkeypatch.setattr(update_op_mod, "enqueue_event", lambda *a, **k: None)


def _part(session, item_id: str, *, number: str | None) -> Item:
    props = {"name": f"name-{item_id}"}
    if number is not None:
        props["item_number"] = number
        props["number"] = number
    item = Item(
        id=item_id,
        item_type_id="Part",
        config_id=f"cfg-{item_id}-{uuid.uuid4()}",
        generation=1,
        is_current=True,
        state="Draft",
        properties=props,
    )
    session.add(item)
    session.flush()
    return item


def _op(session, roles):
    engine = SimpleNamespace(
        session=session,
        permission_service=SimpleNamespace(check_permission=lambda *a, **k: True),
        user_id="1",
        roles=roles,
        validator=SimpleNamespace(validate_and_normalize=lambda item_type, props: props),
        method_executor=None,
    )
    return UpdateOperation(engine)


_PART_TYPE = ItemType(id="Part")


def test_change_assigned_item_number_rejected_for_normal_role(session):
    _part(session, "I1", number="P-001")
    aml = GenericItem(id="I1", type="Part", properties={"item_number": "P-002"})
    with pytest.raises(ValidationError, match="immutable"):
        _op(session, ["engineer"]).execute(_PART_TYPE, aml)
    # unchanged in the DB
    session.expire_all()
    assert session.get(Item, "I1").properties["item_number"] == "P-001"


def test_change_via_number_alias_also_rejected(session):
    # supplying the change through the ``number`` alias is caught the same way
    _part(session, "I1", number="P-001")
    aml = GenericItem(id="I1", type="Part", properties={"number": "P-999"})
    with pytest.raises(ValidationError, match="immutable"):
        _op(session, ["engineer"]).execute(_PART_TYPE, aml)


def test_first_assignment_is_allowed(session):
    _part(session, "I1", number=None)  # no number yet
    aml = GenericItem(id="I1", type="Part", properties={"item_number": "P-001"})
    _op(session, ["engineer"]).execute(_PART_TYPE, aml)
    props = session.get(Item, "I1").properties
    assert props["item_number"] == "P-001"
    assert props["number"] == "P-001"  # alias synced


def test_resubmitting_same_number_and_editing_other_fields_allowed(session):
    _part(session, "I1", number="P-001")
    aml = GenericItem(id="I1", type="Part", properties={"item_number": "P-001", "name": "renamed"})
    _op(session, ["engineer"]).execute(_PART_TYPE, aml)
    props = session.get(Item, "I1").properties
    assert props["item_number"] == "P-001"
    assert props["name"] == "renamed"


def test_editing_other_fields_without_number_allowed(session):
    _part(session, "I1", number="P-001")
    aml = GenericItem(id="I1", type="Part", properties={"name": "renamed-only"})
    _op(session, ["engineer"]).execute(_PART_TYPE, aml)
    props = session.get(Item, "I1").properties
    assert props["item_number"] == "P-001"  # preserved
    assert props["number"] == "P-001"
    assert props["name"] == "renamed-only"


@pytest.mark.parametrize("override_role", ["admin", "superuser"])
def test_admin_override_allowed_audited_and_aliases_synced(session, caplog, override_role):
    _part(session, "I1", number="P-001")
    aml = GenericItem(id="I1", type="Part", properties={"item_number": "P-002"})
    with caplog.at_level(logging.WARNING, logger=update_op_mod.__name__):
        _op(session, [override_role]).execute(_PART_TYPE, aml)
    props = session.get(Item, "I1").properties
    assert props["item_number"] == "P-002"
    assert props["number"] == "P-002"  # both aliases moved together
    # audited
    assert "item_number immutability overridden" in caplog.text
    assert "I1" in caplog.text
