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


# -- BOM-line (item_id-scoped) flag-only path --------------------------------
def _bom_line(db, rid, parent, child, *, is_current=True, item_type_id="Part BOM", **props):
    # a real BOM line is item_type_id="Part BOM", is_current=True, source_id=parent,
    # related_id=child. (is_current/item_type_id are overridable to exercise the guards.)
    db.add(Item(id=rid, config_id=rid, item_type_id=item_type_id, source_id=parent,
                related_id=child, is_current=is_current, properties=props or {}))


def _eff_item(db, eid, item_id, *, end_date, start=_PAST - timedelta(days=10)):
    e = Effectivity(id=eid, item_id=item_id, version_id=None, effectivity_type="Date",
                    start_date=start, end_date=end_date)
    db.add(e)
    return e


def test_bom_line_expiry_flags_the_line_only(db):
    _item(db, "P"); _item(db, "C")
    _bom_line(db, "L1", "P", "C", uom="EA", quantity=2, find_num="10")
    e = _eff_item(db, "e-bl", "L1", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    db.commit()
    assert res["status"] == "processed" and res["scope"] == "bom_line"
    assert res["parent_item_id"] == "P" and res["child_item_id"] == "C"
    assert res["reason"] == "bom_line_effectivity_expired"
    assert res["child_obsoleted"] is False and res["flagged_parents"] == 1
    flag = db.query(DateObsoleteImpact).one()
    assert flag.parent_item_id == "P" and flag.child_item_id == "C"
    assert flag.reason == "bom_line_effectivity_expired" and flag.child_obsoleted is False
    assert (flag.properties or {}).get("bom_line_id") == "L1"
    assert (flag.properties or {}).get("uom") == "EA"
    # flag-only: neither part nor assembly is promoted
    assert db.get(Item, "C").state == "Released" and db.get(Item, "P").state == "Released"


def test_bom_line_expiry_is_idempotent(db):
    _item(db, "P"); _item(db, "C"); _bom_line(db, "L1", "P", "C")
    e = _eff_item(db, "e-bl", "L1", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    svc.process_expired(e, user_id=1, now=_NOW); db.commit()
    assert db.query(DateObsoleteImpact).count() == 1


def test_multi_uom_same_parent_child_two_lines_each_flagged(db):
    # guard (d): two BOM lines between the SAME parent and child (e.g. different UOM) are
    # distinct relationship Items with distinct effectivities -> each expired line produces
    # its OWN impact (distinct effectivity_id), neither swallowing the other.
    _item(db, "P"); _item(db, "C")
    _bom_line(db, "L-ea", "P", "C", uom="EA")
    _bom_line(db, "L-kg", "P", "C", uom="KG")
    e1 = _eff_item(db, "e-ea", "L-ea", end_date=_PAST)
    e2 = _eff_item(db, "e-kg", "L-kg", end_date=_PAST)
    db.commit()
    svc = DateEffectivityObsoleteService(db)
    svc.process_expired(e1, user_id=1, now=_NOW); db.commit()
    svc.process_expired(e2, user_id=1, now=_NOW); db.commit()
    impacts = db.query(DateObsoleteImpact).all()
    assert len(impacts) == 2
    assert {i.effectivity_id for i in impacts} == {"e-ea", "e-kg"}
    assert {(i.properties or {}).get("uom") for i in impacts} == {"EA", "KG"}
    assert all(i.parent_item_id == "P" and i.child_item_id == "C" for i in impacts)


def test_item_scoped_effectivity_on_non_bom_item_is_skipped(db):
    # guard (a): an item_id-scoped effectivity whose target is NOT a "Part BOM" relationship
    # (e.g. a plain part) must be skipped, never mistaken for a BOM line.
    _item(db, "X")  # item_type_id="t-part", not a Part BOM, no source/related
    e = _eff_item(db, "e-x", "X", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "not_a_bom_line"
    assert db.query(DateObsoleteImpact).count() == 0


def test_non_part_bom_relationship_with_endpoints_is_skipped(db):
    # guard (a), type discriminator: a relationship Item with BOTH endpoints but
    # item_type_id != "Part BOM" (e.g. a legacy "t-bom") must NOT be treated as a BOM line.
    _item(db, "P"); _item(db, "C")
    _bom_line(db, "L-legacy", "P", "C", item_type_id="t-bom", uom="EA")
    e = _eff_item(db, "e-legacy", "L-legacy", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "not_a_bom_line"
    assert db.query(DateObsoleteImpact).count() == 0


def test_superseded_bom_line_is_not_flagged(db):
    # a superseded (is_current=False) Part BOM line is no longer a live usage (bom_obsolete
    # supersedes in place and copies the effectivity to the new current line), so it must NOT
    # be flagged — only the current line carries the live effectivity.
    _item(db, "P"); _item(db, "C")
    _bom_line(db, "L-old", "P", "C", is_current=False, uom="EA")
    e = _eff_item(db, "e-old", "L-old", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "bom_line_not_current"
    assert db.query(DateObsoleteImpact).count() == 0


def test_bom_line_effectivity_with_missing_item_is_skipped(db):
    e = _eff_item(db, "e-missing", "ghost-line", end_date=_PAST)
    db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "bom_line_not_found"


def test_unscoped_effectivity_is_skipped(db):
    # neither version_id nor item_id -> not_scoped
    e = Effectivity(id="e-none", version_id=None, item_id=None, effectivity_type="Date",
                    start_date=_PAST - timedelta(days=10), end_date=_PAST)
    db.add(e); db.commit()
    res = DateEffectivityObsoleteService(db).process_expired(e, user_id=1, now=_NOW)
    assert res["status"] == "skipped" and res["reason"] == "not_scoped"


def test_scan_expired_includes_bom_line_effectivities(db):
    # scan_expired now returns BOTH version- and BOM-line-scoped expired effectivities.
    _version(db, "v1", "c1"); _eff(db, "e-ver", "v1", end_date=_PAST)
    _item(db, "P"); _item(db, "C"); _bom_line(db, "L1", "P", "C")
    _eff_item(db, "e-bl", "L1", end_date=_PAST)
    db.commit()
    found = {e.id for e in DateEffectivityObsoleteService(db).scan_expired(now=_NOW)}
    assert {"e-ver", "e-bl"} <= found
