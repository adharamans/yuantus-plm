"""PLM-COLLAB-P3-A: governed READ-ONLY BOM-context projection for the multi-table review.

Mirrors the P2-C ECO projection with the object swapped to BOM/Part. REUSES BOMService's
proven BOM read (`get_tree`) — the BOM line is a "Part BOM" relationship-Item
(`Item.source_id`/`related_id` + `properties`; the legacy `Relationship` class is NOT the
current BOM source) — and CURATES the result: it projects ONLY review fields and NEVER the
raw `Item.to_dict()` internals (`config_id` / `current_version_id` / `source_id` /
`related_id` / `permission_id` / `owner_id` …). Read-only: no write-back, no audit, no embed.

This service does NOT gate -- the router enforces the entitlement/permission order (P3-A:
auth -> is_entitled -> query part -> PLM read permission -> project).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from yuantus.meta_engine.services.bom_service import BOMService

FEATURE_KEY = "bom_multitable"
TEMPLATE_KEY = "bom_review"

# Curated review-field allowlists. ONLY these keys are projected; everything else from the
# raw node (config_id/current_version_id/source_id/related_id/permission_id/…) is dropped.
_PART_FIELDS = ("item_number", "name", "state", "generation")
_LINE_ITEM_FIELDS = ("item_number", "name", "state", "generation")
_LINE_REL_FIELDS = ("quantity", "uom", "find_num", "refdes")


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, datetime) else value


def _curate_item(node: Dict[str, Any], fields: tuple) -> Dict[str, Any]:
    return {key: node.get(key) for key in fields}


class BOMMultitableProjectionService:
    """Curated read-only BOM-context projection (no gating here; the router gates)."""

    def __init__(self, session: Session):
        self.session = session

    def project_context(self, part_id: str) -> Dict[str, Any]:
        """Curated read-only snapshot of a part + its direct BOM lines for a review table.

        Assumes the router already enforced entitlement + part existence + read permission.
        Reuses BOMService.get_tree (depth=1 = direct children) and projects ONLY the review
        fields, with 铁律-5 provenance markers (source_version/source_updated_at/sync_status)
        so the consumer can detect staleness. PLM stays authoritative.
        """
        tree = BOMService(self.session).get_tree(part_id, depth=1)
        part = {"part_id": tree.get("id"), **_curate_item(tree, _PART_FIELDS)}
        lines: List[Dict[str, Any]] = []
        for child_node in tree.get("children") or []:
            rel_props = (child_node.get("relationship") or {}).get("properties") or {}
            child = child_node.get("child") or {}
            line = _curate_item(child, _LINE_ITEM_FIELDS)
            line.update({key: rel_props.get(key) for key in _LINE_REL_FIELDS})
            lines.append(line)
        return {
            "part": part,
            "lines": lines,
            # govern-projection markers (PLM is SoT; this is a read-only snapshot)
            "source_version": tree.get("generation"),
            "source_updated_at": _iso(tree.get("modified_on")),
            "sync_status": "snapshot",
            "template_key": TEMPLATE_KEY,
        }
