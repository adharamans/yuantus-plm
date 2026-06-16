"""ECM-P1A: release() hook-point hardening — release provenance + no mis-stamp.

`VersionService.release(item_id, user_id)` is the canonical runtime `is_released`
writer. P1A makes it stamp `released_at` / `released_by_id` on the version being
released (previously unset) — the seam the ECM publish enqueue hook (P1B) will read.
This pins:
- the just-released version gets `released_at` (a timestamp) + `released_by_id == user_id`;
- the superseded predecessor keeps its OWN original provenance (no mis-stamp);
- an already-released version is idempotent (early return, no re-stamp);
- the B1 supersede / lock-release behavior is unchanged.

No ECM code yet — pure release hardening. `ChangeService._release_version` is left
fenced (deprecated legacy surface), guarded by `test_version_supersede_b1`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.version.service import VersionService
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'release-hardening.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    yield db
    db.close()


def _item(session, iid: str) -> Item:
    it = Item(
        id=iid,
        item_type_id="Part",
        config_id=f"c-{iid}-{uuid.uuid4()}",
        generation=1,
        is_current=True,
    )
    session.add(it)
    session.flush()
    return it


def _version(
    session,
    item: Item,
    *,
    gen: int = 1,
    rev: str = "A",
    released: bool,
    current: bool,
    predecessor_id: str | None = None,
    superseded: bool = False,
    released_at: datetime | None = None,
    released_by_id: int | None = None,
) -> ItemVersion:
    v = ItemVersion(
        id=f"v-{uuid.uuid4()}",
        item_id=item.id,
        generation=gen,
        revision=rev,
        version_label=f"{gen}.{rev}",
        state="Released" if released else "Draft",
        is_current=current,
        is_released=released,
        is_superseded=superseded,
        predecessor_id=predecessor_id,
        branch_name="main",
        released_at=released_at,
        released_by_id=released_by_id,
    )
    session.add(v)
    session.flush()
    if current:
        item.current_version_id = v.id
        session.flush()
    return v


def test_release_stamps_released_at_and_released_by(session):
    item = _item(session, "P")
    v = _version(session, item, rev="A", released=False, current=True)
    session.commit()

    before = datetime.utcnow()
    released = VersionService(session).release(item.id, user_id=7)

    assert released.is_released is True
    assert released.state == "Released"
    assert released.released_by_id == 7
    assert released.released_at is not None
    assert released.released_at >= before  # stamped during this call


def test_release_does_not_restamp_superseded_predecessor(session):
    # vN released earlier by user 1 with its own provenance; vN+1 open draft on the line.
    item = _item(session, "P")
    original_at = datetime(2026, 1, 1, 0, 0, 0)
    v1 = _version(
        session, item, gen=1, rev="A", released=True, current=False,
        released_at=original_at, released_by_id=1,
    )
    _version(
        session, item, gen=1, rev="B", released=False, current=True,
        predecessor_id=v1.id,
    )
    session.commit()

    VersionService(session).release(item.id, user_id=2)
    session.refresh(v1)

    # the supersede hook touched v1's status...
    assert v1.is_superseded is True
    assert v1.state == "Superseded"
    # ...but must NOT have re-stamped its release provenance (only the new version is stamped)
    assert v1.released_at == original_at
    assert v1.released_by_id == 1


def test_already_released_release_is_idempotent_no_restamp(session):
    # release() early-returns on an already-released current version -> no re-stamp.
    item = _item(session, "P")
    original_at = datetime(2026, 1, 1, 0, 0, 0)
    v = _version(
        session, item, rev="A", released=True, current=True,
        released_at=original_at, released_by_id=1,
    )
    session.commit()

    out = VersionService(session).release(item.id, user_id=2)

    assert out.id == v.id
    assert out.released_at == original_at  # unchanged
    assert out.released_by_id == 1  # not re-stamped to user 2
