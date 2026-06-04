"""PLM-COLLAB-P2-C: ECO approval-automation governed projection + notify action.

The first scenario wired onto the (already-complete, MetaSheet-side) approval bridge.
This is the YUANTUS contribution: a GOVERNED, READ-ONLY projection of an ECO into an
"approval context" snapshot, plus a minimal governed NOTIFY action placeholder.

Hard boundaries (owner-ratified):
- The projection is read-only and curated -- ONLY ECO/approval context fields, never
  the writable PLM authoritative machinery (version targets, BOM/routing change
  payloads). PLM stays the source of truth; the snapshot carries ``source_updated_at``
  + ``sync_status`` so a consumer can detect staleness.
- The notify action is a STUB: it writes an AuditLog row and returns a stubbed
  dispatch result. It does NOT call DingTalk, does NOT write back to the ECO, does NOT
  touch any approval/state. Real write-back stays on the existing governed ECO
  approve/reject endpoints (+ the MetaSheet bridge).

This service does NOT gate -- the router enforces the entitlement/admin order
(P2-C F-D). It only projects and records.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.models.eco import ECOApproval
from yuantus.meta_engine.services.eco_service import ECOService
from yuantus.models.audit import AuditLog

# Reuse the single feature key so entitlement converges on one vocabulary.
from yuantus.meta_engine.services.approval_automation_service import FEATURE_KEY  # noqa: F401

TEMPLATE_KEY = "eco_approval"
ACTION_ALLOWLIST = frozenset({"notify"})


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


class ECOApprovalAutomationService:
    """Governed read-only ECO projection + notify-action recorder (no gating here)."""

    def __init__(self, session: Session):
        self.session = session

    def project_context(self, eco_id: str) -> Optional[Dict[str, Any]]:
        """Curated READ-ONLY approval-context snapshot, or None if the ECO is absent.

        Deliberately excludes writable authoritative fields (source/target version
        ids, BOM/routing change payloads, created_by). PLM remains authoritative;
        ``source_updated_at`` + ``sync_status`` mark this as a snapshot.
        """
        eco = ECOService(self.session).get_eco(eco_id)
        if eco is None:
            return None
        approvals = (
            self.session.query(ECOApproval)
            .filter(ECOApproval.eco_id == eco_id)
            .order_by(ECOApproval.created_at)
            .all()
        )
        return {
            "eco_id": eco.id,
            "name": eco.name,
            "description": eco.description,
            "eco_type": eco.eco_type,
            "state": eco.state,
            "priority": eco.priority,
            "kanban_state": eco.kanban_state,
            "product_id": eco.product_id,
            "approval_deadline": _iso(eco.approval_deadline),
            "product_version_before": eco.product_version_before,
            "product_version_after": eco.product_version_after,
            "approvals": [
                {
                    "id": a.id,
                    "stage_id": a.stage_id,
                    "approval_type": a.approval_type,
                    "required_role": a.required_role,
                    "status": a.status,
                    "approved_at": _iso(a.approved_at),
                }
                for a in approvals
            ],
            # govern-projection markers (PLM is SoT; this is a read-only snapshot)
            "source_updated_at": _iso(eco.updated_at),
            "sync_status": "snapshot",
            "template_key": TEMPLATE_KEY,
        }

    def record_notify(
        self, eco_id: str, *, user_id: Optional[int], action: str
    ) -> Optional[Dict[str, Any]]:
        """Record a governed NOTIFY action as an AuditLog row; return a STUB result.

        Assumes the router already enforced admin + entitlement + action allowlist.
        Returns None (without writing audit) if the ECO does not exist, so the router
        can 404 with no audit side effect. Never writes back to the ECO/approval.
        """
        eco = ECOService(self.session).get_eco(eco_id)
        if eco is None:
            return None
        tenant_id, org_id = resolve_license_scope()
        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                org_id=org_id,
                user_id=user_id,
                method="NOTIFY",
                path=f"approvals/automation/eco/{eco_id}/actions:{action}",
                status_code=200,
                duration_ms=0,
            )
        )
        self.session.commit()
        return {
            "accepted": True,
            "dispatch_status": "stubbed",
            "channel": "dingtalk",
            "stub": True,
        }
