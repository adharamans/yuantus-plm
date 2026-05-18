from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.models.parallel_tasks import BreakageIncident
from yuantus.meta_engine.services import parallel_tasks_service as service_module
from yuantus.meta_engine.services.ecr_intake_contract import EcoDraftInputs
from yuantus.meta_engine.services.parallel_tasks_service import (
    BreakageDesignLoopbackPreparation,
    BreakageIncidentService,
)
from yuantus.models.base import Base
from yuantus.security.rbac.models import RBACUser


def _session():
    import_all_models()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            RBACUser.__table__,
            BreakageIncident.__table__,
        ],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    return SessionLocal()


def test_resolved_incident_prepares_descriptor_intake_and_draft_inputs():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="design defect under load",
            status="resolved",
            severity="critical",
            product_item_id="product-1",
            bom_id="bom-1",
            version_id="version-1",
        )
        session.commit()

        with patch.object(
            service_module,
            "resolve_breakage_eco_closure_descriptor",
            wraps=service_module.resolve_breakage_eco_closure_descriptor,
        ) as resolver_spy, patch.object(
            service_module,
            "is_breakage_eligible_for_design_loopback",
            wraps=service_module.is_breakage_eligible_for_design_loopback,
        ) as eligibility_spy, patch.object(
            service_module,
            "map_breakage_to_change_request_intake",
            wraps=service_module.map_breakage_to_change_request_intake,
        ) as intake_spy:
            prepared = service.prepare_breakage_design_loopback_intake(incident.id)

        assert isinstance(prepared, BreakageDesignLoopbackPreparation)
        assert prepared.incident_id == incident.id
        assert prepared.eligible is True
        assert prepared.ineligible_reason is None
        assert prepared.descriptor.status == "resolved"
        assert prepared.descriptor.incident_code == incident.incident_code
        assert prepared.intake is not None
        assert prepared.intake.change_type == "bom"
        assert prepared.intake.priority == "urgent"
        assert isinstance(prepared.eco_draft_inputs, EcoDraftInputs)
        assert prepared.eco_draft_inputs.as_kwargs()["eco_type"] == "bom"
        assert resolver_spy.called
        assert eligibility_spy.called
        assert intake_spy.called
    finally:
        session.close()


def test_closed_incident_is_eligible_for_design_loopback():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="closed confirmed design issue",
            status="closed",
            severity="high",
            product_item_id="product-2",
        )
        session.commit()

        prepared = service.prepare_breakage_design_loopback_intake(incident.id)

        assert prepared.eligible is True
        assert prepared.intake is not None
        assert prepared.intake.priority == "high"
        assert prepared.eco_draft_inputs is not None
        assert prepared.eco_draft_inputs.name.startswith("Design loopback:")
    finally:
        session.close()


@pytest.mark.parametrize("status", ["open", "in_progress"])
def test_ineligible_status_produces_descriptor_without_intake(status: str):
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description=f"{status} issue still under triage",
            status=status,
            severity="medium",
        )
        session.commit()

        with patch.object(
            service_module,
            "map_breakage_to_change_request_intake",
            wraps=service_module.map_breakage_to_change_request_intake,
        ) as intake_spy:
            prepared = service.prepare_breakage_design_loopback_intake(incident.id)

        assert prepared.eligible is False
        assert prepared.descriptor.status == status
        assert prepared.intake is None
        assert prepared.eco_draft_inputs is None
        assert prepared.ineligible_reason == (
            f"breakage status {status!r} is not eligible for design loopback"
        )
        assert not intake_spy.called
    finally:
        session.close()


def test_missing_incident_raises_existing_not_found_shape():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        with pytest.raises(ValueError, match="Breakage incident not found: missing"):
            service.prepare_breakage_design_loopback_intake("missing")
    finally:
        session.close()


def test_dirty_severity_maps_to_normal_through_contract():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="resolved issue with dirty severity",
            status="resolved",
            severity="p0",
        )
        session.commit()

        prepared = service.prepare_breakage_design_loopback_intake(incident.id)

        assert prepared.eligible is True
        assert prepared.descriptor.severity == "p0"
        assert prepared.intake is not None
        assert prepared.intake.priority == "normal"
    finally:
        session.close()


def test_bom_without_product_stays_valid_product_type_intake():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="bom-linked issue missing product",
            status="resolved",
            severity="critical",
            bom_id="bom-without-product",
            product_item_id=None,
        )
        session.commit()

        prepared = service.prepare_breakage_design_loopback_intake(incident.id)

        assert prepared.eligible is True
        assert prepared.intake is not None
        assert prepared.intake.change_type == "product"
        assert prepared.intake.product_id is None
        assert prepared.eco_draft_inputs is not None
        assert prepared.eco_draft_inputs.eco_type == "product"
    finally:
        session.close()


def test_preparation_does_not_mutate_incident():
    session = _session()
    try:
        service = BreakageIncidentService(session)
        incident = service.create_incident(
            description="read-only preparation",
            status="resolved",
            severity="high",
            product_item_id="product-3",
        )
        session.commit()
        before = (
            incident.status,
            incident.updated_at,
            incident.description,
            incident.product_item_id,
        )

        service.prepare_breakage_design_loopback_intake(incident.id)

        after = (
            incident.status,
            incident.updated_at,
            incident.description,
            incident.product_item_id,
        )
        assert after == before
        assert session.is_modified(incident) is False
    finally:
        session.close()


def test_runtime_wiring_does_not_import_or_call_eco_creation():
    tree = ast.parse(inspect.getsource(service_module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)
    assert "yuantus.meta_engine.services.eco_service" not in joined
    assert "ECOService" not in joined

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "create_eco" not in called
