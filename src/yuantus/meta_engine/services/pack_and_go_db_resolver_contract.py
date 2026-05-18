"""Pack-and-go DB-resolver pure contract (R1, pure, contract-only).

R2 closeout §4 Tier-A follow-up #3. Supplies the typed, pure mapping
from persisted-row inputs to the merged
``pack_and_go_version_lock_contract.BundleDocumentDescriptor`` (PR
#570, ``c7e6fd5``). The contract still does **not** read the DB — the
caller fetches rows and passes typed row views; the pure function
maps them.

Reproduces the three version-resolution branches of
``WorkorderDocumentPackService.serialize_link`` bit-for-bit (taskbook
§3, PR #587 ``27f58ae``):

- no ``document_version_id`` → ``version_belongs_to_item=None``,
  ``version_is_current=None``;
- version pinned, row found → ``version_belongs_to_item =
  (str(version_row.item_id) == str(link_row.document_item_id))`` and
  ``version_is_current = bool(version_row.is_current)`` (so a
  nullable ``is_current=None`` maps to ``False``, matching
  ``serialize_link``'s ``bool(version.is_current)``);
- version pinned, row missing (``version_row is None``) →
  ``version_belongs_to_item=False``, ``version_is_current=None``.

Two RATIFIED policies (taskbook §3):

1. If a ``version_row`` is supplied, its ``id`` MUST equal
   ``link_row.document_version_id`` — *unconditional* on
   ``version_id`` state. A mismatch is a caller bug, the resolver
   **raises ``ValueError``**, and the rule catches both
   ``both-set-ids-differ`` and ``stray-row-without-pinned-version``.
   This is *input-shape validation*, not version-lock enforcement
   (which remains ``assert_bundle_version_locks``, untouched).
2. ``ItemVersionRow.is_current`` is ``Optional[bool] = None`` so the
   legal nullable ``ItemVersion.is_current`` value is representable;
   the output computes ``bool(version_row.is_current)`` so
   ``None → False``.

Hard boundary (taskbook §8): NO DB read / NO ``session`` / NO plugin
edit / NO version-lock enforcement / NO edit to the shipped pack-and-go
contract or ``parallel_tasks_service``. Imports the merged
``BundleDocumentDescriptor`` **only**.

See ``docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_R1_20260516.md``.
"""

from __future__ import annotations

from typing import Optional, Sequence, Tuple

from pydantic import BaseModel, ConfigDict, field_validator

# Type+constructor reuse of the merged version-lock bridge contract.
# We do NOT import or call evaluate/assert_bundle_version_locks; the
# resolver only produces descriptors.
from yuantus.meta_engine.services.pack_and_go_version_lock_contract import (
    BundleDocumentDescriptor,
)


class WorkorderDocLinkRow(BaseModel):
    """Caller-supplied subset of a ``meta_workorder_document_links`` row.

    Field names mirror the real column names so the test-side drift
    guard can assert the field-set is a strict subset of
    ``WorkorderDocumentLink.__table__.columns``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_item_id: str
    document_version_id: Optional[str] = None

    @field_validator("document_item_id")
    @classmethod
    def _non_empty_document_item_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("document_item_id must be a non-empty string")
        return cleaned


class ItemVersionRow(BaseModel):
    """Caller-supplied subset of a ``meta_item_versions`` row.

    ``is_current`` is ``Optional[bool] = None`` because the real
    ``ItemVersion.is_current`` column is nullable; the resolver
    coerces with ``bool(...)`` so ``None`` maps to ``False`` — matching
    ``serialize_link``'s ``bool(version.is_current)`` bit-for-bit
    (taskbook §3, RATIFIED).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    item_id: str
    is_current: Optional[bool] = None

    @field_validator("id")
    @classmethod
    def _non_empty_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("id must be a non-empty string")
        return cleaned

    @field_validator("item_id")
    @classmethod
    def _non_empty_item_id(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("item_id must be a non-empty string")
        return cleaned


def resolve_bundle_document_descriptor(
    link_row: WorkorderDocLinkRow,
    version_row: Optional[ItemVersionRow] = None,
) -> BundleDocumentDescriptor:
    """Map one (link_row, version_row?) pair to a descriptor — pure.

    Reproduces ``serialize_link``'s three version-resolution branches
    exactly (taskbook §3). Raises ``ValueError`` on the §3 input-shape
    rule (``version_row.id != link_row.document_version_id``). Never
    reads a DB.
    """

    version_id = link_row.document_version_id

    # §3 RATIFIED input-shape validation FIRST — the rule is
    # unconditional on `version_id` state: *if* a ``version_row`` is
    # supplied, its ``id`` MUST equal ``link_row.document_version_id``.
    # This catches both kinds of caller bug:
    #   (a) both set, ids differ (wrong row paired with the link);
    #   (b) ``document_version_id`` falsy but a ``version_row`` is
    #       still supplied (stray row whose link has no pinned
    #       version — caller has confused intent).
    # Silently dropping (b) would let the caller bug ride through as
    # Branch A and discard the row; raising surfaces it.
    if version_row is not None and version_row.id != version_id:
        raise ValueError(
            "version_row.id does not match link_row.document_version_id: "
            f"version_row.id={version_row.id!r}, "
            f"link_row.document_version_id={version_id!r}"
        )

    if not version_id:
        # Branch A: no version pinned. Mirrors
        # `if link.document_version_id:` truthiness in serialize_link.
        # version_row is guaranteed to be None here by the check above.
        return BundleDocumentDescriptor(
            document_item_id=link_row.document_item_id,
            document_version_id=None,
            version_belongs_to_item=None,
            version_is_current=None,
        )

    if version_row is None:
        # Branch C: version pinned, row missing (caller resolved the
        # id and the ItemVersion does not exist).
        return BundleDocumentDescriptor(
            document_item_id=link_row.document_item_id,
            document_version_id=version_id,
            version_belongs_to_item=False,
            version_is_current=None,
        )

    # Branch B: version pinned, row found, ids matched.
    return BundleDocumentDescriptor(
        document_item_id=link_row.document_item_id,
        document_version_id=version_id,
        version_belongs_to_item=(
            str(version_row.item_id) == str(link_row.document_item_id)
        ),
        version_is_current=bool(version_row.is_current),
    )


def resolve_bundle_document_descriptors(
    pairs: Sequence[Tuple[WorkorderDocLinkRow, Optional[ItemVersionRow]]],
) -> Tuple[BundleDocumentDescriptor, ...]:
    """Batch-map a sequence of (link_row, version_row?) pairs — pure.

    Deterministic: input order is preserved. Each pair is passed
    through ``resolve_bundle_document_descriptor`` independently, so
    the §3 input-shape rule applies per-pair and a mismatch in any
    pair raises ``ValueError`` from that pair's call.
    """

    return tuple(
        resolve_bundle_document_descriptor(link_row, version_row)
        for link_row, version_row in pairs
    )
