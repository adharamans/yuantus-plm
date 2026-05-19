"""Tier-B #3 §3.4 helpdesk-sync auto-trigger — R1 tests.

Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_HELPDESK_SYNC_AUTO_TRIGGER_20260519.md`
(merged #607 `cab1162`, §3.C ratified as β). The 8 MANDATORY
exactly-named tests from §5 plus the route-level tests §5
requires. `test_breakage_update_status_auto_trigger.py` stays
green UNCHANGED — that file is the §3.E behavior-preservation
proof for the shared `_auto_trigger_design_loopback` helper
extraction.

Ratified behavior pinned here:

- default-OFF (`auto_loopback=False`) byte-identical pre-§3.4.
- §3.D **double gate**: fire iff `derived_sync_status ==
  "completed"` AND the post-update status is loopback-eligible.
  Vector A (`canceled` → incident `closed`/sync `failed`) and
  Vector B (explicit `incident_status` override + failed sync)
  both MUST NOT fire.
- §3.C-β: status-only flush → shared §3.3 helper → THEN the
  heavy helpdesk mutations, so a §3.2 CAS-loser rollback never
  silently drops job payload / responsibility / event ids.
- unrecoverable race → `BreakageDesignLoopbackLinkRace` → 409.
- ECO-permission failure → whole transaction rolls back,
  propagates verbatim (NOT collapsed into 400).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
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
from yuantus.meta_engine.models.job import ConversionJob
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
# Harness — mirrors §3.2/§3.3 (StaticPool shared in-memory + ConversionJob).
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
        ConversionJob.__table__,
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


def _incident_with_job(service, *, description="helpdesk auto-trigger"):
    """Create an `open` incident + a helpdesk sync job (so
    `_resolve_helpdesk_sync_job` resolves it). Returns (incident, job).
    """

    incident = service.create_incident(
        description=description, status="open", severity="high"
    )
    service.session.commit()
    job = service.enqueue_helpdesk_stub_sync(
        incident.id, user_id=42, provider="stub"
    )
    service.session.commit()
    return incident, job


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
# MANDATORY 1 — default-OFF byte-identical
# ==========================================================================


def test_helpdesk_ticket_update_default_off_is_byte_identical():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)

        with patch.object(
            BreakageIncidentService,
            "_auto_trigger_design_loopback",
        ) as helper_spy, patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
        ) as create_spy, patch(
            "yuantus.meta_engine.services.parallel_tasks_service."
            "is_breakage_eligible_for_design_loopback"
        ) as eligible_spy:
            # auto_loopback omitted → default False. provider
            # 'resolved' → incident 'resolved', sync 'completed'.
            service.apply_helpdesk_ticket_update(
                incident.id,
                provider_ticket_status="resolved",
                job_id=job.id,
                provider_assignee="alice",
            )
        session.commit()

        helper_spy.assert_not_called()
        create_spy.assert_not_called()
        eligible_spy.assert_not_called()

        session.refresh(incident)
        assert incident.status == "resolved"
        assert incident.responsibility == "alice"
        assert incident.eco_id is None
        assert session.query(ECO).count() == 0
        refreshed_job = session.get(ConversionJob, job.id)
        assert refreshed_job.payload["helpdesk_sync"]["sync_status"] == "completed"
    finally:
        session.close()


# ==========================================================================
# MANDATORY 2 — completed + eligible spawns the durable link
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_completed_eligible_spawns_link():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            result = service.apply_helpdesk_ticket_update(
                incident.id,
                provider_ticket_status="resolved",
                job_id=job.id,
                provider_assignee="alice",
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        session.refresh(incident)
        assert incident.status == "resolved"
        assert incident.responsibility == "alice"
        assert incident.eco_id is not None
        assert session.get(ECO, incident.eco_id) is not None
        assert session.query(ECO).count() == 1
        # Response shape UNCHANGED (§3.J): still the
        # get_helpdesk_sync_status dict — no loopback field added.
        assert result["incident_status"] == "resolved"
        assert result["incident_responsibility"] == "alice"
        assert result["sync_status"] == "completed"
        assert "eco_id" not in result and "loopback" not in result
        refreshed_job = session.get(ConversionJob, job.id)
        assert refreshed_job.payload["helpdesk_sync"]["sync_status"] == "completed"
    finally:
        session.close()


# ==========================================================================
# MANDATORY 3 — §3.D Vector A: canceled → closed (eligible) but sync failed
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_canceled_eligible_but_failed_sync_does_not_fire():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
        ) as create_spy:
            service.apply_helpdesk_ticket_update(
                incident.id,
                provider_ticket_status="canceled",
                job_id=job.id,
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        # canceled → incident 'closed' (eligible) BUT derived sync
        # 'failed' → gate #1 blocks the trigger.
        create_spy.assert_not_called()
        session.refresh(incident)
        assert incident.status == "closed"
        assert incident.eco_id is None
        assert session.query(ECO).count() == 0
        refreshed_job = session.get(ConversionJob, job.id)
        assert refreshed_job.payload["helpdesk_sync"]["sync_status"] == "failed"
    finally:
        session.close()


# ==========================================================================
# MANDATORY 4 — §3.D Vector B: incident_status override + failed sync
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_incident_status_override_with_failed_sync_does_not_fire():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
        ) as create_spy:
            # explicit override forces incident 'closed' (eligible)
            # while provider 'failed' → derived sync 'failed'.
            service.apply_helpdesk_ticket_update(
                incident.id,
                provider_ticket_status="failed",
                incident_status="closed",
                job_id=job.id,
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        create_spy.assert_not_called()
        session.refresh(incident)
        assert incident.status == "closed"
        assert incident.eco_id is None
        assert session.query(ECO).count() == 0
    finally:
        session.close()


# ==========================================================================
# MANDATORY 5 — idempotent replay does not fire
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_idempotent_replay_does_not_fire():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)

        # First call records event 'evt-1' (auto_loopback OFF so it
        # cannot fire regardless — isolates the replay path).
        service.apply_helpdesk_ticket_update(
            incident.id,
            provider_ticket_status="resolved",
            job_id=job.id,
            event_id="evt-1",
        )
        session.commit()

        with patch.object(
            BreakageIncidentService,
            "_auto_trigger_design_loopback",
        ) as helper_spy:
            replay = service.apply_helpdesk_ticket_update(
                incident.id,
                provider_ticket_status="resolved",
                job_id=job.id,
                event_id="evt-1",
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        # Replay short-circuit returns before the status mutation /
        # branch → the shared helper is never reached.
        helper_spy.assert_not_called()
        assert replay["idempotent_replay"] is True
        session.refresh(incident)
        assert incident.eco_id is None
        assert session.query(ECO).count() == 0
    finally:
        session.close()


# ==========================================================================
# MANDATORY 6 — §3.C-β centerpiece: CAS race preserves status AND helpdesk
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_cas_race_preserves_status_and_helpdesk_mutations():
    """§3.C-β. Winner links an ECO via the §3.3 path + commits.
    The loser's helpdesk ticket-update auto-trigger does §3.2's
    internal rollback (unwinding ONLY the loser's status-only
    flush); the shared helper self-heals the status; then the
    §3.C-β continuation applies the heavy helpdesk mutations on
    the helper's returned incident + a PK-refetched job. The
    committed row must show: status self-healed, winner's ECO
    linked, exactly one ECO, AND responsibility + the job payload/
    status mutations present (proving the reorder kept helpdesk
    state out of the rolled-back window).
    """

    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 7)
        svc_setup = BreakageIncidentService(setup)
        incident, job = _incident_with_job(svc_setup, description="cas helpdesk")
        incident_id = incident.id
        job_id = job.id

        # Winner: §3.3 update_status path links ECO_W + commits.
        winner_sess = _session(engine)
        try:
            with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                BreakageIncidentService(winner_sess).update_status(
                    incident_id,
                    status="resolved",
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

        # Loser: helpdesk ticket-update auto-trigger. Force the
        # pre-create dedupe to miss so it proceeds to create_eco +
        # CAS, then loses to the winner's committed eco_id. NO
        # try/except — "no exception" is pinned by propagation.
        loser_sess = _session(engine)
        try:
            with patch.object(
                BreakageIncidentService,
                "_find_breakage_design_loopback_eco_by_reference",
                return_value=None,
            ), patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                BreakageIncidentService(loser_sess).apply_helpdesk_ticket_update(
                    incident_id,
                    provider_ticket_status="resolved",
                    job_id=job_id,
                    provider_assignee="alice",
                    auto_loopback=True,
                    loopback_user_id=7,
                )
            loser_sess.commit()
        finally:
            loser_sess.close()

        probe = _session(engine)
        try:
            linked = probe.get(BreakageIncident, incident_id)
            assert linked.status == "resolved"  # self-healed
            assert linked.eco_id == winner_eco_id  # winner link intact
            assert linked.responsibility == "alice"  # helpdesk mutation kept
            assert probe.query(ECO).count() == 1  # loser ECO rolled back
            probed_job = probe.get(ConversionJob, job_id)
            assert (
                probed_job.payload["helpdesk_sync"]["sync_status"] == "completed"
            )
            assert probed_job.status == "completed"
        finally:
            probe.close()
    finally:
        setup.close()


# ==========================================================================
# MANDATORY 7 — unrecoverable race → dedicated exception → route 409
# ==========================================================================


def _unrecoverable_creation(
    self, incident_id, *, user_id, allow_duplicate=False, **_kw
):
    # **_kw absorbs the Tier-B #3 §3.6 §3.F additive kwargs
    # (trigger_source / sync_status / provider_ticket_status) that
    # the helper now threads to create_…eco; autospec forwards them
    # to this side_effect. Assertion-preserving signature widening
    # only — no behavior change.
    """Taskbook-sanctioned forcing: a §3.2 CAS-loser that rolled
    back but left NO determinable winner ECO."""

    self.session.rollback()
    return BreakageDesignLoopbackEcoCreation(
        incident_id=incident_id,
        preparation=None,
        reference="",
        eco=None,
        created=False,
    )


def test_helpdesk_ticket_update_auto_loopback_unrecoverable_race_maps_409():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _incident_with_job(service)
        incident_id = incident.id

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
            autospec=True,
            side_effect=_unrecoverable_creation,
        ):
            with pytest.raises(BreakageDesignLoopbackLinkRace):
                service.apply_helpdesk_ticket_update(
                    incident_id,
                    provider_ticket_status="resolved",
                    job_id=job.id,
                    auto_loopback=True,
                    loopback_user_id=42,
                )
    finally:
        session.close()

    # Route-level: dedicated exception → retryable 409, NOT 400.
    client, mock_db = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "apply_helpdesk_ticket_update",
        MagicMock(
            side_effect=BreakageDesignLoopbackLinkRace(
                "breakage design loopback link race — cannot determine "
                "the linked ECO for incident brk-x"
            )
        ),
    ):
        response = client.post(
            "/api/v1/breakages/brk-x/helpdesk-sync/ticket-update",
            json={"provider_ticket_status": "resolved", "auto_loopback": True},
        )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "breakage_loopback_link_race"
    assert detail["context"] == {"incident_id": "brk-x"}
    assert mock_db.rollback.called
    assert not mock_db.commit.called


# ==========================================================================
# MANDATORY 8 — ECO permission failure rolls back ALL (status + helpdesk)
# ==========================================================================


def test_helpdesk_ticket_update_auto_loopback_eco_permission_failure_rolls_back_all():
    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 42)
        svc = BreakageIncidentService(setup)
        incident, job = _incident_with_job(svc, description="perm fail")
        incident_id = incident.id
        job_id = job.id
    finally:
        setup.close()

    client = _real_db_client(engine)
    with patch.object(
        router_module.BreakageIncidentService,
        "create_breakage_design_loopback_eco",
        MagicMock(
            side_effect=PLMPermissionError(action="create", resource="ECO")
        ),
    ):
        # The real apply_helpdesk_ticket_update runs (status-only
        # flush + the shared helper); only the ECO-create boundary
        # denies. No app-wide PLMException handler → it propagates
        # (RAISES — not collapsed into a 400 JSON response).
        with pytest.raises(PLMPermissionError):
            client.post(
                f"/api/v1/breakages/{incident_id}/helpdesk-sync/ticket-update",
                json={
                    "provider_ticket_status": "resolved",
                    "provider_assignee": "alice",
                    "job_id": job_id,
                    "auto_loopback": True,
                },
            )

    probe = _session(engine)
    try:
        after = probe.get(BreakageIncident, incident_id)
        assert after.status == "open"  # status-only flush rolled back
        assert after.responsibility is None  # heavy mutations never ran
        assert after.eco_id is None
        assert probe.query(ECO).count() == 0
        probed_job = probe.get(ConversionJob, job_id)
        # This call's heavy helpdesk mutation (which would stamp
        # provider_ticket_status='resolved' / sync_status='completed'
        # into the envelope) was rolled back — only the enqueue-time
        # payload survives.
        helpdesk_sync = (probed_job.payload or {}).get("helpdesk_sync") or {}
        assert helpdesk_sync.get("provider_ticket_status") != "resolved"
        assert helpdesk_sync.get("sync_status") != "completed"
    finally:
        probe.close()


# ==========================================================================
# Route-level — §5: auto_loopback default False & forwarded; verbatim ECO err
# ==========================================================================


def test_route_helpdesk_ticket_update_auto_loopback_defaults_false_and_forwards():
    client, _ = _mock_db_client(_current_user(user_id=99))
    spy = MagicMock(return_value={"incident_id": "brk-1"})
    with patch.object(
        router_module.BreakageIncidentService,
        "apply_helpdesk_ticket_update",
        spy,
    ):
        client.post(
            "/api/v1/breakages/brk-1/helpdesk-sync/ticket-update",
            json={"provider_ticket_status": "resolved"},
        )
        assert spy.call_args.kwargs["auto_loopback"] is False
        assert spy.call_args.kwargs["loopback_user_id"] == 99

        client.post(
            "/api/v1/breakages/brk-1/helpdesk-sync/ticket-update",
            json={"provider_ticket_status": "resolved", "auto_loopback": True},
        )
        assert spy.call_args.kwargs["auto_loopback"] is True
        assert spy.call_args.kwargs["loopback_user_id"] == 99


def test_route_helpdesk_ticket_update_eco_permission_propagates_verbatim_not_400():
    # Carrier 1 — HTTPException(403): surfaces verbatim.
    client, db = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "apply_helpdesk_ticket_update",
        MagicMock(
            side_effect=HTTPException(
                status_code=403,
                detail={"code": "eco_create_denied", "message": "Forbidden"},
            )
        ),
    ):
        response = client.post(
            "/api/v1/breakages/brk-1/helpdesk-sync/ticket-update",
            json={"provider_ticket_status": "resolved", "auto_loopback": True},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "eco_create_denied"
    assert response.json()["detail"]["code"] != "breakage_helpdesk_sync_invalid"
    assert db.rollback.called
    assert not db.commit.called

    # Carrier 2 — real PermissionError (PLMException): re-raised
    # (a 400 collapse would return a JSON response, not raise).
    client2, db2 = _mock_db_client()
    with patch.object(
        router_module.BreakageIncidentService,
        "apply_helpdesk_ticket_update",
        MagicMock(
            side_effect=PLMPermissionError(action="create", resource="ECO")
        ),
    ):
        with pytest.raises(PLMPermissionError):
            client2.post(
                "/api/v1/breakages/brk-1/helpdesk-sync/ticket-update",
                json={"provider_ticket_status": "resolved", "auto_loopback": True},
            )
    assert db2.rollback.called
    assert not db2.commit.called
