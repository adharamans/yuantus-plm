"""PLM-COLLAB BOM multi-table routes (P3-A projection + P3-D1 embed-token mint).

``GET /api/v1/bom/multitable/{part_id}/context`` (P3-A) -- a governed READ-ONLY projection of
a part + its FULL (flattened) BOM tree into a review-table context (the consumer side is
P3-C). READ-ONLY: no write-back, no audit.

``POST /api/v1/bom/multitable/{part_id}/embed-token`` (P3-D1) -- mints a short-lived Ed25519
embed token so the BOM review can later be shown INSIDE the PLM UI via an embedded MetaSheet
iframe (the consumer-side verifier + host is P3-D2). This route DOES audit the issuance.

Both share the PINNED gate: authenticate (get_current_user) -> is_entitled("bom_multitable")
-> ONLY THEN query the part -> Part-type guard -> PLM read permission. An unentitled caller
gets a null affordance (``context:null`` / ``embed_token:null``) and the part is NEVER queried,
so object existence is not leaked. The entitlement gate is the single ``is_entitled`` check (no
second license read, no ``license_data`` authorization). ``bom_multitable`` is lit (P3-B) to
its own SKU ``plm.bom_multitable``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.lifecycle.guard import is_item_locked
from yuantus.meta_engine.models.item import Item
from yuantus.meta_engine.models.meta_schema import ItemType
from yuantus.meta_engine.schemas.aml import AMLAction
from yuantus.meta_engine.services.bom_multitable_embed_token_service import (
    EmbedTokenNotConfigured,
    is_origin_allowed,
    mint_embed_token,
)
from yuantus.meta_engine.services.bom_multitable_projection_service import (
    BOM_LINE_TYPE,
    FEATURE_KEY,
    BOMMultitableProjectionService,
)
from yuantus.meta_engine.services.bom_multitable_writeback_service import (
    WRITE_FEATURE_KEY,
    WRITE_WHITELIST,
    BomLineNotInPartError,
    BomLineWritebackConflictError,
    BomLineWritebackPreconditionFailedError,
    BOMMultitableWritebackService,
    bom_line_write_etag,
)
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService
from yuantus.models.audit import AuditLog

bom_multitable_router = APIRouter(prefix="/bom", tags=["BOM"])


class EmbedTokenRequest(BaseModel):
    """The PLM UI requests an embed token for a specific target origin (the iframe host)."""

    origin: str


class BomLineWriteRequest(BaseModel):
    """Whitelisted write-back of a single BOM multi-table line's editable cells.

    All four are OPTIONAL; only fields the consumer actually sets are applied
    (``exclude_unset``), so a partial PATCH never clobbers untouched cells. ``quantity`` is
    typed ``Any`` to accept a number or numeric string, but the handler rejects a non-scalar
    quantity (object/array/bool) at the 400 gate; the others are strings. Unknown
    keys are dropped by the whitelist (defense-in-depth in the write service too); a body that
    whitelists to nothing is a malformed/empty write -> 400.
    """

    quantity: Optional[Any] = None
    uom: Optional[str] = None
    find_num: Optional[str] = None
    refdes: Optional[str] = None


def _affordance(entitled: bool) -> Dict[str, Any]:
    return {
        "feature_key": FEATURE_KEY,
        "entitled": entitled,
        "upgrade": {"available": not entitled},
    }


@bom_multitable_router.get("/multitable/{part_id}/context")
def bom_multitable_context(
    part_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Read-only BOM-context projection. PINNED order: auth -> entitled -> part -> Part-type
    -> permission.

    Unentitled -> ``context: null`` + upgrade affordance, WITHOUT touching the part (no
    existence leak). Entitled but part absent -> 404; a non-Part Item -> 400; read permission
    denied -> 403.
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        # Do NOT look up the part -- unentitled callers must not learn whether it exists.
        return {**_affordance(False), "context": None}

    root = db.get(Item, part_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Part not found")
    # The endpoint is {part_id} and the projection is a Part BOM review; a non-Part Item is
    # a bad request (mirrors bom_tree_router's `item_type_id != "Part"` 400 guard). Parts
    # (and BOM-line targets) are item_type_id == "Part" throughout the engine.
    if root.item_type_id != "Part":
        raise HTTPException(status_code=400, detail="Item is not a Part")

    perm = MetaPermissionService(db)
    user_id = str(user.id)
    if not perm.check_permission(
        root.item_type_id, AMLAction.get, user_id=user_id, user_roles=user.roles
    ) or not perm.check_permission(
        BOM_LINE_TYPE, AMLAction.get, user_id=user_id, user_roles=user.roles
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    context = BOMMultitableProjectionService(db).project_context(part_id)
    return {**_affordance(True), "context": context}


@bom_multitable_router.post("/multitable/{part_id}/embed-token")
def bom_multitable_embed_token(
    part_id: str,
    body: EmbedTokenRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """PLM-COLLAB-P3-D1: mint a short-lived Ed25519 embed token for a Part's BOM review.

    PINNED order (mirrors the GET gate, then mints): auth -> is_entitled -> part -> Part-type
    -> read permission -> [mint configured? else 503 fail-closed] -> origin allowlist (403)
    -> mint + jti audit. Unentitled -> ``embed_token: null`` + upgrade affordance, WITHOUT
    touching the part (no existence leak) and WITHOUT minting. The token authorizes nothing but
    the P3-A read projection; the consumer (P3-D2) verifies it offline with the PUBLIC key.
    """
    if not EntitlementService(db).is_entitled(FEATURE_KEY):
        # Do NOT look up the part -- unentitled callers get no token and learn nothing.
        return {**_affordance(False), "embed_token": None}

    root = db.get(Item, part_id)
    if root is None:
        raise HTTPException(status_code=404, detail="Part not found")
    if root.item_type_id != "Part":
        raise HTTPException(status_code=400, detail="Item is not a Part")

    perm = MetaPermissionService(db)
    user_id = str(user.id)
    if not perm.check_permission(
        root.item_type_id, AMLAction.get, user_id=user_id, user_roles=user.roles
    ) or not perm.check_permission(
        BOM_LINE_TYPE, AMLAction.get, user_id=user_id, user_roles=user.roles
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    settings = get_settings()
    # fail-closed: a deployment without a signing key cannot mint
    if not settings.EMBED_TOKEN_SIGNING_KEY:
        raise HTTPException(status_code=503, detail="embed token minting unavailable")

    origin = (body.origin or "").strip()
    if not is_origin_allowed(origin, settings.EMBED_ALLOWED_ORIGINS):
        raise HTTPException(status_code=403, detail="embed origin not allowed")

    tenant_id, org_id = resolve_license_scope()
    try:
        minted = mint_embed_token(
            user_id=user.id,
            tenant_id=tenant_id,
            org_id=org_id,
            part_id=part_id,
            origin=origin,
            audience=settings.EMBED_TOKEN_AUDIENCE,
            signing_key_b64=settings.EMBED_TOKEN_SIGNING_KEY,
            key_id=settings.EMBED_TOKEN_KEY_ID,
            ttl_seconds=settings.EMBED_TOKEN_TTL_SECONDS,
        )
    except EmbedTokenNotConfigured as exc:
        # unset OR invalid signing key (malformed base64 / wrong-length seed) -> fail closed,
        # never a 500 that leaks the deployment's key state.
        raise HTTPException(status_code=503, detail="embed token minting unavailable") from exc

    # jti-trackable audit of the issuance; NEVER record the token itself.
    db.add(
        AuditLog(
            method="MINT",
            path=f"/api/v1/bom/multitable/{part_id}/embed-token?jti={minted['jti']}",
            status_code=200,
            duration_ms=0,
            user_id=int(user.id),
            tenant_id=tenant_id,
            org_id=org_id,
        )
    )
    db.commit()

    return {
        **_affordance(True),
        "embed_token": minted["token"],
        "token_type": "embed",
        "expires_in": minted["expires_in"],
        "jti": minted["jti"],
        "aud": minted["aud"],
        "embed_origin": origin,
    }


@bom_multitable_router.patch("/multitable/{part_id}/lines/{bom_line_id}")
def bom_multitable_write_line(
    part_id: str,
    bom_line_id: str,
    body: BomLineWriteRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    if_match: Optional[str] = Header(default=None, alias="If-Match"),
) -> Dict[str, Any]:
    """PLM-COLLAB Phase-7 write-back: PATCH the editable cells of one governed BOM line.

    Honors the metasheet2 consumer pact (#3332): entitled + permitted -> 200
    ``{"ok": true, "bom_line_id": "..."}`` (THIN -- NOT the GET/embed affordance envelope).
    EXACT guard order (design-resolution 20260629 §1):

    1. ``401`` unauthenticated (the ``get_current_user`` dependency).
    2. ``403`` NOT ``is_entitled(WRITE_FEATURE_KEY)`` -- the DISTINCT write SKU, checked FIRST
       so a write surface never leaks object existence to an unentitled caller (no affordance).
    3. ``403`` NOT ``check_permission("Part BOM", AMLAction.update)``.
    4. ``400`` malformed / empty whitelist OR MISSING ``Idempotency-Key`` header -- fail-fast,
       fail-closed, BEFORE any object lookup.
    5. ``404`` part missing OR line ∉ part (``item_type_id == "Part BOM"`` AND
       ``source_id == part_id``) -- the three cases are indistinguishable.
    6. ``409`` parent lifecycle-locked (``is_item_locked`` on the PARENT part; the
       ``add_bom_child`` precedent) -- a Released/locked BOM is the deferred ECO route.
    7. optional ``If-Match`` optimistic-concurrency guard (T6): stale new writes -> 412,
       identical idempotency replays still return cached 200.
    8. apply: single-use replay cache (P2) + audit (P3) + property mutation, atomic.
    """
    # (2) write entitlement FIRST -- no existence-leak on a write surface, no affordance body.
    if not EntitlementService(db).is_entitled(WRITE_FEATURE_KEY):
        raise HTTPException(status_code=403, detail="bom_multitable_writeback not entitled")

    # (3) PLM "Part BOM" UPDATE permission.
    perm = MetaPermissionService(db)
    if not perm.check_permission(
        BOM_LINE_TYPE, AMLAction.update, user_id=str(user.id), user_roles=user.roles
    ):
        raise HTTPException(status_code=403, detail="Permission denied")

    # (4) fail-closed BEFORE any object lookup: a missing or over-long Idempotency-Key, a body
    # that whitelists to nothing, or a non-scalar quantity is a malformed/empty write.
    idem = (idempotency_key or "").strip()
    if not idem:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    if len(idem) > 64:
        # the audit column is VARCHAR(64); reject over-long keys here so Postgres never raises a
        # length error at insert (SQLite silently accepts it -> this 400 gate is the real guard).
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header must be at most 64 characters",
        )
    fields = {
        key: value
        for key, value in body.model_dump(exclude_unset=True).items()
        if key in WRITE_WHITELIST
    }
    if not fields:
        raise HTTPException(status_code=400, detail="no editable cells to write")
    # quantity must be a scalar (number | string | null): the whitelist filters by KEY not value
    # type, so without this a write-permitted caller could persist an object/array (e.g.
    # {"quantity": {"x": 1}}) into the BOM quantity cell. bool is rejected (int subclass in Python).
    if "quantity" in fields:
        quantity = fields["quantity"]
        if quantity is not None and (
            isinstance(quantity, bool) or not isinstance(quantity, (int, float, str))
        ):
            raise HTTPException(
                status_code=400,
                detail="quantity must be a number, string, or null",
            )

    # (5) 404: part missing, then line ∈ part. The three failure shapes are indistinguishable.
    part = db.get(Item, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="BOM line not found under part")
    line = db.get(Item, bom_line_id)
    if line is None or line.item_type_id != BOM_LINE_TYPE or line.source_id != part_id:
        raise HTTPException(status_code=404, detail="BOM line not found under part")

    # (6) 409: parent part lifecycle-locked (Released/Review/Suspended/Obsolete). Reuses the
    # add_bom_child precedent; a locked BOM is editable only via the deferred ECO route.
    part_type = db.get(ItemType, part.item_type_id)
    locked, locked_state = is_item_locked(db, part, part_type)
    if locked:
        raise HTTPException(
            status_code=409,
            detail=f"Item is locked in state '{locked_state or part.state}'",
        )

    # (7) governed atomic apply: replay cache + audit + property mutation.
    tenant_id, org_id = resolve_license_scope()
    try:
        result = BOMMultitableWritebackService(db).write_line(
            part_id,
            bom_line_id,
            idem,
            fields,
            user_id=user.id,
            tenant_id=tenant_id,
            org_id=org_id,
            if_match=if_match,
        )
    except BomLineNotInPartError as exc:
        # defense-in-depth (the §5 gate already enforced line∈part) -> same 404.
        raise HTTPException(status_code=404, detail="BOM line not found under part") from exc
    except BomLineWritebackConflictError as exc:
        # same Idempotency-Key reused for a different write -> conflict.
        raise HTTPException(
            status_code=409, detail="Idempotency-Key reused for a different write"
        ) from exc
    except BomLineWritebackPreconditionFailedError as exc:
        raise HTTPException(status_code=412, detail="If-Match precondition failed") from exc

    updated_line = db.get(Item, result["bom_line_id"])
    if updated_line is not None:
        response.headers["ETag"] = bom_line_write_etag(updated_line)
    return {"ok": True, "bom_line_id": result["bom_line_id"]}
