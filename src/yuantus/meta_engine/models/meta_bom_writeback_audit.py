"""BOM multi-table write-back governed audit + single-use replay cache.

Phase-7 Day-2 (design-resolution 20260629). One row simultaneously serves:
- P2 single-use / replay guard: `idempotency_key` is NOT NULL UNIQUE, so a
  replayed `PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` maps to
  one row (the MES-inbox `begin_nested` + IntegrityError pattern returns the
  cached `{ok, bom_line_id}` without re-applying).
- P3 write-back domain audit: `before`/`after` capture the touched-cell diff,
  committed atomically with the property mutation (an audit-insert failure rolls
  back the mutation — a governed write must not succeed without its diff).

Mirrors `MesConsumptionInbox` (idempotency_key shape) + `CadChangeLog`
(JSON-with-JSONB-variant payload + tenant/org/user provenance columns).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


class MetaBomWritebackAudit(Base):
    __tablename__ = "meta_bom_writeback_audit"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # P2: explicit per-edit Idempotency-Key header. The single-use guard is scoped PER TENANT
    # via the (tenant_id, idempotency_key) composite UNIQUE in __table_args__ below: a replay
    # collides on insert WITHIN a tenant (begin_nested SAVEPOINT -> IntegrityError -> cached 200),
    # but the SAME key under a DIFFERENT tenant does NOT collide (cross-tenant isolation). The
    # composite (not a column-level unique=True) is emitted by create_all to match the migration's
    # uq_meta_bom_writeback_audit_tenant_idem constraint -- no model<->migration autogenerate drift.
    idempotency_key = Column(String(64), nullable=False)
    tenant_id = Column(String(64), nullable=True, index=True)
    org_id = Column(String(64), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    part_id = Column(String, nullable=False, index=True)
    bom_line_id = Column(String, nullable=False, index=True)
    # P3: snapshotted touched-cells BEFORE `properties` reassignment, and the
    # applied result. JSON on SQLite, JSONB on Postgres.
    before = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    after = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    status = Column(String, nullable=False, default="applied")
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    # P2 single-use guard scoped PER TENANT: the same Idempotency-Key may legitimately recur
    # across tenants (each tenant's relay mints its own jti namespace), so uniqueness is
    # (tenant_id, idempotency_key) -- NOT a global key. (A NULL tenant_id does not collide with
    # another NULL, which is acceptable: a governed write always carries a resolved tenant.)
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_meta_bom_writeback_audit_tenant_idem"
        ),
    )
