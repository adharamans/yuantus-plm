"""Lifecycle transition-history read route (Slice 2):
GET /api/v1/items/{item_id}/transition-history.

Reads the audit rows written by promote() (Slice 1). Tests insert rows directly with explicit
distinct created_at (not via promote(), to keep this slice's tests decoupled from the write
path) and assert the most-recent-first order, item isolation, the empty-list vs 404 split, the
auth gate, and route ownership.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.app import create_app
from yuantus.api.dependencies.admin_auth import require_superuser
from yuantus.api.dependencies.auth import get_current_identity, get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.lifecycle.models import LifecycleTransitionHistory
from yuantus.meta_engine.models.item import Item
from yuantus.models.base import Base

_USER = SimpleNamespace(id=1, roles=["user"], is_superuser=False)
_ADMIN = SimpleNamespace(id=9, roles=["superuser"], is_superuser=True)
_URL = "/api/v1/items/{}/transition-history"
_FORENSIC = "/api/v1/transition-history/forensic/{}"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # the auth-enforce middleware reads AUTH_MODE per request; "optional" lets the TestClient
    # through so the get_current_user override is what supplies the (authenticated) user.
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


@pytest.fixture(autouse=True)
def _perm_allow(monkeypatch):
    # The item-scoped read now does a per-item ACL (check_permission(item_type_id, get) -> 403,
    # matching bom_where_used/impact). Default-allow here so the existing read tests exercise the
    # 200 path; the deny -> 403 path has its own test that re-patches to False.
    monkeypatch.setattr(
        "yuantus.meta_engine.web.lifecycle_transition_history_router.MetaPermissionService",
        lambda db: SimpleNamespace(check_permission=lambda *a, **k: True),
    )
    yield


@pytest.fixture()
def Session():
    from yuantus.meta_engine.bootstrap import import_all_models
    from yuantus.models import user as _user  # noqa: F401  - registers the 'users' FK target

    import_all_models()
    eng = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


@pytest.fixture()
def db(Session):
    s = Session()
    try:
        yield s
    finally:
        s.close()


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
    app.dependency_overrides[get_current_user] = lambda: _USER
    app.dependency_overrides[get_current_identity] = lambda: _ADMIN
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _item(db, iid):
    db.add(Item(id=iid, config_id=iid, item_type_id="t", state="Released", is_current=True, properties={}))
    db.commit()


def _hist(db, *, item_id, created_at, to_state_name="Released", outcome="success", **kw):
    row = LifecycleTransitionHistory(
        id=str(uuid.uuid4()), item_id=item_id, created_at=created_at,
        to_state_name=to_state_name, outcome=outcome, **kw,
    )
    db.add(row)
    db.commit()
    return row


def test_returns_history_most_recent_first(client, db):
    _item(db, "I")
    # explicit, distinct created_at — order must follow created_at desc, NOT insertion order.
    _hist(db, item_id="I", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 3), to_state_name="C")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 2), to_state_name="B")
    body = client.get(_URL.format("I")).json()
    assert body["count"] == 3
    assert [r["to_state_name"] for r in body["items"]] == ["C", "B", "A"]


def test_item_isolation(client, db):
    _item(db, "I")
    _item(db, "J")
    _hist(db, item_id="I", created_at=datetime(2026, 6, 1))
    _hist(db, item_id="J", created_at=datetime(2026, 6, 2))
    body = client.get(_URL.format("I")).json()
    assert body["count"] == 1 and all(r["item_id"] == "I" for r in body["items"])


def test_empty_list_for_existing_item_with_no_history(client, db):
    _item(db, "I")  # exists, but no history
    r = client.get(_URL.format("I"))
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_404_for_missing_item(client, db):
    assert client.get(_URL.format("ghost")).status_code == 404


def test_item_route_403_without_read_permission(client, db, monkeypatch):
    # per-item ACL (2a): a caller without get-permission on the item's type gets 403, not data.
    _item(db, "I")
    monkeypatch.setattr(
        "yuantus.meta_engine.web.lifecycle_transition_history_router.MetaPermissionService",
        lambda db: SimpleNamespace(check_permission=lambda *a, **k: False),
    )
    assert client.get(_URL.format("I")).status_code == 403


def test_limit_caps_to_most_recent(client, db):
    _item(db, "I")
    for d in (1, 2, 3, 4):
        _hist(db, item_id="I", created_at=datetime(2026, 6, d), to_state_name=f"s{d}")
    body = client.get(_URL.format("I") + "?limit=2").json()
    assert body["count"] == 2
    assert [r["to_state_name"] for r in body["items"]] == ["s4", "s3"]  # most recent 2


def test_serializes_permission_actor_and_comment(client, db):
    _item(db, "I")
    _hist(
        db, item_id="I", created_at=datetime(2026, 6, 1), from_state_name="Draft",
        from_permission_id="p_draft", to_permission_id="p_rel", actor_user_id=7, comment="go",
    )
    row = client.get(_URL.format("I")).json()["items"][0]
    assert row["from_permission_id"] == "p_draft" and row["to_permission_id"] == "p_rel"
    assert row["actor_user_id"] == 7 and row["comment"] == "go" and row["outcome"] == "success"


# -- route owner / auth contracts ---------------------------------------------
def _the_route():
    app = create_app()
    return next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/items/{item_id}/transition-history"
    )


def test_route_is_registered_and_owned():
    import yuantus.meta_engine.web.lifecycle_transition_history_router as mod

    route = _the_route()
    assert "GET" in route.methods
    assert route.endpoint.__module__ == mod.__name__  # owned by our router module


def test_route_is_auth_gated():
    # the get_current_user dependency must be wired (item-read auth pattern).
    deps = [d.call for d in _the_route().dependant.dependencies]
    assert get_current_user in deps


# -- forensic admin route (GET /api/v1/transition-history/forensic/{item_id}) --
def test_forensic_returns_deleted_item_history(client, db):
    # No _item(): the item never exists in the table (a *deleted* item). Its FK-free history rows
    # are still present and MUST be returned — the whole point of the forensic route.
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), to_state_name="B")
    r = client.get(_FORENSIC.format("GONE"))
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [x["to_state_name"] for x in body["items"]] == ["B", "A"]  # created_at desc


def test_forensic_never_existed_id_is_empty_200_not_404(client, db):
    # Behavioral inverse of the item-scoped route's 404: a forensic lookup of an id with no
    # history is an empty 200, never 404 — this route deliberately does not resolve the item.
    r = client.get(_FORENSIC.format("neverwas"))
    assert r.status_code == 200 and r.json() == {"items": [], "count": 0}


def test_forensic_requires_superuser(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1))
    # a non-superuser identity must be rejected (require_superuser -> 403)
    client.app.dependency_overrides[get_current_identity] = lambda: SimpleNamespace(is_superuser=False)
    assert client.get(_FORENSIC.format("GONE")).status_code == 403


def _forensic_route():
    app = create_app()
    return next(
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/transition-history/forensic/{item_id}"
    )


def test_forensic_route_is_registered_and_owned():
    import yuantus.meta_engine.web.lifecycle_transition_history_router as mod

    route = _forensic_route()
    assert "GET" in route.methods
    assert route.endpoint.__module__ == mod.__name__  # owned by our router module


def test_forensic_route_is_admin_gated():
    # require_superuser must be wired; the bare get_current_user item-read pattern must NOT be.
    deps = [d.call for d in _forensic_route().dependant.dependencies]
    assert require_superuser in deps
    assert get_current_user not in deps


# -- forensic ?outcome filter (L2-1) ------------------------------------------
def test_forensic_filters_by_single_outcome(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="success")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="denied")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 3), outcome="blocked")
    body = client.get(_FORENSIC.format("GONE") + "?outcome=denied").json()
    assert body["count"] == 1
    assert [r["outcome"] for r in body["items"]] == ["denied"]


def test_forensic_filters_by_multiple_outcomes(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="success")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="denied")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 3), outcome="blocked")
    # repeatable query param: ?outcome=denied&outcome=blocked -> the union, success excluded.
    body = client.get(_FORENSIC.format("GONE") + "?outcome=denied&outcome=blocked").json()
    assert body["count"] == 2
    assert {r["outcome"] for r in body["items"]} == {"denied", "blocked"}


def test_forensic_no_outcome_filter_returns_all_outcomes(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="success")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="denied")
    body = client.get(_FORENSIC.format("GONE")).json()
    assert body["count"] == 2  # unfiltered forensic read still returns every outcome


def test_forensic_invalid_outcome_is_400(client, db):
    r = client.get(_FORENSIC.format("GONE") + "?outcome=bogus")
    assert r.status_code == 400
    assert "invalid outcome" in r.json()["detail"]


def test_forensic_outcome_filter_composes_with_limit(client, db):
    for d in (1, 2, 3):
        _hist(db, item_id="GONE", created_at=datetime(2026, 6, d), outcome="denied")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 4), outcome="success")
    # filter (denied) AND limit (2) compose; success row excluded, most-recent-2 denied returned.
    body = client.get(_FORENSIC.format("GONE") + "?outcome=denied&limit=2").json()
    assert body["count"] == 2
    assert all(r["outcome"] == "denied" for r in body["items"])


# -- forensic ?reason_code filter ---------------------------------------------
# reason_code lives in LifecycleTransitionHistory.properties JSON. Design (owner B): accept ANY
# string; unknown -> empty (no whitelist, no 400) so the surface is robust to vocabulary growth.
def test_forensic_filters_by_single_reason_code(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied",
          properties={"reason_code": "permission_denied"})
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="aborted",
          properties={"reason_code": "condition_failed"})
    body = client.get(_FORENSIC.format("GONE") + "?reason_code=permission_denied").json()
    assert body["count"] == 1
    assert [r["properties"]["reason_code"] for r in body["items"]] == ["permission_denied"]


def test_forensic_filters_by_multiple_reason_codes(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied",
          properties={"reason_code": "permission_denied"})
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="aborted",
          properties={"reason_code": "condition_failed"})
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 3), outcome="blocked",
          properties={"reason_code": "assembly_release_blocked"})
    # repeatable query param: ?reason_code=A&reason_code=B -> the union.
    body = client.get(
        _FORENSIC.format("GONE")
        + "?reason_code=permission_denied&reason_code=condition_failed"
    ).json()
    assert body["count"] == 2
    assert {r["properties"]["reason_code"] for r in body["items"]} == {
        "permission_denied", "condition_failed"
    }


def test_forensic_no_reason_code_filter_returns_all(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied",
          properties={"reason_code": "permission_denied"})
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="success")
    body = client.get(_FORENSIC.format("GONE")).json()
    assert body["count"] == 2  # unfiltered forensic read still returns every row


def test_forensic_reason_code_filter_composes_with_limit(client, db):
    for d in (1, 2, 3):
        _hist(db, item_id="GONE", created_at=datetime(2026, 6, d), outcome="denied",
              properties={"reason_code": "permission_denied"})
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 4), outcome="success",
          properties={"reason_code": "other"})
    # filter (permission_denied) AND limit (2) compose; most-recent-2 matching rows returned.
    body = client.get(
        _FORENSIC.format("GONE") + "?reason_code=permission_denied&limit=2"
    ).json()
    assert body["count"] == 2
    assert all(r["properties"]["reason_code"] == "permission_denied" for r in body["items"])
    assert [r["created_at"] for r in body["items"]] == [
        datetime(2026, 6, 3).isoformat(), datetime(2026, 6, 2).isoformat()
    ]


def test_forensic_unknown_reason_code_is_empty_not_400(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied",
          properties={"reason_code": "permission_denied"})
    r = client.get(_FORENSIC.format("GONE") + "?reason_code=never_minted_code")
    assert r.status_code == 200  # ANY string accepted; unknown -> empty (no whitelist, no 400)
    assert r.json()["count"] == 0


# -- forensic ?actor filter (L2 Fork-A cont.) ---------------------------------
def test_forensic_filters_by_single_actor(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied", actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), outcome="denied", actor_user_id=42)
    body = client.get(_FORENSIC.format("GONE") + "?actor=42").json()
    assert body["count"] == 1
    assert [r["actor_user_id"] for r in body["items"]] == [42]


def test_forensic_filters_by_multiple_actors(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 2), actor_user_id=42)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 3), actor_user_id=99)
    body = client.get(_FORENSIC.format("GONE") + "?actor=7&actor=99").json()
    assert body["count"] == 2
    assert {r["actor_user_id"] for r in body["items"]} == {7, 99}


def test_forensic_unknown_actor_is_empty_not_error(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), actor_user_id=7)
    r = client.get(_FORENSIC.format("GONE") + "?actor=123456")
    assert r.status_code == 200  # FK-free id; unknown actor simply matches nothing
    assert r.json()["count"] == 0


def test_forensic_actor_composes_with_outcome_and_limit(client, db):
    for d in (1, 2, 3):
        _hist(db, item_id="GONE", created_at=datetime(2026, 6, d), outcome="denied", actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 4), outcome="success", actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5), outcome="denied", actor_user_id=42)
    body = client.get(_FORENSIC.format("GONE") + "?actor=7&outcome=denied&limit=2").json()
    assert body["count"] == 2
    assert all(r["actor_user_id"] == 7 and r["outcome"] == "denied" for r in body["items"])


# -- forensic ?created-after / ?created-before (date-range) --------------------
def test_forensic_filters_by_created_after(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5), to_state_name="B")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 9), to_state_name="C")
    body = client.get(_FORENSIC.format("GONE") + "?created_after=2026-06-05").json()
    assert body["count"] == 2  # inclusive: 06-05 and 06-09
    assert [r["to_state_name"] for r in body["items"]] == ["C", "B"]  # created_at desc


def test_forensic_filters_by_created_before(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), to_state_name="A")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5), to_state_name="B")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 9), to_state_name="C")
    body = client.get(_FORENSIC.format("GONE") + "?created_before=2026-06-05").json()
    assert body["count"] == 2  # inclusive: 06-01 and 06-05


def test_forensic_filters_by_created_range_and_other_filters(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 1), outcome="denied", actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5), outcome="denied", actor_user_id=7)
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 9), outcome="denied", actor_user_id=7)
    body = client.get(
        _FORENSIC.format("GONE")
        + "?created_after=2026-06-02&created_before=2026-06-06&outcome=denied&actor=7"
    ).json()
    assert body["count"] == 1  # only 06-05 in [06-02, 06-06]


def test_forensic_created_with_datetime_component(client, db):
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 8, 0, 0))
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 18, 0, 0))
    body = client.get(_FORENSIC.format("GONE") + "?created_after=2026-06-05T12:00:00").json()
    assert body["count"] == 1  # only the 18:00 row is >= noon


def test_forensic_invalid_created_after_is_400(client, db):
    r = client.get(_FORENSIC.format("GONE") + "?created_after=not-a-date")
    assert r.status_code == 400
    assert "invalid created_after" in r.json()["detail"]


def test_forensic_invalid_created_before_is_400(client, db):
    r = client.get(_FORENSIC.format("GONE") + "?created_before=2026-13-99")
    assert r.status_code == 400
    assert "invalid created_before" in r.json()["detail"]


def test_forensic_created_before_date_only_includes_whole_day(client, db):
    # date-only upper bound must include daytime rows on that day (end-of-day semantics),
    # not just the 00:00 instant — the footgun a midnight-only fixture would hide.
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 0, 0, 0), to_state_name="mid")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 14, 0, 0), to_state_name="day")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 6, 0, 0, 0), to_state_name="next")
    body = client.get(_FORENSIC.format("GONE") + "?created_before=2026-06-05").json()
    assert body["count"] == 2  # both 06-05 rows (00:00 AND 14:00); 06-06 excluded
    assert {r["to_state_name"] for r in body["items"]} == {"mid", "day"}


def test_forensic_created_before_datetime_is_exact_instant(client, db):
    # a datetime upper bound (explicit time) is used exactly, NOT widened to end-of-day
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 8, 0, 0), to_state_name="am")
    _hist(db, item_id="GONE", created_at=datetime(2026, 6, 5, 18, 0, 0), to_state_name="pm")
    body = client.get(_FORENSIC.format("GONE") + "?created_before=2026-06-05T12:00:00").json()
    assert body["count"] == 1  # only the 08:00 row is <= noon
    assert [r["to_state_name"] for r in body["items"]] == ["am"]


# -- forensic cross-item summary (L2 next-slice) ------------------------------
def test_forensic_summary_aggregates_top_failures_and_reasons(client, db):
    _hist(db, item_id="I-1", created_at=datetime(2026, 6, 1), outcome="denied",
          actor_user_id=7, properties={"reason_code": "permission_denied"})
    _hist(db, item_id="I-1", created_at=datetime(2026, 6, 2), outcome="denied",
          actor_user_id=7, properties={"reason_code": "permission_denied"})
    _hist(db, item_id="I-2", created_at=datetime(2026, 6, 3), outcome="blocked",
          actor_user_id=42, properties={"reason_code": "condition_failed"})
    _hist(db, item_id="I-3", created_at=datetime(2026, 6, 4), outcome="success",
          actor_user_id=7)
    r = client.get("/api/v1/transition-history/forensic/summary?top_n=2")
    assert r.status_code == 200  # /summary is literal, not forensic/{item_id}
    body = r.json()
    assert body["total_rows"] == 4
    assert body["failed_rows"] == 3
    assert body["top_outcomes"] == [
        {"outcome": "denied", "count": 2},
        {"outcome": "blocked", "count": 1},
    ]
    assert body["top_reason_codes"] == [
        {"reason_code": "permission_denied", "count": 2},
        {"reason_code": "condition_failed", "count": 1},
    ]
    assert body["top_failed_item_ids"][0] == {"item_id": "I-1", "count": 2}
    assert body["top_failed_actor_user_ids"][0] == {"actor_user_id": 7, "count": 2}


def test_forensic_summary_reuses_filters(client, db):
    _hist(db, item_id="I-1", created_at=datetime(2026, 6, 1), outcome="denied",
          actor_user_id=7, properties={"reason_code": "permission_denied"})
    _hist(db, item_id="I-2", created_at=datetime(2026, 6, 2), outcome="blocked",
          actor_user_id=42, properties={"reason_code": "condition_failed"})
    _hist(db, item_id="I-3", created_at=datetime(2026, 6, 7), outcome="denied",
          actor_user_id=42, properties={"reason_code": "permission_denied"})
    body = client.get(
        "/api/v1/transition-history/forensic/summary"
        "?outcome=denied&actor=42&created_after=2026-06-05"
    ).json()
    assert body["total_rows"] == 1
    assert body["failed_rows"] == 1
    assert body["top_failed_item_ids"] == [{"item_id": "I-3", "count": 1}]
    assert body["top_reason_codes"] == [{"reason_code": "permission_denied", "count": 1}]


def test_forensic_summary_invalid_outcome_is_400(client, db):
    r = client.get("/api/v1/transition-history/forensic/summary?outcome=bogus")
    assert r.status_code == 400
    assert "invalid outcome" in r.json()["detail"]


def test_forensic_summary_requires_superuser(client, db):
    client.app.dependency_overrides[get_current_identity] = lambda: SimpleNamespace(is_superuser=False)
    assert client.get("/api/v1/transition-history/forensic/summary").status_code == 403
