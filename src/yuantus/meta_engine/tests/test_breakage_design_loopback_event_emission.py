"""Tier-B #3 §3.6 design-loopback event emission — R1 tests.

Taskbook: `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DESIGN_LOOPBACK_EVENT_EMISSION_20260519.md`
(merged #609 `61ce226`; §3.E ratified S2 settings flag).

`event_bus` is a process-global singleton, so every test
subscribes its capture handler via the
`captured_loopback_events` fixture, which **removes the handler
on teardown** — mandatory or the suite is flaky.

Pinned:
- §3.E S2: default-OFF (flag) → zero events; flag ON → emit.
- §3.B/§3.C: emit at the CAS-winner + dedupe-reuse branches
  only; CAS-loser / unrecoverable / allow_duplicate emit ZERO;
  exactly one event per incident-link, no double-emit (reusing
  the existing transactional-outbox rollback-drop).
- §3.F: `trigger_source` ("route"/"update_status"/"helpdesk_sync")
  and the helpdesk `sync_status` / `provider_ticket_status`
  context threaded to the emit point inside `create_…eco`.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from yuantus.config import get_settings
from yuantus.exceptions.handlers import PermissionError as PLMPermissionError
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.events.domain_events import BreakageDesignLoopbackEcoEvent
from yuantus.meta_engine.events.event_bus import event_bus
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


def _eligible_incident(service, *, status="resolved", description="loopback event"):
    incident = service.create_incident(
        description=description, status=status, severity="high"
    )
    service.session.commit()
    return incident


def _open_incident_with_job(service, *, description="helpdesk event"):
    incident = service.create_incident(
        description=description, status="open", severity="high"
    )
    service.session.commit()
    job = service.enqueue_helpdesk_stub_sync(incident.id, user_id=42, provider="stub")
    service.session.commit()
    return incident, job


@pytest.fixture
def captured_loopback_events():
    """Subscribe a capture handler for the duration of the test,
    then remove it on teardown (event_bus is a global singleton —
    a leaked handler would fire stale on every later test)."""

    events: list[BreakageDesignLoopbackEcoEvent] = []

    def _handler(evt):
        events.append(evt)

    event_bus.subscribe(BreakageDesignLoopbackEcoEvent, _handler)
    try:
        yield events
    finally:
        subs = event_bus._subscribers.get(BreakageDesignLoopbackEcoEvent, [])
        if _handler in subs:
            subs.remove(_handler)


def _enable_events(monkeypatch) -> None:
    """§3.E S2: flip the real settings flag (all other settings
    stay real; monkeypatch restores on teardown)."""

    monkeypatch.setattr(
        get_settings(), "BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED", True
    )


# ==========================================================================
# MANDATORY 1 — CAS winner emits one event, created=True
# ==========================================================================


def test_cas_winner_emits_one_event_with_created_true(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service)

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            result = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
        session.commit()  # after_commit publishes

        assert result.created is True
        assert len(captured_loopback_events) == 1
        evt = captured_loopback_events[0]
        assert evt.incident_id == incident.id
        assert evt.eco_id == result.eco.id
        assert evt.created is True
        assert evt.trigger_source == "route"
        assert evt.incident_status == "resolved"
        assert evt.sync_status is None
        assert evt.provider_ticket_status is None
        assert evt.actor_id == 42
    finally:
        session.close()


# ==========================================================================
# MANDATORY 2 — durable-dedupe reuse emits one event, created=False
# ==========================================================================


def test_dedupe_reuse_emits_one_event_with_created_false(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service)

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
            session.commit()
            second = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
            session.commit()

        assert first.created is True
        assert second.created is False
        assert second.eco.id == first.eco.id
        assert len(captured_loopback_events) == 2
        assert captured_loopback_events[0].created is True
        reuse = captured_loopback_events[1]
        assert reuse.created is False
        assert reuse.eco_id == first.eco.id
        assert session.query(ECO).count() == 1
    finally:
        session.close()


# ==========================================================================
# MANDATORY 3 — centerpiece: CAS-loser emits zero, winner emits one
# ==========================================================================


def test_cas_loser_race_emits_zero_events_winner_emits_one(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    engine = _engine()
    setup = _session(engine)
    try:
        _add_user(setup, 7)
        incident = _eligible_incident(
            BreakageIncidentService(setup), description="cas race event"
        )
        incident_id = incident.id

        # Winner: create + link + commit → publishes one event.
        winner_sess = _session(engine)
        try:
            with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                winner = BreakageIncidentService(
                    winner_sess
                ).create_breakage_design_loopback_eco(incident_id, user_id=7)
            winner_sess.commit()
            assert winner.created is True
        finally:
            winner_sess.close()

        # Loser: forced past dedupe → create_eco + CAS-loss →
        # §3.2 self.session.rollback() drops its queued events.
        loser_sess = _session(engine)
        try:
            with patch.object(
                BreakageIncidentService,
                "_find_breakage_design_loopback_eco_by_reference",
                return_value=None,
            ), patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
                loser = BreakageIncidentService(
                    loser_sess
                ).create_breakage_design_loopback_eco(incident_id, user_id=7)
            loser_sess.commit()
            assert loser.created is False
        finally:
            loser_sess.close()

        # Exactly one event — the winner's created=True. The loser
        # enqueued nothing (§3.B) and its rollback would have
        # dropped it anyway.
        assert len(captured_loopback_events) == 1
        assert captured_loopback_events[0].created is True
        assert captured_loopback_events[0].eco_id == winner.eco.id
    finally:
        setup.close()


# ==========================================================================
# MANDATORY 4 — unrecoverable arm emits zero events
# ==========================================================================


def _unrecoverable_creation(self, incident_id, *, user_id, allow_duplicate=False, **_kw):
    self.session.rollback()
    return BreakageDesignLoopbackEcoCreation(
        incident_id=incident_id,
        preparation=None,
        reference="",
        eco=None,
        created=False,
    )


def test_unrecoverable_race_emits_zero_events(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="unrecoverable", status="open", severity="high"
        )
        session.commit()

        with patch.object(
            BreakageIncidentService,
            "create_breakage_design_loopback_eco",
            autospec=True,
            side_effect=_unrecoverable_creation,
        ):
            with pytest.raises(BreakageDesignLoopbackLinkRace):
                service.update_status(
                    incident.id,
                    status="resolved",
                    auto_loopback=True,
                    loopback_user_id=42,
                )
        assert captured_loopback_events == []
    finally:
        session.close()


# ==========================================================================
# MANDATORY 5 — ECO permission failure emits zero events
# ==========================================================================


def test_eco_permission_failure_emits_zero_events(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="perm fail event")

        with patch(
            "yuantus.meta_engine.services.eco_service.ECOService.create_eco",
            side_effect=PLMPermissionError(action="create", resource="ECO"),
        ):
            with pytest.raises(PLMPermissionError):
                service.create_breakage_design_loopback_eco(
                    incident.id, user_id=42
                )
        # create_eco raised before either enqueue branch; nothing
        # committed.
        assert captured_loopback_events == []
    finally:
        session.close()


# ==========================================================================
# MANDATORY 6 — idempotent replay emits zero events
# ==========================================================================


def test_idempotent_replay_emits_zero_events(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident, job = _open_incident_with_job(service)

        # First call records event 'e1' (auto_loopback OFF).
        service.apply_helpdesk_ticket_update(
            incident.id,
            provider_ticket_status="resolved",
            job_id=job.id,
            event_id="e1",
        )
        session.commit()

        # Replayed 'e1' with auto_loopback ON → short-circuit
        # returns before create_…eco → zero events.
        replay = service.apply_helpdesk_ticket_update(
            incident.id,
            provider_ticket_status="resolved",
            job_id=job.id,
            event_id="e1",
            auto_loopback=True,
            loopback_user_id=42,
        )
        session.commit()

        assert replay["idempotent_replay"] is True
        assert captured_loopback_events == []
    finally:
        session.close()


# ==========================================================================
# MANDATORY 7 — default-OFF emits zero events (§3.E S2)
# ==========================================================================


def test_default_off_emits_zero_events(captured_loopback_events):
    # NOTE: _enable_events NOT called → real default flag (False).
    assert (
        get_settings().BREAKAGE_DESIGN_LOOPBACK_EVENTS_ENABLED is False
    ), "the S2 flag must default OFF"
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="default off")

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            result = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
        session.commit()

        assert result.created is True  # loopback still works
        assert captured_loopback_events == []  # but no event emitted
    finally:
        session.close()


# ==========================================================================
# MANDATORY 8 — trigger_source threaded for all three sources
# ==========================================================================


def test_trigger_source_threaded_route_update_status_helpdesk(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    engine = _engine()
    session = _session(engine)
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)

        # route — direct create_…eco, default trigger_source.
        route_inc = _eligible_incident(service, description="route src")
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.create_breakage_design_loopback_eco(route_inc.id, user_id=42)
        session.commit()

        # update_status — auto_loopback path.
        us_inc = service.create_incident(
            description="update_status src", status="open", severity="high"
        )
        session.commit()
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.update_status(
                us_inc.id,
                status="resolved",
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        # helpdesk_sync — apply_helpdesk_ticket_update path.
        hd_inc, hd_job = _open_incident_with_job(service, description="hd src")
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.apply_helpdesk_ticket_update(
                hd_inc.id,
                provider_ticket_status="resolved",
                job_id=hd_job.id,
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        by_incident = {e.incident_id: e for e in captured_loopback_events}
        assert by_incident[route_inc.id].trigger_source == "route"
        assert by_incident[us_inc.id].trigger_source == "update_status"
        assert by_incident[hd_inc.id].trigger_source == "helpdesk_sync"
    finally:
        session.close()


# ==========================================================================
# MANDATORY 9 — helpdesk-source event carries sync context (Medium finding)
# ==========================================================================


def test_helpdesk_source_event_carries_sync_context(
    monkeypatch, captured_loopback_events
):
    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)

        hd_inc, hd_job = _open_incident_with_job(service, description="hd ctx")
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.apply_helpdesk_ticket_update(
                hd_inc.id,
                provider_ticket_status="resolved",
                job_id=hd_job.id,
                auto_loopback=True,
                loopback_user_id=42,
            )
        session.commit()

        # provider 'resolved' → derived sync 'completed',
        # normalized provider ticket 'resolved'.
        hd_evt = next(
            e for e in captured_loopback_events if e.incident_id == hd_inc.id
        )
        assert hd_evt.trigger_source == "helpdesk_sync"
        assert hd_evt.sync_status == "completed"
        assert hd_evt.provider_ticket_status == "resolved"

        # A route-source event leaves both None.
        route_inc = _eligible_incident(service, description="route ctx")
        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            service.create_breakage_design_loopback_eco(route_inc.id, user_id=42)
        session.commit()
        route_evt = next(
            e for e in captured_loopback_events if e.incident_id == route_inc.id
        )
        assert route_evt.sync_status is None
        assert route_evt.provider_ticket_status is None
    finally:
        session.close()


# ==========================================================================
# Defensive (advisor) — allow_duplicate=True emits zero events
# ==========================================================================


def test_allow_duplicate_true_emits_zero_events(
    monkeypatch, captured_loopback_events
):
    """§3.B lists exactly two enqueue branches (CAS-winner,
    dedupe-reuse) and explicitly NOT the CAS-loser; the
    `allow_duplicate=True` branch is outside the durable
    loopback-link lifecycle (no CAS, no `eco_id` link) and is
    intentionally not an enqueue site. Pins that reading.
    """

    _enable_events(monkeypatch)
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = _eligible_incident(service, description="dup event")

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42
            )
            session.commit()
            dup = service.create_breakage_design_loopback_eco(
                incident.id, user_id=42, allow_duplicate=True
            )
            session.commit()

        assert first.created is True
        assert dup.created is True
        assert dup.eco.id != first.eco.id
        # Exactly one event — the first (CAS winner). The explicit
        # duplicate emits nothing.
        assert len(captured_loopback_events) == 1
        assert captured_loopback_events[0].eco_id == first.eco.id
    finally:
        session.close()
