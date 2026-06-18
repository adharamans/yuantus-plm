"""Date-effectivity auto-obsolete — where-used impact flags (CAD-PDM C3).

When a date effectivity expires, C3 obsoletes the affected Item (only if it has no
remaining currently-effective version) and **flags** its depth-1 where-used parents
for review — it never cascades an obsolete up the BOM. Each row here is one such
parent flag; it is advisory (a review signal), not a lifecycle transition.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class DateObsoleteImpact(Base):
    """A depth-1 where-used parent flagged because a child's date effectivity expired."""

    __tablename__ = "meta_date_obsolete_impacts"

    id = Column(String, primary_key=True, default=_uuid)
    # The expired effectivity that triggered this flag + the child whose window closed.
    effectivity_id = Column(String, nullable=False, index=True)
    child_item_id = Column(String, nullable=False, index=True)
    # The depth-1 parent that uses the child (flagged for review, NOT obsoleted).
    parent_item_id = Column(String, nullable=False, index=True)
    # Whether the child was promoted to the Obsolete lifecycle state (no effective
    # version remained) or merely marked expired (an effective version still exists).
    child_obsoleted = Column(Boolean, nullable=False, default=False)
    reason = Column(String(200), nullable=True)
    # Review lifecycle of the flag itself: open -> acknowledged.
    state = Column(String(30), nullable=False, default="open", index=True)
    detected_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)
    acknowledged_by_id = Column(
        Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True
    )
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        # One flag per (expired effectivity, parent) — makes re-scan idempotent.
        UniqueConstraint(
            "effectivity_id", "parent_item_id", name="uq_date_obsolete_impact_eff_parent"
        ),
    )
