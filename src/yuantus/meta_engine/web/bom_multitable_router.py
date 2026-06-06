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

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from yuantus.api.dependencies.auth import CurrentUser, get_current_user
from yuantus.config import get_settings
from yuantus.database import get_db
from yuantus.meta_engine.app_framework.entitlement_service import EntitlementService
from yuantus.meta_engine.app_framework.license_scope import resolve_license_scope
from yuantus.meta_engine.models.item import Item
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
from yuantus.meta_engine.services.meta_permission_service import MetaPermissionService
from yuantus.models.audit import AuditLog

bom_multitable_router = APIRouter(prefix="/bom", tags=["BOM"])


class EmbedTokenRequest(BaseModel):
    """The PLM UI requests an embed token for a specific target origin (the iframe host)."""

    origin: str


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
