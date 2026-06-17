"""Dedicated machine-credential auth for the MES consumption ingest route
(Consumption R2.2).

This is the SOLE auth for ``POST /api/v1/consumption/plans/{plan_id}/mes-actuals``:
that path is whitelisted from the global JWT enforcement (see
``api/middleware/auth_enforce._is_mes_ingest_path``), so this dependency must be
airtight and fail-closed -- there is no user session behind it.

Load-bearing guarantees (owner-ratified, R2.2 taskbook D1/D3/D5):

* fail-closed: if ``MES_INGEST_SECRET`` or ``MES_INGEST_TENANT_ID`` is unset the
  route is DISABLED (503) -- checked before any header is read.
* auth: both ``X-MES-Ingest-User`` and ``X-MES-Ingest-Secret`` are compared
  constant-time (``secrets.compare_digest``); a miss/mismatch is 401. Neither
  value is ever logged or echoed.
* tenant binding: tenant/org come from config (``MES_INGEST_TENANT_ID`` /
  ``MES_INGEST_ORG_ID``), NEVER the ``x-tenant-id`` header. The contextvars are
  set BEFORE the session is created (``get_db_session`` reads ``tenant_id_var``
  at session-creation time to pick the schema), so the request is structurally
  scoped to the bound tenant and a cross-tenant ``plan_id`` is simply not found.
* order: 503 (unconfigured) -> 401 (bad/missing cred) -> pin tenant -> yield
  session. The handler never runs (no plan read, no write) until auth passes, so
  unauthorized callers cannot probe plan existence.
"""
from __future__ import annotations

import secrets
from typing import Generator, Optional

from fastapi import Header, HTTPException
from sqlalchemy.orm import Session

from yuantus.config import get_settings
from yuantus.context import org_id_var, tenant_id_var
from yuantus.database import get_db_session


def _credential_ok(provided: Optional[str], configured: str) -> bool:
    """Constant-time compare of one credential half. A missing header is a clean
    False without comparing (a missing header is not a timing oracle on the
    secret); a present value is compared in constant time.

    Compares on UTF-8 bytes, NOT str: ``secrets.compare_digest`` with str operands
    raises TypeError on any non-ASCII character, and these header values are
    attacker-controlled (Starlette decodes header bytes via latin-1, so a high byte
    reaches here). A bytes compare is content-safe and constant-time, so a malformed/
    non-ASCII header is a clean reject (-> 401), never an unhandled 500.
    """
    if not provided:
        return False
    return secrets.compare_digest(
        str(provided).encode("utf-8"), str(configured).encode("utf-8")
    )


def require_mes_ingest_credential(
    x_mes_ingest_user: Optional[str] = Header(default=None),
    x_mes_ingest_secret: Optional[str] = Header(default=None),
) -> Generator[Session, None, None]:
    settings = get_settings()
    configured_user = settings.MES_INGEST_USER or ""
    configured_secret = settings.MES_INGEST_SECRET or ""
    configured_tenant = settings.MES_INGEST_TENANT_ID or ""
    configured_org = settings.MES_INGEST_ORG_ID or ""

    # (1) fail-closed: route disabled unless secret AND tenant are configured.
    if not configured_secret or not configured_tenant:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "mes_ingest_disabled",
                "message": "MES ingest is not configured",
            },
        )

    # (2) constant-time auth (both halves computed; secret never logged/echoed).
    user_ok = _credential_ok(x_mes_ingest_user, configured_user)
    secret_ok = _credential_ok(x_mes_ingest_secret, configured_secret)
    if not (user_ok and secret_ok):
        raise HTTPException(
            status_code=401,
            detail={
                "code": "mes_ingest_unauthorized",
                "message": "invalid MES ingest credential",
            },
        )

    # (3) Bind the session to the CONFIG tenant (never the x-tenant-id header).
    # get_db_session() reads tenant_id_var AT SESSION-CREATION time to pick the
    # schema (and bakes it into a per-transaction `SET LOCAL search_path`), so we
    # set the contextvars, open the session, then reset -- all SYNCHRONOUSLY in
    # this one context. We must NOT hold a contextvar Token across the `yield`:
    # FastAPI runs a sync-generator dependency's setup and teardown in different
    # threadpool contexts, so `reset()` after the yield raises. The session keeps
    # the bound-tenant schema for its whole life regardless of the contextvar.
    tenant_token = tenant_id_var.set(configured_tenant)
    org_token = org_id_var.set(configured_org or None)
    try:
        cm = get_db_session()
        session = cm.__enter__()
    finally:
        tenant_id_var.reset(tenant_token)
        org_id_var.reset(org_token)
    # Drive the session context manager's teardown with the REAL exception info:
    # get_db_session commits on a clean exit and rolls back on an exception, so a
    # bare cm.__exit__(None, None, None) would force a commit even when the handler
    # raised. Propagate the active exception (if any) so it rolls back correctly.
    try:
        yield session
    except BaseException as exc:
        cm.__exit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        cm.__exit__(None, None, None)
