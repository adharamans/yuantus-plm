"""B1: version Superseded signal + concurrent-revision guard.

Version-LEVEL semantics on ``ItemVersion`` (NOT an Item lifecycle state):
- D2 supersede: releasing vN+1 marks the immediate prior released version
  ``Superseded`` (keeps ``is_released=True`` per Q-A; not re-superseded).
- D3 under-modification: derived read-time predicate, no stored state.
- D4 app-guard: revise()/new_generation() require a released source.
- D4b DB guard: a partial-unique index makes a concurrent double-revise (two open
  current drafts on a line) impossible -- tested via BOTH create_all (model index)
  AND the real migration (closes the model-vs-migration match gap). The expression
  index is NOT reflectable, so enforcement is always asserted behaviourally.
- D5 merge_branch inherits target branch_name (no mainline collision).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.version.service import VersionError, VersionService
from yuantus.models import user as _user  # noqa: F401 - registers users table
from yuantus.models.base import Base

import_all_models()


@pytest.fixture()
def session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'b1-supersede.db'}",
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
    branch: str = "main",
    superseded: bool = False,
    checked_out_by: int | None = None,
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
        branch_name=branch,
        checked_out_by_id=checked_out_by,
    )
    session.add(v)
    session.flush()
    if current:
        item.current_version_id = v.id
        session.flush()
    return v


# ---------- D2: supersede on release ------------------------------------------
def test_release_supersedes_immediate_predecessor(session):
    item = _item(session, "P")
    v1 = _version(session, item, gen=1, rev="A", released=True, current=False)
    v2 = _version(
        session, item, gen=1, rev="B", released=False, current=True,
        predecessor_id=v1.id,
    )
    session.commit()

    VersionService(session).release(item.id, user_id=1)
    session.refresh(v1)
    session.refresh(v2)
    assert v1.is_superseded is True
    assert v1.state == "Superseded"
    assert v1.is_released is True  # Q-A: kept (it WAS released)
    assert v2.is_released is True
    assert v2.is_superseded is False


def test_release_only_supersedes_immediate_not_already_superseded(session):
    # v1 released+superseded, v2 released (current), v3 draft (current after revise).
    item = _item(session, "P")
    v1 = _version(
        session, item, gen=1, rev="A", released=True, current=False, superseded=True
    )
    v2 = _version(
        session, item, gen=1, rev="B", released=True, current=False,
        predecessor_id=v1.id,
    )
    v3 = _version(
        session, item, gen=1, rev="C", released=False, current=True,
        predecessor_id=v2.id,
    )
    session.commit()

    VersionService(session).release(item.id, user_id=1)
    session.refresh(v1)
    session.refresh(v2)
    assert v2.is_superseded is True  # immediate predecessor superseded
    assert v1.is_superseded is True  # already superseded -> untouched (not an error)
    assert v1.state == "Released"  # NOT re-written to "Superseded" (predicate guard)


def test_release_leaf_no_predecessor_no_supersede(session):
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=False, current=True)
    session.commit()
    # No predecessor -> nothing to supersede, release succeeds.
    v = VersionService(session).release(item.id, user_id=1)
    assert v.is_released is True


# ---------- D4: app-guard ------------------------------------------------------
def test_revise_rejects_unreleased_source(session):
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=False, current=True)
    session.commit()
    with pytest.raises(VersionError, match="unreleased"):
        VersionService(session).revise(item.id, user_id=1)


def test_new_generation_rejects_unreleased_source(session):
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=False, current=True)
    session.commit()
    with pytest.raises(VersionError, match="unreleased"):
        VersionService(session).new_generation(item.id, user_id=1)


def test_revise_released_source_succeeds(session):
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=True, current=True)
    session.commit()
    new_ver = VersionService(session).revise(item.id, user_id=1)
    assert new_ver.revision == "B"
    assert new_ver.is_released is False


# ---------- D4b: DB constraint ITSELF (model create_all index) -----------------
def test_db_constraint_rejects_second_open_draft(session):
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=False, current=True)
    session.commit()
    # A second OPEN current draft on the same line+branch must be impossible at the
    # DB level (the concurrent-revise race backstop), independent of the app-guard.
    session.add(
        ItemVersion(
            id="dup", item_id="P", generation=1, revision="A2",
            version_label="1.A2", state="Draft", is_current=True,
            is_released=False, branch_name="main",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_db_constraint_allows_released_prev_plus_open_draft(session):
    # released (is_current False) + open current draft on the same line: allowed.
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=True, current=False)
    _version(session, item, gen=1, rev="B", released=False, current=True)
    session.commit()  # must NOT raise
    open_drafts = (
        session.query(ItemVersion)
        .filter(
            ItemVersion.item_id == "P",
            ItemVersion.is_current.is_(True),
            ItemVersion.is_released.is_(False),
        )
        .count()
    )
    assert open_drafts == 1


# ---------- D3: under-modification (derived) ----------------------------------
def test_under_modification_predicate(session):
    svc = VersionService(session)
    # released current -> not under modification
    item = _item(session, "P")
    _version(session, item, gen=1, rev="A", released=True, current=True)
    session.commit()
    assert svc.is_under_modification("P") is False
    # revise -> open draft current with a released predecessor -> under modification
    svc.revise("P", user_id=1)
    session.commit()
    assert svc.is_under_modification("P") is True
    # brand-new item with only a never-released draft -> NOT under modification
    item2 = _item(session, "Q")
    _version(session, item2, gen=1, rev="A", released=False, current=True)
    session.commit()
    assert svc.is_under_modification("Q") is False


# ---------- D5: merge_branch inherits branch_name -----------------------------
def test_merge_branch_inherits_target_branch_name(session):
    # Target version lives on a non-'main' branch; the merge result must stay on it,
    # not default to 'main' (which would collide with the mainline open-current index).
    item = _item(session, "P")
    # mainline released current
    main_v = _version(session, item, gen=1, rev="A", released=True, current=True)
    # a branch version (parallel, not current), checked out by the merging user
    branch_v = _version(
        session, item, gen=1, rev="A", released=False, current=False,
        branch="feature-x", predecessor_id=main_v.id, checked_out_by=1,
    )
    session.commit()

    merged = VersionService(session).merge_branch(
        item.id, source_version_id=main_v.id, target_version_id=branch_v.id, user_id=1
    )
    assert merged.branch_name == "feature-x"  # inherited target branch, not 'main'


def test_merge_branch_into_branch_not_caught_by_mainline_index(session):
    # An open mainline draft AND an open feature-branch tip coexist (allowed: the
    # partial-unique is per (item, branch)). Merging into the feature branch yields
    # an open-current feature-x version -- which would COLLIDE with the mainline open
    # draft if branch_name defaulted to 'main'. With inheritance (D5) it stays on
    # feature-x, so the commit succeeds.
    item = _item(session, "P")
    main_open = _version(session, item, gen=1, rev="B", released=False, current=True)
    branch_v = _version(
        session, item, gen=1, rev="A", released=False, current=True,
        branch="feature-x", checked_out_by=1,
    )
    session.commit()
    merged = VersionService(session).merge_branch(
        item.id, source_version_id=main_open.id, target_version_id=branch_v.id,
        user_id=1,
    )
    session.commit()  # must NOT raise -- merged is on feature-x, not 'main'
    assert merged.branch_name == "feature-x"
    assert merged.is_current is True
    # the mainline open draft is untouched and still the sole open-current on 'main'
    session.refresh(main_open)
    assert main_open.is_current is True and main_open.branch_name == "main"


# ---------- model<->migration lock-step (closes the create_all-vs-migration gap) -
def test_model_and_migration_index_are_lockstep():
    from pathlib import Path

    # Model side: the partial-unique index exists on the table (its ENFORCEMENT is
    # proven by test_db_constraint_rejects_second_open_draft via create_all).
    assert "uq_itemversion_open_current_per_line" in {
        ix.name for ix in ItemVersion.__table__.indexes
    }
    # Migration side: SAME name + NULL-safe COALESCE + both dialect predicates.
    # (The migration was verified to enforce end-to-end via `alembic upgrade head`
    # at authoring; this guards the two definitions against silent divergence.)
    mig = (
        Path(__file__).resolve().parents[4]
        / "migrations"
        / "versions"
        / "b1_supersede_001_add_itemversion_supersede_and_open_current_guard.py"
    ).read_text()
    assert "uq_itemversion_open_current_per_line" in mig
    assert "coalesce(branch_name, 'main')" in mig
    assert "is_current IS TRUE AND is_released IS NOT TRUE" in mig  # postgresql_where
    assert "is_current = 1 AND is_released = 0" in mig  # sqlite_where


# ---------- B1 taskbook #734 §5: static guard on the deprecated ChangeService ----
def test_no_runtime_use_of_deprecated_changeservice_release_path():
    """The release() supersede hook (D2) relies on VersionService.release() being
    the SOLE runtime release point. The deprecated ChangeService
    (services/change_service.py) still sets ItemVersion.is_released=True directly via
    its `_release_version` (test-only, per the module header). Assert NO runtime
    source imports or uses ChangeService / that path -- a runtime use would bypass
    the supersede hook and silently break the invariant. (Taskbook §5 deliverable.)"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "src" / "yuantus"
    # Non-vacuous: the root resolves AND the protected pattern really exists in the
    # (excluded) deprecated module -- so the guard is checking for a real path, not
    # passing because the scan reached nothing.
    cs = root / "meta_engine" / "services" / "change_service.py"
    assert cs.is_file() and "_release_version(" in cs.read_text(encoding="utf-8")

    forbidden = ("import ChangeService", "ChangeService(", "_release_version(")
    offenders = []
    for py in root.rglob("*.py"):
        rel = py.relative_to(root).as_posix()
        if "/tests/" in f"/{rel}" or rel.rsplit("/", 1)[-1].startswith("test_"):
            continue
        if rel.endswith("services/change_service.py"):
            continue  # the deprecated module itself defines the path
        for line in py.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lstrip().startswith("#"):
                continue  # a commented-out reference is not runtime use
            if any(tok in line for tok in forbidden):
                offenders.append(f"{rel}: {line.strip()}")
    assert not offenders, (
        "runtime use of the deprecated ChangeService release path (would bypass "
        "release()'s supersede hook):\n" + "\n".join(offenders)
    )
