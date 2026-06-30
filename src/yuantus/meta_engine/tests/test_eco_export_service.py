from __future__ import annotations

from io import BytesIO

import pytest

from yuantus.meta_engine.services.eco_export_service import (
    EcoImpactExportService,
    _spreadsheet_safe_cell,
)


@pytest.mark.parametrize(
    "payload",
    ["=1+1", "+1", "-1+1", "@SUM(A1)", "\tformula", "\nformula", "  =evil()"],
)
def test_spreadsheet_safe_cell_neutralizes_formula_like_text(payload):
    assert _spreadsheet_safe_cell(payload) == "'" + payload


def test_spreadsheet_safe_cell_preserves_benign_and_numeric_values():
    assert _spreadsheet_safe_cell("safe,=1+2") == "safe,=1+2"
    assert _spreadsheet_safe_cell("Part-001") == "Part-001"
    assert _spreadsheet_safe_cell(-5) == -5
    assert _spreadsheet_safe_cell(None) is None


def test_xlsx_formatting_applies_guard_after_json_formatting():
    svc = EcoImpactExportService({})
    assert svc._format_xlsx_cell({"formula": "=1+1"}) == '{"formula": "=1+1"}'
    assert svc._format_xlsx_cell(["=1+1"]) == '["=1+1"]'
    assert svc._format_xlsx_cell("=1+1") == "'=1+1"


def test_to_xlsx_neutralizes_formula_cells_when_openpyxl_is_available():
    openpyxl = pytest.importorskip("openpyxl")
    payload = {
        "eco_id": "=HYPERLINK(1)",
        "changed_product_id": "part-1",
        "impact_count": 1,
        "impact_level": "safe",
        "impact_score": -5,
        "impact_scope": "  =scope()",
        "impact_summary": {"risk": "+SUM(A1:A2)"},
        "bom_diff": {
            "changed": [
                {
                    "parent_id": "p",
                    "child_id": "c",
                    "relationship_id": "r",
                    "line_key": "@line",
                    "level": 1,
                    "changes": [
                        {
                            "field": "qty",
                            "left": {"old": "=1+1"},
                            "right": "safe,=1+2",
                            "severity": "high",
                        }
                    ],
                }
            ]
        },
    }

    workbook_bytes = EcoImpactExportService(payload).to_xlsx()
    workbook = openpyxl.load_workbook(BytesIO(workbook_bytes), data_only=False)

    assert workbook["Overview"]["B2"].value == "'=HYPERLINK(1)"
    assert workbook["Overview"]["B6"].value == -5
    assert workbook["Overview"]["B7"].value == "'  =scope()"
    assert workbook["Impact Summary"]["B2"].value == "'+SUM(A1:A2)"
    assert workbook["BOM Diff Changed"]["D2"].value == "'@line"
    assert workbook["BOM Diff Changed"]["G2"].value == '{"old": "=1+1"}'
    assert workbook["BOM Diff Changed"]["H2"].value == "safe,=1+2"
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                assert cell.data_type != "f"
