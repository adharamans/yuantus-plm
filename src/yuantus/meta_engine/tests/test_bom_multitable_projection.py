"""PLM-COLLAB-P3-A: BOM multi-table governed READ-ONLY projection route.

A minimal FastAPI app mounts ONLY ``bom_multitable_router`` with ``get_db`` overridden
to an in-memory SQLite session and ``get_current_user`` overridden to drive the auth
gate. The full schema is created (Item's FK web) by importing the app to register all
models, then ``create_all``. Tenancy is single-mode -> resolver yields "default".

``bom_multitable`` is gated by ``EntitlementService.is_entitled``. The entitled-path tests
TEST-ONLY force it by mapping the key to ``plm.collab`` (monkeypatch ``FEATURE_APP_NAMES``)
+ adding a matching ``AppLicense`` -- isolating these projection tests from the production
SKU wiring (``plm.bom_multitable``, lit in P3-B) while still exercising the REAL is_entitled
query path, not a stub.

Pins (the owner-listed P3-A obligations + review follow-ups):
- GET unauthenticated -> 401 (auth is the outermost gate, before entitlement).
- GET unentitled -> ``context: null`` + upgrade affordance, IDENTICAL for an existing and
  a non-existent part: the part is never queried (no existence leak) AND PLM permission is
  never checked (pinned order: auth -> is_entitled -> part -> Part-type -> permission).
- GET entitled -> CURATED read-only snapshot of the FULL (flattened) BOM tree: ONLY the
  review fields + level/path + per-row provenance, and NONE of the raw ``Item.to_dict()``
  internals (config_id / current_version_id / source_id / related_id / permission_id /
  is_current / item_type_id) -- asserted via EXACT key sets, which is only meaningful
  because the snapshot is built from REAL Items through the REAL get_tree.
- Second-level BOM lines are NOT silently dropped (level/path correct); provenance is on the
  envelope AND every row, and source_updated_at falls back to created_on (never null).
- GET entitled + missing part -> 404; non-Part Item -> 400; permission denied -> 403.
- the router exposes exactly the 1 route, and the LIVE app owns that path with this router.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Importing the app registers ALL ORM models on Base.metadata (Item's FK web), so
# create_all below builds the full schema.
from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.models.base import Base
from yuantus.meta_engine.app_framework import entitlement_service as es
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService
from yuantus.meta_engine.web.bom_multitable_router import bom_multitable_router

FEATURE = "bom_multitable"
APP = "plm.collab"
TENANT = "default"
ROUTE_PATH = "/api/v1/bom/multitable/{part_id}/context"

_USER = type("_User", (), {"id": 7, "roles": ["engineer"], "is_superuser": False})()

# Curated row keys (the FULL allowed surface per row).
_PART_KEYS = {"part_id", "item_number", "name", "state", "generation"}
_LINE_KEYS = {
    "bom_line_id",  # read-only STABLE row key (the rel-Item id)
    "part_id",  # read-only technical key (the child PART id)
    "item_number", "name", "state", "generation",
    "quantity", "uom", "find_num", "refdes",
    "level", "path", "path_labels",
    "source_version", "source_updated_at", "sync_status",
}

# The complete raw-internal surface of Item.to_dict() that MUST NOT reach MetaSheet.
_LEAKY_KEYS = {
    "id", "item_type_id", "config_id", "is_current", "current_state",
    "current_version_id", "created_by_id", "created_on", "modified_by_id",
    "modified_on", "owner_id", "permission_id", "source_id", "related_id",
}


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)  # full schema (Item FK web is satisfied)
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


def _light_entitlement(monkeypatch, db_session):
    """TEST-ONLY: force-entitle ``bom_multitable`` + add a matching license.

    Maps the key to ``plm.collab`` so the REAL is_entitled query path runs and matches the
    license below (single-mode tenant == "default"), isolated from the production
    ``plm.bom_multitable`` SKU. Auto-reverted by monkeypatch.
    """
    monkeypatch.setitem(es.FEATURE_APP_NAMES, FEATURE, frozenset({APP}))
    db_session.add(
        AppLicense(
            id="lic1", app_name=APP, license_key="key1", status="Active", tenant_id=TENANT
        )
    )
    db_session.commit()


def _allow_permission(monkeypatch, *, allow=True):
    monkeypatch.setattr(
        MetaPermissionService, "check_permission", lambda self, *a, **k: allow
    )


def _part(item_id, *, item_number, name, state, generation, item_type="Part"):
    return Item(
        id=item_id,
        item_type_id=item_type,
        config_id=item_id,
        generation=generation,
        is_current=True,
        state=state,
        properties={"item_number": item_number, "name": name},
    )


def _bom_line(rel_id, *, parent, child, quantity=1, uom="EA", find_num="", refdes="", item_type="Part BOM"):
    # item_type defaults to "Part BOM"; override to forge a NON-BOM relationship off the same
    # parent (used to pin that the projection filters it out).
    return Item(
        id=rel_id,
        item_type_id=item_type,
        config_id=rel_id,
        is_current=True,
        source_id=parent,
        related_id=child,
        properties={"quantity": quantity, "uom": uom, "find_num": find_num, "refdes": refdes},
    )


def _make_bom(db_session):
    """A real 2-level BOM: P1 -[R1]-> C1 -[R2]-> D1, all is_current.

    P1 (gen 3) is the root Part; C1 (gen 1) a level-1 line; D1 (gen 2) a level-2 line under
    C1 -- so the flatten must emit BOTH levels (the level-2 line must NOT be dropped).
    """
    db_session.add(_part("P1", item_number="P-001", name="Assembly", state="Released", generation=3))
    db_session.add(_part("C1", item_number="C-001", name="Bracket", state="Draft", generation=1))
    db_session.add(_part("D1", item_number="D-001", name="Screw", state="Released", generation=2))
    db_session.add(_bom_line("R1", parent="P1", child="C1", quantity=2, uom="EA", find_num="10", refdes="R1,R2"))
    db_session.add(_bom_line("R2", parent="C1", child="D1", quantity=4, uom="EA", find_num="20", refdes="R3"))
    db_session.commit()


# --- auth ---------------------------------------------------------------------

def test_get_unauthenticated_is_401(db_session):
    r = _client(db_session, user="unauth").get(ROUTE_PATH.format(part_id="P1"))
    assert r.status_code == 401


# --- unentitled: no existence leak, no permission check -----------------------

def test_get_unentitled_does_not_leak_part_existence_or_check_permission(
    db_session, monkeypatch
):
    # Reserved key is NOT lit. An EXISTING part and a NON-existent one must return the
    # SAME null affordance (the part is never queried). Permission is patched to BLOW UP
    # so a 200 proves the unentitled path returns BEFORE the permission gate.
    _make_bom(db_session)

    def _explode(self, *a, **k):
        raise AssertionError("permission must not be checked when unentitled")

    monkeypatch.setattr(MetaPermissionService, "check_permission", _explode)

    client = _client(db_session)
    existing = client.get(ROUTE_PATH.format(part_id="P1"))
    missing = client.get(ROUTE_PATH.format(part_id="P999"))
    assert existing.status_code == 200 and missing.status_code == 200
    assert existing.json() == missing.json()
    body = existing.json()
    assert body["feature_key"] == FEATURE
    assert body["entitled"] is False
    assert body["upgrade"]["available"] is True
    assert body["context"] is None


# --- entitled: curated read-only flattened-tree snapshot ----------------------

def test_get_entitled_returns_curated_flattened_tree_snapshot(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _make_bom(db_session)

    body = _client(db_session).get(ROUTE_PATH.format(part_id="P1")).json()
    assert body["entitled"] is True
    assert body["upgrade"]["available"] is False

    ctx = body["context"]
    # envelope provenance (the root Part's) is RETAINED at the top level
    assert ctx["source_version"] == 3  # part.generation
    assert ctx["sync_status"] == "snapshot"
    assert ctx["template_key"] == "bom_review"
    # fresh Part has no updated_at -> source_updated_at falls back to created_on (NOT null)
    assert isinstance(ctx["source_updated_at"], str) and ctx["source_updated_at"]

    # part: EXACTLY the curated identity keys -- no provenance baggage, no to_dict internals.
    part = ctx["part"]
    assert set(part.keys()) == _PART_KEYS
    assert part == {
        "part_id": "P1", "item_number": "P-001", "name": "Assembly",
        "state": "Released", "generation": 3,
    }
    assert not (_LEAKY_KEYS & set(part.keys()))

    # FULL flattened tree: two lines, pre-order (level-1 C1 then level-2 D1).
    lines = ctx["lines"]
    assert len(lines) == 2
    for line in lines:
        assert set(line.keys()) == _LINE_KEYS
        assert not (_LEAKY_KEYS & set(line.keys()))
        assert line["sync_status"] == "snapshot"
        # per-row provenance is never null (created_on fallback)
        assert isinstance(line["source_updated_at"], str) and line["source_updated_at"]

    c1, d1 = lines
    # bom_line_id is the relationship-Item id (the stable ROW key); part_id keys the child PART.
    # path is the ancestor PART-ID chain; path_labels the parallel item_number chain (display).
    assert c1["bom_line_id"] == "R1" and c1["part_id"] == "C1"
    assert c1["item_number"] == "C-001" and c1["level"] == 1
    assert c1["path"] == ["P1"] and c1["path_labels"] == ["P-001"]
    assert c1["quantity"] == 2 and c1["uom"] == "EA" and c1["find_num"] == "10" and c1["refdes"] == "R1,R2"
    assert c1["source_version"] == 1  # C1.generation (the displayed one)

    assert d1["bom_line_id"] == "R2" and d1["part_id"] == "D1"
    assert d1["item_number"] == "D-001" and d1["level"] == 2
    assert d1["path"] == ["P1", "C1"] and d1["path_labels"] == ["P-001", "C-001"]
    assert d1["quantity"] == 4 and d1["source_version"] == 2  # D1.generation
    # the stable-key invariant the owner asked for: a line's path[-1] is its parent's part_id
    assert d1["path"][-1] == c1["part_id"]


def test_second_level_bom_line_is_not_dropped(db_session, monkeypatch):
    # Owner pin: depth>1 must NOT be silently truncated. The level-2 line (D1 under C1)
    # must appear, tagged level=2 with the full ancestor path.
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _make_bom(db_session)

    lines = _client(db_session).get(ROUTE_PATH.format(part_id="P1")).json()["context"]["lines"]
    by_num = {ln["item_number"]: ln for ln in lines}
    assert "D-001" in by_num, "level-2 BOM line was dropped"
    d1 = by_num["D-001"]
    assert d1["part_id"] == "D1" and d1["level"] == 2
    assert d1["path"] == ["P1", "C1"]  # ancestor part-ids
    assert d1["path_labels"] == ["P-001", "C-001"]  # ancestor item_numbers


def test_non_part_bom_relationship_is_not_projected(db_session, monkeypatch):
    # The tree read is restricted to "Part BOM" relationships, so a non-BOM relationship off
    # the same parent (here a "Part Document" link) is neither projected into the review nor
    # escapes the "Part BOM"-scoped read-permission check the router enforces.
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    db_session.add(_part("P1", item_number="P-001", name="Assembly", state="Released", generation=1))
    db_session.add(_part("C1", item_number="C-001", name="Bracket", state="Draft", generation=1))
    db_session.add(_part("DOC1", item_number="DOC-1", name="Spec", state="Released", generation=1, item_type="Document"))
    db_session.add(_bom_line("R1", parent="P1", child="C1", quantity=2))
    db_session.add(_bom_line("X1", parent="P1", child="DOC1", item_type="Part Document"))
    db_session.commit()

    lines = _client(db_session).get(ROUTE_PATH.format(part_id="P1")).json()["context"]["lines"]
    assert {ln["item_number"] for ln in lines} == {"C-001"}  # the doc link is NOT projected
    assert {ln["part_id"] for ln in lines} == {"C1"}


def test_duplicate_parent_child_lines_have_distinct_bom_line_id(db_session, monkeypatch):
    # Yuantus allows the same parent->child as multiple BOM lines (e.g. different UOM). Both
    # rows share part_id + path, so the rel-Item id (bom_line_id) is what keeps them distinct
    # / stably addressable for P3-C -- not the mutable uom/find_num/refdes.
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    db_session.add(_part("P1", item_number="P-001", name="Assembly", state="Released", generation=1))
    db_session.add(_part("C1", item_number="C-001", name="Bracket", state="Draft", generation=1))
    db_session.add(_bom_line("R1a", parent="P1", child="C1", quantity=2, uom="EA"))
    db_session.add(_bom_line("R1b", parent="P1", child="C1", quantity=5, uom="PCS"))
    db_session.commit()

    lines = _client(db_session).get(ROUTE_PATH.format(part_id="P1")).json()["context"]["lines"]
    assert len(lines) == 2
    # same child PART + same path, but distinct STABLE row keys
    assert {ln["part_id"] for ln in lines} == {"C1"}
    assert all(ln["path"] == ["P1"] for ln in lines)
    assert {ln["bom_line_id"] for ln in lines} == {"R1a", "R1b"}
    assert {ln["uom"] for ln in lines} == {"EA", "PCS"}


def test_get_entitled_missing_part_is_404(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    r = _client(db_session).get(ROUTE_PATH.format(part_id="NOPE"))
    assert r.status_code == 404


def test_get_entitled_non_part_item_is_400(db_session, monkeypatch):
    # The endpoint is a Part BOM review; a non-Part Item is a bad request (before permission).
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    db_session.add(_part("DOC1", item_number="DOC-1", name="Spec", state="Released", generation=1, item_type="Document"))
    db_session.commit()
    r = _client(db_session).get(ROUTE_PATH.format(part_id="DOC1"))
    assert r.status_code == 400


def test_get_entitled_permission_denied_is_403(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch, allow=False)
    _make_bom(db_session)
    r = _client(db_session).get(ROUTE_PATH.format(part_id="P1"))
    assert r.status_code == 403


# --- route surface + live-app ownership ---------------------------------------

def test_router_exposes_exactly_one_route():
    assert len(bom_multitable_router.routes) == 1


def test_live_app_owns_the_route_with_this_router():
    # Guard against a mis-mount / missing mount in app.py being caught only indirectly via
    # the route-count pin: assert the LIVE app exposes the exact path, GET, owned by this
    # router's handler.
    app = create_app()
    matches = [
        r for r in app.routes
        if getattr(r, "path", None) == "/api/v1/bom/multitable/{part_id}/context"
    ]
    assert len(matches) == 1, "exactly one route must own the BOM multi-table context path"
    route = matches[0]
    assert "GET" in route.methods
    assert route.endpoint.__name__ == "bom_multitable_context"
    assert route.endpoint.__module__ == "yuantus.meta_engine.web.bom_multitable_router"
