import subprocess
import shutil
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
# CI invokes this API test directly before installing the local package.
sys.path.insert(0, str(REPO_ROOT / "src"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from yuantus.api.app import create_app
from yuantus.api.middleware.auth_enforce import _is_public_path
from yuantus.api.routers.plm_workspace import router as plm_workspace_router
from yuantus.config import get_settings


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(plm_workspace_router, prefix="/api/v1")
    return TestClient(app)


def _route_paths(app: FastAPI) -> set[str]:
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    for route in app.routes:
        route_contexts = getattr(route, "effective_route_contexts", None)
        if route_contexts:
            paths.update(context.path for context in route_contexts())
    return paths


def test_web_html_assets_are_packaged_in_wheel(tmp_path):
    project_root = tmp_path / "project"
    wheel_dir = tmp_path / "dist"
    project_root.mkdir()
    wheel_dir.mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", project_root / "pyproject.toml")
    shutil.copy2(REPO_ROOT / "README.md", project_root / "README.md")
    shutil.copytree(
        REPO_ROOT / "src",
        project_root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--wheel-dir",
            str(wheel_dir),
            str(project_root),
        ],
        check=True,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    wheel = next(wheel_dir.glob("yuantus_plm-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())

    assert "yuantus/web/cad_preview.html" in names
    assert "yuantus/web/cad_review.html" in names
    assert "yuantus/web/plm_workspace.html" in names
    assert "yuantus/web/workbench.html" in names


def test_plm_workspace_page_renders_html():
    client = _client()

    response = client.get("/api/v1/plm-workspace")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Yuantus PLM Workspace" in response.text
    assert "Native PLM Workspace" in response.text
    assert "Object Explorer" in response.text
    assert "Approval Rail" in response.text
    assert "Open Admin Workbench" in response.text
    assert "Session Bootstrap" in response.text
    assert "Sign In" in response.text
    assert "Reuse Workbench Session" in response.text
    assert "Load Approval Rail" in response.text
    assert "Load Release Readiness" in response.text
    assert "Protected APIs" in response.text
    assert "Phase 0.5 Demo Presets" in response.text
    assert "Config Parent" in response.text
    assert "Doc UI Product" in response.text
    assert "Load BOM Demo" in response.text
    assert "Load Change Demo" in response.text
    assert "Resume Demo Sync" in response.text
    assert "Approval detail lens has not been loaded yet." in response.text
    assert "Resource detail lens has not been loaded yet." in response.text
    assert "Open Entity BOM" in response.text
    assert "Sync Active Object" in response.text
    assert "Active object context has not been loaded yet." in response.text
    assert "Change workflow has not been loaded yet." in response.text
    assert "File Attachments" in response.text
    assert "Related Documents (AML)" in response.text
    assert "Document Surface Status" in response.text
    assert "Document overview has not been loaded yet." in response.text
    assert "Document surfaces unavailable" in response.text
    assert "Current documents view is partial." in response.text
    assert "Documents partial:" in response.text
    assert "Documents unavailable" in response.text
    assert "/aml/query (expand ${RELATED_DOCUMENT_RELATION})" in response.text
    assert 'fetchJson("/aml/query"' in response.text
    assert 'fetchJson("/api/aml/query"' not in response.text
    assert "Attachments Source" in response.text
    assert "AML Source" in response.text
    assert "These status lines describe the two document surfaces separately: physical file attachments and AML related documents." in response.text
    assert "No AML related documents loaded yet." in response.text
    assert "Open Governance Rail" in response.text
    assert "ECO Focus" in response.text
    assert "Focus ECO" in response.text
    assert "Open Change" in response.text
    assert "Return to Source Detail" in response.text
    assert "Return to Source Change" in response.text
    assert "Source Recovery" in response.text
    assert "Source Object" in response.text
    assert "Source Files" in response.text
    assert "Source AML Docs" in response.text
    assert "Document Workspace" in response.text
    assert "Document Boundary" in response.text
    assert "Document Focus" in response.text
    assert "Document Source" in response.text
    assert "Governance Boundary" in response.text
    assert "Not published for this object" in response.text
    assert "Recent ECO Activity" in response.text
    assert "Workspace Journey" in response.text
    assert "Journey Path" in response.text
    assert "Current Object" in response.text
    assert "Handoff from" in response.text
    assert "Return to Source Product" in response.text
    assert "Return to Source Documents" in response.text
    assert "Return to Source Change" in response.text
    assert "Open Change Workspace" in response.text
    assert "Viewing related document object." in response.text
    assert "Phase 0.5 Demo Track" in response.text
    assert "BOM Review (MetaSheet)" in response.text
    assert "Open BOM Review" in response.text
    assert "Re-authorize" in response.text
    assert 'fetchJson("/integrations/capabilities"' in response.text
    assert '/bom/multitable/${encodeURIComponent(partId)}/embed-token' in response.text
    assert 'body: { origin }' in response.text
    assert 'postMessage({ type: "plm-embed:token", token }, origin)' in response.text
    assert response.text.index('frame.addEventListener("load", postToken, { once: true })') < response.text.index(
        "frame.src = embedUrl"
    )
    assert "payload.embed_token" in response.text
    assert "localStorage.setItem(storageKeys.workspaceBearerToken" in response.text
    assert "localStorage.setItem(storageKeys.metasheet" not in response.text


def test_metasheet_embed_url_uses_yuantus_env_prefix(monkeypatch):
    monkeypatch.setenv("YUANTUS_METASHEET_EMBED_URL", "https://metasheet.example/plm-embed/bom-review")
    monkeypatch.setenv("METASHEET_EMBED_URL", "https://wrong.example/plm-embed/bom-review")
    get_settings.cache_clear()
    try:
        assert get_settings().METASHEET_EMBED_URL == "https://metasheet.example/plm-embed/bom-review"
    finally:
        get_settings.cache_clear()


def test_plm_workspace_injects_configured_metasheet_embed(monkeypatch):
    monkeypatch.setenv("YUANTUS_METASHEET_EMBED_URL", "https://metasheet.example/plm-embed/bom-review")
    monkeypatch.setenv("YUANTUS_EMBED_ALLOWED_ORIGINS", "https://metasheet.example")
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_SIGNING_KEY", "non-empty-test-key")
    get_settings.cache_clear()
    try:
        response = _client().get("/api/v1/plm-workspace")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert 'metasheetEmbedUrl: "https://metasheet.example/plm-embed/bom-review"' in response.text
    assert 'metasheetEmbedOrigin: "https://metasheet.example"' in response.text
    assert 'metasheetEmbedConfigured: "true" === "true"' in response.text


def test_plm_workspace_disables_metasheet_embed_when_url_missing(monkeypatch):
    monkeypatch.delenv("YUANTUS_METASHEET_EMBED_URL", raising=False)
    monkeypatch.setenv("YUANTUS_EMBED_ALLOWED_ORIGINS", "https://metasheet.example")
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_SIGNING_KEY", "non-empty-test-key")
    get_settings.cache_clear()
    try:
        response = _client().get("/api/v1/plm-workspace")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert 'metasheetEmbedUrl: ""' in response.text
    assert 'metasheetEmbedOrigin: ""' in response.text
    assert 'metasheetEmbedConfigured: "false" === "true"' in response.text


def test_plm_workspace_disables_metasheet_embed_when_url_invalid(monkeypatch):
    monkeypatch.setenv("YUANTUS_METASHEET_EMBED_URL", "not-a-url")
    monkeypatch.setenv("YUANTUS_EMBED_ALLOWED_ORIGINS", "https://metasheet.example")
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_SIGNING_KEY", "non-empty-test-key")
    get_settings.cache_clear()
    try:
        response = _client().get("/api/v1/plm-workspace")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert 'metasheetEmbedUrl: "not-a-url"' in response.text
    assert 'metasheetEmbedOrigin: ""' in response.text
    assert 'metasheetEmbedConfigured: "false" === "true"' in response.text


def test_plm_workspace_disables_metasheet_embed_when_origin_not_allowlisted(monkeypatch):
    monkeypatch.setenv("YUANTUS_METASHEET_EMBED_URL", "https://metasheet.example/plm-embed/bom-review")
    monkeypatch.setenv("YUANTUS_EMBED_ALLOWED_ORIGINS", "https://other.example")
    monkeypatch.setenv("YUANTUS_EMBED_TOKEN_SIGNING_KEY", "non-empty-test-key")
    get_settings.cache_clear()
    try:
        response = _client().get("/api/v1/plm-workspace")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert 'metasheetEmbedOrigin: "https://metasheet.example"' in response.text
    assert 'metasheetEmbedConfigured: "false" === "true"' in response.text


def test_plm_workspace_route_registered_in_create_app():
    app = create_app()
    paths = _route_paths(app)

    assert "/api/v1/plm-workspace" in paths


def test_plm_workspace_page_is_public_path():
    assert _is_public_path("/api/v1/plm-workspace") is True
    assert _is_public_path("/api/v1/plm-workspace/") is True
