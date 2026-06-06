from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Mapping, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class JWTError(ValueError):
    pass


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def encode_hs256(payload: Dict[str, Any], *, secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def decode_hs256(token: str, *, secret: str, leeway_seconds: int = 0) -> Dict[str, Any]:
    try:
        header_b64, payload_b64, sig_b64 = token.split(".", 2)
    except ValueError as e:
        raise JWTError("Invalid token format") from e

    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        raise JWTError("Invalid token encoding") from e

    if header.get("alg") != "HS256":
        raise JWTError("Unsupported alg")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        got = _b64url_decode(sig_b64)
    except Exception as e:
        raise JWTError("Invalid signature encoding") from e
    if not hmac.compare_digest(expected, got):
        raise JWTError("Invalid signature")

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_int = int(exp)
        except Exception as e:
            raise JWTError("Invalid exp claim") from e
        now = int(time.time())
        if now > exp_int + int(leeway_seconds):
            raise JWTError("Token expired")

    return payload


def now_ts() -> int:
    return int(time.time())


def build_access_token_payload(
    *,
    user_id: int,
    tenant_id: str,
    org_id: Optional[str] = None,
    ttl_seconds: int = 3600,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    issued_at = now_ts()
    payload: Dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": tenant_id,
        "iat": issued_at,
        "exp": issued_at + int(ttl_seconds),
    }
    if org_id:
        payload["org_id"] = org_id
    if extra:
        payload.update(extra)
    return payload


# --- EdDSA (Ed25519) JWTs --------------------------------------------------------------
# Asymmetric variant for cross-service tokens (PLM-COLLAB-P3-D embed tokens): the issuer
# signs with an Ed25519 PRIVATE key it never shares; a consumer verifies offline with the
# PUBLIC key (kid-addressed, same shape as the P1-C license public-key allowlist) and so can
# never mint. Standard RFC 8037 EdDSA JWS over the `header_b64.payload_b64` signing input.


def load_ed25519_private_key_b64(b64_seed: str) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from a base64 raw 32-byte seed (never committed)."""
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(b64_seed))


def load_ed25519_public_key_b64(b64_key: str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from a base64 raw 32-byte key."""
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(b64_key))


def encode_eddsa(payload: Dict[str, Any], *, private_key: Ed25519PrivateKey, kid: str) -> str:
    header = {"alg": "EdDSA", "typ": "JWT", "kid": kid}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    sig = private_key.sign(signing_input)
    return f"{header_b64}.{payload_b64}.{_b64url_encode(sig)}"


def decode_eddsa(
    token: str, *, public_keys: Mapping[str, str], leeway_seconds: int = 0
) -> Dict[str, Any]:
    """Verify an EdDSA JWT against a kid->base64-public-key map (mirrors P1-C's allowlist)."""
    try:
        header_b64, payload_b64, sig_b64 = token.split(".", 2)
    except ValueError as e:
        raise JWTError("Invalid token format") from e
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except Exception as e:
        raise JWTError("Invalid token encoding") from e

    if header.get("alg") != "EdDSA":
        raise JWTError("Unsupported alg")
    kid = header.get("kid")
    if not kid or kid not in public_keys:
        raise JWTError("Unknown key id")

    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    try:
        load_ed25519_public_key_b64(public_keys[kid]).verify(_b64url_decode(sig_b64), signing_input)
    except (InvalidSignature, ValueError, TypeError) as e:
        raise JWTError("Invalid signature") from e

    exp = payload.get("exp")
    if exp is not None:
        try:
            exp_int = int(exp)
        except Exception as e:
            raise JWTError("Invalid exp claim") from e
        if now_ts() > exp_int + int(leeway_seconds):
            raise JWTError("Token expired")

    return payload

