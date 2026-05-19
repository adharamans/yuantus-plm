"""Tier-B #3 §3.3 `update_status` auto-trigger — R1 tests.

Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_UPDATE_STATUS_AUTO_TRIGGER_20260519.md`
(merged #605 `fb9d0b5`). The 7 MANDATORY exactly-named tests from
§5 plus the route-level tests §5 requires (auto_loopback default
False; 409 race mapping; ECO-permission verbatim propagation) and
one defensive byte-identical-route guard.

Ratified behavior pinned here (§3.C, single deterministic):

- default-OFF (`auto_loopback=False`) is byte-identical pre-§3.3
  — no eligibility check, no create call, no extra query.
- eligible NEW status (product policy A1, no delta gating) fires
  `create_breakage_design_loopback_eco(..., allow_duplicate=False)`.
- normal CAS-loser race → the service self-heals (re-reads,
  re-applies the target status, flushes) and returns an ordinary
  success — NO client-visible retry, NO 409.
- unrecoverable race only (re-read finds no incident / no winner
  ECO) → `BreakageDesignLoopbackLinkRace` → route 409
  `breakage_loopback_link_race`.
- ECO-create permission failure → propagates verbatim (rollback +
  re-raise, mirroring §3.1), NOT collapsed into the legacy 400.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.database import get_db
from yuantus.exceptions.handlers import PermissionError as PLMPermissionError
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.lifecycle.models import LifecycleMap, LifecycleState
from yuantus.meta_engine.models.eco import ECO, ECOStage
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.models.parallel_tasks import BreakageIncident
from yuantus.meta_engine.permission.models import Permission
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageDesignLoopbackEcoCreation,
    BreakageDesignLoopbackLinkRace,
    BreakageIncidentService,
)
from yuantus.meta_engine.version.models import ItemVersion
from yuantus.meta_engine.web import parallel_tasks_breakage_router as router_module
from yuantus.meta_engine.web.parallel_tasks_breakage_router import (
    parallel_tasks_breakage_router,
)
from yuantus.meta_engine.workflow.models import WorkflowMap
from yuantus.models.base import Base
from yuantus.security.rbac.models import (
    RBACPermission,
    RBACResource,
    RBACRole,
    RBACUser,
    rbac_user_permissions,
    rbac_user_roles,
    role_permissions,
)


# --------------------------------------------------------------------------
# Harness — mirrors test_breakage_design_loopback_durable_idempotency.py
# (StaticPool shared in-memory so multiple sessions see one DB, which the
# §3.C CAS-loser self-heal test needs).
# --------------------------------------------------------------------------


def _tables():
    return [
        RBACResource.__table__,
        RBACPermission.__table__,
        RBACRole.__table__,
        RBACUser.__table__,
        rbac_user_roles,
        role_permissions,
        rbac_user_permissions,
        Permission.__table__,
        WorkflowMap.__table__,
        LifecycleMap.__table__,
        LifecycleState.__table__,
        ItemType.__table__,
        Item.__table__,
        ItemVersion.__table__,
        BreakageIncident.__table__,
        ECOStage.__table__,
        ECO.__table__,
    ]


def _engine():
    import_all_models()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine, tables=_tables())
    return engine


def _session(engine=None):
    engine = engine or _engine()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()


def _add_user(session, user_id: int) -> None:
    session.add(
        RBACUser(
            id=user_id,
            user_id=user_id,
            username=f"user-{user_id}",
            email=f"user-{user_id}@example.test",
        )
    )
    session.flush()


def _open_incident(service, *, description="auto-trigger case"):
    """Create a NON-eligible (status='open') incident so that the
    eligibility decision is driven by the `update_status` target,
    not the create-time status (exercises §3.A's post-update gate).
    """

    return service.create_incident(
        description=description,
        status="open",
        severity="high",
    )


def _current_user(user_id: int = 42) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        tenant_id="tenant-1",
        org_id="org-1",
        username=f"user-{user_id}",
        email=f"user-{user_id}@example.test",
        roles=["operator"],
        is_superuser=False,
    )


def _mock_db_client(user: CurrentUser | None = None):
    """Route HTTP-semantics client with a MagicMock db (mirrors the
    §3.1 route test idiom — isolates status-code mapping / rollback
    bookkeeping from a real session).
    """

    user = user or _current_user()
    mock_db = MagicMock()

    def override_get_db():
        yield mock_db

    app = FastAPI()
    app.include_router(parallel_tasks_breakage_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app), mock_db


def _real_db_client(engine, user: CurrentUser | None = None):
    """Route client whose `get_db` yields a real session on the
    shared engine — needed to assert end-to-end rollback (status
    NOT persisted) for the ECO-permission-failure test.
    """

    user = user or _current_user()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(parallel_tasks_breakage_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# ==========================================================================
# MANDATORY 1 — default-OFF is byte-identical pre-§3.3
# ==========================================================================


def test_update_status_default_off_is_byte_identical():
    """`auto_loopback` absent/False: NO eligibility check, NO
    create_…eco call, NO eco_id write. Status + updated_at behave
    exactly as the pre-§3.3 two-line body.
    """

    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _open_incident(service)
        session.commit()
        before = incident.updated_at

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
        ) as create_spy, patch(
            "yuantus.meta_engine.services.parallel_tasks_service."
            "is_breakage_eligible_for_design_loopback"
        ) as eligible_spy, patch(
            "yuantus.meta_engine.services.parallel_tasks_service."
            "resolve_breakage_eco_closure_descriptor"
        ) as descriptor_spy:
            # default (no auto_loopback kwarg) AND explicit False —
            # both must take the byte-identical path.
            service.update_status(incident.id, status="resolved")
            service.update_status(
                incident.id, status="closed", auto_loopback=False
            )
        session.commit()

        # No eligibility query, no descriptor build, no create call.
        create_spy.assert_not_called()
        eligible_spy.assert_not_called()
        descriptor_spy.assert_not_called()

        session.refresh(incident)
        assert incident.status == "closed"
        assert incident.eco_id is None
        assert incident.updated_at >= before
        assert session.query(ECO).count() == 0
    finally:
        session.close()


# ==========================================================================
# MANDATORY 2 — eligible new status spawns the durable link
# ==========================================================================


def test_update_status_auto_loopback_on_eligible_status_spawns_link():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _open_incident(service)
        session.commit()
        assert incident.eco_id is None

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            returned = service.update_status(
                incident.id,
                status="resolved",
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        session.refresh(incident)
        assert returned.id == incident.id
        assert incident.status == "resolved"
        assert incident.eco_id is not None
        eco = session.get(ECO, incident.eco_id)
        assert eco is not None
        assert session.query(ECO).count() == 1
    finally:
        session.close()


# ==========================================================================
# MANDATORY 3 — ineligible new status: status changes, loopback skipped
# ==========================================================================


def test_update_status_auto_loopback_skips_ineligible_status():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _open_incident(service)
        session.commit()

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
        ) as create_spy:
            service.update_status(
                incident.id,
                status="in_progress",
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        # §3.E: an ineligible new status is a valid no-op — gated
        # BEFORE create_…eco (never called and ValueError-swallowed).
        create_spy.assert_not_called()
        session.refresh(incident)
        assert incident.status == "in_progress"
        assert incident.eco_id is None
        assert session.query(ECO).count() == 0
    finally:
        session.close()


# ==========================================================================
# MANDATORY 4 — repeat trigger is idempotent (§3.2 durable dedupe)
# ==========================================================================


def test_update_status_auto_loopback_repeat_is_idempotent():
    """Two sequential `update_status(resolved, auto_loopback=True)`:
    the second hits §3.2's durable `eco_id` dedupe (`created=False`,
    no rollback). Per the unified self-heal path the status is
    re-applied harmlessly. Asserts only the contract guarantees —
    exactly ONE ECO, the SAME ECO, status stable — NOT update-count
    or updated_at invariance (the no-rollback sub-case re-applies).
    """

    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _open_incident(service)
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.update_status(
                incident.id,
                status="resolved",
                auto_loopback=True,
                loopback_user_id=42,
            )
            session.commit()
            session.refresh(incident)
            first_eco_id = incident.eco_id
            assert first_eco_id is not None

            service.update_status(
                incident.id,
                status="resolved",
                auto_loopback=True,
                loopback_user_id=42,
            )
            session.commit()

        session.refresh(incident)
        assert incident.eco_id == first_eco_id  # same ECO, no rewire
        assert incident.status == "resolved"  # stable
        assert session.query(ECO).count() == 1  # NO duplicate
    finally:
        session.close()


# ==========================================================================
# MANDATORY 5 — normal CAS-loser race: service self-heals the status
# ==========================================================================


def test_update_status_auto_loopback_cas_race_self_heals_status():
    """§3.C normal-race arm. Two sessions on a shared engine
    (mirroring §3.2's CAS test). The loser's auto-trigger does
    §3.2's internal `self.session.rollback()` (which ALSO unwinds
    the loser's status flush), then `update_status` re-reads,
    re-applies the LOSER's target status, flushes, and returns the
    incident as an **ordinary success** — NO exception, NO 409.

    Winner sets `closed`, loser sets `resolved` (both eligible) so
    the assertion proves the self-heal restores the *loser's*
    intended status, not coincidentally the winner's.
    """

    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 7)
        incident = _open_incident(
            BreakageIncidentService(setup), description="cas self-heal"
        )
        setup.commit()
        incident_id = incident.id

        # Winner: update_status(closed, auto_loopback) creates+links
        # ECO_W, commits, closes — fully before the loser starts.
        winner_sess = _session(engine)
        try:
            with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                BreakageIncidentService(winner_sess).update_status(
                    incident_id,
                    status="closed",
                    auto_loopback=True,
                    loopback_user_id=7,
                )
            winner_sess.commit()
            winner_eco_id = winner_sess.get(
                BreakageIncident, incident_id
            ).eco_id
            assert winner_eco_id is not None
        finally:
            winner_sess.close()

        # Loser: force its pre-create dedupe to miss so it proceeds
        # to create_eco + CAS; the CAS sees the winner's committed
        # eco_id → rowcount 0 → §3.2 rollback → §3.3 self-heal.
        # NO try/except: "no exception" is pinned by letting it
        # propagate.
        loser_sess = _session(engine)
        try:
            with patch.object(
                BreakageIncidentService,
                "_find_breakage_design_loopback_eco_by_reference",
                return_value=None,
            ), patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                returned = BreakageIncidentService(loser_sess).update_status(
                    incident_id,
                    status="resolved",
                    auto_loopback=True,
                    loopback_user_id=7,
                )
            loser_sess.commit()
            assert returned.status == "resolved"
        finally:
            loser_sess.close()

        # Third probe: the loser's target status was self-healed and
        # committed; the link is the winner's ECO; the loser's ECO
        # INSERT was rolled back (exactly one ECO).
        probe = _session(engine)
        try:
            linked = probe.get(BreakageIncident, incident_id)
            assert linked.status == "resolved"  # loser target re-applied
            assert linked.eco_id == winner_eco_id  # winner link intact
            assert probe.query(ECO).count() == 1  # loser ECO rolled back
        finally:
            probe.close()
    finally:
        setup.close()


# ==========================================================================
# MANDATORY 6 — unrecoverable race → dedicated exception → route 409
# ==========================================================================


def _unrecoverable_creation(self, incident_id, *, user_id, allow_duplicate=False):
    """Taskbook-sanctioned forcing (§5): emulate a §3.2 CAS-loser
    that rolled back but left NO determinable winner ECO. Does the
    real `self.session.rollback()` (unwinding the status flush)
    then returns `created=False, eco=None` — the exact
    unrecoverable shape §3.C step 2 raises on.
    """

    self.session.rollback()
    return BreakageDesignLoopbackEcoCreation(
        incident_id=incident_id,
        preparation=None,
        reference="",
        eco=None,
        created=False,
    )


def test_update_status_auto_loopback_unrecoverable_race_maps_409():
    # Service-level: the dedicated exception is raised.
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _open_incident(service)
        session.commit()
        incident_id = incident.id

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
            autospec=True,
            side_effect=_unrecoverable_creation,
        ):
            with pytest.raises(BreakageDesignLoopbackLinkRace):
                service.update_status(
                    incident_id,
                    status="resolved",
                    auto_loopback=True,
                    loopback_user_id=42,
                )
    finally:
        session.close()

    # Route-level: the dedicated exception maps to a retryable 409
    # `breakage_loopback_link_race` — NOT the legacy 400.
    client, mock_db = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "update_status",
        MagicMock(
            side_effect=BreakageDesignLoopbackLinkRace(
                "breakage design loopback link race — cannot determine "
                "the linked ECO for incident brk-x"
            )
        ),
    ):
        response = client.post(
            "/api/v1/breakages/brk-x/status",
            json={"status": "resolved", "auto_loopback": True},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "breakage_loopback_link_race"
    assert detail["context"] == {"incident_id": "brk-x"}
    assert mock_db.rollback.called
    assert not mock_db.commit.called


# ==========================================================================
# MANDATORY 7 — ECO permission failure rolls back the status change
# ==========================================================================


def test_update_status_auto_loopback_eco_permission_failure_rolls_back_status():
    """Eligible + `ECOService.create_eco` permission error: the
    WHOLE transaction rolls back (status NOT changed) and the error
    propagates verbatim — NOT collapsed into 400. End-to-end via a
    real session on the shared engine.
    """

    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 42)
        incident = _open_incident(
            BreakageIncidentService(setup), description="perm fail"
        )
        setup.commit()
        incident_id = incident.id
    finally:
        setup.close()

    client = _real_db_client(engine)
    # The REAL `update_status` runs (status flush + real eligibility
    # gate); only the ECO-create boundary is forced to deny. No
    # app-wide PLMException handler exists (verified by direct read)
    # so the raw PermissionError propagates out — exactly §3.1's
    # "verbatim" behavior the taskbook mirrors. The discriminator:
    # it RAISES (was NOT collapsed into a 400 JSON response by the
    # legacy `except Exception` clause).
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        MagicMock(
            side_effect=PLMPermissionError(action="create", resource="ECO")
        ),
    ):
        with pytest.raises(PLMPermissionError):
            client.post(
                f"/api/v1/breakages/{incident_id}/status",
                json={"status": "resolved", "auto_loopback": True},
            )

    probe = _session(engine)
    try:
        after = probe.get(BreakageIncident, incident_id)
        assert after.status == "open"  # status change rolled back
        assert after.eco_id is None
        assert probe.query(ECO).count() == 0
    finally:
        probe.close()


# ==========================================================================
# Route-level — §5 requires: auto_loopback default False; ECO-permission
# verbatim (HTTPException carrier); plus a defensive legacy-400 guard.
# ==========================================================================


def test_route_status_update_auto_loopback_defaults_false_and_forwards():
    """The route forwards `auto_loopback` verbatim and
    `loopback_user_id=int(user.id)`. Omitted body field → False
    (byte-identical default-OFF reaches the service as False).
    """

    client, _ = _mock_db_client(_current_user(user_id=99))
    spy = MagicMock(
        return_value=MagicMock(
            id="brk-1", status="resolved", updated_at=None
        )
    )
    with patch.object(
        router_module.BreakageIncidentService, "update_status", spy
    ):
        # Omitted → default False.
        client.post(
            "/api/v1/breakages/brk-1/status", json={"status": "resolved"}
        )
        assert spy.call_args.kwargs["auto_loopback"] is False
        assert spy.call_args.kwargs["loopback_user_id"] == 99

        # Explicit True forwarded.
        client.post(
            "/api/v1/breakages/brk-1/status",
            json={"status": "resolved", "auto_loopback": True},
        )
        assert spy.call_args.kwargs["auto_loopback"] is True
        assert spy.call_args.kwargs["loopback_user_id"] == 99


def test_route_eco_permission_failure_propagates_verbatim_not_400():
    """§10 cross-callsite subtlety. Two carriers:

    1. `HTTPException(403)` stand-in (the §3.1 idiom): surfaces 403
       verbatim, NOT `breakage_status_invalid`.
    2. Real `PermissionError` (PLMException): the route's
       `except (HTTPException, PLMException)` clause rolls back and
       re-raises — it RAISES out (was not swallowed by the legacy
       `except Exception → 400`). `pytest.raises` is the
       discriminator: a 400 collapse would return a JSON response,
       not raise.
    """

    # Carrier 1 — HTTPException(403).
    client, db = _mock_db_client()
    http_403 = HTTPException(
        status_code=403,
        detail={"code": "eco_create_denied", "message": "Forbidden"},
    )
    with patch.object(
        router_module.BreakageIncidentService,
        "update_status",
        MagicMock(side_effect=http_403),
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/status",
            json={"status": "resolved", "auto_loopback": True},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "eco_create_denied"
    assert response.json()["detail"]["code"] != "breakage_status_invalid"
    assert db.rollback.called
    assert not db.commit.called

    # Carrier 2 — real PermissionError (PLMException subclass).
    client2, db2 = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "update_status",
        MagicMock(
            side_effect=PLMPermissionError(action="create", resource="ECO")
        ),
    ):
        with pytest.raises(PLMPermissionError):
            client2.post(
                "/api/v1/breakages/brk-1/status",
                json={"status": "resolved", "auto_loopback": True},
            )
    assert db2.rollback.called
    assert not db2.commit.called


def test_route_default_off_flush_error_still_maps_legacy_400():
    """Defensive byte-identical guard (route side). A non-
    HTTPException / non-PLMException failure on the default-OFF
    path (e.g. a generic service error) must STILL map to the
    pre-§3.3 `400 breakage_status_invalid` — proving the new
    `except (HTTPException, PLMException)` clause did not steal the
    legacy 400.
    """

    client, db = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "update_status",
        MagicMock(side_effect=RuntimeError("flush blew up")),
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/status",
            json={"status": "resolved"},  # auto_loopback omitted → False
        )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "breakage_status_invalid"
    assert db.rollback.called
    assert not db.commit.called
