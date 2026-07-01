"""CAD-PDM C3 Slice 2 — worker gating + drain, and admin ops routes."""
from __future__ import annotations

import csv
import json
import uuid
from datetime import datetime, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.context import tenant_id_var
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.models.date_obsolete import (
    DateObsoleteImpact,
    DateObsoleteImpactCorrection,
)
from yuantus.meta_engine.models.effectivity import Effectivity
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services import date_obsolete_worker as worker_mod
from yuantus.meta_engine.services.date_obsolete_worker import DateObsoleteWorker
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.models.base import Base

_NOW = datetime(2026, 6, 18, 12, 0, 0)
_PAST = _NOW - timedelta(days=2)
_ADMIN = SimpleNamespace(id=1, roles=["admin"], is_superuser=True)
_C3_APP = "plm.cadpdm_date_obsolete"   # the SKU registered for cadpdm_date_obsolete
_TENANT = "t-c3"


@pytest.fixture()
def tenant_ctx():
    tok = tenant_id_var.set(_TENANT)
    try:
        yield _TENANT
    finally:
        tenant_id_var.reset(tok)


def _license(db, *, tenant_id=_TENANT, app_name=_C3_APP, status="Active"):
    db.add(AppLicense(id=str(uuid.uuid4()), app_name=app_name,
                      license_key=str(uuid.uuid4()).upper(), status=status,
                      tenant_id=tenant_id, license_data={}))
    db.commit()


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture()
def Session():
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.models import user as _user  # noqa: F401

    import_all_models()
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture()
def db(Session):
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _settings(enabled=True):
    return SimpleNamespace(
        DATE_EFFECTIVITY_OBSOLETE_ENABLED=enabled,
        DATE_EFFECTIVITY_OBSOLETE_BATCH_SIZE=100,
        DATE_EFFECTIVITY_OBSOLETE_POLL_INTERVAL_SECONDS=300,
        DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID=0,
    )


def _seed_expired(db):
    db.add(Item(id="C", config_id="C", item_type_id="t", state="Released", is_current=True, properties={}))
    db.add(Item(id="P", config_id="P", item_type_id="t", source_id=None, related_id=None, is_current=True, properties={}))
    db.add(Item(id="R", config_id="R", item_type_id="bom", source_id="P", related_id="C", is_current=True, properties={}))
    db.add(ItemVersion(id="vC", item_id="C", state="Released", is_current=True, is_released=True))
    db.add(Effectivity(id="e1", version_id="vC", effectivity_type="Date",
                       start_date=_PAST - timedelta(days=10), end_date=_PAST))
    db.commit()


# -- worker gating + drain ---------------------------------------------------
def test_worker_disabled_is_noop(db, monkeypatch):
    monkeypatch.setattr(worker_mod, "get_settings", lambda: _settings(enabled=False))
    _seed_expired(db)
    n = DateObsoleteWorker().run_once_with_session(db)
    assert n == 0
    assert db.query(DateObsoleteImpact).count() == 0


def test_worker_enabled_but_not_entitled_is_noop(db, monkeypatch, tenant_ctx):
    # REAL entitlement path: enabled globally, but NO active license for this tenant
    # -> is_entitled returns False -> no-op (no monkeypatch of is_entitled).
    monkeypatch.setattr(worker_mod, "get_settings", lambda: _settings(enabled=True))
    _seed_expired(db)
    assert DateObsoleteWorker().run_once_with_session(db) == 0
    assert db.query(DateObsoleteImpact).count() == 0


def test_worker_enabled_and_entitled_drains(db, monkeypatch, tenant_ctx):
    # REAL entitlement path: enabled + an Active AppLicense for plm.cadpdm_date_obsolete
    # under this tenant -> is_entitled returns True -> drains. Proves the gate CAN flip
    # true (the registered key is reachable), not just a monkeypatched stub.
    monkeypatch.setattr(worker_mod, "get_settings", lambda: _settings(enabled=True))
    _license(db)
    _seed_expired(db)
    n = DateObsoleteWorker().run_once_with_session(db)
    assert n == 1
    flag = db.query(DateObsoleteImpact).one()
    assert flag.parent_item_id == "P" and flag.child_item_id == "C"


def test_worker_drains_all_beyond_batch_size(db, monkeypatch, tenant_ctx):
    # batch_size must NOT bound TOTAL reachable work: 2 expired effectivities on distinct
    # parents + batch_size=1 -> ONE tick still flags BOTH (process-all + idempotent).
    monkeypatch.setattr(worker_mod, "get_settings", lambda: _settings(enabled=True))
    _license(db)
    for i in (1, 2):
        db.add(Item(id=f"C{i}", config_id=f"C{i}", item_type_id="t", state="Released", is_current=True, properties={}))
        db.add(Item(id=f"P{i}", config_id=f"P{i}", item_type_id="t", is_current=True, properties={}))
        db.add(Item(id=f"R{i}", config_id=f"R{i}", item_type_id="bom", source_id=f"P{i}", related_id=f"C{i}", is_current=True, properties={}))
        db.add(ItemVersion(id=f"vC{i}", item_id=f"C{i}", state="Released", is_current=True, is_released=True))
        db.add(Effectivity(id=f"e{i}", version_id=f"vC{i}", effectivity_type="Date",
                           start_date=_PAST - timedelta(days=10), end_date=_PAST))
    db.commit()
    DateObsoleteWorker(batch_size=1).run_once_with_session(db)
    db.commit()
    flagged = {f.parent_item_id for f in db.query(DateObsoleteImpact).all()}
    assert flagged == {"P1", "P2"}   # both reached despite batch_size=1


def test_worker_run_once_short_circuits_when_disabled(monkeypatch):
    # global kill-switch off => run_once returns 0 without opening a session.
    monkeypatch.setattr(worker_mod, "get_settings", lambda: _settings(enabled=False))
    called = {"n": 0}
    monkeypatch.setattr(worker_mod, "get_db_session", lambda: called.__setitem__("n", 1))
    assert DateObsoleteWorker().run_once() == 0
    assert called["n"] == 0


# -- ops routes --------------------------------------------------------------
@pytest.fixture()
def client(Session):
    app = create_app()
    def _override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _ADMIN
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _impact(db, iid, state="open"):
    db.add(DateObsoleteImpact(id=iid, effectivity_id=f"e-{iid}", child_item_id="C", parent_item_id="P",
                              child_obsoleted=False, reason="child_effectivity_expired", state=state,
                              detected_at=_NOW))
    db.commit()


def test_ops_list_and_filter(client, db):
    _impact(db, "i1", "open"); _impact(db, "i2", "acknowledged")
    assert client.get("/api/v1/cadpdm/date-obsolete-impacts").json()["count"] == 2
    r = client.get("/api/v1/cadpdm/date-obsolete-impacts?state=open")
    assert r.json()["count"] == 1 and r.json()["rows"][0]["state"] == "open"


def test_ops_invalid_state_422(client, db):
    assert client.get("/api/v1/cadpdm/date-obsolete-impacts?state=bogus").status_code == 422


def test_ops_get_404(client, db):
    assert client.get("/api/v1/cadpdm/date-obsolete-impacts/ghost").status_code == 404


def test_ops_acknowledge(client, db):
    _impact(db, "i9", "open")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/i9/acknowledge")
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "acknowledged" and body["acknowledged_by_id"] == 1
    assert body["acknowledged_at"] is not None


def test_ops_acknowledge_404(client, db):
    assert client.post("/api/v1/cadpdm/date-obsolete-impacts/ghost/acknowledge").status_code == 404


# -- Fork-B: batch-acknowledge + summary -------------------------------------
_NONADMIN = SimpleNamespace(id=2, roles=[], is_superuser=False)


@pytest.fixture()
def client_nonadmin(Session):
    app = create_app()

    def _override_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: _NONADMIN
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _impact_co(
    db,
    iid,
    *,
    state="open",
    child_obsoleted=False,
    child_item_id="C",
    reason="child_effectivity_expired",
    properties=None,
):
    db.add(DateObsoleteImpact(id=iid, effectivity_id=f"e-{iid}", child_item_id=child_item_id, parent_item_id="P",
                              child_obsoleted=child_obsoleted, reason=reason,
                              state=state, detected_at=_NOW, properties=properties or {}))
    db.commit()


def test_batch_acknowledge_transitions_open_only(client, db):
    _impact(db, "b1", "open"); _impact(db, "b2", "open"); _impact(db, "b3", "acknowledged")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                    json={"impact_ids": ["b1", "b2", "b3"]})
    assert r.status_code == 200
    body = r.json()
    assert body["acknowledged_count"] == 2  # b3 already acknowledged -> not re-counted
    assert {row["id"] for row in body["rows"]} == {"b1", "b2"}
    # real DB effect: b1+b2 now acknowledged with actor/timestamp
    for iid in ("b1", "b2"):
        row = db.get(DateObsoleteImpact, iid)
        assert row.state == "acknowledged" and row.acknowledged_by_id == 1
        assert row.acknowledged_at is not None


def test_batch_acknowledge_unknown_ids_skipped_no_leak(client, db):
    _impact(db, "b1", "open")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                    json={"impact_ids": ["ghost", "b1", "alsoghost"]})
    assert r.status_code == 200  # unknown ids silently skipped (no existence leak, no 404)
    assert r.json()["acknowledged_count"] == 1


def test_batch_acknowledge_dedups_and_is_idempotent(client, db):
    _impact(db, "b1", "open")
    # duplicate ids in one request -> counted once
    r1 = client.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                     json={"impact_ids": ["b1", "b1"]})
    assert r1.json()["acknowledged_count"] == 1 and r1.json()["requested"] == 1
    # re-running -> already acknowledged -> no-op
    r2 = client.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                     json={"impact_ids": ["b1"]})
    assert r2.json()["acknowledged_count"] == 0


def test_batch_acknowledge_empty_list(client, db):
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                    json={"impact_ids": []})
    assert r.status_code == 200 and r.json()["acknowledged_count"] == 0


# -- #932 DP1 (i)/(ii): review-flag revert (reopen + un-acknowledge) ----------

def test_revert_single_reopens_clears_ack_and_appends_correction(client, db):
    _impact(db, "r1", "acknowledged")
    row = db.get(DateObsoleteImpact, "r1")
    row.acknowledged_at = _NOW; row.acknowledged_by_id = 7; db.commit()
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/r1/revert", json={"reason": "mis-ack"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "open"
    assert body["acknowledged_at"] is None and body["acknowledged_by_id"] is None
    corr = db.query(DateObsoleteImpactCorrection).filter_by(impact_id="r1").all()
    assert len(corr) == 1
    c = corr[0]
    assert c.action == "revert_reopen" and c.prior_state == "acknowledged"
    assert c.prior_acknowledged_by_id == 7 and c.reverted_by_id == 1 and c.reason == "mis-ack"


def test_revert_single_already_open_is_noop(client, db):
    _impact(db, "r2", "open")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/r2/revert")
    assert r.status_code == 200 and r.json()["state"] == "open"
    assert db.query(DateObsoleteImpactCorrection).filter_by(impact_id="r2").count() == 0


def test_revert_single_404(client, db):
    assert client.post("/api/v1/cadpdm/date-obsolete-impacts/ghost/revert").status_code == 404


def test_revert_batch_transitions_acknowledged_only(client, db):
    _impact(db, "rb1", "acknowledged"); _impact(db, "rb2", "acknowledged"); _impact(db, "rb3", "open")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch",
                    json={"impact_ids": ["rb1", "rb2", "rb3"]})
    assert r.status_code == 200
    body = r.json()
    assert body["reverted_count"] == 2  # rb3 already open -> not counted
    assert {row["id"] for row in body["rows"]} == {"rb1", "rb2"}
    for row in body["rows"]:
        assert row["state"] == "open" and row["acknowledged_at"] is None and row["acknowledged_by_id"] is None
    assert db.query(DateObsoleteImpactCorrection).count() == 2  # one append per transition


def test_revert_batch_unknown_ids_skipped_no_leak(client, db):
    _impact(db, "rb1", "acknowledged")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch",
                    json={"impact_ids": ["ghost", "rb1", "alsoghost"]})
    assert r.status_code == 200 and r.json()["reverted_count"] == 1  # no 404, no existence leak


def test_revert_batch_dedups_and_idempotent(client, db):
    _impact(db, "rb1", "acknowledged")
    r1 = client.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch", json={"impact_ids": ["rb1", "rb1"]})
    assert r1.json()["reverted_count"] == 1 and r1.json()["requested"] == 1
    r2 = client.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch", json={"impact_ids": ["rb1"]})
    assert r2.json()["reverted_count"] == 0  # already open -> no-op


def test_revert_batch_empty_list(client, db):
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch", json={"impact_ids": []})
    assert r.status_code == 200 and r.json()["reverted_count"] == 0


def test_revert_does_not_touch_child_obsoleted_or_item_lifecycle(client, db):
    # (iii)-ISOLATION GOLDEN: an acknowledged impact whose child WAS obsoleted, with the child
    # Item in the Obsolete lifecycle state. Reverting the review flag must leave BOTH the
    # child_obsoleted flag AND the Item's lifecycle state completely untouched — proving the
    # revert never crosses into the deferred DP1 (iii) undo-promotion.
    db.add(Item(id="OBS", config_id="OBS", item_type_id="t", state="Obsolete", is_current=True, properties={}))
    db.commit()
    _impact_co(db, "iso1", state="acknowledged", child_obsoleted=True,
               child_item_id="OBS", reason="child_obsoleted")
    r = client.post("/api/v1/cadpdm/date-obsolete-impacts/iso1/revert")
    assert r.status_code == 200 and r.json()["state"] == "open"
    row = db.get(DateObsoleteImpact, "iso1")
    assert row.child_obsoleted is True            # (iii) axis untouched
    assert row.reason == "child_obsoleted"          # worker-derived reason untouched
    assert db.get(Item, "OBS").state == "Obsolete"  # Item lifecycle NOT un-obsoleted
    assert db.query(DateObsoleteImpactCorrection).filter_by(impact_id="iso1").count() == 1


def test_revert_authz_nonadmin_denied(client_nonadmin, db):
    _impact(db, "r1", "acknowledged")
    assert client_nonadmin.post("/api/v1/cadpdm/date-obsolete-impacts/r1/revert").status_code == 403
    assert client_nonadmin.post("/api/v1/cadpdm/date-obsolete-impacts/revert-batch",
                                json={"impact_ids": ["r1"]}).status_code == 403


def test_revert_correction_survives_worker_properties_overwrite(client, db):
    # The append-only trail lives in its OWN table, so a worker re-scan that overwrites
    # DateObsoleteImpact.properties (service._upsert_impact) does NOT wipe the correction,
    # and the review axis (state) is worker-stable.
    _impact(db, "r1", "acknowledged")
    client.post("/api/v1/cadpdm/date-obsolete-impacts/r1/revert")
    assert db.query(DateObsoleteImpactCorrection).filter_by(impact_id="r1").count() == 1
    row = db.get(DateObsoleteImpact, "r1")
    db.refresh(row)
    row.properties = {"obsolete_error": None}; row.child_obsoleted = True; db.commit()  # mimic worker refresh
    assert db.get(DateObsoleteImpact, "r1").state == "open"  # review axis untouched by "worker"
    assert db.query(DateObsoleteImpactCorrection).filter_by(impact_id="r1").count() == 1


def test_revert_history_is_append_only(client, db):
    # ack -> revert -> ack -> revert appends TWO correction events (history never destroyed).
    _impact(db, "r1", "open")
    for _ in range(2):
        client.post("/api/v1/cadpdm/date-obsolete-impacts/r1/acknowledge")
        client.post("/api/v1/cadpdm/date-obsolete-impacts/r1/revert")
    assert db.query(DateObsoleteImpactCorrection).filter_by(impact_id="r1").count() == 2


def test_batch_acknowledge_requires_admin(client_nonadmin, db):
    _impact(db, "b1", "open")
    r = client_nonadmin.post("/api/v1/cadpdm/date-obsolete-impacts/acknowledge-batch",
                             json={"impact_ids": ["b1"]})
    assert r.status_code == 403
    assert db.get(DateObsoleteImpact, "b1").state == "open"  # gate tripped before any write


def test_summary_counts_by_state(client, db):
    _impact(db, "s1", "open"); _impact(db, "s2", "open"); _impact(db, "s3", "acknowledged")
    body = client.get("/api/v1/cadpdm/date-obsolete-impacts/summary").json()
    assert body == {"by_state": {"acknowledged": 1, "open": 2}, "total": 3}


def test_summary_stable_shape_when_empty(client, db):
    body = client.get("/api/v1/cadpdm/date-obsolete-impacts/summary").json()
    assert body == {"by_state": {"acknowledged": 0, "open": 0}, "total": 0}


def test_summary_route_not_captured_as_impact_id(client, db):
    # /summary must resolve to the aggregate, NOT the /{impact_id} route (404 "not found")
    r = client.get("/api/v1/cadpdm/date-obsolete-impacts/summary")
    assert r.status_code == 200 and "by_state" in r.json()


def test_summary_child_obsoleted_filter(client, db):
    _impact_co(db, "c1", state="open", child_obsoleted=True)
    _impact_co(db, "c2", state="open", child_obsoleted=False)
    _impact_co(db, "c3", state="acknowledged", child_obsoleted=True)
    all_body = client.get("/api/v1/cadpdm/date-obsolete-impacts/summary").json()
    assert all_body["total"] == 3
    only_co = client.get("/api/v1/cadpdm/date-obsolete-impacts/summary?child_obsoleted=true").json()
    assert only_co == {"by_state": {"acknowledged": 1, "open": 1}, "total": 2}


def test_summary_requires_admin(client_nonadmin, db):
    assert client_nonadmin.get("/api/v1/cadpdm/date-obsolete-impacts/summary").status_code == 403


# -- L3 next-slice: export/ad-hoc query surface -------------------------------
def test_ops_list_filters_child_obsoleted(client, db):
    _impact_co(db, "l1", child_obsoleted=True)
    _impact_co(db, "l2", child_obsoleted=False)
    body = client.get("/api/v1/cadpdm/date-obsolete-impacts?child_obsoleted=true").json()
    assert body["count"] == 1
    assert body["rows"][0]["id"] == "l1"


def test_export_json_uses_filters_and_route_not_captured(client, db):
    _impact_co(db, "x1", state="open", child_obsoleted=True, properties={"source": "worker"})
    _impact_co(db, "x2", state="open", child_obsoleted=False)
    r = client.get(
        "/api/v1/cadpdm/date-obsolete-impacts/export"
        "?format=json&state=open&child_obsoleted=true"
    )
    assert r.status_code == 200  # /export is a literal route, not /{impact_id}
    assert r.headers["content-type"].startswith("application/json")
    assert 'filename="date-obsolete-impacts.json"' in r.headers["content-disposition"]
    body = r.json()
    assert body["count"] == 1
    assert body["rows"][0]["id"] == "x1"
    assert body["rows"][0]["properties"] == {"source": "worker"}


def test_export_csv_has_stable_header_and_json_properties(client, db):
    _impact_co(db, "csv1", state="open", properties={"note": "需要复核"})
    _impact_co(db, "csv2", state="acknowledged")
    r = client.get("/api/v1/cadpdm/date-obsolete-impacts/export?format=csv&state=open")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="date-obsolete-impacts.csv"' in r.headers["content-disposition"]
    rows = list(csv.DictReader(StringIO(r.text)))
    assert list(rows[0]) == [
        "id", "effectivity_id", "child_item_id", "parent_item_id", "child_obsoleted",
        "reason", "state", "detected_at", "acknowledged_at", "acknowledged_by_id",
        "properties",
    ]
    assert len(rows) == 1
    assert rows[0]["id"] == "csv1"
    assert json.loads(rows[0]["properties"]) == {"note": "需要复核"}


def test_export_csv_neutralizes_formula_cells(client, db):
    _impact_co(
        db,
        "csv-formula",
        state="open",
        child_item_id="=cmd|' /C calc'!A0",
        reason="+SUM(1,2)",
        properties={"note": "@HYPERLINK(\"http://example.test\")"},
    )
    r = client.get("/api/v1/cadpdm/date-obsolete-impacts/export?format=csv&state=open")
    assert r.status_code == 200
    rows = list(csv.DictReader(StringIO(r.text)))
    assert rows[0]["child_item_id"].startswith("'=")
    assert rows[0]["reason"].startswith("'+")
    assert json.loads(rows[0]["properties"]) == {"note": "@HYPERLINK(\"http://example.test\")"}


def test_export_invalid_format_422(client, db):
    r = client.get("/api/v1/cadpdm/date-obsolete-impacts/export?format=xlsx")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "cadpdm_date_obsolete_invalid_export_format"


def test_export_requires_admin(client_nonadmin, db):
    assert client_nonadmin.get("/api/v1/cadpdm/date-obsolete-impacts/export").status_code == 403
