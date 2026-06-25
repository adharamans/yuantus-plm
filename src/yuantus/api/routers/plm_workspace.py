from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from yuantus.config import get_settings
from yuantus.meta_engine.services.bom_multitable_embed_token_service import (
    is_origin_allowed,
)

router = APIRouter(prefix="/plm-workspace", tags=["PLM Workspace"])

_HTML_PATH = Path(__file__).resolve().parents[2] / "web" / "plm_workspace.html"

_API_BASE = "/api/v1"
_SETTING_REPLACEMENTS = {
    "__YUANTUS_TENANT_HEADER__": "TENANT_HEADER",
    "__YUANTUS_ORG_HEADER__": "ORG_HEADER",
    "__YUANTUS_AUTH_MODE__": "AUTH_MODE",
}


def _escape_js(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("<", "\\x3c")
        .replace(">", "\\x3e")
        .replace("&", "\\x26")
    )


def _derive_origin(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _metasheet_embed_config() -> dict[str, object]:
    settings = get_settings()
    embed_url = (settings.METASHEET_EMBED_URL or "").strip()
    embed_origin = _derive_origin(embed_url)
    configured = bool(
        embed_url
        and embed_origin
        and settings.EMBED_TOKEN_SIGNING_KEY
        and settings.EMBED_TOKEN_KEY_ID
        and settings.EMBED_TOKEN_AUDIENCE
        and is_origin_allowed(embed_origin, settings.EMBED_ALLOWED_ORIGINS)
    )
    return {
        "url": embed_url,
        "origin": embed_origin,
        "configured": configured,
    }


def _load_workspace_html() -> str:
    try:
        html = _HTML_PATH.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=500, detail="plm_workspace.html not found"
        ) from exc

    html = html.replace("__YUANTUS_API_BASE__", _escape_js(_API_BASE))
    settings = get_settings()
    for token, setting_name in _SETTING_REPLACEMENTS.items():
        value = getattr(settings, setting_name, "")
        html = html.replace(token, _escape_js(str(value or "")))
    embed_config = _metasheet_embed_config()
    html = html.replace(
        "__YUANTUS_METASHEET_EMBED_URL__",
        _escape_js(str(embed_config["url"] or "")),
    )
    html = html.replace(
        "__YUANTUS_METASHEET_EMBED_ORIGIN__",
        _escape_js(str(embed_config["origin"] or "")),
    )
    html = html.replace(
        "__YUANTUS_METASHEET_EMBED_CONFIGURED__",
        "true" if embed_config["configured"] else "false",
    )
    return html


@router.get("", response_class=HTMLResponse)
def plm_workspace_page() -> HTMLResponse:
    return HTMLResponse(_load_workspace_html())
