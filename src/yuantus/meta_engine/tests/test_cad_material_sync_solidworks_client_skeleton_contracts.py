"""Contracts for the SolidWorks CAD material client skeleton."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
CLIENT = ROOT / "clients/solidworks-material-sync"
SRC = CLIENT / "SolidWorksMaterialSync"
DEV_MD = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_SOLIDWORKS_CLIENT_SKELETON_R1_20260512.md"
)
TODO = ROOT / "docs/TODO_CAD_MATERIAL_SYNC_PLUGIN_20260506.md"
DOC_INDEX = ROOT / "docs/DELIVERY_DOC_INDEX.md"
CI_YML = ROOT / ".github/workflows/ci.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_solidworks_client_skeleton_files_exist() -> None:
    expected = {
        CLIENT / "README.md",
        CLIENT / "MANIFEST.md",
        SRC / "SolidWorksMaterialSync.csproj",
        SRC / "ICadMaterialFieldAdapter.cs",
        SRC / "ISolidWorksMaterialDocumentGateway.cs",
        SRC / "SolidWorksMaterialFieldAdapter.cs",
        SRC / "SolidWorksMaterialFieldMapper.cs",
        SRC / "SolidWorksDiffPreviewClient.cs",
        SRC / "SolidWorksWriteBackPlan.cs",
    }

    missing = sorted(str(path.relative_to(ROOT)) for path in expected if not path.exists())
    assert missing == []


def test_solidworks_gateway_pins_custom_property_manager_read_path() -> None:
    gateway = _text(SRC / "ISolidWorksMaterialDocumentGateway.cs")
    adapter = _text(SRC / "SolidWorksMaterialFieldAdapter.cs")
    mapper = _text(SRC / "SolidWorksMaterialFieldMapper.cs")

    for required in (
        "CustomPropertyManager.GetAll3",
        "CustomPropertyManager.Get6",
        "ReadCustomProperties",
        "ReadCutListProperties",
        "ReadTableRows",
        "ApplyCustomProperties",
    ):
        assert required in gateway

    for required in (
        "ICadMaterialFieldAdapter<ISolidWorksMaterialDocumentGateway>",
        "ExtractFields",
        "ApplyFields",
        "SolidWorksWriteBackPlan.FromWriteCadFields",
    ):
        assert required in adapter

    for required in (
        "SW-Part Number",
        "SW-Description",
        "SW-Material",
        "SW-Specification",
        "SW-MaterialCategory",
        "SW-Length",
        "SW-Width",
        "SW-Thickness",
        "heat_treatment",
        "SW-Coating",
        "SW-Finish",
        "finish",
    ):
        assert required in mapper


def test_solidworks_diff_preview_and_writeback_are_solidworks_only() -> None:
    diff_client = _text(SRC / "SolidWorksDiffPreviewClient.cs")
    write_plan = _text(SRC / "SolidWorksWriteBackPlan.cs")

    assert "/api/v1/plugins/cad-material-sync/diff/preview" in diff_client
    assert 'CadSystem = "solidworks"' in diff_client
    assert '"current_cad_fields"' in diff_client
    assert '"cad_system"' in diff_client
    assert "PostJsonAsync<Dictionary<string, object>" in diff_client

    assert "write_cad_fields" in write_plan
    assert "StartsWith(\"SW-\"" in write_plan
    assert "IndexOf('@') > 0" in write_plan
    for forbidden_label in ("材料", "规格", "长", "宽", "厚", "图号", "名称"):
        assert forbidden_label in write_plan


def test_solidworks_client_skeleton_avoids_autocad_and_binary_artifacts() -> None:
    source_text = "\n".join(_text(path) for path in SRC.glob("*.cs"))
    manifest = _text(CLIENT / "MANIFEST.md")
    readme = _text(CLIENT / "README.md")

    for forbidden in (
        "Autodesk.AutoCAD",
        "CADDedupPlugin",
        "PLMMATPROFILES",
        "PLMMATCOMPOSE",
        "PLMMATPUSH",
        "PLMMATPULL",
    ):
        assert forbidden not in source_text

    assert "No compiled DLLs." in manifest
    assert "No SolidWorks interop assemblies." in manifest
    assert "No filled Windows evidence." in manifest
    assert "Runtime acceptance still\nrequires the Windows evidence template" in readme


def test_solidworks_client_skeleton_todo_tracks_substeps_without_parent_completion() -> None:
    todo = _text(TODO)

    assert "- [ ] SolidWorks 明细表/属性表字段读取。" in todo
    assert (
        "    - [x] SDK-free SolidWorks client skeleton：定义 Add-in/COM gateway、"
        "`CustomPropertyManager` 字段读取 seam 和写回边界。"
    ) in todo
    assert "    - [ ] 真实 SolidWorks Add-in/COM 读取实现与 Windows smoke。" in todo
    assert "- [ ] SolidWorks 本地客户端可视化差异预览和确认写回 UI。" in todo
    assert (
        "    - [x] SDK-free SolidWorks diff-preview client skeleton：固定 "
        "`cad_system=solidworks`、`write_cad_fields` 和确认/取消边界。"
    ) in todo
    assert "    - [ ] 真实 SolidWorks 本地确认 UI、COM 写回和 Windows smoke。" in todo

    assert "- [x] SolidWorks 明细表/属性表字段读取。" not in todo
    assert "- [x] SolidWorks 本地客户端可视化差异预览和确认写回 UI。" not in todo


def test_solidworks_client_skeleton_artifacts_are_indexed_and_ci_wired() -> None:
    doc_index = _text(DOC_INDEX)
    ci_yml = _text(CI_YML)
    dev_md = _text(DEV_MD)

    assert str(DEV_MD.relative_to(ROOT)) in doc_index
    assert "test_cad_material_sync_solidworks_client_skeleton_contracts.py" in ci_yml
    assert "clients/solidworks-material-sync/" in dev_md
    assert "No SolidWorks SDK dependency is introduced." in dev_md
