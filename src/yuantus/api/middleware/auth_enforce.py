from __future__ import annotations

import re
from typing import Any, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from yuantus.config import get_settings
from yuantus.context import org_id_var, tenant_id_var, user_id_var
from yuantus.security.auth.database import get_identity_sessionmaker
from yuantus.security.auth.jwt import JWTError, decode_hs256
from yuantus.security.auth.models import AuthUser
from yuantus.security.auth.service import AuthService


def _get_bearer_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def _is_cad_preview_public_path(path: str, settings) -> bool:
    if not getattr(settings, "CAD_PREVIEW_PUBLIC", False):
        return False
    if not path.startswith("/api/v1/file/"):
        return False
    if "/cad_asset/" in path:
        return True
    return path.endswith(
        ("/cad_manifest", "/cad_document", "/cad_metadata")
    )


def _is_public_path(path: str, settings=None) -> bool:
    if path in {
        "/favicon.ico",
        "/api/v1/health",
        "/api/v1/health/deps",
        "/api/v1/metrics",
        "/api/v1/auth/login",
        "/api/v1/plm-workspace",
        "/api/v1/plm-workspace/",
        "/api/v1/workbench",
        "/api/v1/workbench/",
    }:
        return True
    if path in {"/docs", "/redoc", "/openapi.json"}:
        return True
    if path.startswith("/docs/") or path.startswith("/redoc/"):
        return True
    if settings and _is_cad_preview_public_path(path, settings):
        return True
    return False


# Dedicated MES consumption ingest route (Consumption R2.2). It is whitelisted
# from global JWT enforcement because it is a MACHINE entrypoint authenticated by
# its own dedicated credential (api/dependencies/mes_ingest_auth). The pattern
# matches ONLY .../plans/{plan_id}/mes-actuals -- a single plan_id segment -- and
# must NOT be broadened to the rest of /consumption/*.
_MES_INGEST_PATH_RE = re.compile(
    r"^/api/v1/consumption/plans/[^/]+/mes-actuals/?$"
)


def _is_mes_ingest_path(path: str) -> bool:
    return bool(_MES_INGEST_PATH_RE.match(path))


def _is_tenant_only_path(path: str) -> bool:
    """
    Endpoints that are tenant-scoped and must not require an org context.

    These are typically used before an org is selected.
    """
    if path.startswith("/api/v1/admin"):
        return True
    return path in {
        "/api/v1/auth/me",
        "/api/v1/auth/orgs",
        "/api/v1/auth/switch-org",
    }


class AuthEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Enforce JWT authentication globally when `YUANTUS_AUTH_MODE=required`.

    This avoids relying on per-route dependencies to require authentication.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        mode = (settings.AUTH_MODE or "optional").strip().lower()
        if mode != "required":
            return await call_next(request)

        if (
            request.method.upper() == "OPTIONS"
            or _is_public_path(request.url.path, settings)
            or _is_mes_ingest_path(request.url.path)
        ):
            # MES ingest is whitelisted from JWT: its own dedicated credential
            # dependency is the sole, fail-closed auth (R2.2).
            return await call_next(request)

        token = _get_bearer_token(request)
        if not token:
            return JSONResponse(
                {"detail": "Missing bearer token"}, status_code=HTTP_401_UNAUTHORIZED
            )

        try:
            payload: dict[str, Any] = decode_hs256(
                token,
                secret=settings.JWT_SECRET_KEY,
                leeway_seconds=settings.AUTH_LEEWAY_SECONDS,
            )
        except JWTError as e:
            return JSONResponse({"detail": str(e)}, status_code=HTTP_401_UNAUTHORIZED)

        tenant_claim = payload.get("tenant_id")
        sub = payload.get("sub")
        if not tenant_claim or not sub:
            return JSONResponse(
                {"detail": "Invalid token claims"}, status_code=HTTP_401_UNAUTHORIZED
            )
        try:
            user_id = int(sub)
        except Exception:
            return JSONResponse(
                {"detail": "Invalid sub claim"}, status_code=HTTP_401_UNAUTHORIZED
            )

        tenant_header = request.headers.get(settings.TENANT_HEADER)
        if tenant_header and str(tenant_header) != str(tenant_claim):
            return JSONResponse({"detail": "Tenant mismatch"}, status_code=HTTP_401_UNAUTHORIZED)
        tenant_id = str(tenant_header or tenant_claim)

        tenant_only = _is_tenant_only_path(request.url.path)
        org_header = request.headers.get(settings.ORG_HEADER)
        org_id = str(org_header or (payload.get("org_id") or "")).strip() or None
        if not tenant_only and not org_id:
            return JSONResponse(
                {"detail": "Missing org id"}, status_code=HTTP_401_UNAUTHORIZED
            )

        tenant_token = tenant_id_var.set(tenant_id)
        org_token = org_id_var.set(org_id)
        user_token = user_id_var.set(str(user_id))
        request.state.tenant_id = tenant_id
        request.state.org_id = org_id
        request.state.user_id = str(user_id)
        try:
            SessionLocal = get_identity_sessionmaker()
            db = SessionLocal()
            try:
                user = (
                    db.query(AuthUser)
                    .filter(AuthUser.id == user_id, AuthUser.tenant_id == tenant_id)
                    .first()
                )
                if not user or not user.is_active:
                    return JSONResponse(
                        {"detail": "User not found or inactive"},
                        status_code=HTTP_401_UNAUTHORIZED,
                    )

                if not tenant_only:
                    svc = AuthService(db)
                    try:
                        svc.get_roles_for_user_org(
                            tenant_id=tenant_id, org_id=org_id, user_id=user_id
                        )
                    except Exception:
                        return JSONResponse(
                            {"detail": "Not a member of this org"},
                            status_code=HTTP_403_FORBIDDEN,
                        )
            finally:
                db.close()

            return await call_next(request)
        finally:
            user_id_var.reset(user_token)
            org_id_var.reset(org_token)
            tenant_id_var.reset(tenant_token)
