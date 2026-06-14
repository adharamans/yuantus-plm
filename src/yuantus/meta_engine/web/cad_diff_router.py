from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.meta_engine.models.file import FileContainer
from yuantus.meta_engine.services.job_errors import JobFatalError
from yuantus.meta_engine.tasks.cad_pipeline_tasks import render_containers_visual_diff

logger = logging.getLogger(__name__)

cad_diff_router = APIRouter(prefix="/cad", tags=["CAD"])


class CadDiffResponse(BaseModel):
    file_id: str
    other_file_id: str
    properties: Dict[str, Any]
    cad_document_schema_version: Dict[str, Optional[int]]


def _diff_dicts(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    added = {key: after[key] for key in after.keys() - before.keys()}
    removed = {key: before[key] for key in before.keys() - after.keys()}
    changed = {
        key: {"from": before[key], "to": after[key]}
        for key in before.keys() & after.keys()
        if before[key] != after[key]
    }
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
    }


@cad_diff_router.get("/files/{file_id}/diff", response_model=CadDiffResponse)
def diff_cad_properties(
    file_id: str,
    other_file_id: Optional[str] = Query(
        None, description="Compare against this file id"
    ),
    other_id: Optional[str] = Query(
        None, description="Legacy alias for other_file_id"
    ),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CadDiffResponse:
    resolved_other_file_id = other_file_id or other_id
    if not resolved_other_file_id:
        raise HTTPException(
            status_code=422,
            detail="other_file_id is required",
        )

    file_container = db.get(FileContainer, file_id)
    other_container = db.get(FileContainer, resolved_other_file_id)
    if not file_container or not other_container:
        raise HTTPException(status_code=404, detail="File not found")

    before = file_container.cad_properties or {}
    after = other_container.cad_properties or {}
    return CadDiffResponse(
        file_id=file_container.id,
        other_file_id=other_container.id,
        properties=_diff_dicts(before, after),
        cad_document_schema_version={
            "from": file_container.cad_document_schema_version,
            "to": other_container.cad_document_schema_version,
        },
    )


@cad_diff_router.get("/files/{file_id}/visual-diff")
def visual_diff_cad_render(
    file_id: str,
    other_file_id: Optional[str] = Query(
        None, description="Compare against this file id (Rev B)"
    ),
    other_id: Optional[str] = Query(
        None, description="Legacy alias for other_file_id"
    ),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Version VISUAL diff (L1): render Rev A (file_id) and Rev B
    (other_file_id) and return a 3-colour overlay PNG plus X-Diff-* summary
    headers, via the VemCAD render service POST /diff. Distinct from
    /files/{id}/diff above, which compares CAD *properties*. Read-only; disabled
    (503) unless RENDER_SERVICE_BASE_URL is configured."""
    settings = get_settings()
    if not settings.RENDER_SERVICE_BASE_URL:
        raise HTTPException(status_code=503, detail="render service not configured")

    resolved_other_file_id = other_file_id or other_id
    if not resolved_other_file_id:
        raise HTTPException(status_code=422, detail="other_file_id is required")

    file_container = db.get(FileContainer, file_id)
    other_container = db.get(FileContainer, resolved_other_file_id)
    if not file_container or not other_container:
        raise HTTPException(status_code=404, detail="File not found")

    for container in (file_container, other_container):
        if (container.get_extension() or "").lower() != "dxf":
            raise HTTPException(
                status_code=422,
                detail="visual diff v0 requires DXF on both revisions",
            )

    try:
        result = render_containers_visual_diff(
            db, file_container, other_container, authorization=None
        )
    except (JobFatalError, FileNotFoundError) as exc:
        # A source blob is missing from our own storage — that's our-data-gone,
        # not an upstream render failure, so 404 rather than a misleading 502.
        logger.warning("CAD visual diff source missing: %s", exc)
        raise HTTPException(status_code=404, detail="source file missing") from exc
    except Exception as exc:  # render service / breaker / IO failure → degrade
        logger.warning("CAD visual diff failed: %s", exc)
        raise HTTPException(status_code=502, detail="visual diff render failed") from exc

    return Response(
        content=result.content,
        media_type=result.content_type or "application/octet-stream",
        headers=result.summary,
    )
