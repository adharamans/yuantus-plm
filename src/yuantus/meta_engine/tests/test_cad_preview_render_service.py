"""S2 — cad_preview() render-service wiring. Verifies the branching (render
service preferred for DXF; CAD-ML fallback on failure; render service NOT used
for DWG) by patching the seams — no DB / live service / renderer needed."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from yuantus.meta_engine.tasks import cad_pipeline_tasks as cpt


def _settings(*, render_url="http://render:8077", cad_ml_url="http://cadml:8001"):
    return SimpleNamespace(
        RENDER_SERVICE_BASE_URL=render_url,
        CAD_ML_BASE_URL=cad_ml_url,
        CAD_ML_SERVICE_TOKEN="",
    )


def _fc(ext="dxf"):
    fc = MagicMock()
    fc.id = "fc-1"
    fc.preview_path = None
    fc.preview_data = None
    fc.filename = f"drawing.{ext}"
    fc.system_path = f"/vault/drawing.{ext}"
    fc.document_type = "2d"
    fc.conversion_status = "PENDING"
    fc.get_extension.return_value = ext
    return fc


def _run(monkeypatch, tmp_path, ext, *, render_ok=True, render_raises=False,
         render_url="http://render:8077"):
    fc = _fc(ext)
    session = MagicMock()
    session.get.return_value = fc

    src = tmp_path / f"drawing.{ext}"
    src.write_bytes(b"0\nSECTION\n2\nENTITIES\n0\nEOF\n")
    png = cpt._create_minimal_png_bytes(600, 600)

    conv = MagicMock()
    conv._get_file_path.return_value = str(src)
    conv._get_generated_dir.return_value = str(tmp_path)
    conv._generate_preview.return_value = str(tmp_path / "fallback_local.png")

    render_client = MagicMock()
    if render_raises:
        render_client.render_preview_sync.side_effect = RuntimeError("svc down")
    else:
        render_client.render_preview_sync.return_value = png if render_ok else b""
    cadml_client = MagicMock()
    cadml_client.render_cad_preview_sync.return_value = png

    with patch.object(cpt, "FileService", MagicMock()), \
         patch.object(cpt, "_ensure_source_exists", MagicMock()), \
         patch.object(cpt, "_is_s3_storage", return_value=False), \
         patch.object(cpt, "_vault_base_path", return_value=str(tmp_path)), \
         patch.object(cpt, "_require_connector_for_remote_3d", MagicMock()), \
         patch.object(cpt, "_cad_connector_enabled_for_file", return_value=False), \
         patch.object(cpt, "CADConverterService", return_value=conv), \
         patch.object(cpt, "RenderServiceClient", return_value=render_client), \
         patch.object(cpt, "CadMLClient", return_value=cadml_client), \
         patch.object(cpt, "get_settings", return_value=_settings(render_url=render_url)):
        result = cpt.cad_preview({"file_id": "fc-1"}, session)
    return result, fc, render_client, cadml_client, conv


def test_dxf_prefers_render_service(monkeypatch, tmp_path):
    result, fc, render_client, cadml_client, conv = _run(monkeypatch, tmp_path, "dxf")
    assert result["ok"] is True
    render_client.render_preview_sync.assert_called_once()
    # render service produced the preview → CAD-ML NOT consulted, local not used
    cadml_client.render_cad_preview_sync.assert_not_called()
    conv._generate_preview.assert_not_called()
    assert fc.preview_path  # stored


def test_dxf_falls_back_to_cadml_when_render_service_fails(monkeypatch, tmp_path):
    result, fc, render_client, cadml_client, conv = _run(
        monkeypatch, tmp_path, "dxf", render_raises=True)
    assert result["ok"] is True
    render_client.render_preview_sync.assert_called_once()   # tried
    cadml_client.render_cad_preview_sync.assert_called_once()  # fell back
    assert fc.preview_path


def test_dwg_does_not_use_render_service(monkeypatch, tmp_path):
    # render service v0 rejects .dwg → DWG must skip it and use CAD-ML.
    result, fc, render_client, cadml_client, conv = _run(monkeypatch, tmp_path, "dwg")
    assert result["ok"] is True
    render_client.render_preview_sync.assert_not_called()
    cadml_client.render_cad_preview_sync.assert_called_once()


def test_render_service_disabled_keeps_cadml_path(monkeypatch, tmp_path):
    # Empty RENDER_SERVICE_BASE_URL = status quo: render client never invoked.
    result, fc, render_client, cadml_client, conv = _run(
        monkeypatch, tmp_path, "dxf", render_url="")
    assert result["ok"] is True
    render_client.render_preview_sync.assert_not_called()
    cadml_client.render_cad_preview_sync.assert_called_once()
