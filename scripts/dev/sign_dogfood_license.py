#!/usr/bin/env python3
"""DEV / DOGFOOD ONLY -- sign a one-time offline PLM license for V1 dogfood / controlled demo.

This is NOT the vendor production issuance tool (that is a separate, private,
out-of-the-product tool -- taskbook Phase 4 / V2). It generates an EPHEMERAL
Ed25519 keypair, signs a single PERPETUAL ``plm.bom_multitable`` license, and
prints the PUBLIC key to configure on the dogfood deployment. The private key is
ephemeral (discarded unless you pass ``--priv-out``) and must NEVER be committed.

It reuses the exact canonical signing scheme the deployment verifies with
(``yuantus.meta_engine.app_framework.license_verification.canonical_payload_bytes``),
so the output is accepted by ``yuantus license import``.

V1 sells perpetual only (``expires_at = null``) -- there is no grace window until
Phase 4, so a dated license could hard-cut the feature mid-review.

Usage:
    python scripts/dev/sign_dogfood_license.py --tenant-id <tenant> [--subject "ACME"] \
        --out dogfood-license.json
Then on the dogfood deployment:
    1. add the printed kid->pubkey to YUANTUS_LICENSE_PUBLIC_KEYS
    2. run: yuantus license import dogfood-license.json
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Make the in-repo package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from yuantus.meta_engine.app_framework.license_verification import (
    canonical_payload_bytes,
    verify_license,
)

# The authorization app for the bom_multitable feature
# (FEATURE_APP_NAMES["bom_multitable"] == {"plm.bom_multitable"}; entitlement_service.py).
BOM_MULTITABLE_APP = "plm.bom_multitable"


def build_and_sign(priv: Ed25519PrivateKey, *, tenant_id: str, subject: str,
                   kid: str, plan_type: str, issued_at: str, seats: int | None = None,
                   clear_seats: bool = False) -> dict:
    # --seats and --clear-seats are mutually exclusive: one SETS a cap, the other CLEARS it. Guard
    # here too (not just argparse), because the tests call build_and_sign directly.
    if clear_seats and seats is not None:
        raise ValueError("seats and clear_seats are mutually exclusive: one sets a cap, the other clears it")
    # Refuse to MINT what seat_projection.py would refuse to HONOR: a seats clause < 1 is
    # fail-open-skipped at import (silently no projection), so enforce the signer's stated "int >= 1"
    # contract at the source rather than emitting a self-verifying-but-inert license. bool is an
    # int subclass -> reject explicitly; this mirrors security/auth/seat_projection.py.
    if seats is not None and (isinstance(seats, bool) or not isinstance(seats, int) or seats < 1):
        raise ValueError(f"seats must be an int >= 1 when provided (omit to skip projection); got {seats!r}")
    payload = {
        "tenant_id": tenant_id,
        "app_names": [BOM_MULTITABLE_APP],
        "features": ["bom_multitable"],
        "plan_type": plan_type,
        "license_key": uuid.uuid4().hex,
        "subject": subject,
        "issued_at": issued_at,
        "expires_at": None,  # perpetual -- V1 constraint (no grace until Phase 4)
    }
    if clear_seats:
        # Explicit ``seats: null`` -> CLEARS TenantQuota.max_users (-> unlimited) at import.
        # Distinct from omitting seats (no-op); the null is part of the signed canonical payload.
        payload["seats"] = None
    elif seats is not None:
        # PLM-COLLAB-V2 seats (Option A): projected onto identity-side TenantQuota.max_users
        # at `yuantus license import` (see security/auth/seat_projection.py).
        payload["seats"] = seats
    signature = base64.b64encode(priv.sign(canonical_payload_bytes(payload))).decode()
    return {"alg": "Ed25519", "kid": kid, "payload": payload, "signature": signature}


def _seats_arg(raw: str) -> int:
    """argparse ``type=`` for ``--seats``: enforce the int >= 1 contract at parse time.

    Fail fast so an operator cannot mint a self-verifying license whose seats clause the
    import-time projection then silently rejects (< 1 -> fail-open no-op = no projection).
    """
    try:
        value = int(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--seats must be an integer >= 1; got {raw!r}")
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--seats must be >= 1 (a 0 or negative cap is skipped at import: no projection); got {value}"
        )
    return value


def main() -> int:
    ap = argparse.ArgumentParser(description="Sign a perpetual plm.bom_multitable dogfood license.")
    ap.add_argument("--tenant-id", required=True, help="tenant the license activates for")
    ap.add_argument("--subject", default="Dogfood Pilot", help="license subject (org / customer name)")
    ap.add_argument("--kid", default="dogfood-1",
                    help="key id; must match the deployment's YUANTUS_LICENSE_PUBLIC_KEYS entry")
    ap.add_argument("--plan-type", default="Pilot")
    seats_group = ap.add_mutually_exclusive_group()
    seats_group.add_argument("--seats", type=_seats_arg, default=None,
                    help="optional paid seat cap (int >= 1; values < 1 are rejected); projected onto "
                         "TenantQuota.max_users at import (PLM-COLLAB-V2 Option A). Omit to skip "
                         "projection: max_users is left unchanged (an existing cap is preserved, not cleared).")
    seats_group.add_argument("--clear-seats", action="store_true",
                    help="emit an explicit seats:null -> CLEARS TenantQuota.max_users (unlimited) at "
                         "import. Mutually exclusive with --seats.")
    ap.add_argument("--issued-at", default=None, help="ISO-8601; default: now (UTC)")
    ap.add_argument("--out", default="dogfood-license.json", help="output license file path")
    ap.add_argument("--priv-out", default=None,
                    help="optional: write the EPHEMERAL private key (PEM) here to re-sign later. "
                         "Keep in custody; never commit.")
    args = ap.parse_args()

    priv = Ed25519PrivateKey.generate()
    pub_raw = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    pub_b64 = base64.b64encode(pub_raw).decode()
    issued_at = args.issued_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    license_obj = build_and_sign(
        priv, tenant_id=args.tenant_id, subject=args.subject,
        kid=args.kid, plan_type=args.plan_type, issued_at=issued_at, seats=args.seats,
        clear_seats=args.clear_seats,
    )

    # Self-check: the deployment MUST accept what we just signed (same offline verify path).
    try:
        verify_license(license_obj, {args.kid: pub_b64})
    except Exception as exc:  # noqa: BLE001
        print(f"FATAL: self-verification failed: {exc}", file=sys.stderr)
        return 1

    Path(args.out).write_text(json.dumps(license_obj, indent=2) + "\n", encoding="utf-8")
    if args.priv_out:
        Path(args.priv_out).write_bytes(
            priv.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )

    print(f"license written: {args.out}  (PERPETUAL {BOM_MULTITABLE_APP} for tenant '{args.tenant_id}')")
    print("self-verification: PASS (the deployment's offline verify will accept this license)")
    print("\n1) configure the dogfood deployment so it can verify this license:")
    print(f'   YUANTUS_LICENSE_PUBLIC_KEYS={json.dumps({args.kid: pub_b64})}')
    print(f"\n2) import on the deployment:\n   yuantus license import {args.out}")
    if not args.priv_out:
        print("\n(The ephemeral private key was discarded. Pass --priv-out to keep it for re-signing.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
