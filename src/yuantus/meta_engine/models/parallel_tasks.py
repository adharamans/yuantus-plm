"""
Parallel branch task models.

These tables back the multi-track PLM extensions planned from the Odoo 18 reference:
- multi-site document synchronization
- ECO activity validation gate
- workflow custom actions
- consumption plans
- breakage incident loop
- workorder document package
- 3D metadata overlay
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    JSON,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from yuantus.models.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class RemoteSite(Base):
    __tablename__ = "meta_remote_sites"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(120), nullable=False, unique=True, index=True)
    endpoint = Column(String(500), nullable=False)
    auth_mode = Column(String(50), nullable=False, default="token")
    auth_secret_ciphertext = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    metadata_json = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    last_health_status = Column(String(30), nullable=True)
    last_health_error = Column(Text, nullable=True)
    last_health_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ECOActivityGate(Base):
    __tablename__ = "meta_eco_activity_gates"

    id = Column(String, primary_key=True, default=_uuid)
    eco_id = Column(String, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    status = Column(String(30), nullable=False, default="pending", index=True)
    is_blocking = Column(Boolean, nullable=False, default=True)
    assignee_id = Column(Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True)
    depends_on_activity_ids = Column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    closed_at = Column(DateTime, nullable=True)
    closed_by_id = Column(
        Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ECOActivityGateEvent(Base):
    __tablename__ = "meta_eco_activity_gate_events"

    id = Column(String, primary_key=True, default=_uuid)
    eco_id = Column(String, nullable=False, index=True)
    activity_id = Column(String, nullable=False, index=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=False)
    reason = Column(Text, nullable=True)
    user_id = Column(
        Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkflowCustomActionRule(Base):
    __tablename__ = "meta_workflow_custom_action_rules"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False, unique=True)
    target_object = Column(String(60), nullable=False, default="ECO")
    workflow_map_id = Column(String, nullable=True, index=True)
    from_state = Column(String(120), nullable=True, index=True)
    to_state = Column(String(120), nullable=True, index=True)
    trigger_phase = Column(String(30), nullable=False, default="before")
    action_type = Column(String(80), nullable=False)
    action_params = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    fail_strategy = Column(String(30), nullable=False, default="block")
    is_enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class WorkflowCustomActionRun(Base):
    __tablename__ = "meta_workflow_custom_action_runs"

    id = Column(String, primary_key=True, default=_uuid)
    rule_id = Column(String, nullable=False, index=True)
    object_id = Column(String, nullable=False, index=True)
    target_object = Column(String(60), nullable=False)
    from_state = Column(String(120), nullable=True)
    to_state = Column(String(120), nullable=True)
    trigger_phase = Column(String(30), nullable=False)
    status = Column(String(30), nullable=False, default="completed")
    attempts = Column(Integer, nullable=False, default=1)
    last_error = Column(Text, nullable=True)
    result = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ConsumptionPlan(Base):
    __tablename__ = "meta_consumption_plans"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String(200), nullable=False, index=True)
    state = Column(String(30), nullable=False, default="active", index=True)
    item_id = Column(String, nullable=True, index=True)
    period_unit = Column(String(20), nullable=False, default="week")
    period_start = Column(DateTime, nullable=True)
    period_end = Column(DateTime, nullable=True)
    planned_quantity = Column(Float, nullable=False, default=0.0)
    uom = Column(String(20), nullable=False, default="EA")
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_by_id = Column(
        Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class ConsumptionRecord(Base):
    __tablename__ = "meta_consumption_records"

    id = Column(String, primary_key=True, default=_uuid)
    plan_id = Column(String, nullable=False, index=True)
    source_type = Column(String(60), nullable=False, default="workorder")
    source_id = Column(String(120), nullable=True, index=True)
    actual_quantity = Column(Float, nullable=False, default=0.0)
    recorded_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    # MES-ingestion idempotency key (ECM-style per-event identity). NULL for the
    # manual `/actuals` path (human entries are intentionally never deduped);
    # the MES ingestion route sets the R1-derived sha256 hex. `nullable + unique`
    # gives the wanted semantics on both SQLite and Postgres (NULLs compare
    # unequal so legacy/manual rows coexist; non-null keys are globally unique),
    # mirroring this table's own BreakageIncident.eco_id / incident_code idiom.
    idempotency_key = Column(String(64), nullable=True, unique=True, index=True)


class MesConsumptionInbox(Base):
    """Durable inbound queue for async MES consumption ingestion (Consumption R2.5,
    default-OFF). When `MES_INGEST_ASYNC` is on, the route persists the validated
    raw event here and returns 202; the inbox worker drains it through the same
    `ConsumptionPlanService.ingest_mes_consumption` as the sync path. The unique
    `idempotency_key` makes accept idempotent (a replay is one inbox row)."""

    __tablename__ = "meta_mes_consumption_inbox"

    id = Column(String, primary_key=True, default=_uuid)
    # The R1-derived key = sha256(plan_id|source_type|mes_event_id); unique so a
    # replayed delivery maps to one inbox row.
    idempotency_key = Column(String(64), nullable=False, unique=True, index=True)
    # Raw validated event (original uom/qty kept; conversion happens at PROCESS).
    plan_id = Column(String, nullable=False, index=True)
    mes_event_id = Column(String(200), nullable=False)
    source_type = Column(String(60), nullable=False)
    source_id = Column(String(120), nullable=True)
    actual_quantity = Column(Float, nullable=False, default=0.0)
    uom = Column(String(20), nullable=True)
    recorded_at = Column(DateTime, nullable=True)
    attributes = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    # Lifecycle: pending -> processed | conflict | failed (retryable reschedules).
    state = Column(String(30), nullable=False, default="pending", index=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    next_attempt_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    worker_id = Column(String, nullable=True)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    error = Column(Text, nullable=True)
    # The ConsumptionRecord this inbox row produced (set on processed).
    record_id = Column(String, nullable=True)
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), nullable=True)


class BreakageIncident(Base):
    __tablename__ = "meta_breakage_incidents"

    id = Column(String, primary_key=True, default=_uuid)
    incident_code = Column(String(40), nullable=True, unique=True, index=True)
    product_item_id = Column(String, nullable=True, index=True)
    bom_id = Column(String, nullable=True, index=True)
    bom_line_item_id = Column(String, nullable=True, index=True)
    production_order_id = Column(String(120), nullable=True, index=True)
    version_id = Column(String, nullable=True, index=True)
    mbom_id = Column(String, nullable=True, index=True)
    routing_id = Column(String(120), nullable=True, index=True)
    batch_code = Column(String(120), nullable=True, index=True)
    customer_name = Column(String(200), nullable=True, index=True)
    description = Column(Text, nullable=False)
    responsibility = Column(String(120), nullable=True)
    status = Column(String(30), nullable=False, default="open", index=True)
    severity = Column(String(30), nullable=False, default="medium", index=True)
    # Durable design-loopback ECO link (Tier-B #3 §3.2, taskbook
    # `3e5104f`). Bare String soft-link — NO ForeignKey, matching
    # this table's existing convention (product_item_id / bom_id /
    # version_id) and sidestepping the tenant-baseline FK-ordering
    # problem (meta_breakage_incidents is created before meta_ecos).
    # UNIQUE is a cross-incident data-integrity backstop only; the
    # race-safety mechanism is the compare-and-swap UPDATE in
    # BreakageIncidentService.create_breakage_design_loopback_eco.
    eco_id = Column(String, nullable=True, unique=True, index=True)
    created_by_id = Column(
        Integer, ForeignKey("rbac_users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )


class WorkorderDocumentLink(Base):
    __tablename__ = "meta_workorder_document_links"

    id = Column(String, primary_key=True, default=_uuid)
    routing_id = Column(String, nullable=False, index=True)
    operation_id = Column(String, nullable=True, index=True)
    document_item_id = Column(String, nullable=False, index=True)
    inherit_to_children = Column(Boolean, nullable=False, default=True)
    visible_in_production = Column(Boolean, nullable=False, default=True)
    document_version_id = Column(String, nullable=True, index=True)
    version_locked_at = Column(DateTime, nullable=True)
    version_lock_source = Column(String(40), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "routing_id",
            "operation_id",
            "document_item_id",
            name="uq_workorder_doc_link_scope",
        ),
    )


class ThreeDOverlay(Base):
    __tablename__ = "meta_3d_overlays"

    id = Column(String, primary_key=True, default=_uuid)
    document_item_id = Column(String, nullable=False, unique=True, index=True)
    version_label = Column(String(120), nullable=True)
    status = Column(String(60), nullable=True, index=True)
    visibility_role = Column(String(120), nullable=True)
    part_refs = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    properties = Column(JSON().with_variant(JSONB, "postgresql"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
