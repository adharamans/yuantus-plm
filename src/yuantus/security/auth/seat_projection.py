"""PLM-COLLAB-V2 seats (Option A): project a license's paid seat cap into the
identity-side ``TenantQuota.max_users`` at license-import time.

Design of record: ``docs/development/plm-collab-v2-seats-design-20260619.md`` (Option A) and
``docs/development/plm-collab-v2-seats-cap-clearing-design-20260621.md`` (the seats:null clear,
owner-ratified). In one paragraph:

- A vendor-signed license payload optionally carries ``seats``: a **positive int** = the number
  of users the tenant's paid seat pack covers; an explicit **``null``** = clear the cap
  (unlimited); **absent** = no-op (legacy / pact-seed licenses, and the default).
- ``yuantus license import`` commits the license first (it is the commercial source of truth),
  then calls this helper **best-effort** to land the seat clause onto the identity-side
  ``TenantQuota.max_users`` -- the *same* cap the existing ``QuotaService`` / ``_apply_quota_limits``
  provisioning gate already enforces at ``POST /admin/users``, itself gated by ``QUOTA_MODE``
  (default ``disabled`` -> ships inert).
- ``is_entitled()`` never touches seats: feature authorization and seat-cap enforcement are
  separate code paths. This helper is the only meta<->identity hop, and it is a single import-time
  write, never a hot-path join (the limit and the active-user count both live identity-side).

Fail-open by contract: the caller runs this *after* the license is committed and treats any raised
exception as non-fatal. A transient identity-DB failure must never un-activate a valid paid
license; with ``QUOTA_MODE`` default-off a missing cap is inert, and re-running the import
re-projects (``upsert_quota`` is idempotent). The clear path shares this exact structure.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeatProjectionOutcome:
    """What ``project_license_seats`` did, so the CLI + audit can report it truthfully.

    - ``"noop"``  — nothing projected (``seats`` absent, or present-but-invalid). ``seats`` None.
    - ``"set"``   — ``TenantQuota.max_users`` set to ``seats`` (a positive int).
    - ``"clear"`` — ``TenantQuota.max_users`` cleared to NULL (unlimited) by an explicit
                    ``seats: null`` in the signed payload. ``seats`` None.
    """

    action: str
    seats: Optional[int] = None


_NOOP = SeatProjectionOutcome("noop")


def project_license_seats(
    identity_session: Session, payload: Mapping[str, Any]
) -> SeatProjectionOutcome:
    """Land the signed payload's seat clause onto ``TenantQuota.max_users``.

    Three outcomes (see :class:`SeatProjectionOutcome`):

    - **absent** ``seats`` key -> ``noop`` (backward-compatible: every pre-seats license and the
      pact seed has no ``seats`` key);
    - explicit **``seats: null``** -> ``clear`` (``max_users`` -> NULL = unlimited);
    - a **positive int** -> ``set``; **0 / negative / bool / non-int** -> ``noop`` (fail-open).

    ``absent`` and ``null`` are deliberately distinct -- ``payload.get`` can't tell them apart, so
    this keys on ``"seats" in payload``. Raises only on a DB failure, which the CLI catches and
    reports without failing the already-activated license (the clear path raises identically, so a
    failed clear can never reach the CLI's post-commit audit).
    """
    if "seats" not in payload:
        # No seats clause at all -> nothing to project (legacy / pact-seed licenses).
        return _NOOP

    seats = payload["seats"]

    if seats is None:
        # Explicit ``seats: null`` -> a deliberate, *signed* instruction to clear the cap. Distinct
        # from "absent" above. Clearing a tenant that never had a quota row simply creates one with
        # max_users=None (= unlimited = the default) -- harmless, and matches the set path, which
        # also creates the row; the audit records "cleared" either way.
        tenant_id = _require_tenant(payload)
        if tenant_id is None:
            return _NOOP
        _ensure_tenant_and_set_cap(identity_session, tenant_id, max_users=None)
        logger.info(
            "seat projection: tenant %s -> TenantQuota.max_users CLEARED (unlimited; explicit "
            "seats:null). QUOTA_MODE gates enforcement.",
            tenant_id,
        )
        return SeatProjectionOutcome("clear")

    # A seat cap must be a concrete positive integer. Skip (fail-open: leave max_users unchanged,
    # preserving any prior cap) and log loudly on anything else, rather than projecting a footgun
    # -- e.g. ``max_users=0`` would lock the whole tenant out under enforce mode. ``bool`` is an
    # ``int`` subclass, so reject it explicitly.
    if isinstance(seats, bool) or not isinstance(seats, int) or seats < 1:
        logger.warning(
            "seat projection skipped: license for tenant %r has invalid seats=%r (need an int "
            ">= 1, or null to clear); TenantQuota.max_users left unchanged (any prior cap "
            "preserved). Re-issue the license with a valid seat count.",
            payload.get("tenant_id"),
            seats,
        )
        return _NOOP

    tenant_id = _require_tenant(payload)
    if tenant_id is None:
        return _NOOP
    _ensure_tenant_and_set_cap(identity_session, tenant_id, max_users=seats)
    logger.info(
        "seat projection: tenant %s -> TenantQuota.max_users=%d (QUOTA_MODE gates enforcement)",
        tenant_id,
        seats,
    )
    return SeatProjectionOutcome("set", seats)


def _require_tenant(payload: Mapping[str, Any]) -> Optional[str]:
    tenant_id = str(payload.get("tenant_id") or "").strip()
    if not tenant_id:
        logger.warning("seat projection skipped: license payload missing tenant_id")
        return None
    return tenant_id


def _ensure_tenant_and_set_cap(
    identity_session: Session, tenant_id: str, *, max_users: Optional[int]
) -> None:
    """Ensure the tenant row exists, then set (or clear, ``max_users=None``) the cap.

    Lazy imports keep this identity-layer helper free of import-time coupling, and keep every
    non-CLI ``import_license()`` caller (unit tests, the pact provider seed) clean -- they never
    import this module, so they never project.
    """
    from yuantus.security.auth.quota_service import QuotaService
    from yuantus.security.auth.service import AuthService

    # ``TenantQuota.tenant_id`` FKs ``auth_tenants`` -> the tenant row must exist first.
    AuthService(identity_session).ensure_tenant(tenant_id, name=tenant_id)
    # License is the source of truth for the seat cap. Idempotent; a manually-set max_users is
    # overwritten (the documented Option-A tradeoff), and max_users=None clears it to unlimited.
    QuotaService(identity_session).upsert_quota(tenant_id, updates={"max_users": max_users})
