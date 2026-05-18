"""Maintenance DB-resolver pure contract (R1, pure, contract-only).

Maps caller-supplied persisted-row views to the merged
``maintenance_workorder_bridge_contract.WorkcenterMaintenanceDescriptor``.
This module does not read the database: the caller fetches
``Equipment`` / ``MaintenanceRequest`` rows, orders request rows, and
passes the typed row views in.

RATIFIED policy from the taskbook:

- active request state means the first non-terminal state in caller
  input order: ``draft``, ``submitted``, or ``in_progress``;
- terminal ``done`` / ``cancelled`` requests collapse to ``None``;
- any request row whose ``equipment_id`` does not match the equipment
  row is a caller bug and raises ``ValueError`` even if that row would
  otherwise be filtered out;
- ``Equipment.workcenter_id`` remains a soft nullable link. The merged
  descriptor validator rejects missing/blank workcenter ids; this
  module does not harden the schema.

Hard boundary: no DB read, no ``session``, no router/service/plugin
wiring, no readiness enforcement. The resolver only produces merged
descriptors.

See
``docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_MAINTENANCE_DB_RESOLVER_CONTRACT_20260517.md``.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

from yuantus.meta_engine.maintenance.models import (
    EquipmentStatus,
    MaintenanceRequestState,
)
from yuantus.meta_engine.services.maintenance_workorder_bridge_contract import (
    WorkcenterMaintenanceDescriptor,
)

_EQUIPMENT_STATUS_VALUES = frozenset(s.value for s in EquipmentStatus)
_REQUEST_STATE_VALUES = frozenset(s.value for s in MaintenanceRequestState)
_TERMINAL_REQUEST_STATES = frozenset(
    {
        MaintenanceRequestState.DONE.value,
        MaintenanceRequestState.CANCELLED.value,
    }
)
_ACTIVE_REQUEST_STATES = frozenset(
    s.value for s in MaintenanceRequestState
) - _TERMINAL_REQUEST_STATES


class EquipmentRow(BaseModel):
    """Caller-supplied subset of ``meta_maintenance_equipment``.

    Field names mirror real column names so test-side drift guards can
    assert the field-set is a strict subset of ``Equipment`` columns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    status: str
    workcenter_id: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("id must be a non-empty string")
        return cleaned

    @field_validator("status")
    @classmethod
    def _known_status(cls, value: str) -> str:
        if value not in _EQUIPMENT_STATUS_VALUES:
            raise ValueError(
                f"status must be one of {sorted(_EQUIPMENT_STATUS_VALUES)}"
            )
        return value


class MaintenanceRequestRow(BaseModel):
    """Caller-supplied subset of ``meta_maintenance_requests``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    equipment_id: str
    state: str

    @field_validator("id", "equipment_id")
    @classmethod
    def _non_empty_id_fields(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("must be a non-empty string")
        return cleaned

    @field_validator("state")
    @classmethod
    def _known_state(cls, value: str) -> str:
        if value not in _REQUEST_STATE_VALUES:
            raise ValueError(
                f"state must be one of {sorted(_REQUEST_STATE_VALUES)}"
            )
        return value


def resolve_workcenter_maintenance_descriptor(
    equipment_row: EquipmentRow,
    request_rows: Sequence[MaintenanceRequestRow] = (),
) -> WorkcenterMaintenanceDescriptor:
    """Map one equipment row and its already-ordered request rows.

    Request row order is caller-owned; the first non-terminal request
    state in that order becomes ``active_request_state``. Terminal rows
    never surface as active.
    """

    for request_row in request_rows:
        if request_row.equipment_id != equipment_row.id:
            raise ValueError(
                "request_row.equipment_id does not match equipment_row.id: "
                f"request_row.id={request_row.id!r}, "
                f"request_row.equipment_id={request_row.equipment_id!r}, "
                f"equipment_row.id={equipment_row.id!r}"
            )

    active_request_state: Optional[str] = None
    for request_row in request_rows:
        if request_row.state in _ACTIVE_REQUEST_STATES:
            active_request_state = request_row.state
            break

    return WorkcenterMaintenanceDescriptor(
        workcenter_id=equipment_row.workcenter_id,
        equipment_id=equipment_row.id,
        equipment_status=equipment_row.status,
        active_request_state=active_request_state,
    )


def resolve_workcenter_maintenance_descriptors(
    pairs: Sequence[
        Tuple[EquipmentRow, Sequence[MaintenanceRequestRow]]
    ],
) -> Tuple[WorkcenterMaintenanceDescriptor, ...]:
    """Batch-map equipment/request-row pairs, preserving input order."""

    return tuple(
        resolve_workcenter_maintenance_descriptor(equipment_row, request_rows)
        for equipment_row, request_rows in pairs
    )
