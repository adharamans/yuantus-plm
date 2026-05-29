"""PLM->ERP publication outbox model (G2 R2).

A dedicated, single-level outbox table (one row per version-scoped idempotency
key). Modeled on the `document_sync` PATTERN (String-UUID PK, String(30) value
enums with `default=Enum.VALUE.value`, JSONB bag, `created_by_id` FK, the
state-vs-outcome split) but NOT an extension of meta_sync_jobs/records — see the
R2 build taskbook §3 for the four grounded column-level mismatches that force a
dedicated table.

State (the row lifecycle) is kept ORTHOGONAL to reason (why a row is in a
non-happy state), mirroring SyncJobState vs SyncRecordOutcome
(document_sync/models.py:44,58).
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


DEFAULT_PUBLICATION_KIND = "readiness"


class ErpPublicationState(str, enum.Enum):
    """Row lifecycle (orthogonal to reason)."""

    PENDING = "pending"
    DRY_RUN_READY = "dry_run_ready"
    SENT = "sent"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErpPublicationReason(str, enum.Enum):
    """Why a row is in a non-happy state (separate column; never encoded into
    state names). Retry applies to remote_error/adapter_error only; never to
    not_eligible/validation_error (R2 build taskbook §5/§6)."""

    NOT_ELIGIBLE = "not_eligible"
    ADAPTER_ERROR = "adapter_error"
    REMOTE_ERROR = "remote_error"
    VALIDATION_ERROR = "validation_error"


class ErpPublicationOutbox(Base):
    """A durable outbound-publication row for one (item, version, target ERP,
    publication kind). Carries the R1-B verdict snapshot captured at enqueue."""

    __tablename__ = "meta_erp_publication_outbox"

    id = Column(String, primary_key=True)

    # Version-scoped identity (the idempotency key — see UniqueConstraint below).
    item_id = Column(String, nullable=False, index=True)
    version_id = Column(String, nullable=False)
    target_system = Column(String(120), nullable=False)
    publication_kind = Column(
        String(60), default=DEFAULT_PUBLICATION_KIND, nullable=False
    )

    # Lifecycle (orthogonal to reason).
    state = Column(
        String(30), default=ErpPublicationState.PENDING.value, nullable=False
    )
    reason = Column(String(30), nullable=True)

    # Publication snapshot at enqueue (1:1 with the R1-B response) + content hash.
    snapshot = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    payload_fingerprint = Column(String(128), nullable=True)

    # Retry / dispatch bookkeeping.
    attempt_count = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    replay_of = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    dispatched_at = Column(DateTime(timezone=True), nullable=True)

    # Worker/scheduling (R2 worker daemon, additive — orthogonal to the state
    # machine: a row stays `pending` while claimed). next_attempt_at defaults to
    # now() so a never-failed pending row is immediately due; a retry reschedule
    # pushes it into the future. worker_id/claimed_at are the claim markers.
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
            "target_system",
            "publication_kind",
            name="uq_erp_publication_outbox_identity",
        ),
    )
