"""PLM-COLLAB Phase-7 Day-2: governed BOM multi-table line WRITE-BACK.

Replaces the spike's in-place ``props.update`` + bare ``commit`` (no audit, no replay
guard) with the design-ratified governed write path (design-resolution 20260629 §1-§4):

- **WRITE_FEATURE_KEY** is a DISTINCT entitlement SKU from the read projection's
  ``bom_multitable`` -- a read license must never unlock the write surface.
- **Single-use / replay (P2)** is a DB guard (Redis absent; verifier on SQLite) reusing the
  proven MES-inbox pattern, scoped PER TENANT: ``begin_nested`` SAVEPOINT + insert + flush on the
  ``(tenant_id, idempotency_key)`` composite UNIQUE, ``IntegrityError`` -> fetch the existing
  SAME-TENANT row -> same payload returns the cached ``{ok, bom_line_id}`` WITHOUT re-applying;
  a different payload under the same (tenant, key) is a 409 conflict. The SAME key under a
  DIFFERENT tenant does NOT collide (cross-tenant isolation).
- **Write-back audit (P3)** rides the SAME ``meta_bom_writeback_audit`` insert: the touched
  cells' before/after snapshot is committed ATOMICALLY with the property mutation, so an
  audit-insert failure rolls back the mutation (a governed write must not succeed without its
  diff -- a deliberate departure from the repo's best-effort audit).

The router enforces the guard ORDER (auth -> entitled -> permitted -> 400 malformed/empty/
missing-Idempotency-Key -> 404 part-missing/line∉part -> 409 lifecycle-lock). This service
re-enforces ``line ∈ part`` as defense-in-depth and performs the atomic apply.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_bom_writeback_audit import MetaBomWritebackAudit

# The DISTINCT write entitlement key (NOT the read projection's ``bom_multitable``). Lit in
# FEATURE_APP_NAMES to its own SKU ``plm.bom_multitable_writeback``.
WRITE_FEATURE_KEY = "bom_multitable_writeback"

# The BOM-line relationship-Item type the line must be (mirrors the projection service's
# BOM_LINE_TYPE). A line ∈ part iff it is this type AND its source_id is exactly part_id.
BOM_LINE_TYPE = "Part BOM"

# The ONLY editable cells of a governed BOM multi-table line. Mirrors the projection's
# ``_LINE_REL_FIELDS`` and the consumer pact (#3332) request whitelist. Defense-in-depth:
# re-applied here even though the router already whitelisted the body.
WRITE_WHITELIST = ("quantity", "uom", "find_num", "refdes")


class BomLineNotInPartError(Exception):
    """``bom_line_id`` did not resolve to a ``Part BOM`` line under the given ``part_id``.

    The provider-side ``line ∈ part`` boundary the consumer pact (#3332) deliberately does
    NOT assert. Missing / not-a-BOM-line / wrong-parent are indistinguishable -> router 404.
    """


class BomLineWritebackConflictError(Exception):
    """The same ``Idempotency-Key`` was reused for a DIFFERENT write (different line or cells).

    Replay safety: an identical replay returns the cached result, but a key reused for a
    different intent is a real conflict -> router 409.
    """


class BomLineWritebackPreconditionFailedError(Exception):
    """The optional ``If-Match`` precondition did not match the current BOM line ETag."""


def bom_line_write_etag_from_values(
    *,
    bom_line_id: Any,
    source_id: Any,
    related_id: Any,
    generation: Any,
    properties: Dict[str, Any],
) -> str:
    """Return the strong ETag for the editable representation of a BOM line.

    The tag covers the stable line identity + parent/child relation + current editable cell
    values. It intentionally avoids DB timestamps: ``updated_at`` is nullable on fresh rows and
    backend-specific precision can drift, while the write-back race we need to catch is a stale
    editable-cell snapshot.
    """

    payload = {
        "bom_line_id": bom_line_id,
        "source_id": source_id,
        "related_id": related_id,
        "generation": generation,
        "editable": {key: properties.get(key) for key in WRITE_WHITELIST},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
    return f'"bom-line:{digest}"'


def bom_line_write_etag(line: Item) -> str:
    return bom_line_write_etag_from_values(
        bom_line_id=line.id,
        source_id=line.source_id,
        related_id=line.related_id,
        generation=line.generation,
        properties=dict(line.properties or {}),
    )


def if_match_allows(if_match: Optional[str], current_etag: str) -> bool:
    """Minimal RFC-style If-Match matcher for a single current representation."""

    if if_match is None or not if_match.strip():
        return True
    candidates = {part.strip() for part in if_match.split(",") if part.strip()}
    if "*" in candidates:
        return True
    unquoted = current_etag.strip('"')
    return current_etag in candidates or unquoted in candidates


class BOMMultitableWritebackService:
    """Governed single-line BOM write-back (replay guard + atomic audit + property mutation).

    Gating (entitlement/permission/order) is the router's job; this service assumes those
    passed and owns the atomic apply + replay/audit semantics.
    """

    def __init__(self, session: Session):
        self.session = session

    def write_line(
        self,
        part_id: str,
        bom_line_id: str,
        idempotency_key: str,
        fields: Dict[str, Any],
        *,
        user_id: Optional[int],
        tenant_id: Optional[str],
        org_id: Optional[str],
        if_match: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply the whitelisted cells of ONE governed BOM line, governed + atomically audited.

        Raises :class:`BomLineNotInPartError` (-> 404) if the line is missing / not a BOM line
        / under a different part, BEFORE any write. On a replayed ``idempotency_key``: same
        payload -> cached ``{ok, bom_line_id}`` (no re-apply); different payload ->
        :class:`BomLineWritebackConflictError` (-> 409).
        """
        line = (
            self.session.query(Item)
            .filter(Item.id == bom_line_id)
            .with_for_update()
            .one_or_none()
        )
        if (
            line is None
            or line.item_type_id != BOM_LINE_TYPE
            or line.source_id != part_id
        ):
            # defense-in-depth: the router already enforced line∈part for the 404/409 ordering.
            raise BomLineNotInPartError(bom_line_id)

        # defense-in-depth: re-apply the editable-cell whitelist (router already filtered).
        updates = {key: fields[key] for key in WRITE_WHITELIST if key in fields}

        # Snapshot the TOUCHED cells' BEFORE values prior to any `properties` reassignment.
        props = dict(line.properties or {})
        before = {key: props.get(key) for key in updates}
        after = dict(updates)
        current_etag = bom_line_write_etag(line)

        # If-Match is a T6 fast-follow optimistic-concurrency guard. A legitimate retry of an
        # already-applied write must still be cacheable even though the current ETag changed, so
        # check an existing same-tenant idempotency row before returning 412 for a stale new key.
        if not if_match_allows(if_match, current_etag):
            existing = (
                self.session.query(MetaBomWritebackAudit)
                .filter(
                    MetaBomWritebackAudit.tenant_id == tenant_id,
                    MetaBomWritebackAudit.idempotency_key == idempotency_key,
                )
                .one_or_none()
            )
            if existing is not None:
                if (
                    existing.part_id == part_id
                    and existing.bom_line_id == bom_line_id
                    and (existing.after or {}) == after
                ):
                    return {"ok": True, "bom_line_id": bom_line_id}
                raise BomLineWritebackConflictError(idempotency_key)
            raise BomLineWritebackPreconditionFailedError(if_match)

        # P2/P3 single insert: the replay guard's unique key AND the audit diff are one row.
        audit = MetaBomWritebackAudit(
            idempotency_key=idempotency_key,
            tenant_id=tenant_id,
            org_id=org_id,
            user_id=int(user_id) if user_id is not None else None,
            part_id=part_id,
            bom_line_id=bom_line_id,
            before=before,
            after=after,
            status="applied",
        )
        try:
            # SAVEPOINT so a duplicate-key IntegrityError is catchable without poisoning the
            # outer transaction (the MES-inbox pattern). On success the row is flushed but the
            # OUTER transaction is NOT yet committed -- the property mutation joins it below so
            # an audit-insert failure rolls the mutation back (atomic).
            with self.session.begin_nested():
                self.session.add(audit)
                self.session.flush()
        except IntegrityError:
            # Scope the replay lookup to the SAME tenant as the guard's composite UNIQUE
            # (tenant_id, idempotency_key): a collision can only be a same-tenant reuse, and we
            # must never resolve a replay/conflict against ANOTHER tenant's row.
            existing = (
                self.session.query(MetaBomWritebackAudit)
                .filter(
                    MetaBomWritebackAudit.tenant_id == tenant_id,
                    MetaBomWritebackAudit.idempotency_key == idempotency_key,
                )
                .one_or_none()
            )
            if existing is None:
                # not the uniqueness collision we guard for -> surface it
                raise
            # Same key, same intent (same line + same applied cells) -> cached, NO re-apply.
            if (
                existing.part_id == part_id
                and existing.bom_line_id == bom_line_id
                and (existing.after or {}) == after
            ):
                return {"ok": True, "bom_line_id": bom_line_id}
            # Same key, different intent -> conflict.
            raise BomLineWritebackConflictError(idempotency_key)

        # Apply the mutation in the SAME outer transaction as the audit insert. REASSIGN
        # `properties` so SQLAlchemy detects the JSON change (the bom_service pattern).
        props.update(updates)
        line.properties = props
        self.session.commit()
        return {"ok": True, "bom_line_id": bom_line_id}
