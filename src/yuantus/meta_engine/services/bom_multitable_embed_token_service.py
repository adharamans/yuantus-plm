"""PLM-COLLAB-P3-D1: mint a short-lived Ed25519 (EdDSA) embed token for the BOM review.

The Yuantus deployment holds the signing PRIVATE key (`EMBED_TOKEN_SIGNING_KEY`, base64 raw
Ed25519 seed, NEVER committed) and signs; a consumer (metasheet2, P3-D2) verifies OFFLINE with
the matching PUBLIC key (kid-addressed, same shape as the P1-C license allowlist) and therefore
can never mint. This module ONLY mints — verification + the iframe host are P3-D2 (the public
key is distributed to the consumer by deployment config, not exposed by an endpoint here).

The token is a standard EdDSA JWT whose claims (`tenant_id`/`org_id`/`sub`=user_id/`part_id`/
`feature_key`/`aud`=origin/`exp`/`jti`/`typ:"embed"`) let the P3-D2 data side re-run is_entitled
+ permission and match part/origin. Short TTL + a unique `jti`; this is jti-TRACKABLE (recorded
on mint), NOT yet a revocation denylist (a later slice). Fail-closed: no signing key -> no mint.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, Tuple

from yuantus.meta_engine.services.bom_multitable_projection_service import FEATURE_KEY
from yuantus.security.auth.jwt import (
    build_access_token_payload,
    encode_eddsa,
    load_ed25519_private_key_b64,
)

TOKEN_TYP = "embed"


class EmbedTokenNotConfigured(RuntimeError):
    """Raised when the deployment has no embed signing key -> mint must fail closed (503)."""


def parse_allowed_origins(raw: str) -> Tuple[str, ...]:
    """CSV allowlist -> tuple of explicit origins.

    A literal ``'*'`` is DROPPED, not honored: the mint side never allows-all, so even a
    misconfigured ``EMBED_ALLOWED_ORIGINS='*'`` cannot issue a wildcard-audience token (the
    P3-D0 'production must forbid *' rule enforced structurally, not by deploy discipline).
    """
    return tuple(o for o in (part.strip() for part in (raw or "").split(",")) if o and o != "*")


def is_origin_allowed(origin: str, raw_allowlist: str) -> bool:
    return bool(origin) and origin in parse_allowed_origins(raw_allowlist)


def mint_embed_token(
    *,
    user_id: Any,
    tenant_id: str,
    org_id: Any,
    part_id: str,
    origin: str,
    signing_key_b64: str,
    key_id: str,
    ttl_seconds: int,
) -> Dict[str, Any]:
    """Mint the signed EdDSA embed token. Assumes the caller already gated
    entitlement/permission/origin. Fail-closed: empty signing key -> EmbedTokenNotConfigured.
    """
    if not signing_key_b64:
        raise EmbedTokenNotConfigured("embed token signing key is not configured")
    private_key = load_ed25519_private_key_b64(signing_key_b64)
    jti = str(uuid.uuid4())
    payload = build_access_token_payload(
        user_id=user_id,
        tenant_id=tenant_id,
        org_id=org_id,
        ttl_seconds=int(ttl_seconds),
        extra={
            "part_id": part_id,
            "feature_key": FEATURE_KEY,
            "aud": origin,
            "jti": jti,
            "typ": TOKEN_TYP,
        },
    )
    token = encode_eddsa(payload, private_key=private_key, kid=key_id)
    return {
        "token": token,
        "jti": jti,
        "expires_in": int(ttl_seconds),
        "exp": payload["exp"],
        "aud": origin,
        "claims": payload,
    }
