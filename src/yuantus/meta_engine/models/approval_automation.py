"""PLM-COLLAB-P2-B: provisioned approval-automation template drafts.

One tenant-scoped row per provisioned template. Templates are CONFIG SKELETONS
only -- P2-B provisions ``draft`` rows; it does NOT enable or execute them (that is
P2-C/P2-D). The ``(tenant_id, template_key)`` unique constraint is what makes
provisioning idempotent: re-provisioning the same scenario upserts the existing
draft instead of creating a duplicate.

This is a NEW dedicated table, deliberately NOT reusing the generic
``WorkflowCustomActionRule`` -- the owner ruled that overloading a generic
action-rule schema with PLM approval-automation semantics tangles the two domains.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


class ApprovalAutomationTemplate(Base):
    __tablename__ = "meta_approval_automation_templates"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

    # Tenant scope. Always concrete -- provisioning resolves it via
    # resolve_license_scope(), so there is no NULL-tenant draft.
    tenant_id = Column(String(120), nullable=False, default="default", index=True)

    # The scenario key (e.g. "eco_approval"); see service TEMPLATE_DEFINITIONS.
    template_key = Column(String(100), nullable=False, index=True)

    # Lifecycle. P2-B only ever writes "draft"; "enabled" is reserved for a later
    # slice (P2-C/D) -- provisioning never enables or executes.
    state = Column(String(20), nullable=False, default="draft")

    # The template config skeleton (illustrative; not an execution spec in P2-B).
    definition_json = Column(JSON().with_variant(JSONB, "postgresql"), default=dict)

    version = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "template_key",
            name="uq_approval_automation_template_scope",
        ),
    )
