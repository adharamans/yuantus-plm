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

from sqlalchemy import Column, DateTime, Integer, JSON, String
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


class MetaBomWritebackAudit(Base):
    __tablename__ = "meta_bom_writeback_audit"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # P2: explicit per-edit Idempotency-Key header. NOT NULL UNIQUE so a replay
    # collides on insert (begin_nested SAVEPOINT -> IntegrityError -> cached 200).
    # unique=True (NOT a separate index=True) so create_all emits a UNIQUE CONSTRAINT
    # matching the migrations' uq_ constraint -- no model<->migration autogenerate drift.
    idempotency_key = Column(String(64), nullable=False, unique=True)
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
