from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.lifecycle.models import LifecycleMap, LifecycleState
from yuantus.meta_engine.models.eco import ECO, ECOStage
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.models.parallel_tasks import BreakageIncident
from yuantus.meta_engine.permission.models import Permission
from yuantus.meta_engine.services import parallel_tasks_service as service_module
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageDesignLoopbackEcoCreation,
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


def _session():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine, tables=_tables())
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()


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


def test_explicit_creation_builds_eco_from_eligible_resolved_incident():
    session = _session()
    try:
        _add_user(session, 42)
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="critical design defect under load",
            status="resolved",
            severity="critical",
        )
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            result = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=42,
            )

        assert isinstance(result, BreakageDesignLoopbackEcoCreation)
        assert result.created is True
        assert result.incident_id == incident.id
        assert result.preparation.eligible is True
        assert result.eco.name.startswith("Design loopback:")
        assert result.eco.eco_type == "product"
        assert result.eco.priority == "urgent"
        assert result.eco.created_by_id == 42
        assert result.eco.description is not None
        assert "breakage-eco-closeout" in result.eco.description
        assert "ecr-intake" in result.eco.description
        assert f"reference={result.reference}" in result.eco.description
    finally:
        session.close()


def test_ineligible_incident_raises_before_eco_creation():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="open issue still under triage",
            status="open",
            severity="critical",
        )
        session.commit()

        with patch.object(service_module.ECOService, "create_eco") as create_spy:
            with pytest.raises(ValueError, match="not eligible for design loopback"):
                service.create_breakage_design_loopback_eco(
                    incident.id,
                    user_id=7,
                )

        create_spy.assert_not_called()
        assert session.query(ECO).count() == 0
    finally:
        session.close()


def test_default_idempotency_returns_existing_eco_by_breakage_reference():
    session = _session()
    try:
        _add_user(session, 7)
        _add_user(session, 8)
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="repeatable resolved design issue",
            status="resolved",
            severity="high",
            product_item_id="product-1",
        )
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=7,
            )
            second = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=8,
            )

        assert first.created is True
        assert second.created is False
        assert second.eco.id == first.eco.id
        assert second.reference == first.reference
        assert session.query(ECO).count() == 1
        assert session.get(ECO, first.eco.id).created_by_id == 7
    finally:
        session.close()


def test_allow_duplicate_bypasses_reference_dedupe():
    session = _session()
    try:
        _add_user(session, 10)
        _add_user(session, 11)
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="operator explicitly requests duplicate ECO",
            status="closed",
            severity="medium",
        )
        session.commit()

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            first = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=10,
            )
            second = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=11,
                allow_duplicate=True,
            )

        assert first.created is True
        assert second.created is True
        assert second.eco.id != first.eco.id
        assert second.reference == first.reference
        assert session.query(ECO).count() == 2
    finally:
        session.close()


def test_missing_incident_preserves_existing_error_shape():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        with pytest.raises(ValueError, match="Breakage incident not found: missing"):
            service.create_breakage_design_loopback_eco("missing", user_id=1)
    finally:
        session.close()


def test_explicit_creation_does_not_mutate_breakage_incident_status():
    session = _session()
    try:
        _add_user(session, 12)
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="creation leaves source incident unchanged",
            status="resolved",
            severity="low",
        )
        session.commit()
        before = (incident.status, incident.updated_at, incident.description)

        with patch("yuantus.meta_engine.services.eco_service.enqueue_event"):
            result = service.create_breakage_design_loopback_eco(
                incident.id,
                user_id=12,
            )

        after = (incident.status, incident.updated_at, incident.description)
        assert result.created is True
        assert after == before
        assert session.is_modified(incident) is False
    finally:
        session.close()


def test_creation_permission_error_propagates_without_status_mutation():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="permission failure should stay caller-owned",
            status="resolved",
            severity="critical",
        )
        session.commit()
        before = (incident.status, incident.updated_at, incident.description)

        with patch.object(
            service_module.ECOService,
            "create_eco",
            side_effect=PermissionError("no permission"),
        ):
            with pytest.raises(PermissionError, match="no permission"):
                service.create_breakage_design_loopback_eco(
                    incident.id,
                    user_id=99,
                )

        assert session.query(ECO).count() == 0
        assert (incident.status, incident.updated_at, incident.description) == before
    finally:
        session.close()
