"""PLM-COLLAB-P3-A: BOM multi-table governed READ-ONLY projection route.

A minimal FastAPI app mounts ONLY ``bom_multitable_router`` with ``get_db`` overridden
to an in-memory SQLite session and ``get_current_user`` overridden to drive the auth
gate. The full schema is created (Item's FK web) by importing the app to register all
models, then ``create_all``. Tenancy is single-mode -> resolver yields "default".

``bom_multitable`` is a RESERVED entitlement key (maps to no app -> always False) until a
later slice lights it, so the entitled-path tests TEST-ONLY light it by mapping the key
to ``plm.collab`` (monkeypatch ``FEATURE_APP_NAMES``) + adding a matching ``AppLicense``.
This exercises the REAL ``EntitlementService.is_entitled`` query path, not a stub.

Pins (the owner-listed P3-A obligations):
- GET unauthenticated -> 401 (auth is the outermost gate, before entitlement).
- GET unentitled -> ``context: null`` + upgrade affordance, IDENTICAL for an existing and
  a non-existent part: the part is never queried (no existence leak) AND PLM permission is
  never checked (pinned order: auth -> is_entitled -> part -> permission).
- GET entitled -> CURATED read-only snapshot: ONLY the review fields, and NONE of the raw
  ``Item.to_dict()`` internals (config_id / current_version_id / source_id / related_id /
  permission_id / is_current / item_type_id) -- asserted via EXACT key sets, which is only
  meaningful because the snapshot is built from a REAL Item through the REAL get_tree.
- GET entitled + missing part -> 404 (before permission). + permission denied -> 403.
- the router exposes exactly the 1 route (app-level 700 pinned elsewhere).
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
from yuantus.api.app import create_app  # noqa: F401  (import side-effect: model registration)
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

_USER = type("_User", (), {"id": 7, "roles": ["engineer"], "is_superuser": False})()

# The complete raw-internal surface of Item.to_dict() that MUST NOT reach MetaSheet.
_LEAKY_KEYS = {
    "id",
    "item_type_id",
    "config_id",
    "is_current",
    "current_state",
    "current_version_id",
    "created_by_id",
    "created_on",
    "modified_by_id",
    "modified_on",
    "owner_id",
    "permission_id",
    "source_id",
    "related_id",
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
    """TEST-ONLY: light the reserved ``bom_multitable`` key + add a matching license.

    Maps the key to ``plm.collab`` so the REAL is_entitled query path runs and matches the
    license below (single-mode tenant == "default"). Auto-reverted by monkeypatch.
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


def _make_bom(db_session):
    """A real 2-level BOM: part P1 -[Part BOM rel R1]-> child C1, all is_current."""
    db_session.add(
        Item(
            id="P1",
            item_type_id="Part",
            config_id="P1",
            generation=3,
            is_current=True,
            state="Released",
            properties={"item_number": "P-001", "name": "Assembly"},
        )
    )
    db_session.add(
        Item(
            id="C1",
            item_type_id="Part",
            config_id="C1",
            generation=1,
            is_current=True,
            state="Draft",
            properties={"item_number": "C-001", "name": "Bracket"},
        )
    )
    db_session.add(
        Item(
            id="R1",
            item_type_id="Part BOM",
            config_id="R1",
            is_current=True,
            source_id="P1",
            related_id="C1",
            properties={"quantity": 2, "uom": "EA", "find_num": "10", "refdes": "R1,R2"},
        )
    )
    db_session.commit()


URL = "/api/v1/bom/multitable/{part_id}/context"


# --- auth ---------------------------------------------------------------------

def test_get_unauthenticated_is_401(db_session):
    r = _client(db_session, user="unauth").get(URL.format(part_id="P1"))
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
    existing = client.get(URL.format(part_id="P1"))
    missing = client.get(URL.format(part_id="P999"))
    assert existing.status_code == 200 and missing.status_code == 200
    assert existing.json() == missing.json()
    body = existing.json()
    assert body["feature_key"] == FEATURE
    assert body["entitled"] is False
    assert body["upgrade"]["available"] is True
    assert body["context"] is None


# --- entitled: curated read-only snapshot -------------------------------------

def test_get_entitled_returns_curated_snapshot_without_internal_fields(
    db_session, monkeypatch
):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    _make_bom(db_session)

    body = _client(db_session).get(URL.format(part_id="P1")).json()
    assert body["entitled"] is True
    assert body["upgrade"]["available"] is False

    ctx = body["context"]
    assert ctx["sync_status"] == "snapshot"
    assert ctx["template_key"] == "bom_review"
    assert ctx["source_version"] == 3  # part.generation, the 铁律-5 provenance marker

    # part: EXACTLY the curated review keys -- no raw to_dict() internals leak through.
    part = ctx["part"]
    assert set(part.keys()) == {"part_id", "item_number", "name", "state", "generation"}
    assert part == {
        "part_id": "P1",
        "item_number": "P-001",
        "name": "Assembly",
        "state": "Released",
        "generation": 3,
    }
    assert not (_LEAKY_KEYS & set(part.keys()))

    # one BOM line, again EXACTLY the curated keys (item fields + relationship props).
    assert len(ctx["lines"]) == 1
    line = ctx["lines"][0]
    assert set(line.keys()) == {
        "item_number",
        "name",
        "state",
        "generation",
        "quantity",
        "uom",
        "find_num",
        "refdes",
    }
    assert line == {
        "item_number": "C-001",
        "name": "Bracket",
        "state": "Draft",
        "generation": 1,
        "quantity": 2,
        "uom": "EA",
        "find_num": "10",
        "refdes": "R1,R2",
    }
    assert not (_LEAKY_KEYS & set(line.keys()))


def test_get_entitled_missing_part_is_404(db_session, monkeypatch):
    # Entitled + permission allowed, but the part is absent -> 404 (raised BEFORE the
    # permission gate; existence is allowed to be revealed once entitled).
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch)
    r = _client(db_session).get(URL.format(part_id="NOPE"))
    assert r.status_code == 404


def test_get_entitled_permission_denied_is_403(db_session, monkeypatch):
    _light_entitlement(monkeypatch, db_session)
    _allow_permission(monkeypatch, allow=False)
    _make_bom(db_session)
    r = _client(db_session).get(URL.format(part_id="P1"))
    assert r.status_code == 403


# --- route surface ------------------------------------------------------------

def test_router_exposes_exactly_one_route():
    assert len(bom_multitable_router.routes) == 1
