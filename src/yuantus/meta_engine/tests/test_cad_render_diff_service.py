"""Visual diff wiring: the render_containers_visual_diff helper (resolves two
revisions → render service /diff) and the cad_diff_router visual-diff route.
Seams patched — no DB / live render service / renderer needed."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from yuantus.integrations.render_service import RenderDiffResult
from yuantus.meta_engine.tasks import cad_pipeline_tasks as cpt
from yuantus.meta_engine.web import cad_diff_router as router_mod
from yuantus.meta_engine.web.cad_diff_router import visual_diff_cad_render


def _container(cid: str, ext: str = "dxf"):
    fc = MagicMock()
    fc.id = cid
    fc.filename = f"drawing_{cid}.{ext}"
    fc.system_path = f"/vault/drawing_{cid}.{ext}"
    fc.get_extension.return_value = ext
    return fc


# ── helper: render_containers_visual_diff (mirrors S2 _run seams) ──
def test_render_containers_visual_diff_renders_both_revisions(tmp_path):
    a, b = _container("a"), _container("b")
    (tmp_path / "a.dxf").write_bytes(b"DXF-A")
    (tmp_path / "b.dxf").write_bytes(b"DXF-B")
    conv = MagicMock()
    conv._get_file_path.side_effect = lambda c: str(tmp_path / f"{c.id}.dxf")
    render_client = MagicMock()
    render_client.render_diff_sync.return_value = RenderDiffResult(
        b"\x89PNGoverlay", "image/png", {"x-diff-comparable": "true"}
    )

    with patch.object(cpt, "FileService", MagicMock()), \
         patch.object(cpt, "_ensure_source_exists", MagicMock()), \
         patch.object(cpt, "_is_s3_storage", return_value=False), \
         patch.object(cpt, "_vault_base_path", return_value=str(tmp_path)), \
         patch.object(cpt, "CADConverterService", return_value=conv), \
         patch.object(cpt, "RenderServiceClient", return_value=render_client):
        res = cpt.render_containers_visual_diff(MagicMock(), a, b)

    assert res.content == b"\x89PNGoverlay"
    render_client.render_diff_sync.assert_called_once()
    kwargs = render_client.render_diff_sync.call_args.kwargs
    assert kwargs["file_a"].endswith("a.dxf")
    assert kwargs["file_b"].endswith("b.dxf")
    assert kwargs["filename_a"] == "drawing_a.dxf"
    assert kwargs["filename_b"] == "drawing_b.dxf"


def test_render_containers_visual_diff_cleans_up_s3_temps(tmp_path):
    # use_s3 path: each source is downloaded to a temp file that must be removed.
    a, b = _container("a"), _container("b")
    made = []

    def fake_download(_fs, _system_path, suffix=""):
        p = tmp_path / f"tmp{len(made)}{suffix}"
        p.write_bytes(b"x")
        made.append(str(p))
        return str(p)

    render_client = MagicMock()
    render_client.render_diff_sync.return_value = RenderDiffResult(b"PNG", "image/png", {})

    with patch.object(cpt, "FileService", MagicMock()), \
         patch.object(cpt, "_ensure_source_exists", MagicMock()), \
         patch.object(cpt, "_is_s3_storage", return_value=True), \
         patch.object(cpt, "_vault_base_path", return_value=str(tmp_path)), \
         patch.object(cpt, "_download_to_temp", side_effect=fake_download), \
         patch.object(cpt, "RenderServiceClient", return_value=render_client):
        cpt.render_containers_visual_diff(MagicMock(), a, b)

    assert len(made) == 2
    import os
    assert all(not os.path.exists(p) for p in made)   # temps cleaned up


# ── route guards (call the handler directly; Depends bypassed) ──
def _settings(base_url="http://render:8077"):
    return SimpleNamespace(RENDER_SERVICE_BASE_URL=base_url)


def test_route_503_when_render_service_not_configured():
    with patch.object(router_mod, "get_settings", return_value=_settings("")):
        with pytest.raises(HTTPException) as ei:
            visual_diff_cad_render(file_id="a", other_file_id="b",
                                   user=MagicMock(), db=MagicMock())
    assert ei.value.status_code == 503


def test_route_422_when_other_missing():
    with patch.object(router_mod, "get_settings", return_value=_settings()):
        with pytest.raises(HTTPException) as ei:
            visual_diff_cad_render(file_id="a", other_file_id=None, other_id=None,
                                   user=MagicMock(), db=MagicMock())
    assert ei.value.status_code == 422


def test_route_404_when_file_missing():
    db = MagicMock(); db.get.return_value = None
    with patch.object(router_mod, "get_settings", return_value=_settings()):
        with pytest.raises(HTTPException) as ei:
            visual_diff_cad_render(file_id="a", other_file_id="b",
                                   user=MagicMock(), db=db)
    assert ei.value.status_code == 404


def test_route_422_when_not_dxf():
    db = MagicMock(); db.get.return_value = _container("a", ext="dwg")
    with patch.object(router_mod, "get_settings", return_value=_settings()):
        with pytest.raises(HTTPException) as ei:
            visual_diff_cad_render(file_id="a", other_file_id="b",
                                   user=MagicMock(), db=db)
    assert ei.value.status_code == 422


def test_route_returns_png_with_summary_headers():
    db = MagicMock(); db.get.return_value = _container("a")
    result = RenderDiffResult(
        b"\x89PNGdata", "image/png",
        {"X-Diff-Comparable": "true", "X-Diff-Changed-Fraction": "0.1"},
    )
    with patch.object(router_mod, "get_settings", return_value=_settings()), \
         patch.object(router_mod, "render_containers_visual_diff", return_value=result):
        resp = visual_diff_cad_render(file_id="a", other_file_id="b",
                                      user=MagicMock(), db=db)
    assert resp.status_code == 200
    assert resp.media_type == "image/png"
    assert resp.body.startswith(b"\x89PNG")
    assert resp.headers["X-Diff-Comparable"] == "true"


def test_route_502_when_render_fails():
    db = MagicMock(); db.get.return_value = _container("a")
    with patch.object(router_mod, "get_settings", return_value=_settings()), \
         patch.object(router_mod, "render_containers_visual_diff",
                      side_effect=RuntimeError("svc down")):
        with pytest.raises(HTTPException) as ei:
            visual_diff_cad_render(file_id="a", other_file_id="b",
                                   user=MagicMock(), db=db)
    assert ei.value.status_code == 502
