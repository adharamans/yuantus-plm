"""PLM-COLLAB Phase-7 Day-2: governed BOM multi-table line WRITE-BACK (§7 acceptance).

The metasheet2 consumer pact (#3332) asserts ONLY the 200 success path, and the pact
fixture CANNOT prove the lifecycle lock (its Part type has no ``lifecycle_map_id`` so
``is_item_locked`` is inert there -- design §5). This provider suite carries the load-bearing
governance coverage the contract cannot: the full guard ladder (write-entitlement 403 ->
permission 403 -> 400 malformed/empty/missing-Idempotency-Key -> 404 part-missing/line∉part
-> 409 REAL ``LifecycleState(version_lock=True)`` lock vs Draft 200), the single-use replay
cache (same key -> cached 200 with NO re-apply; different payload same key -> 409), and the
atomic write-back audit (before->after captured; an audit-insert failure rolls the mutation
back -- design §3's "a governed write must not succeed without its diff").

The router mounts on a real in-memory SQLite session (full schema via ``create_app`` model
registration + ``create_all``). Entitlement runs the REAL ``is_entitled`` query against seeded
``AppLicense`` rows keyed to the PRODUCTION SKUs (``plm.bom_multitable_writeback`` for write,
``plm.bom_multitable`` for the read-only-entitled 403), single-mode tenant == "default".
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the app registers ALL ORM models on Base.metadata (Item's FK web), so
# create_all below builds the full schema -- including meta_bom_writeback_audit.
from yuantus.api.app import create_app  # noqa: F401  (import side-effect: model registration)
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.base import Base
from yuantus.meta_engine.lifecycle.models import LifecycleState
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_bom_writeback_audit import MetaBomWritebackAudit
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.services.bom_multitable_writeback_service import (
    BomLineWritebackConflictError,
    BOMMultitableWritebackService,
    bom_line_write_etag,
)
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService
from yuantus.meta_engine.web.bom_multitable_router import bom_multitable_router

TENANT = "default"  # single-mode resolve_license_scope() yields this
WRITE_APP = "plm.bom_multitable_writeback"
READ_APP = "plm.bom_multitable"

PART_ID = "WBP1"
LINE_ID = "WBR1"
CHILD_ID = "WBC1"
OTHER_PART_ID = "WBP2"

_USER = type("_User", (), {"id": 7, "roles": ["engineer"], "is_superuser": False})()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)  # full schema incl. meta_bom_writeback_audit
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _single_mode(monkeypatch):
    monkeypatch.setenv("YUANTUS_TENANCY_MODE", "single")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client(db_session, *, user="auth"):
    app = FastAPI()
    app.include_router(bom_multitable_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: db_session
    if user == "auth":
        app.dependency_overrides[get_current_user] = lambda: _USER
    elif user == "unauth":
        def _unauth():
            raise HTTPException(status_code=401, detail="Unauthorized")

        app.dependency_overrides[get_current_user] = _unauth
    return TestClient(app)


def _entitle(db_session, app_name=WRITE_APP):
    db_session.add(
        AppLicense(
            id=f"lic-{app_name}", app_name=app_name, license_key=f"key-{app_name}",
            status="Active", tenant_id=TENANT,
        )
    )
    db_session.commit()


def _allow_permission(monkeypatch, *, allow=True):
    monkeypatch.setattr(
        MetaPermissionService, "check_permission", lambda self, *a, **k: allow
    )


def _seed_line(
    db_session,
    *,
    part_id=PART_ID,
    line_id=LINE_ID,
    quantity=1,
    uom="ea",
    find_num="10",
    refdes="WB1",
    parent_current_state=None,
):
    """A dedicated parent part (inert lifecycle unless ``parent_current_state`` set) + child +
    one editable "Part BOM" line whose ``source_id`` is the parent (so line ∈ part)."""
    db_session.add(
        Item(
            id=part_id, item_type_id="Part", config_id=part_id, generation=1,
            is_current=True, state="Draft", current_state=parent_current_state,
            properties={"item_number": "WB-PART", "name": "WB Parent"},
        )
    )
    db_session.add(
        Item(
            id=CHILD_ID, item_type_id="Part", config_id=CHILD_ID, generation=1,
            is_current=True, state="Released",
            properties={"item_number": "WB-CHILD", "name": "WB Child"},
        )
    )
    db_session.add(
        Item(
            id=line_id, item_type_id="Part BOM", config_id=line_id, generation=1,
            is_current=True, source_id=part_id, related_id=CHILD_ID,
            properties={"quantity": quantity, "uom": uom, "find_num": find_num, "refdes": refdes},
        )
    )
    db_session.commit()


_PATH = "/api/v1/bom/multitable/{p}/lines/{l}"
_HDR = {"Idempotency-Key": "k-1"}
_BODY = {"quantity": 5, "uom": "box"}


# --- guard ladder -------------------------------------------------------------

def test_unauthenticated_is_401(db_session):
    r = _client(db_session, user="unauth").patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 401


def test_read_only_entitled_is_403_on_write(db_session, monkeypatch):
    # A tenant holding ONLY the read SKU (plm.bom_multitable) must NOT unlock the write
    # surface -- is_entitled(WRITE_FEATURE_KEY) is False -> 403 (checked first, no existence leak).
    _entitle(db_session, app_name=READ_APP)
    _allow_permission(monkeypatch)  # permission would pass; entitlement still blocks
    _seed_line(db_session)
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 403


def test_unentitled_does_not_leak_existence(db_session, monkeypatch):
    # No write license at all + a NON-existent line: still 403 (entitlement is the first gate,
    # the object is never looked up) -- identical to the existing-line 403.
    def _explode(self, *a, **k):
        raise AssertionError("permission must not be checked when unentitled")

    monkeypatch.setattr(MetaPermissionService, "check_permission", _explode)
    r = _client(db_session).patch(
        _PATH.format(p="NOPE", l="NOPE"), json=_BODY, headers=_HDR
    )
    assert r.status_code == 403


def test_permission_denied_is_403(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch, allow=False)
    _seed_line(db_session)
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 403


def test_missing_idempotency_key_is_400(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY  # NO Idempotency-Key header
    )
    assert r.status_code == 400


def test_empty_whitelist_body_is_400(db_session, monkeypatch):
    # A body that whitelists to nothing (only unknown keys) is a malformed/empty write -> 400,
    # BEFORE any object lookup.
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"bogus": "x"}, headers=_HDR
    )
    assert r.status_code == 400


def test_malformed_quantity_is_400_no_audit_no_change(db_session, monkeypatch):
    # quantity must be a scalar; an object/array/bool is rejected at the 400 gate BEFORE
    # write_line -> no audit row, line unchanged. The KEY whitelist alone would let it through
    # and persist a structured value into the BOM quantity cell.
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)
    for bad in ({"x": 1}, [1, 2], True):
        r = _client(db_session).patch(
            _PATH.format(p=PART_ID, l=LINE_ID),
            json={"quantity": bad}, headers={"Idempotency-Key": "qk"},
        )
        assert r.status_code == 400, bad
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 1  # unchanged
    assert db_session.query(MetaBomWritebackAudit).count() == 0       # nothing persisted


def test_over_long_idempotency_key_is_400(db_session, monkeypatch):
    # the audit column is VARCHAR(64); a >64-char key is rejected at the 400 gate so Postgres
    # never raises a length error at insert (SQLite would silently accept it -> false green).
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY, headers={"Idempotency-Key": "x" * 65}
    )
    assert r.status_code == 400
    assert db_session.query(MetaBomWritebackAudit).count() == 0


def test_bad_key_against_ghost_part_is_400_before_404(db_session, monkeypatch):
    # locks guard order: the Idempotency-Key gate (missing OR over-long) fires BEFORE the part
    # lookup, so a bad key against a NON-existent part is 400, not 404.
    _entitle(db_session)
    _allow_permission(monkeypatch)
    # no part seeded
    missing = _client(db_session).patch(
        _PATH.format(p="GHOST", l="GHOST"), json=_BODY  # no Idempotency-Key
    )
    assert missing.status_code == 400
    overlong = _client(db_session).patch(
        _PATH.format(p="GHOST", l="GHOST"), json=_BODY, headers={"Idempotency-Key": "x" * 65}
    )
    assert overlong.status_code == 400


def test_part_missing_is_404(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    # no part seeded at all
    r = _client(db_session).patch(
        _PATH.format(p="GHOST", l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 404


def test_line_not_under_part_is_404(db_session, monkeypatch):
    # The line exists but its source_id is a DIFFERENT part -> line ∉ part -> 404 (the boundary
    # the consumer pact deliberately does not assert).
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)  # line LINE_ID has source_id == PART_ID
    db_session.add(
        Item(id=OTHER_PART_ID, item_type_id="Part", config_id=OTHER_PART_ID, generation=1,
             is_current=True, state="Draft", properties={"item_number": "OTHER"})
    )
    db_session.commit()
    r = _client(db_session).patch(
        _PATH.format(p=OTHER_PART_ID, l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 404


# --- lifecycle: REAL version_lock 409 vs Draft 200 (design §5 -- pact cannot prove this) ------

def test_lifecycle_locked_parent_is_409(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    # A REAL released/locked LifecycleState; the parent's current_state points at it so
    # is_item_locked (current_state lookup) returns True -> 409, mirroring add_bom_child.
    db_session.add(
        LifecycleState(id="LS-REL", name="Released", version_lock=True)
    )
    db_session.commit()
    _seed_line(db_session, parent_current_state="LS-REL")
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json=_BODY, headers=_HDR
    )
    assert r.status_code == 409
    # and NO mutation / audit happened (the lock gate is BEFORE the apply)
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 1
    assert db_session.query(MetaBomWritebackAudit).count() == 0


def test_draft_parent_applies_200(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)  # parent_current_state=None -> not locked
    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 5, "uom": "box"}, headers=_HDR
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True, "bom_line_id": LINE_ID}
    db_session.expire_all()
    props = db_session.get(Item, LINE_ID).properties
    assert props["quantity"] == 5 and props["uom"] == "box"
    # untouched cells preserved (partial PATCH)
    assert props["find_num"] == "10" and props["refdes"] == "WB1"


# --- T6 optimistic concurrency: optional If-Match / 412 -----------------------

def test_if_match_matching_etag_applies_and_returns_new_etag(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)
    current_etag = bom_line_write_etag(db_session.get(Item, LINE_ID))

    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 10},
        headers={"Idempotency-Key": "im-ok", "If-Match": current_etag},
    )

    assert r.status_code == 200
    assert r.headers["etag"].startswith('"bom-line:')
    assert r.headers["etag"] != current_etag
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 10
    assert db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="im-ok").count() == 1


def test_if_match_stale_etag_is_412_no_audit_no_change(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)

    r = _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 10},
        headers={"Idempotency-Key": "im-stale", "If-Match": '"bom-line:stale"'},
    )

    assert r.status_code == 412
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 1
    assert db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="im-stale").count() == 0


def test_idempotent_retry_with_old_if_match_is_cached_not_412(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)
    client = _client(db_session)
    original_etag = bom_line_write_etag(db_session.get(Item, LINE_ID))

    first = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 10},
        headers={"Idempotency-Key": "im-retry", "If-Match": original_etag},
    )
    assert first.status_code == 200

    # Make the original ETag stale and prove the duplicate key returns the cached result rather
    # than re-applying or failing 412. This preserves retry safety for the write-back surface.
    line = db_session.get(Item, LINE_ID)
    line.properties = {**line.properties, "quantity": 999}
    db_session.commit()

    retry = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 10},
        headers={"Idempotency-Key": "im-retry", "If-Match": original_etag},
    )
    assert retry.status_code == 200
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 999
    assert db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="im-retry").count() == 1


# --- single-use replay (design §2): cached 200 with NO re-apply; different payload -> 409 -----

def test_replay_same_key_returns_cached_without_reapplying(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)
    client = _client(db_session)

    first = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 10}, headers={"Idempotency-Key": "rk"}
    )
    assert first.status_code == 200
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 10

    # Mutate the line OUT-OF-BAND to a sentinel; if the replay re-applied, it would overwrite
    # 999 back to 10. The cached path must leave 999 intact.
    line = db_session.get(Item, LINE_ID)
    line.properties = {**line.properties, "quantity": 999}
    db_session.commit()

    replay = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 10}, headers={"Idempotency-Key": "rk"}
    )
    assert replay.status_code == 200
    assert replay.json() == {"ok": True, "bom_line_id": LINE_ID}
    db_session.expire_all()
    # PROOF of no double-apply: the out-of-band 999 survives the replay.
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 999
    # exactly one audit row for the key
    assert db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="rk").count() == 1


def test_same_key_different_payload_is_409(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)
    client = _client(db_session)
    assert client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 10}, headers={"Idempotency-Key": "ck"}
    ).status_code == 200
    conflict = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 20}, headers={"Idempotency-Key": "ck"}
    )
    assert conflict.status_code == 409


def test_same_key_same_payload_different_line_is_409(db_session, monkeypatch):
    # Regression nail for the service-layer replay guard: a duplicate key is cacheable only for
    # the SAME tenant + SAME line + SAME payload. Reusing a key for another line with identical
    # cells must be a 409, not a cached response for the first line.
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session)
    other_line_id = "WBR2"
    db_session.add(
        Item(
            id=other_line_id,
            item_type_id="Part BOM",
            config_id=other_line_id,
            generation=1,
            is_current=True,
            source_id=PART_ID,
            related_id=CHILD_ID,
            properties={"quantity": 1, "uom": "ea", "find_num": "20", "refdes": "WB2"},
        )
    )
    db_session.commit()
    client = _client(db_session)

    first = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 10}, headers={"Idempotency-Key": "lk"}
    )
    assert first.status_code == 200
    conflict = client.patch(
        _PATH.format(p=PART_ID, l=other_line_id), json={"quantity": 10}, headers={"Idempotency-Key": "lk"}
    )
    assert conflict.status_code == 409


# --- write-back audit (design §3): before->after captured + audit-failure rolls back ----------

def test_audit_captures_before_and_after(db_session, monkeypatch):
    _entitle(db_session)
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1, uom="ea")
    _client(db_session).patch(
        _PATH.format(p=PART_ID, l=LINE_ID), json={"quantity": 5, "uom": "box"},
        headers={"Idempotency-Key": "ak"},
    )
    row = db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="ak").one()
    # before = the TOUCHED cells' prior values (snapshotted pre-reassignment)
    assert row.before == {"quantity": 1, "uom": "ea"}
    assert row.after == {"quantity": 5, "uom": "box"}
    assert row.part_id == PART_ID and row.bom_line_id == LINE_ID
    assert row.tenant_id == TENANT and row.user_id == 7
    assert row.status == "applied"


def test_audit_insert_failure_rolls_back_mutation(db_session, monkeypatch):
    # design §3: a governed write must NOT succeed without its diff. Force a NON-IntegrityError
    # at the audit flush (the IntegrityError path is the replay/conflict branch, a different
    # concern). The mutation must then be rolled back and NO audit row persisted.
    _seed_line(db_session, quantity=1)
    svc = BOMMultitableWritebackService(db_session)

    real_flush = db_session.flush

    def _boom(*a, **k):
        raise RuntimeError("audit storage exploded")

    monkeypatch.setattr(db_session, "flush", _boom)
    with pytest.raises(RuntimeError):
        svc.write_line(
            PART_ID, LINE_ID, "fk", {"quantity": 42},
            user_id=7, tenant_id=TENANT, org_id=None,
        )
    # restore flush and verify in a state that reflects committed reality
    monkeypatch.setattr(db_session, "flush", real_flush)
    db_session.rollback()
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 1  # NOT 42
    assert db_session.query(MetaBomWritebackAudit).count() == 0


def test_service_line_not_in_part_raises(db_session):
    # defense-in-depth at the service boundary (the router maps this to 404).
    _seed_line(db_session)
    svc = BOMMultitableWritebackService(db_session)
    with pytest.raises(Exception):
        svc.write_line(
            OTHER_PART_ID, LINE_ID, "x", {"quantity": 3},
            user_id=7, tenant_id=TENANT, org_id=None,
        )


def test_service_same_key_different_payload_raises_conflict(db_session):
    _seed_line(db_session)
    svc = BOMMultitableWritebackService(db_session)
    svc.write_line(PART_ID, LINE_ID, "sk", {"quantity": 3},
                   user_id=7, tenant_id=TENANT, org_id=None)
    with pytest.raises(BomLineWritebackConflictError):
        svc.write_line(PART_ID, LINE_ID, "sk", {"quantity": 9},
                       user_id=7, tenant_id=TENANT, org_id=None)


# --- route surface ------------------------------------------------------------

def test_router_exposes_three_routes_incl_patch_writeback():
    paths = {(r.path, tuple(sorted(r.methods))) for r in bom_multitable_router.routes}
    assert (
        "/bom/multitable/{part_id}/lines/{bom_line_id}", ("PATCH",)
    ) in paths


# --- P1 follow-up: per-tenant idempotency scope (cross-tenant isolation) -------

def test_service_cross_tenant_same_key_each_applies(db_session):
    # The tenant-scope fix: the SAME Idempotency-Key under DIFFERENT tenants must EACH apply --
    # no cross-tenant replay/conflict. Pre-fix (global single-column unique + key-only re-query),
    # tenant B collided on tenant A's key and was wrongly treated as a replay/409.
    _seed_line(db_session, quantity=1)
    svc = BOMMultitableWritebackService(db_session)

    r_a = svc.write_line(PART_ID, LINE_ID, "shared-key", {"quantity": 3},
                         user_id=7, tenant_id="tenantA", org_id=None)
    r_b = svc.write_line(PART_ID, LINE_ID, "shared-key", {"quantity": 4},
                         user_id=7, tenant_id="tenantB", org_id=None)

    assert r_a == {"ok": True, "bom_line_id": LINE_ID}
    assert r_b == {"ok": True, "bom_line_id": LINE_ID}
    rows = db_session.query(MetaBomWritebackAudit).filter_by(idempotency_key="shared-key").all()
    assert len(rows) == 2
    assert {r.tenant_id for r in rows} == {"tenantA", "tenantB"}


def test_service_same_tenant_same_key_same_payload_is_cached(db_session):
    # The fix must NOT regress same-tenant replay: same (tenant, key, payload) -> cached, NO
    # re-apply (the out-of-band sentinel survives) -> exactly one row for (tenant, key).
    _seed_line(db_session, quantity=1)
    svc = BOMMultitableWritebackService(db_session)

    svc.write_line(PART_ID, LINE_ID, "rk", {"quantity": 7},
                   user_id=7, tenant_id="tenantA", org_id=None)
    line = db_session.get(Item, LINE_ID)
    line.properties = {**line.properties, "quantity": 999}  # out-of-band sentinel
    db_session.commit()

    cached = svc.write_line(PART_ID, LINE_ID, "rk", {"quantity": 7},
                            user_id=7, tenant_id="tenantA", org_id=None)
    assert cached == {"ok": True, "bom_line_id": LINE_ID}
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 999  # NOT re-applied
    assert db_session.query(MetaBomWritebackAudit).filter_by(
        tenant_id="tenantA", idempotency_key="rk"
    ).count() == 1


def test_service_same_tenant_same_key_different_payload_conflicts(db_session):
    # Same (tenant, key) with a DIFFERENT payload is still a conflict (router maps to 409) --
    # the fix preserves main's diagnostic conflict semantics within a tenant.
    _seed_line(db_session)
    svc = BOMMultitableWritebackService(db_session)
    svc.write_line(PART_ID, LINE_ID, "ck", {"quantity": 3},
                   user_id=7, tenant_id="tenantA", org_id=None)
    with pytest.raises(BomLineWritebackConflictError):
        svc.write_line(PART_ID, LINE_ID, "ck", {"quantity": 9},
                       user_id=7, tenant_id="tenantA", org_id=None)


def test_projection_write_etag_round_trips_as_if_match(db_session, monkeypatch):
    """End-to-end loop: the ``write_etag`` a client RECEIVES from the read projection
    (GET .../context), after JSON + If-Match HEADER transport, is accepted by the write
    endpoint for an UNCHANGED line -> 200 (never a spurious 412), and the same value goes
    stale after a write -> 412.

    This locks the cross-surface invariant read_etag == write_etag through the real client
    path: the existing If-Match tests feed a write-side ``bom_line_write_etag(Item)`` and the
    projection test pins the value only at the unit level -- neither exercises GET-context ->
    extract -> PATCH over HTTP, so an etag drift (or a serialization/quoting regression in the
    header round-trip) between the two surfaces would ship green today.
    """
    _entitle(db_session, app_name=WRITE_APP)
    _entitle(db_session, app_name=READ_APP)  # GET /context requires the read SKU
    _allow_permission(monkeypatch)
    _seed_line(db_session, quantity=1)
    client = _client(db_session)

    # READ: the exact write_etag a client receives over HTTP/JSON
    ctx = client.get(f"/api/v1/bom/multitable/{PART_ID}/context")
    assert ctx.status_code == 200, ctx.text
    line = next(l for l in ctx.json()["context"]["lines"] if l["bom_line_id"] == LINE_ID)
    read_etag = line["write_etag"]
    assert read_etag.startswith('"bom-line:')

    # WRITE: that same etag, transported back as an If-Match HEADER, satisfies the guard -> 200
    r = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 9},
        headers={"Idempotency-Key": "im-rt", "If-Match": read_etag},
    )
    assert r.status_code == 200, r.text  # read-etag accepted -> NO spurious 412
    assert r.headers["etag"] != read_etag  # post-write representation changed

    # the now-stale read_etag is rejected on a second write -> 412 (staleness detection works)
    r2 = client.patch(
        _PATH.format(p=PART_ID, l=LINE_ID),
        json={"quantity": 11},
        headers={"Idempotency-Key": "im-rt-2", "If-Match": read_etag},
    )
    assert r2.status_code == 412
    db_session.expire_all()
    assert db_session.get(Item, LINE_ID).properties["quantity"] == 9  # second write blocked
