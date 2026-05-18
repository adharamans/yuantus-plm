"""Tests for the breakage DB-resolver pure contract (R1).

Includes the three MANDATORY exactly-named tests pinned by the
merged taskbook (PR #592, ``3b41702``):

- test_resolver_mirrors_breakage_incident_columns_one_to_one
- test_resolver_pass_through_normalization_via_descriptor_validators
- test_resolver_output_is_the_merged_breakage_descriptor

plus row-DTO behavior, batch determinism, drift guards, AST
purity, AST no-evaluate/no-assert guards.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from pydantic import ValidationError

from yuantus.meta_engine.models.item import Item  # noqa: F401 (mapper registry)
from yuantus.meta_engine.models.parallel_tasks import BreakageIncident
from yuantus.meta_engine.services import (
    breakage_db_resolver_contract as mod,
)
from yuantus.meta_engine.services.breakage_db_resolver_contract import (
    BreakageIncidentRow,
    resolve_breakage_eco_closure_descriptor,
    resolve_breakage_eco_closure_descriptors,
)
from yuantus.meta_engine.services.breakage_eco_closeout_contract import (
    BreakageEcoClosureDescriptor,
    is_breakage_eligible_for_design_loopback,
    map_breakage_to_change_request_intake,
)
from yuantus.meta_engine.services.ecr_intake_contract import (
    ChangeRequestIntake,
)


# --------------------------------------------------------------------------
# Row DTO — frozen, extra=forbid, non-empty validators on required fields
# --------------------------------------------------------------------------


def _required_kwargs(**overrides) -> dict:
    base = {
        "description": "panel cracked under load",
        "status": "resolved",
        "severity": "high",
    }
    base.update(overrides)
    return base


def test_row_is_frozen_and_forbids_extra():
    row = BreakageIncidentRow(**_required_kwargs())
    with pytest.raises(ValidationError):
        row.description = "another"  # frozen
    with pytest.raises(ValidationError):
        BreakageIncidentRow(**_required_kwargs(), unknown="x")  # extra=forbid


def test_row_required_fields_non_empty():
    with pytest.raises(ValidationError):
        BreakageIncidentRow(**_required_kwargs(description=""))
    with pytest.raises(ValidationError):
        BreakageIncidentRow(**_required_kwargs(description="   "))
    with pytest.raises(ValidationError):
        BreakageIncidentRow(**_required_kwargs(status=""))
    with pytest.raises(ValidationError):
        BreakageIncidentRow(**_required_kwargs(severity=""))


def test_row_optional_fields_default_to_none_and_accept_blanks():
    row = BreakageIncidentRow(**_required_kwargs())
    assert row.incident_code is None
    assert row.product_item_id is None
    assert row.bom_id is None
    assert row.version_id is None
    # Optional fields can be set to None, empty, whitespace, or a value;
    # Policy P means the descriptor (not the row) collapses blanks → None.
    BreakageIncidentRow(**_required_kwargs(incident_code=""))
    BreakageIncidentRow(**_required_kwargs(incident_code="   "))
    BreakageIncidentRow(**_required_kwargs(incident_code="BRK-000001"))


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — 1:1 column→field mapping
# --------------------------------------------------------------------------


def test_resolver_mirrors_breakage_incident_columns_one_to_one():
    """§3 Policy F: every descriptor field is populated from the row
    column of the same name. A drift on any single mapping —
    including a same-type field swap (e.g. status ↔ severity, both
    str-valued and validator-normalized identically) — fails this
    test loudly.

    Each row field carries a **distinctive lowercase value** with a
    unique substring so a swap is detectable; values are already
    lowercase because the descriptor's ``_required_lower`` validator
    would otherwise lowercase them and mask a swap that produces
    the wrong lowercase result.
    """

    row = BreakageIncidentRow(
        description="row-description-distinct",
        status="row-status-distinct",
        severity="row-severity-distinct",
        incident_code="row-incident_code-distinct",
        product_item_id="row-product_item_id-distinct",
        bom_id="row-bom_id-distinct",
        version_id="row-version_id-distinct",
    )
    descriptor = resolve_breakage_eco_closure_descriptor(row)

    # Each descriptor field must equal the row field of the same name
    # — and ONLY that one. The unique `row-<field>-distinct` markers
    # make a swap (e.g. status reads from row.severity) impossible to
    # hide behind validator-normalized equivalence.
    assert descriptor.description == "row-description-distinct"
    assert descriptor.status == "row-status-distinct"
    assert descriptor.severity == "row-severity-distinct"
    assert descriptor.incident_code == "row-incident_code-distinct"
    assert descriptor.product_item_id == "row-product_item_id-distinct"
    assert descriptor.bom_id == "row-bom_id-distinct"
    assert descriptor.version_id == "row-version_id-distinct"


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — Policy P (pass-through normalization)
# --------------------------------------------------------------------------


def test_resolver_pass_through_normalization_via_descriptor_validators():
    """§3 Policy P: the resolver does NOT re-implement
    lower/trim/blank→None. The merged descriptor's validators
    (status/severity: lower+trim; incident_code/product_item_id/
    bom_id/version_id: blank→None) handle all normalization.
    This test feeds an adversarial fixture through the resolver
    and asserts the descriptor's existing validators do the work.
    """

    row = BreakageIncidentRow(
        description="  Cosmetic chip on inlet  ",
        status="  RESOLVED  ",
        severity="  HIGH  ",
        incident_code="",
        product_item_id="   ",
        bom_id="",
        version_id="   ",
    )
    descriptor = resolve_breakage_eco_closure_descriptor(row)

    # description: descriptor.description strips (the merged
    # `_description_non_empty` validator does (v or "").strip()).
    assert descriptor.description == "Cosmetic chip on inlet"
    # status / severity: descriptor lowers + trims.
    assert descriptor.status == "resolved"
    assert descriptor.severity == "high"
    # All four Optional fields: descriptor's `_blank_to_none` collapses
    # empty/whitespace → None. The resolver passed the raw row values
    # in; the descriptor produced None.
    assert descriptor.incident_code is None
    assert descriptor.product_item_id is None
    assert descriptor.bom_id is None
    assert descriptor.version_id is None


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — compose proof at the resolver↔bridge seam
# --------------------------------------------------------------------------


def test_resolver_output_is_the_merged_breakage_descriptor():
    """Compose proof: every output is the merged
    ``BreakageEcoClosureDescriptor`` unchanged, and an eligible row
    feeds the full closeout path
    ``resolver → is_breakage_eligible_for_design_loopback →
    map_breakage_to_change_request_intake`` to produce a valid
    ``ChangeRequestIntake`` — no DB.
    """

    row = BreakageIncidentRow(
        description="Wear-through on cooling jacket",
        status="resolved",
        severity="critical",
        incident_code="BRK-000042",
        product_item_id="product-99",
        bom_id=None,  # no bom_id, so change_type falls back to "product"
        version_id="v-1",
    )
    descriptor = resolve_breakage_eco_closure_descriptor(row)

    # Reuse, not reimplementation: type identity, not just structural.
    assert type(descriptor) is BreakageEcoClosureDescriptor
    # Strict field-set parity with the merged descriptor (Policy F).
    assert set(descriptor.model_dump().keys()) == set(
        BreakageEcoClosureDescriptor.model_fields
    )

    # Eligibility predicate is the caller's seam — calling it here in
    # the test (not in the resolver module) is correct under Policy Z1.
    assert is_breakage_eligible_for_design_loopback(descriptor) is True

    intake = map_breakage_to_change_request_intake(descriptor)
    assert isinstance(intake, ChangeRequestIntake)
    # severity_priority RATIFIED §3.2: critical → urgent.
    assert intake.priority == "urgent"
    # bom_id is None → change_type falls back to "product" (merged
    # contract's bom⇒product_id invariant).
    assert intake.change_type == "product"
    assert intake.product_id == "product-99"


# --------------------------------------------------------------------------
# Batch — order preserved across mixed status / severity / eligibility
# --------------------------------------------------------------------------


def test_batch_preserves_input_order_and_does_not_filter_by_eligibility():
    """Policy Z1: batch produces a descriptor for every input row,
    including ineligible ones. Order is preserved.
    """

    rows = [
        BreakageIncidentRow(**_required_kwargs(status="open", description=f"row-{i}-open"))
        if i == 1
        else BreakageIncidentRow(**_required_kwargs(description=f"row-{i}-resolved"))
        for i in range(4)
    ]
    descriptors = resolve_breakage_eco_closure_descriptors(rows)

    assert len(descriptors) == 4
    assert [d.description for d in descriptors] == [
        "row-0-resolved",
        "row-1-open",  # ineligible row still produces a descriptor
        "row-2-resolved",
        "row-3-resolved",
    ]
    # Spot-check: the open row is correctly classified as ineligible by
    # the caller-composable predicate.
    assert is_breakage_eligible_for_design_loopback(descriptors[1]) is False
    assert is_breakage_eligible_for_design_loopback(descriptors[0]) is True


# --------------------------------------------------------------------------
# Drift guards — row DTO ⊆ real columns; strict == with descriptor; nullables
# --------------------------------------------------------------------------


def test_row_fields_are_subset_of_real_columns():
    real_cols = {c.name for c in BreakageIncident.__table__.columns}
    assert set(BreakageIncidentRow.model_fields) <= real_cols


def test_row_fields_strictly_equal_descriptor_fields():
    """§3 Policy F: catches a future descriptor field added without
    mirroring on the row DTO (or vice versa).
    """

    assert set(BreakageIncidentRow.model_fields) == set(
        BreakageEcoClosureDescriptor.model_fields
    )


def test_required_columns_are_non_nullable_in_real_table():
    """Pin the assumption the row DTO's required-field typing rests
    on: the three required descriptor fields correspond to columns
    that are ``nullable=False`` in `BreakageIncident`.
    """

    cols = BreakageIncident.__table__.columns
    assert cols["description"].nullable is False
    assert cols["status"].nullable is False
    assert cols["severity"].nullable is False


def test_resolver_reuses_the_merged_descriptor_type():
    descriptor = resolve_breakage_eco_closure_descriptor(
        BreakageIncidentRow(**_required_kwargs())
    )
    assert type(descriptor) is BreakageEcoClosureDescriptor


# --------------------------------------------------------------------------
# Purity guard (AST) — taskbook §5
# --------------------------------------------------------------------------


def test_module_is_pure_by_ast():
    """Pin the §5 boundary: no DB / no session / no plugin / no
    parallel_tasks_service / no router / no ``*_service``; the only
    cross-contract import is the merged closeout contract.
    """

    tree = ast.parse(inspect.getsource(mod))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
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
    ):
        assert forbidden not in joined, f"impure import: {forbidden!r}"

    # The only cross-contract reuse permitted: the merged closeout
    # contract (we import BreakageEcoClosureDescriptor from there).
    assert "breakage_eco_closeout_contract" in joined

    # No `session` reference anywhere — name or attribute.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "session" not in names and "session" not in attrs


# --------------------------------------------------------------------------
# No-evaluate / no-assert (AST) — Policy Z1 pinned in code
# --------------------------------------------------------------------------


def test_module_does_not_call_closeout_contract_functions():
    """§3 Policy Z1: the resolver is a mapper, not a composer. It
    MUST NOT call the merged closeout contract's eligibility
    predicate, asserter, or reference deriver — those stay the
    caller's composition surface. This AST guard pins Z1 in code.
    """

    tree = ast.parse(inspect.getsource(mod))
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    } | {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    assert "is_breakage_eligible_for_design_loopback" not in called
    assert "map_breakage_to_change_request_intake" not in called
    assert "derive_breakage_change_reference" not in called
    assert "severity_priority" not in called


def test_module_has_no_assert_callable():
    """The resolver is a mapper, not an enforcer — no ``assert_*``
    function should be defined here (taskbook §8). There is also
    no input-shape raise (Policy N: single row → single descriptor,
    nothing to mismatch); the module raises nothing.
    """

    tree = ast.parse(inspect.getsource(mod))
    func_names = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    assert not [n for n in func_names if n.startswith("assert_")]
