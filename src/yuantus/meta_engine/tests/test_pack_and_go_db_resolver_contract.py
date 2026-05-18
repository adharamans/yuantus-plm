"""Tests for the pack-and-go DB-resolver pure contract (R1).

Includes the four MANDATORY exactly-named tests pinned by the merged
taskbook (PR #587, ``27f58ae``):

- test_resolver_mirrors_serialize_link_three_branches
- test_resolver_rejects_mismatched_version_row
- test_resolver_maps_null_is_current_to_false
- test_resolver_output_is_the_merged_bundle_descriptor

plus row-DTO behavior, batch determinism, drift guards on the row
DTOs and the descriptor, and an AST purity guard.
"""

from __future__ import annotations

import ast
import inspect

import pytest
from pydantic import ValidationError

from yuantus.meta_engine.models.item import Item  # noqa: F401 (mapper registry)
from yuantus.meta_engine.models.parallel_tasks import WorkorderDocumentLink
from yuantus.meta_engine.services import (
    pack_and_go_db_resolver_contract as mod,
)
from yuantus.meta_engine.services.pack_and_go_db_resolver_contract import (
    ItemVersionRow,
    WorkorderDocLinkRow,
    resolve_bundle_document_descriptor,
    resolve_bundle_document_descriptors,
)
from yuantus.meta_engine.services.pack_and_go_version_lock_contract import (
    BundleDocumentDescriptor,
    evaluate_bundle_version_locks,
)
from yuantus.meta_engine.version.models import ItemVersion


# --------------------------------------------------------------------------
# Row DTOs — frozen, extra=forbid, non-empty validation
# --------------------------------------------------------------------------


def test_link_row_frozen_extra_forbid_and_non_empty():
    r = WorkorderDocLinkRow(document_item_id="doc-1")
    assert r.document_version_id is None
    with pytest.raises(ValidationError):
        r.document_item_id = "doc-2"  # frozen
    with pytest.raises(ValidationError):
        WorkorderDocLinkRow(document_item_id="doc-1", unknown="x")  # extra=forbid
    with pytest.raises(ValidationError):
        WorkorderDocLinkRow(document_item_id="   ")  # non-empty (post-strip)


def test_version_row_frozen_extra_forbid_and_non_empty():
    r = ItemVersionRow(id="v-1", item_id="doc-1")
    assert r.is_current is None  # nullable default
    with pytest.raises(ValidationError):
        r.id = "v-2"  # frozen
    with pytest.raises(ValidationError):
        ItemVersionRow(id="v-1", item_id="doc-1", unknown="x")  # extra=forbid
    with pytest.raises(ValidationError):
        ItemVersionRow(id="", item_id="doc-1")  # non-empty id
    with pytest.raises(ValidationError):
        ItemVersionRow(id="v-1", item_id="   ")  # non-empty item_id


def test_version_row_accepts_explicit_is_current_values():
    assert ItemVersionRow(id="v", item_id="d", is_current=True).is_current is True
    assert ItemVersionRow(id="v", item_id="d", is_current=False).is_current is False
    assert ItemVersionRow(id="v", item_id="d", is_current=None).is_current is None


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — three serialize_link branches bit-for-bit
# --------------------------------------------------------------------------


def test_resolver_mirrors_serialize_link_three_branches():
    """Pin the §3 mapping: each branch produces exactly the descriptor
    ``WorkorderDocumentPackService.serialize_link`` would.
    """

    # Branch A — no version_id pinned.
    a = resolve_bundle_document_descriptor(
        WorkorderDocLinkRow(document_item_id="doc-A"),
    )
    assert a == BundleDocumentDescriptor(
        document_item_id="doc-A",
        document_version_id=None,
        version_belongs_to_item=None,
        version_is_current=None,
    )

    # Branch B (owned, current) — version pinned, row found, item_id matches.
    b_owned_current = resolve_bundle_document_descriptor(
        WorkorderDocLinkRow(document_item_id="doc-B", document_version_id="v-1"),
        ItemVersionRow(id="v-1", item_id="doc-B", is_current=True),
    )
    assert b_owned_current == BundleDocumentDescriptor(
        document_item_id="doc-B",
        document_version_id="v-1",
        version_belongs_to_item=True,
        version_is_current=True,
    )

    # Branch B (foreign, not current) — version pinned, row found, item_id differs.
    b_foreign = resolve_bundle_document_descriptor(
        WorkorderDocLinkRow(document_item_id="doc-C", document_version_id="v-2"),
        ItemVersionRow(id="v-2", item_id="some-other-item", is_current=False),
    )
    assert b_foreign == BundleDocumentDescriptor(
        document_item_id="doc-C",
        document_version_id="v-2",
        version_belongs_to_item=False,
        version_is_current=False,
    )

    # Branch C — version pinned, row missing.
    c = resolve_bundle_document_descriptor(
        WorkorderDocLinkRow(document_item_id="doc-D", document_version_id="v-missing"),
        None,
    )
    assert c == BundleDocumentDescriptor(
        document_item_id="doc-D",
        document_version_id="v-missing",
        version_belongs_to_item=False,
        version_is_current=None,
    )


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — input-shape: id mismatch raises ValueError
# --------------------------------------------------------------------------


def test_resolver_rejects_mismatched_version_row():
    """§3 RATIFIED: a supplied ``version_row.id`` MUST equal
    ``link_row.document_version_id`` — *unconditional* on whether
    ``document_version_id`` is set. Silently treating a mismatched
    row as "missing" (or silently dropping a stray row) would mask
    the caller's bug and wrap the wrong row as the
    version-pinned-but-missing branch / Branch A.
    """

    # Sub-case (a): both set, ids differ — wrong row paired with the
    # link.
    link_with_version = WorkorderDocLinkRow(
        document_item_id="doc-1", document_version_id="v-expected"
    )
    bad_version = ItemVersionRow(id="v-other", item_id="doc-1", is_current=True)
    with pytest.raises(ValueError) as exc_a:
        resolve_bundle_document_descriptor(link_with_version, bad_version)
    msg_a = str(exc_a.value)
    assert "version_row.id" in msg_a
    assert "v-other" in msg_a
    assert "v-expected" in msg_a

    # Sub-case (b): document_version_id falsy but version_row supplied
    # — stray row, caller's intent is confused. version_row.id != None
    # so the rule still rejects this.
    link_no_version = WorkorderDocLinkRow(document_item_id="doc-1")
    stray_version = ItemVersionRow(
        id="v-orphan", item_id="doc-1", is_current=True
    )
    with pytest.raises(ValueError) as exc_b:
        resolve_bundle_document_descriptor(link_no_version, stray_version)
    msg_b = str(exc_b.value)
    assert "version_row.id" in msg_b
    assert "v-orphan" in msg_b


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — nullable is_current → False bit-for-bit
# --------------------------------------------------------------------------


def test_resolver_maps_null_is_current_to_false():
    """§3 RATIFIED nullable-``is_current`` policy: the real
    ``ItemVersion.is_current`` column is nullable; ``serialize_link``
    coerces with ``bool(version.is_current)`` so a real NULL row maps
    to ``False``. The resolver must reproduce this exactly:
    ``ItemVersionRow.is_current=None → version_is_current=False``.
    Typing ``is_current: bool`` would make a legal DB state
    unrepresentable — this test prevents that regression.
    """

    link = WorkorderDocLinkRow(document_item_id="doc-1", document_version_id="v-1")
    version_with_null_is_current = ItemVersionRow(
        id="v-1", item_id="doc-1", is_current=None
    )

    descriptor = resolve_bundle_document_descriptor(
        link, version_with_null_is_current
    )

    assert descriptor.version_is_current is False  # bool(None) == False
    # Ownership is independent and must still be computed.
    assert descriptor.version_belongs_to_item is True


# --------------------------------------------------------------------------
# MANDATORY (exactly named) — descriptor reuse / compose proof
# --------------------------------------------------------------------------


def test_resolver_output_is_the_merged_bundle_descriptor():
    """The return value is an instance of the merged
    ``pack_and_go_version_lock_contract.BundleDocumentDescriptor``, and
    the resolved descriptors feed ``evaluate_bundle_version_locks``
    unchanged — compose proof, no DB.
    """

    pairs = [
        (
            WorkorderDocLinkRow(document_item_id="doc-A"),
            None,
        ),  # unlocked
        (
            WorkorderDocLinkRow(
                document_item_id="doc-B", document_version_id="v-1"
            ),
            ItemVersionRow(id="v-1", item_id="doc-B", is_current=True),
        ),  # locked & owned & current
        (
            WorkorderDocLinkRow(
                document_item_id="doc-C", document_version_id="v-2"
            ),
            ItemVersionRow(id="v-2", item_id="some-other-item", is_current=True),
        ),  # mismatched (foreign)
        (
            WorkorderDocLinkRow(
                document_item_id="doc-D", document_version_id="v-3"
            ),
            ItemVersionRow(id="v-3", item_id="doc-D", is_current=None),
        ),  # locked & owned, stale (None → False)
    ]
    descriptors = resolve_bundle_document_descriptors(pairs)

    # Reuse: every descriptor IS the merged Pydantic type unchanged.
    assert all(isinstance(d, BundleDocumentDescriptor) for d in descriptors)
    # No reimplementation: the resolver's output field set equals the
    # merged descriptor's field set exactly.
    for d in descriptors:
        assert set(d.model_dump().keys()) == set(
            BundleDocumentDescriptor.model_fields
        )

    # Compose proof: descriptors plug into the merged evaluator
    # unchanged and produce the expected report.
    report = evaluate_bundle_version_locks(descriptors)
    assert report.total == 4
    assert report.unlocked == ["doc-A"]
    assert report.mismatched == ["doc-C"]
    assert report.stale == ["doc-D"]  # locked & owned & is_current=False
    assert report.locked == 4 - len(report.unlocked) - len(report.mismatched)
    assert report.ok is False  # unlocked + mismatched present


# --------------------------------------------------------------------------
# Batch — order preserved, mixed branches
# --------------------------------------------------------------------------


def test_batch_preserves_input_order_with_mixed_branches():
    pairs = [
        (WorkorderDocLinkRow(document_item_id=f"doc-{i}"), None)
        for i in range(5)
    ]
    out = resolve_bundle_document_descriptors(pairs)
    assert [d.document_item_id for d in out] == [f"doc-{i}" for i in range(5)]


def test_batch_propagates_mismatch_error():
    pairs = [
        (
            WorkorderDocLinkRow(
                document_item_id="doc-1", document_version_id="v-1"
            ),
            ItemVersionRow(id="v-WRONG", item_id="doc-1", is_current=True),
        ),
    ]
    with pytest.raises(ValueError):
        resolve_bundle_document_descriptors(pairs)


# --------------------------------------------------------------------------
# Drift guards — row DTOs ⊆ real columns; descriptor reuse
# --------------------------------------------------------------------------


def test_link_row_fields_are_subset_of_real_columns():
    real_cols = {c.name for c in WorkorderDocumentLink.__table__.columns}
    assert set(WorkorderDocLinkRow.model_fields) <= real_cols
    assert {"document_item_id", "document_version_id"} <= set(
        WorkorderDocLinkRow.model_fields
    )


def test_version_row_fields_are_subset_of_real_columns():
    real_cols = {c.name for c in ItemVersion.__table__.columns}
    assert set(ItemVersionRow.model_fields) <= real_cols
    assert {"id", "item_id", "is_current"} <= set(ItemVersionRow.model_fields)
    # is_current is nullable in the real schema — pin the assumption
    # the §3 nullable policy is built on.
    assert ItemVersion.__table__.columns["is_current"].nullable is True


def test_resolver_reuses_the_merged_descriptor_type():
    # Mirror reuse: no shadow descriptor — the resolver returns the
    # exact merged type so the version-lock evaluator stays the single
    # source of truth.
    d = resolve_bundle_document_descriptor(
        WorkorderDocLinkRow(document_item_id="doc-1"),
    )
    assert type(d) is BundleDocumentDescriptor


# --------------------------------------------------------------------------
# Purity guard (AST) — taskbook §5
# --------------------------------------------------------------------------


def test_module_is_pure_by_ast():
    """Pin the §5 boundary: no DB / no session / no plugin / no
    parallel_tasks_service / no router / no ``*_service``; the only
    cross-module import is the merged pack-and-go version-lock contract.
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

    # The only cross-contract reuse permitted: the merged version-lock
    # bridge contract (we import BundleDocumentDescriptor from there).
    assert "pack_and_go_version_lock_contract" in joined

    # No `session` reference anywhere — name or attribute.
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "session" not in names and "session" not in attrs


def test_module_has_no_evaluate_or_assert_calls():
    """The resolver only produces descriptors. It must NOT call the
    version-lock evaluator/asserter — that responsibility stays with
    the caller (taskbook §8).
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
    assert "evaluate_bundle_version_locks" not in called
    assert "assert_bundle_version_locks" not in called


def test_module_has_no_assert_callable():
    """The resolver is a mapper, not an enforcer — no ``assert_*``
    function should be defined here (taskbook §8). The §3 raise on
    mismatch is *input-shape validation* and lives inside the mapper,
    not as a separate enforcement seam.
    """

    tree = ast.parse(inspect.getsource(mod))
    assert not [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("assert_")
    ]
