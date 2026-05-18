"""Breakage DB-resolver pure contract (R1, pure, contract-only).

R2 closeout §4 Tier-A follow-up #3c. Supplies the typed, pure
mapping from persisted-row inputs to the merged
``breakage_eco_closeout_contract.BreakageEcoClosureDescriptor``
(PR #579, ``2775866``). The contract still does **not** read the
DB — the caller fetches `BreakageIncident` rows and passes typed
row views; the pure function maps them.

Three policies pinned by the merged taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DB_RESOLVER_CONTRACT_20260518.md`,
PR #592 ``3b41702``):

- **Policy Z (PRE-RATIFIED Z1)** — pure 1:1 row→descriptor map.
  The resolver always returns a descriptor regardless of row
  status. Eligibility is the caller's job, composed via the
  merged ``is_breakage_eligible_for_design_loopback`` predicate
  and ``map_breakage_to_change_request_intake`` asserter. Pinned
  in code by the test-side AST `no-evaluate` guard.
- **Policy F (strict field-set parity)** —
  ``BreakageIncidentRow`` has exactly the 7 descriptor fields, no
  more and no fewer. The test drift guard pins
  ``set(BreakageIncidentRow.model_fields) ==
  set(BreakageEcoClosureDescriptor.model_fields)`` so a future
  descriptor field added without mirroring on the row DTO fails
  loudly.
- **Policy P (pass-through normalization)** — the resolver does
  NOT re-implement lower/trim/blank→None; it constructs the
  descriptor directly from row DTO values and lets the merged
  descriptor's validators do that work. Re-implementing
  normalization in two places creates exactly the drift those
  validators were written to prevent.

Hard boundary (taskbook §8): NO DB read / NO ``session`` / NO
plugin edit / NO closeout-contract enforcement / NO edit to the
shipped closeout / ECR intake / breakage service / router. The
only cross-contract import is the merged
``BreakageEcoClosureDescriptor`` (the closeout contract type).

See ``docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DB_RESOLVER_CONTRACT_R1_20260518.md``.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

# Type+constructor reuse of the merged closeout contract. We do NOT
# import or call its eligibility/map/derive functions; those stay
# the caller's composition surface (Policy Z1).
from yuantus.meta_engine.services.breakage_eco_closeout_contract import (
    BreakageEcoClosureDescriptor,
)


class BreakageIncidentRow(BaseModel):
    """Caller-supplied subset of a ``meta_breakage_incidents`` row.

    Field names and field set mirror
    ``BreakageEcoClosureDescriptor`` exactly (Policy F): the row
    DTO is the typed column-subset filter — the 17-column real
    `BreakageIncident` row contains many columns the closeout flow
    doesn't need (``mbom_id``, ``routing_id``, ``batch_code``,
    ``customer_name``, ``responsibility``, etc.) and those are
    deliberately absent here.

    Non-empty validators on ``description`` / ``status`` /
    ``severity`` mirror the real columns' ``nullable=False`` shape
    (model: parallel_tasks.py:182/184/185). Optional fields
    accept ``None``/empty/whitespace; the descriptor's
    ``_blank_to_none`` validator collapses empty/whitespace → None
    downstream (Policy P).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str
    status: str
    severity: str
    incident_code: Optional[str] = None
    product_item_id: Optional[str] = None
    bom_id: Optional[str] = None
    version_id: Optional[str] = None

    @field_validator("description", "status", "severity")
    @classmethod
    def _required_non_empty(cls, value: str) -> str:
        # Mirror the nullable=False columns: the row DTO refuses an
        # empty/whitespace value at construction. The descriptor's
        # own validators then lower/trim downstream — we do NOT
        # pre-normalize here (Policy P).
        if (value or "").strip() == "":
            raise ValueError("must be a non-empty string")
        return value


def resolve_breakage_eco_closure_descriptor(
    row: BreakageIncidentRow,
) -> BreakageEcoClosureDescriptor:
    """Map one ``BreakageIncidentRow`` to a descriptor — pure, 1:1.

    Policy Z1: always returns a descriptor regardless of
    ``row.status``. Eligibility is the caller's responsibility —
    compose via the merged
    ``is_breakage_eligible_for_design_loopback(descriptor)`` and
    ``map_breakage_to_change_request_intake(descriptor)``.

    Policy P: pass-through — every field is handed to the
    descriptor constructor verbatim from the row, and the merged
    descriptor's existing validators do all normalization
    (lower/trim on ``status``/``severity``, blank→None on the four
    Optional fields). The resolver never queries, never raises on
    eligibility, and never re-implements normalization.
    """

    return BreakageEcoClosureDescriptor(
        description=row.description,
        status=row.status,
        severity=row.severity,
        incident_code=row.incident_code,
        product_item_id=row.product_item_id,
        bom_id=row.bom_id,
        version_id=row.version_id,
    )


def resolve_breakage_eco_closure_descriptors(
    rows: Sequence[BreakageIncidentRow],
) -> Tuple[BreakageEcoClosureDescriptor, ...]:
    """Batch-map a sequence of ``BreakageIncidentRow`` — pure.

    Deterministic: input order is preserved. Per Policy Z1, every
    row produces a descriptor (no eligibility filtering), so the
    output length equals the input length.
    """

    return tuple(
        resolve_breakage_eco_closure_descriptor(row) for row in rows
    )
