"""Tests for the maintenance DB-resolver pure contract (R1)."""

from __future__ import annotations

import ast
import inspect
from typing import Optional

import pytest
from pydantic import ValidationError

from yuantus.meta_engine.maintenance.models import (
    Equipment,
    EquipmentStatus,
    MaintenanceRequest,
    MaintenanceRequestState,
)
from yuantus.meta_engine.services import (
    maintenance_db_resolver_contract as mod,
)
from yuantus.meta_engine.services import (
    maintenance_workorder_bridge_contract as bridge_mod,
)
from yuantus.meta_engine.services.maintenance_db_resolver_contract import (
    EquipmentRow,
    MaintenanceRequestRow,
    resolve_workcenter_maintenance_descriptor,
    resolve_workcenter_maintenance_descriptors,
)
from yuantus.meta_engine.services.maintenance_workorder_bridge_contract import (
    WorkcenterMaintenanceDescriptor,
    evaluate_workcenter_readiness,
)


def _equipment(
    equipment_id: str = "eq-1",
    *,
    workcenter_id: Optional[str] = "wc-1",
    status: str = EquipmentStatus.OPERATIONAL.value,
) -> EquipmentRow:
    return EquipmentRow(
        id=equipment_id,
        status=status,
        workcenter_id=workcenter_id,
    )


def _request(
    state: str,
    *,
    request_id: str = "req-1",
    equipment_id: str = "eq-1",
) -> MaintenanceRequestRow:
    return MaintenanceRequestRow(
        id=request_id,
        equipment_id=equipment_id,
        state=state,
    )


# --------------------------------------------------------------------------
# Row DTOs — frozen, extra=forbid, non-empty validation
# --------------------------------------------------------------------------


def test_equipment_row_frozen_extra_forbid_and_validates_status():
    row = _equipment()
    assert row.id == "eq-1"
    assert row.workcenter_id == "wc-1"
    with pytest.raises(ValidationError):
        row.id = "other"
    with pytest.raises(ValidationError):
        EquipmentRow(id="eq-1", status="operational", unknown="x")
    with pytest.raises(ValidationError):
        EquipmentRow(id="   ", status="operational")
    with pytest.raises(ValidationError):
        EquipmentRow(id="eq-1", status="exploded")


def test_request_row_frozen_extra_forbid_and_validates_state():
    row = _request(MaintenanceRequestState.DRAFT.value)
    assert row.equipment_id == "eq-1"
    with pytest.raises(ValidationError):
        row.state = MaintenanceRequestState.DONE.value
    with pytest.raises(ValidationError):
        MaintenanceRequestRow(
            id="req-1",
            equipment_id="eq-1",
            state=MaintenanceRequestState.DRAFT.value,
            unknown="x",
        )
    with pytest.raises(ValidationError):
        MaintenanceRequestRow(id="", equipment_id="eq-1", state="draft")
    with pytest.raises(ValidationError):
        MaintenanceRequestRow(id="req-1", equipment_id=" ", state="draft")
    with pytest.raises(ValidationError):
        MaintenanceRequestRow(
            id="req-1", equipment_id="eq-1", state="waiting"
        )


def test_missing_workcenter_id_is_rejected_by_merged_descriptor():
    with pytest.raises(ValidationError):
        resolve_workcenter_maintenance_descriptor(
            _equipment(workcenter_id=None),
            [],
        )
    with pytest.raises(ValidationError):
        resolve_workcenter_maintenance_descriptor(
            _equipment(workcenter_id="   "),
            [],
        )


# --------------------------------------------------------------------------
# MANDATORY — active request selection, ratified A1
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "active_state",
    [
        MaintenanceRequestState.DRAFT.value,
        MaintenanceRequestState.SUBMITTED.value,
        MaintenanceRequestState.IN_PROGRESS.value,
    ],
)
def test_resolver_picks_first_active_request_state_in_input_order(
    active_state: str,
):
    descriptor = resolve_workcenter_maintenance_descriptor(
        _equipment(),
        [
            _request(MaintenanceRequestState.DONE.value, request_id="r-done"),
            _request(active_state, request_id="r-active"),
            _request(
                MaintenanceRequestState.IN_PROGRESS.value,
                request_id="r-later-active",
            ),
        ],
    )

    assert descriptor.active_request_state == active_state

    terminal_only = resolve_workcenter_maintenance_descriptor(
        _equipment(),
        [
            _request(
                MaintenanceRequestState.CANCELLED.value,
                request_id="r-cancelled",
            ),
            _request(MaintenanceRequestState.DONE.value, request_id="r-done"),
        ],
    )
    assert terminal_only.active_request_state is None

    empty = resolve_workcenter_maintenance_descriptor(_equipment(), [])
    assert empty.active_request_state is None


def test_resolver_active_set_pins_ratified_policy_a():
    expected = frozenset(
        {
            MaintenanceRequestState.DRAFT.value,
            MaintenanceRequestState.SUBMITTED.value,
            MaintenanceRequestState.IN_PROGRESS.value,
        }
    )
    non_terminal = frozenset(
        state.value for state in MaintenanceRequestState
    ) - frozenset(
        {
            MaintenanceRequestState.DONE.value,
            MaintenanceRequestState.CANCELLED.value,
        }
    )

    assert mod._ACTIVE_REQUEST_STATES == expected
    assert mod._ACTIVE_REQUEST_STATES == non_terminal
    assert mod._ACTIVE_REQUEST_STATES != bridge_mod._BLOCKING_REQUEST_STATES
    assert (
        MaintenanceRequestState.DRAFT.value
        not in bridge_mod._BLOCKING_REQUEST_STATES
    )


def test_resolver_pins_draft_as_active_and_non_blocking():
    descriptor = resolve_workcenter_maintenance_descriptor(
        _equipment(),
        [_request(MaintenanceRequestState.DRAFT.value)],
    )

    assert descriptor.active_request_state == MaintenanceRequestState.DRAFT.value
    report = evaluate_workcenter_readiness([descriptor])[0]
    assert report.ready is True
    assert report.blocked == []
    assert report.degraded == []


# --------------------------------------------------------------------------
# MANDATORY — input-shape mismatch
# --------------------------------------------------------------------------


def test_resolver_rejects_mismatched_equipment_request_pair():
    with pytest.raises(ValueError) as exc:
        resolve_workcenter_maintenance_descriptor(
            _equipment(),
            [
                _request(
                    MaintenanceRequestState.SUBMITTED.value,
                    equipment_id="other-eq",
                )
            ],
        )
    assert "request_row.equipment_id" in str(exc.value)
    assert "other-eq" in str(exc.value)
    assert "eq-1" in str(exc.value)

    # Strict reading: reject a mismatched row even if it would be
    # terminal and filtered out by active-state selection.
    with pytest.raises(ValueError):
        resolve_workcenter_maintenance_descriptor(
            _equipment(),
            [
                _request(MaintenanceRequestState.DRAFT.value),
                _request(
                    MaintenanceRequestState.DONE.value,
                    request_id="r-mismatch-terminal",
                    equipment_id="other-eq",
                ),
            ],
        )


# --------------------------------------------------------------------------
# MANDATORY — descriptor reuse / compose proof
# --------------------------------------------------------------------------


def test_resolver_output_is_the_merged_workcenter_descriptor():
    descriptors = resolve_workcenter_maintenance_descriptors(
        [
            (
                _equipment("eq-ready", workcenter_id="wc-a"),
                [],
            ),
            (
                _equipment(
                    "eq-degraded",
                    workcenter_id="wc-a",
                    status=EquipmentStatus.IN_MAINTENANCE.value,
                ),
                [
                    _request(
                        MaintenanceRequestState.DRAFT.value,
                        request_id="r-draft",
                        equipment_id="eq-degraded",
                    )
                ],
            ),
            (
                _equipment("eq-blocked", workcenter_id="wc-b"),
                [
                    _request(
                        MaintenanceRequestState.SUBMITTED.value,
                        request_id="r-submitted",
                        equipment_id="eq-blocked",
                    )
                ],
            ),
        ]
    )

    assert all(
        isinstance(descriptor, WorkcenterMaintenanceDescriptor)
        for descriptor in descriptors
    )
    for descriptor in descriptors:
        assert set(descriptor.model_dump()) == set(
            WorkcenterMaintenanceDescriptor.model_fields
        )

    reports = evaluate_workcenter_readiness(descriptors)
    by_wc = {report.workcenter_id: report for report in reports}
    assert by_wc["wc-a"].ready is True
    assert by_wc["wc-a"].degraded == ["eq-degraded"]
    assert by_wc["wc-b"].ready is False
    assert by_wc["wc-b"].blocked == ["eq-blocked"]


# --------------------------------------------------------------------------
# Batch — order preserved, mismatch propagates
# --------------------------------------------------------------------------


def test_batch_preserves_equipment_input_order():
    descriptors = resolve_workcenter_maintenance_descriptors(
        [
            (_equipment("eq-1", workcenter_id="wc-2"), []),
            (_equipment("eq-2", workcenter_id="wc-1"), []),
        ]
    )
    assert [descriptor.equipment_id for descriptor in descriptors] == [
        "eq-1",
        "eq-2",
    ]


def test_batch_propagates_mismatch_error():
    with pytest.raises(ValueError):
        resolve_workcenter_maintenance_descriptors(
            [
                (
                    _equipment("eq-1"),
                    [
                        _request(
                            MaintenanceRequestState.DONE.value,
                            equipment_id="eq-2",
                        )
                    ],
                )
            ]
        )


# --------------------------------------------------------------------------
# Drift guards — row DTOs subset real columns; descriptor reuse
# --------------------------------------------------------------------------


def test_equipment_row_fields_are_subset_of_real_columns():
    real_cols = {column.name for column in Equipment.__table__.columns}
    assert set(EquipmentRow.model_fields) <= real_cols
    assert {"id", "status", "workcenter_id"} <= set(EquipmentRow.model_fields)
    assert Equipment.__table__.columns["workcenter_id"].nullable is True
    assert Equipment.__table__.columns["status"].nullable is False


def test_request_row_fields_are_subset_of_real_columns():
    real_cols = {column.name for column in MaintenanceRequest.__table__.columns}
    assert set(MaintenanceRequestRow.model_fields) <= real_cols
    assert {"id", "equipment_id", "state"} <= set(
        MaintenanceRequestRow.model_fields
    )
    assert MaintenanceRequest.__table__.columns["state"].nullable is False


def test_resolver_reuses_the_merged_descriptor_type():
    descriptor = resolve_workcenter_maintenance_descriptor(_equipment(), [])
    assert type(descriptor) is WorkcenterMaintenanceDescriptor


# --------------------------------------------------------------------------
# Purity guard (AST)
# --------------------------------------------------------------------------


def test_module_is_pure_by_ast():
    tree = ast.parse(inspect.getsource(mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    joined = " ".join(imported)

    for forbidden in (
        "yuantus.database",
        "sqlalchemy",
        "parallel_tasks_service",
        "_router",
        "plugins",
        "_service",
        "maintenance.service",
    ):
        assert forbidden not in joined, f"impure import: {forbidden!r}"

    assert "yuantus.meta_engine.maintenance.models" in joined
    assert "maintenance_workorder_bridge_contract" in joined

    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "session" not in names and "session" not in attrs


def test_module_has_no_evaluate_or_assert_calls():
    tree = ast.parse(inspect.getsource(mod))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "evaluate_workcenter_readiness" not in called
    assert "assert_workcenter_ready" not in called


def test_module_has_no_assert_callable():
    tree = ast.parse(inspect.getsource(mod))
    assert not [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("assert_")
    ]
