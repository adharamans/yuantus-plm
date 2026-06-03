"""Ed25519 verification for offline PLM license files (PLM-COLLAB-P1-C).

A license file is vendor-signed (the vendor holds the Ed25519 PRIVATE key); the
deployment verifies it offline with a built-in / allowlisted PUBLIC key, so a
customer admin cannot forge a license. The signed bytes are the CANONICAL JSON of
the payload (``sort_keys=True`` + compact separators) -- field order in the file
must never change verification.

The private key NEVER lives in this repo; tests generate an ephemeral keypair.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Dict, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ALG = "Ed25519"


class LicenseVerificationError(ValueError):
    """Raised when a license file fails verification (bad alg / kid / signature)."""


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    """The exact bytes that are signed: canonical JSON of the payload.

    ``sort_keys=True`` + compact separators make this independent of field order
    in the file, so re-serialization never drifts the signature.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _load_public_key(b64_key: str) -> Ed25519PublicKey:
    raw = base64.b64decode(b64_key, validate=True)
    return Ed25519PublicKey.from_public_bytes(raw)


def verify_license(
    license_obj: Mapping[str, Any], public_keys: Mapping[str, str]
) -> Dict[str, Any]:
    """Verify a license object; return its payload dict on success.

    ``public_keys`` maps kid -> base64 raw Ed25519 public key. Raises
    :class:`LicenseVerificationError` on an unknown alg, an unknown kid, or a bad
    signature.
    """
    alg = license_obj.get("alg")
    if alg != ALG:
        raise LicenseVerificationError(f"unsupported license alg: {alg!r} (expected {ALG})")
    kid = license_obj.get("kid")
    if kid not in public_keys:
        raise LicenseVerificationError(f"unknown license kid: {kid!r}")
    payload = license_obj.get("payload")
    if not isinstance(payload, dict):
        raise LicenseVerificationError("license payload missing or not an object")
    sig_b64 = license_obj.get("signature")
    if not isinstance(sig_b64, str):
        raise LicenseVerificationError("license signature missing")
    try:
        signature = base64.b64decode(sig_b64, validate=True)
        pubkey = _load_public_key(public_keys[kid])
        pubkey.verify(signature, canonical_payload_bytes(payload))
    except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
        # binascii.Error (malformed signature / public-key base64) is also funneled
        # into one LicenseVerificationError so callers never see a raw decode error.
        raise LicenseVerificationError("license signature verification failed") from exc
    return dict(payload)
