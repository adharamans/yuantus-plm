"""WP1.2 PR 2/2: stale-drawings thin slice.

Scans an assembly (root + ASSEMBLY descendants) for drawings whose WP1.3
``needs_update`` flag is set. It is **read-only**: it reuses the materialized
``ItemFile.needs_update`` and NEVER recomputes provenance / touches staleness
core. To refresh a verdict, call the existing single-item
``POST /cad/items/{id}/staleness/recompute``.

Cost: the unique Part set comes from ``RelationshipService.get_reachable_items``
(bounded O(V+E) visited-set BFS -- no diamond re-explosion), and the stale drawings
come from a single indexed batch query per chunk (``needs_update`` is indexed), so
there is no per-part N+1 and no full-tree materialization.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from sqlalchemy.orm import Session

from yuantus.meta_engine.models.file import (
    DocumentType,
    FileContainer,
    FileRole,
    ItemFile,
)
from yuantus.meta_engine.relationship.service import RelationshipService

_DOC_2D = DocumentType.CAD_2D.value  # "2d"
_DRAWING_ROLES = [FileRole.DRAWING.value, FileRole.NATIVE_CAD.value]
_IN_CHUNK = 500  # keep the IN() clause well under SQLite's parameter cap


def _chunks(seq: List[str], size: int = _IN_CHUNK) -> Iterable[List[str]]:
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


class CadStaleDrawingsService:
    def __init__(self, session: Session):
        self.session = session

    def scan(self, root_id: str, max_depth: int = 10) -> Dict[str, Any]:
        reachable = RelationshipService(self.session).get_reachable_items(
            root_id, max_depth=max_depth
        )
        part_ids = [r["item_id"] for r in reachable]
        meta_by_part = {r["item_id"]: r for r in reachable}

        drawings: List[Dict[str, Any]] = []
        for chunk in _chunks(part_ids):
            rows = (
                self.session.query(ItemFile, FileContainer)
                .join(FileContainer, FileContainer.id == ItemFile.file_id)
                .filter(
                    ItemFile.item_id.in_(chunk),
                    ItemFile.needs_update.is_(True),
                    FileContainer.document_type == _DOC_2D,
                    ItemFile.file_role.in_(_DRAWING_ROLES),
                )
                .all()
            )
            for itf, _fc in rows:
                meta = meta_by_part.get(itf.item_id, {})
                drawings.append(
                    {
                        "part_id": itf.item_id,
                        "part_number": meta.get("item_number"),
                        "path": meta.get("first_path", []),
                        "relationship_path": meta.get("first_relationship_path", []),
                        "drawing_file_id": itf.file_id,
                        "file_role": itf.file_role,
                        "needs_update": True,
                        "staleness_reason": itf.staleness_reason,
                        "source_batch_id": itf.source_batch_id,
                        "import_batch_id": itf.import_batch_id,
                    }
                )

        return {
            "root_id": root_id,
            "scanned_parts": len(part_ids),
            "stale_count": len(drawings),
            "drawings": drawings,
        }
