"""CAD-PDM C3 date-BOM auto-obsolete mechanism (Slice 1, unwired)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.meta_engine.models.date_obsolete import DateObsoleteImpact
from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.date_effectivity_obsolete_service import (
    DateEffectivityObsoleteService,
)
from yuantus.meta_engine.services.effectivity_service import EffectivityService
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser

_NOW = datetime(2026, 6, 18, 12, 0, 0)
_PAST = _NOW - timedelta(days=2)
_FUTURE = _NOW + timedelta(days=30)


@pytest.fixture()
def db():
    # Register the full model set so cross-model FK targets (users, meta_item_types,
    # …) exist, then create everything. DateObsoleteImpact is imported above.
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.models import user as _user  # noqa: F401  (registers the 'users' table)

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


def _item(db, iid, state="Released"):
    db.add(Item(id=iid, config_id=iid, item_type_id="t-part", state=state, is_current=True, properties={}))


def _rel(db, rid, parent, child):
    # relationship items ARE Items: source_id=parent, related_id=child (get_where_used).
    db.add(Item(id=rid, config_id=rid, item_type_id="t-bom", source_id=parent, related_id=child, is_current=True, properties={}))


def _version(db, vid, item_id):
    db.add(ItemVersion(id=vid, item_id=item_id, state="Released", is_current=True, is_released=True))


def _eff(db, eid, version_id, *, end_date, start=_PAST - timedelta(days=10)):
    e = Effectivity(id=eid, version_id=version_id, effectivity_type="Date", start_date=start, end_date=end_date)
    db.add(e)
    return e


# -- expired query -----------------------------------------------------------
def test_get_expired_excludes_open_ended_and_future(db):
    _version(db, "v1", "c1"); _version(db, "v2", "c2"); _version(db, "v3", "c3")
    _eff(db, "e-expired", "v1", end_date=_PAST)
    _eff(db, "e-open", "v2", end_date=None)       # open-ended => never expired
    _eff(db, "e-future", "v3", end_date=_FUTURE)  # still effective
    db.add(Effectivity(id="e-lot", version_id="v1", effectivity_type="Lot", end_date=_PAST))  # not Date
    db.commit()
    expired = EffectivityService(db).get_expired_date_effectivities(now=_NOW)
    assert {e.id for e in expired} == {"e-expired"}


# -- mark when an effective version remains ----------------------------------
def test_marks_not_obsolete_when_effective_version_remains(db):
    _item(db, "C"); _item(db, "P"); _rel(db, "R", "P", "C")
    _version(db, "vC1", "C"); _version(db, "vC2", "C")
    e1 = _eff(db, "e1", "vC1", end_date=_PAST)   # expired
    _eff(db, "e2", "vC2", end_date=_FUTURE)      # C still has an effective version
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    res = svc.process_expired(e1, user_id=1, now=_NOW)
    db.commit()
    assert res["has_effective_version"] is True
    assert res["child_obsoleted"] is False        # NOT obsoleted
    assert res["flagged_parents"] == 1
    flag = db.query(DateObsoleteImpact).one()
    assert flag.parent_item_id == "P" and flag.child_item_id == "C"
    assert flag.reason == "child_effectivity_expired" and flag.state == "open"
    assert db.get(Item, "C").state == "Released"  # untouched


# -- obsolete when no effective version remains ------------------------------
def test_obsoletes_when_no_effective_version(db, monkeypatch):
    _item(db, "C2"); _item(db, "P2"); _rel(db, "R2", "P2", "C2")
    _version(db, "vC2x", "C2")
    e = _eff(db, "e3", "vC2x", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    # isolate from the lifecycle-map machinery: mock promote to succeed.
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: SimpleNamespace(success=True, error=None))
    res = svc.process_expired(e, user_id=1, now=_NOW)
    db.commit()
    assert res["has_effective_version"] is False
    assert res["child_obsoleted"] is True
    assert res["obsolete_error"] is None
    assert db.query(DateObsoleteImpact).one().reason == "child_obsoleted"


def test_already_obsolete_skips_promote(db, monkeypatch):
    _item(db, "C3", state="Obsolete"); _item(db, "P3"); _rel(db, "R3", "P3", "C3")
    _version(db, "vC3", "C3")
    e = _eff(db, "e4", "vC3", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    called = {"n": 0}
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: called.__setitem__("n", called["n"] + 1) or SimpleNamespace(success=True))
    res = svc.process_expired(e, user_id=1, now=_NOW)
    db.commit()
    assert called["n"] == 0                  # already Obsolete -> no promote attempt
    assert res["child_obsoleted"] is True


# -- depth-1 only, never cascade ---------------------------------------------
def test_depth_1_only_no_cascade(db):
    _item(db, "C"); _item(db, "P"); _item(db, "GP")
    _rel(db, "R-pc", "P", "C")    # P uses C
    _rel(db, "R-gp", "GP", "P")   # GP uses P
    _version(db, "vC", "C")
    e = _eff(db, "e5", "vC", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    svc.process_expired(e, user_id=1, now=_NOW)
    db.commit()
    flagged = {f.parent_item_id for f in db.query(DateObsoleteImpact).all()}
    assert flagged == {"P"}        # GP (depth-2) is NOT flagged


# -- idempotent --------------------------------------------------------------
def test_idempotent_rerun_no_duplicate_flag(db, monkeypatch):
    _item(db, "C"); _item(db, "P"); _rel(db, "R", "P", "C")
    _version(db, "vC", "C")
    e = _eff(db, "e6", "vC", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: SimpleNamespace(success=True, error=None))
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    assert db.query(DateObsoleteImpact).count() == 1


# -- survivor versions the codebase still considers effective -----------------
def test_open_start_null_start_version_keeps_item_effective(db):
    # a surviving version with NULL start_date (open-start) + future end IS effective
    # -> the item is NOT obsoleted (regression: find_effective_version excluded NULL-start).
    _item(db, "C"); _item(db, "P"); _rel(db, "R", "P", "C")
    _version(db, "vC1", "C"); _version(db, "vC2", "C")
    e1 = _eff(db, "e1", "vC1", end_date=_PAST)                  # expired trigger
    _eff(db, "e2", "vC2", end_date=_FUTURE, start=None)         # open-start, still effective
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e1, user_id=1, now=_NOW)
    db.commit()
    assert res["has_effective_version"] is True
    assert res["child_obsoleted"] is False
    assert db.get(Item, "C").state == "Released"


def test_no_date_effectivity_version_keeps_item_effective(db):
    # a sibling version with NO date effectivity is unbounded => always effective =>
    # item NOT obsoleted (regression: find_effective_version excluded no-effectivity).
    _item(db, "C"); _item(db, "P"); _rel(db, "R", "P", "C")
    _version(db, "vC1", "C"); _version(db, "vNoEff", "C")
    e1 = _eff(db, "e1", "vC1", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e1, user_id=1, now=_NOW)
    db.commit()
    assert res["has_effective_version"] is True
    assert res["child_obsoleted"] is False


# -- failed obsolete is distinct + persisted ---------------------------------
def test_promote_failure_reason_is_distinct_and_persisted(db, monkeypatch):
    _item(db, "C2"); _item(db, "P2"); _rel(db, "R2", "P2", "C2")
    _version(db, "vC2x", "C2")
    e = _eff(db, "e3", "vC2x", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: SimpleNamespace(success=False, error="no_lifecycle_map"))
    res = svc.process_expired(e, user_id=1, now=_NOW)
    db.commit()
    assert res["child_obsoleted"] is False
    assert res["reason"] == "child_obsolete_failed"   # NOT confused with a deliberate mark
    flag = db.query(DateObsoleteImpact).one()
    assert flag.reason == "child_obsolete_failed"
    assert flag.child_obsoleted is False
    assert (flag.properties or {}).get("obsolete_error") == "no_lifecycle_map"


def test_rescan_refreshes_reason_on_flip(db, monkeypatch):
    # scan 1: promote fails -> child_obsolete_failed; after a config fix, scan 2: promote
    # succeeds -> the SAME flag must flip to child_obsoleted (no stale/contradictory row).
    _item(db, "C"); _item(db, "P"); _rel(db, "R", "P", "C")
    _version(db, "vC", "C")
    e = _eff(db, "e9", "vC", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: SimpleNamespace(success=False, error="x"))
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    assert db.query(DateObsoleteImpact).one().reason == "child_obsolete_failed"
    monkeypatch.setattr(svc.lifecycle, "promote", lambda *a, **k: SimpleNamespace(success=True, error=None))
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    flag = db.query(DateObsoleteImpact).one()          # still one (idempotent)
    assert flag.reason == "child_obsoleted" and flag.child_obsoleted is True
    assert flag.properties is None                     # error payload cleared on success


# -- out-of-scope: non-version-scoped effectivity ----------------------------
def test_skips_non_version_scoped(db):
    e = Effectivity(id="e7", item_id="some-bom-line", version_id=None, effectivity_type="Date", end_date=_PAST)
    db.add(e); db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "not_version_scoped"
