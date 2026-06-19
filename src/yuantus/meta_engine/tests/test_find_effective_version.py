"""Regression coverage for ``VersionService.find_effective_version`` date semantics.

The helper previously excluded NULL-``start_date`` (open-start) effectivities via a bare
``start_date <= target_date`` filter, so an open-start version that IS effective was wrongly
treated as not-found (a 404 on the read route). The fix mirrors the canonical
``EffectivityService._check_date``: a NULL bound is open (-inf / +inf).

These are the first direct tests of the helper's query logic; they pin both the fix and the
deliberately-unchanged behavior (a version with no Date effectivity is not returned, and the
newest version wins a tie) so the semantics stay stable. DB-free (sqlite in-memory).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.version.service import VersionService
from yuantus.models.base import Base

_NOW = datetime(2026, 6, 18, 12, 0, 0)
_PAST = _NOW - timedelta(days=2)
_FUTURE = _NOW + timedelta(days=30)


@pytest.fixture()
def db():
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.models import user as _user  # noqa: F401

    import_all_models()
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        yield s
    finally:
        s.close()


def _ver(db, vid, *, item_id="I", created_at=None):
    kw = dict(id=vid, item_id=item_id, state="Released", is_current=True, is_released=True)
    if created_at is not None:
        kw["created_at"] = created_at
    db.add(ItemVersion(**kw))


def _eff(db, eid, version_id, *, start, end, etype="Date"):
    db.add(
        Effectivity(
            id=eid, version_id=version_id, effectivity_type=etype,
            start_date=start, end_date=end,
        )
    )


# -- the fix: open-start (NULL start) is effective from the beginning ----------
def test_open_start_version_is_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=None, end=_FUTURE)  # open-start, still in window
    db.commit()
    ver = VersionService(db).find_effective_version("I", _NOW)
    assert ver is not None and ver.id == "v1"


def test_fully_open_window_is_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=None, end=None)  # -inf .. +inf
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW).id == "v1"


# -- unchanged correct cases --------------------------------------------------
def test_bounded_window_covering_date_is_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=_PAST, end=_FUTURE)
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW).id == "v1"


def test_open_end_version_is_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=_PAST, end=None)  # open-end
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW).id == "v1"


def test_future_start_is_not_yet_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=_FUTURE, end=None)
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW) is None


def test_expired_window_is_not_effective(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=_PAST - timedelta(days=10), end=_PAST)
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW) is None


# -- pinned (deliberately unchanged) behavior ---------------------------------
def test_version_with_no_date_effectivity_is_not_returned(db):
    # PINNED: find_effective_version answers "which version's Date window covers this date",
    # so it requires a Date effectivity row. A version with none is NOT returned (distinct
    # from EffectivityService.check_effectivity, where an item with no effectivity is always
    # effective). Changing this would be a separate, ratifiable semantic decision.
    _ver(db, "v1")
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW) is None


def test_non_date_effectivity_is_ignored(db):
    _ver(db, "v1")
    _eff(db, "e1", "v1", start=None, end=None, etype="Lot")  # not a Date window
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW) is None


# -- tie-break: the fix widens the candidate set, so pin created_at desc -------
def test_multiple_matching_versions_returns_newest(db):
    _ver(db, "old", created_at=datetime(2026, 1, 1, 0, 0, 0))
    _ver(db, "new", created_at=datetime(2026, 6, 1, 0, 0, 0))
    _eff(db, "e-old", "old", start=_PAST, end=_FUTURE)
    _eff(db, "e-new", "new", start=None, end=None)  # open-start now also a candidate
    db.commit()
    assert VersionService(db).find_effective_version("I", _NOW).id == "new"
