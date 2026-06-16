"""PLM->ECM publication outbox model (ECM-P1B).

Mirrors `erp_publication/models.py` (String-UUID PK, String(30) value enums, JSON/
JSONB bag, created_by_id FK, the state-vs-reason orthogonal split) with two ECM
specifics:
- the idempotency key is **per file** -- (item_id, version_id, file_id, file_role,
  target_system) -- because a released version may carry multiple controlled files;
- a `CONFLICT` reason exists (a changed fingerprint vs an already-SENT row is recorded,
  never raised -- the call site `release()` must never fail).
"""
from __future__ import annotations

import enum

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.types import JSON

from yuantus.models.base import Base


DEFAULT_ECM_TARGET_SYSTEM = "athena"


class EcmPublicationState(str, enum.Enum):
    """Row lifecycle (orthogonal to reason)."""

    PENDING = "pending"
    DRY_RUN_READY = "dry_run_ready"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class EcmPublicationReason(str, enum.Enum):
    """Why a row is in a non-happy state (separate column; never encoded into state
    names). Retry applies to remote_error/adapter_error only; never to
    not_eligible/config_missing/conflict/validation_error."""

    NOT_ELIGIBLE = "not_eligible"
    CONFIG_MISSING = "config_missing"
    CONFLICT = "conflict"
    VALIDATION_ERROR = "validation_error"
    ADAPTER_ERROR = "adapter_error"
    REMOTE_ERROR = "remote_error"


class EcmPublicationOutbox(Base):
    """A durable outbound-publication row for one (item, version, file, file_role,
    target ECM). Carries the release snapshot captured at enqueue + a content
    fingerprint for idempotent re-enqueue / conflict detection."""

    __tablename__ = "meta_ecm_publication_outbox"

    id = Column(String, primary_key=True)

    # Per-file version-scoped identity (the idempotency key -- see UniqueConstraint).
    item_id = Column(String, nullable=False, index=True)
    version_id = Column(String, nullable=False, index=True)
    file_id = Column(String, nullable=False)
    file_role = Column(String(60), nullable=False)
    target_system = Column(String(120), default=DEFAULT_ECM_TARGET_SYSTEM, nullable=False)

    # Lifecycle (orthogonal to reason).
    state = Column(String(30), default=EcmPublicationState.PENDING.value, nullable=False)
    reason = Column(String(30), nullable=True)

    # Release snapshot at enqueue (no remote I/O, no byte reads) + content hash.
    snapshot = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    payload_fingerprint = Column(String(128), nullable=True)

    # Retry / dispatch bookkeeping (worker is P1C; columns are additive now).
    attempt_count = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    replay_of = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)
    next_attempt_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    worker_id = Column(String, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)

    # Extensible / audit.
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)
    created_by_id = Column(Integer, ForeignKey("rbac_users.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "version_id",
            "file_id",
            "file_role",
            "target_system",
            name="uq_ecm_publication_outbox_identity",
        ),
    )
