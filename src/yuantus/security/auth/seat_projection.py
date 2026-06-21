"""PLM-COLLAB-V2 seats (Option A): project a license's paid seat cap into the
identity-side ``TenantQuota.max_users`` at license-import time.

Design of record: ``docs/development/plm-collab-v2-seats-design-20260619.md`` (Option A,
owner-ratified). In one paragraph:

- A vendor-signed license payload optionally carries ``seats`` (a positive int = the
  number of users the tenant's paid seat pack covers).
- ``yuantus license import`` commits the license first (it is the commercial source of
  truth), then calls this helper **best-effort** to land ``seats`` onto the identity-side
  ``TenantQuota.max_users`` -- the *same* cap the existing ``QuotaService`` /
  ``_apply_quota_limits`` provisioning gate already enforces at ``POST /admin/users``,
  itself gated by ``QUOTA_MODE`` (default ``disabled`` -> ships inert).
- ``is_entitled()`` never touches seats: feature authorization and seat-cap enforcement
  are separate code paths. This helper is the only meta<->identity hop, and it is a single
  import-time write, never a hot-path join (the limit and the active-user count both live
  identity-side, so enforcement stays single-DB).

Fail-open by contract: the caller runs this *after* the license is committed and treats any
raised exception as non-fatal. A transient identity-DB failure must never un-activate a
valid paid license; with ``QUOTA_MODE`` default-off a missing cap is inert, and re-running
the import re-projects (``upsert_quota`` is idempotent).
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def project_license_seats(
    identity_session: Session, payload: Mapping[str, Any]
) -> Optional[int]:
    """Land ``payload['seats']`` onto ``TenantQuota.max_users`` for the license's tenant.

    Returns the projected seat count, or ``None`` when there was nothing to project (the
    license carries no seat cap, or an invalid one). Raises only on an actual DB failure --
    which the CLI catches and reports without failing the already-activated license.
    """
    seats = payload.get("seats")
    if seats is None:
        # License carries no seat cap -> nothing to project. Backward-compatible: every
        # license issued before this slice (and the pact seed) has no ``seats`` key.
        return None

    # A seat cap must be a concrete positive integer. Skip (fail-open: leave max_users
    # unchanged, preserving any prior cap) and log loudly on anything else, rather than projecting a
    # footgun -- e.g. ``max_users=0`` would lock the whole tenant out under enforce mode.
    # ``bool`` is an ``int`` subclass, so reject it explicitly.
    if isinstance(seats, bool) or not isinstance(seats, int) or seats < 1:
        logger.warning(
            "seat projection skipped: license for tenant %r has invalid seats=%r "
            "(need an int >= 1); TenantQuota.max_users left unchanged (any prior cap preserved). "
            "Re-issue the license with a valid seat count.",
            payload.get("tenant_id"),
            seats,
        )
        return None

    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        logger.warning("seat projection skipped: license payload missing tenant_id")
        return None

    # Lazy imports keep this identity-layer helper free of import-time coupling, and keep
    # every non-CLI ``import_license()`` caller (unit tests, the pact provider seed) clean --
    # they never import this module, so they never project.
    from yuantus.security.auth.quota_service import QuotaService
    from yuantus.security.auth.service import AuthService

    # ``TenantQuota.tenant_id`` FKs ``auth_tenants`` -> the tenant row must exist first.
    AuthService(identity_session).ensure_tenant(tenant_id, name=tenant_id)
    # License is the source of truth for the seat cap: set max_users to it (idempotent;
    # a manually-set max_users is overwritten -- documented in the design as A's tradeoff).
    QuotaService(identity_session).upsert_quota(tenant_id, updates={"max_users": seats})
    logger.info(
        "seat projection: tenant %s -> TenantQuota.max_users=%d (QUOTA_MODE gates enforcement)",
        tenant_id,
        seats,
    )
    return seats
