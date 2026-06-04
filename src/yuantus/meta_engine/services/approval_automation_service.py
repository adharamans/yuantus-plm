"""PLM-COLLAB-P2-B: approval-automation template registry + provisioning.

The 2-3 PLM scenario templates are CODE-defined skeletons (``TEMPLATE_DEFINITIONS``).
Provisioning is an idempotent get-or-create of ``draft`` rows scoped to the current
tenant -- it never enables or executes (that is P2-C/P2-D), never touches DingTalk,
never mutates a production approval flow.

Provisioning is GATED: ``provision()`` requires
``EntitlementService.is_entitled("approval_automation")`` -- the single entitlement
check -- and raises ``EntitlementRequiredError`` otherwise, so the write path cannot
be bypassed even by a non-router caller. ``license_data`` is never an authorization
source.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.models.approval_automation import ApprovalAutomationTemplate

FEATURE_KEY = "approval_automation"

# Code-defined scenario skeletons. CONFIG ONLY -- the ``rules`` are illustrative
# affordances describing what the automation WOULD do, NOT an execution spec in P2-B
# (no engine, no DingTalk, no write-back). A later slice (P2-C/D) consumes them.
TEMPLATE_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "eco_approval": {
        "name": "ECO Approval Automation",
        "scenario": "eco",
        "description": "Reminders + escalation for engineering change order approvals.",
        "rules": [
            {"trigger": "approval_due_soon", "action": "notify_assignee"},
            {"trigger": "approval_overdue", "action": "escalate_to_manager"},
            {"trigger": "approval_rejected", "action": "notify_originator"},
        ],
    },
    "bom_change_approval": {
        "name": "BOM Change Approval Automation",
        "scenario": "bom_change",
        "description": "Reminders + escalation for bill-of-materials change approvals.",
        "rules": [
            {"trigger": "approval_due_soon", "action": "notify_assignee"},
            {"trigger": "approval_overdue", "action": "escalate_to_manager"},
        ],
    },
    "document_release_approval": {
        "name": "Document Release Approval Automation",
        "scenario": "document_release",
        "description": "Reminders + escalation for document release approvals.",
        "rules": [
            {"trigger": "approval_due_soon", "action": "notify_assignee"},
            {"trigger": "approval_overdue", "action": "escalate_to_manager"},
        ],
    },
}


class EntitlementRequiredError(Exception):
    """Raised when a gated operation (provision) runs without the entitlement."""

    def __init__(self, feature_key: str):
        self.feature_key = feature_key
        super().__init__(f"feature not entitled: {feature_key!r}")


def _serialize(row: ApprovalAutomationTemplate) -> Dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "template_key": row.template_key,
        "state": row.state,
        "version": row.version,
        "definition": row.definition_json,
    }


class ApprovalAutomationService:
    """Registry of approval-automation template definitions + draft provisioning."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def list_definitions() -> List[Dict[str, Any]]:
        """The available scenario template definitions (static; not tenant-scoped)."""
        return [{"template_key": key, **body} for key, body in TEMPLATE_DEFINITIONS.items()]

    def list_provisioned(self) -> List[Dict[str, Any]]:
        """The drafts already provisioned for the current tenant."""
        tenant_id, _org = resolve_license_scope()
        rows = (
            self.session.query(ApprovalAutomationTemplate)
            .filter(ApprovalAutomationTemplate.tenant_id == tenant_id)
            .order_by(ApprovalAutomationTemplate.template_key)
            .all()
        )
        return [_serialize(row) for row in rows]

    def provision(self) -> List[Dict[str, Any]]:
        """Idempotently provision DRAFT rows for every scenario template.

        GATED: requires ``is_entitled("approval_automation")`` -- raises
        ``EntitlementRequiredError`` otherwise, so the write path cannot be bypassed.
        Get-or-create per ``(tenant_id, template_key)``: re-provisioning returns the
        existing draft (the unique constraint guarantees no duplicate row). Only ever
        writes ``state="draft"``; never enables or executes.

        Concurrency-safe: two simultaneous provisions can both pass the existence
        query and race to INSERT. The unique constraint lets at most one win; the
        loser's commit raises IntegrityError, which we roll back and absorb -- then
        re-read so both callers return the same authoritative draft set.
        """
        if not EntitlementService(self.session).is_entitled(FEATURE_KEY):
            raise EntitlementRequiredError(FEATURE_KEY)
        tenant_id, _org = resolve_license_scope()
        for template_key, definition in TEMPLATE_DEFINITIONS.items():
            existing = (
                self.session.query(ApprovalAutomationTemplate)
                .filter(
                    ApprovalAutomationTemplate.tenant_id == tenant_id,
                    ApprovalAutomationTemplate.template_key == template_key,
                )
                .first()
            )
            if existing is None:
                self.session.add(
                    ApprovalAutomationTemplate(
                        id=uuid.uuid4().hex,
                        tenant_id=tenant_id,
                        template_key=template_key,
                        state="draft",
                        version=1,
                        definition_json=definition,
                    )
                )
        try:
            self.session.commit()
        except IntegrityError:
            # A concurrent provision won the (tenant_id, template_key) race; drop our
            # duplicate inserts and fall through to read the committed state.
            self.session.rollback()
        # Re-read so success and lost-race both return the authoritative draft set.
        return self.list_provisioned()
