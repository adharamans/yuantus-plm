"""PLM-COLLAB-P3-A: governed READ-ONLY BOM-context projection for the multi-table review.

Mirrors the P2-C ECO projection with the object swapped to BOM/Part. REUSES BOMService's
proven BOM read (`get_tree`) — the BOM line is a "Part BOM" relationship-Item
(`Item.source_id`/`related_id` + `properties`; the legacy `Relationship` class is NOT the
current BOM source) — and CURATES the result: it projects ONLY review fields (plus the
read-only ID technical keys below) and NEVER the raw `Item.to_dict()` version-control /
permission internals (`config_id` / `current_version_id` / `source_id` / `related_id` /
`permission_id` / `owner_id` …). Read-only: no write-back, no audit, no embed.

Shape (owner-ratified): the FULL BOM tree (depth = -1, NOT truncated — P3-A semantics are
"the whole tree"; if a pathological depth ever needs capping that is a later pagination/limit
slice, never a silent v1 drop), FLATTENED pre-order into a review table — `part` is the root
context row, `lines` is every descendant BOM line. Each row carries:

- `part_id` — the row's Part Item id, a READ-ONLY TECHNICAL KEY (owner-released past the
  display allowlist): P3-C needs a STABLE locator to attach its collaboration fields / row
  state, which item_number can't be (duplicate / re-numbered / renamed parts). NOT an
  editable PLM field.
- `level` (1 = direct child) + `path` — the ancestor **Part-id** chain (root first), the
  stable hierarchy key (`path[-1]` == the parent row's `part_id`). Plus `path_labels`, the
  parallel ancestor **item_number** chain for display only.

Unbounded depth is safe here the same way `report_service`'s existing `levels=-1` BOM reads
are: `BOMService.add_child` rejects cycles at write time (`detect_cycle_with_path` ->
`CycleDetectedError`), so a service-built BOM is a DAG.

铁律-5 provenance (`source_version`/`source_updated_at`/`sync_status`) is carried BOTH at the
top-level envelope (the root Part's, retained as the overall snapshot marker) AND on EVERY
line, so the consumer can detect staleness per row. `source_updated_at` falls back
`modified_on || created_on` (Item.updated_at has no default, so a freshly-created-unmodified
row would otherwise be null); per line it is the LATER of the child-Item's and the
relationship-Item's last touch, so a quantity edit on the relationship OR a state/generation
change on the child both surface as staleness. PLM stays authoritative; read-only snapshot.

This service does NOT gate -- the router enforces the entitlement/permission order (P3-A:
auth -> is_entitled -> query part -> Part-type guard -> PLM read permission -> project).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from yuantus.meta_engine.services.bom_service import BOMService

FEATURE_KEY = "bom_multitable"
TEMPLATE_KEY = "bom_review"

# Read the WHOLE BOM tree (no cap, no silent truncation). Safe because add_child rejects
# cycles at write time (DAG), matching report_service's existing `levels=-1` reads.
READ_DEPTH = -1

# Curated review-field allowlists. ONLY these keys (+ the ID technical keys set explicitly
# below) are projected; everything else from the raw node (config_id/current_version_id/
# source_id/related_id/permission_id/…) is dropped.
_PART_FIELDS = ("item_number", "name", "state", "generation")
_LINE_ITEM_FIELDS = ("item_number", "name", "state", "generation")
_LINE_REL_FIELDS = ("quantity", "uom", "find_num", "refdes")


def _curate_item(node: Dict[str, Any], fields: tuple) -> Dict[str, Any]:
    return {key: node.get(key) for key in fields}


def _updated_at(node: Dict[str, Any]) -> Optional[str]:
    # Item.updated_at has no default (item.py) -> modified_on is null until first update;
    # fall back to created_on so a fresh row still carries a real provenance timestamp.
    return node.get("modified_on") or node.get("created_on")


def _row_provenance(version: Any, updated_at: Optional[str]) -> Dict[str, Any]:
    return {
        "source_version": version,
        "source_updated_at": updated_at,
        "sync_status": "snapshot",
    }


def _latest(*timestamps: Optional[str]) -> Optional[str]:
    # ISO-8601 UTC strings (func.now()-sourced) compare lexically == chronologically.
    present = [t for t in timestamps if t]
    return max(present) if present else None


class BOMMultitableProjectionService:
    """Curated read-only flattened-BOM-tree projection (no gating here; the router gates)."""

    def __init__(self, session: Session):
        self.session = session

    def project_context(self, part_id: str) -> Dict[str, Any]:
        """Curated read-only snapshot of a part + its FULL (flattened) BOM tree.

        Assumes the router already enforced entitlement + part existence + Part-type +
        read permission. Reuses BOMService.get_tree (depth=-1, whole tree) and projects ONLY
        the review fields + ID technical keys, with per-row 铁律-5 provenance.
        """
        tree = BOMService(self.session).get_tree(part_id, depth=READ_DEPTH)
        part = {"part_id": tree.get("id"), **_curate_item(tree, _PART_FIELDS)}
        lines: List[Dict[str, Any]] = []
        self._flatten(
            tree,
            level=1,
            path=[tree.get("id")],
            path_labels=[tree.get("item_number")],
            out=lines,
        )
        return {
            "part": part,
            "lines": lines,
            # Top-level (envelope) provenance describes the root Part -- RETAINED as the
            # overall snapshot marker; each line ALSO carries its own provenance below, so
            # staleness is detectable both overall and per row.
            **_row_provenance(tree.get("generation"), _updated_at(tree)),
            "template_key": TEMPLATE_KEY,
        }

    def _flatten(
        self,
        node: Dict[str, Any],
        level: int,
        path: List[Any],
        path_labels: List[Any],
        out: List[Dict[str, Any]],
    ) -> None:
        """Pre-order walk: emit one curated row per descendant BOM line, deepest last.

        `node` is a BOMService tree node (a `to_dict` dict + ``children`` of
        ``{"relationship": <rel-Item dict + properties>, "child": <child node>}``). `path` /
        `path_labels` are the ancestor part-id / item_number chains ending at this node; each
        emitted line records the chains to its immediate parent and recurses with the child's
        id / item_number appended.
        """
        for child_node in node.get("children") or []:
            rel = child_node.get("relationship") or {}
            rel_props = rel.get("properties") or {}
            child = child_node.get("child") or {}

            line = {"part_id": child.get("id"), **_curate_item(child, _LINE_ITEM_FIELDS)}
            line.update({key: rel_props.get(key) for key in _LINE_REL_FIELDS})
            line["level"] = level
            line["path"] = list(path)  # ancestor part-ids (stable hierarchy key)
            line["path_labels"] = list(path_labels)  # ancestor item_numbers (display only)
            # per-line provenance: child generation is the displayed version; staleness is
            # the LATER of the child's and the relationship-Item's last touch.
            line.update(
                _row_provenance(
                    child.get("generation"),
                    _latest(_updated_at(child), _updated_at(rel)),
                )
            )
            out.append(line)

            self._flatten(
                child,
                level + 1,
                path + [child.get("id")],
                path_labels + [child.get("item_number")],
                out,
            )
