"""Contracts for the SDK-free SolidWorks CAD material sync fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
FIXTURE = ROOT / "docs/samples/cad_material_solidworks_fixture.json"
SCRIPT = ROOT / "scripts/verify_cad_material_solidworks_fixture.py"
TODO = ROOT / "docs/TODO_CAD_MATERIAL_SYNC_PLUGIN_20260506.md"
DEV_MD = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_SOLIDWORKS_FIXTURE_R1_20260511.md"
)
DOC_INDEX = ROOT / "docs/DELIVERY_DOC_INDEX.md"
SCRIPTS_INDEX = ROOT / "docs/DELIVERY_SCRIPTS_INDEX_20260202.md"
CI_YML = ROOT / ".github/workflows/ci.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_solidworks_fixture_shape_pins_property_table_and_writeback_fields() -> None:
    fixture = json.loads(_text(FIXTURE))

    assert fixture["custom_properties"]["SW-Part Number@Part"] == "SW-1001"
    assert fixture["custom_properties"]["SW-Material@Part"] == "Q235B"
    assert fixture["cut_list_properties"][0]["properties"] == {
        "SW-Length@CutList": "1200",
        "SW-Width@CutList": "600",
    }
    assert fixture["expected_extract"] == {
        "item_number": "SW-1001",
        "name": "支撑板",
        "material": "Q235B",
        "thickness": "12",
        "length": "1200",
        "width": "600",
        "material_category": "sheet",
        "specification": "old-spec",
        "finish": "painted",
        "heat_treatment": "none",
    }
    assert fixture["expected_writeback_fields"] == {
        "SW-Material@Part": "Q355B",
        "SW-Specification@Part": "1200*600*12",
        "SW-Length@Part": "1200",
        "SW-Width@Part": "600",
        "SW-Thickness@Part": "12",
    }

    forbidden_autocad_keys = {"材料", "规格", "长", "宽", "厚", "图号", "名称"}
    assert forbidden_autocad_keys.isdisjoint(fixture["expected_writeback_fields"])
    assert "SW-HeatTreatment@Part" not in fixture["expected_writeback_fields"]
    assert "SW-Coating@Part" not in fixture["expected_writeback_fields"]


def test_solidworks_fixture_script_runs_without_sdk_or_windows_dependencies() -> None:
    script = _text(SCRIPT)

    for required in (
        "--fixture",
        "canonical_key",
        "validate_writeback_package",
        "FORBIDDEN_AUTOCAD_PRIMARY_KEYS",
    ):
        assert required in script

    for forbidden in ("win32com", "pythoncom", "SldWorks.Application"):
        assert forbidden not in script

    cp = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "OK: SolidWorks material sync SDK-free fixture passed" in cp.stdout


def test_solidworks_todo_keeps_real_client_work_incomplete() -> None:
    todo = _text(TODO)

    assert "- [ ] SolidWorks 明细表/属性表字段读取。" in todo
    assert (
        "  - [x] SDK-free SolidWorks 属性表/明细表 fixture 与 contract，"
        "固定字段归一化和写回字段包边界。"
    ) in todo
    assert "  - [ ] 真实 SolidWorks Add-in/COM 读取实现与 Windows smoke。" in todo
    assert "- [ ] SolidWorks 本地客户端可视化差异预览和确认写回 UI。" in todo
    assert "- [x] SolidWorks 明细表/属性表字段读取。" not in todo


def test_solidworks_fixture_artifacts_are_indexed_and_ci_wired() -> None:
    doc_index = _text(DOC_INDEX)
    scripts_index = _text(SCRIPTS_INDEX)
    ci_yml = _text(CI_YML)

    assert str(DEV_MD.relative_to(ROOT)) in doc_index
    assert "verify_cad_material_solidworks_fixture.py" in scripts_index
    assert "test_cad_material_sync_solidworks_fixture_contracts.py" in ci_yml
    assert str(FIXTURE.relative_to(ROOT)) in _text(DEV_MD)
    assert str(SCRIPT.relative_to(ROOT)) in _text(DEV_MD)
