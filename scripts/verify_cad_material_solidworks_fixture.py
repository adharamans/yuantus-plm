#!/usr/bin/env python3
"""Verify the SDK-free SolidWorks material sync fixture.

The script intentionally models only property/table field extraction and
writeback package validation. It does not import SolidWorks COM, run Windows,
or claim real client smoke coverage.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "docs" / "samples" / "cad_material_solidworks_fixture.json"

CANONICAL_ALIASES = {
    "partnumber": "item_number",
    "partno": "item_number",
    "itemnumber": "item_number",
    "drawingno": "item_number",
    "description": "name",
    "partname": "name",
    "name": "name",
    "material": "material",
    "specification": "specification",
    "spec": "specification",
    "length": "length",
    "width": "width",
    "thickness": "thickness",
    "materialcategory": "material_category",
    "category": "material_category",
    "heattreatment": "heat_treatment",
    "coating": "finish",
    "finish": "finish",
}

FORBIDDEN_AUTOCAD_PRIMARY_KEYS = {
    "图号",
    "名称",
    "材料",
    "材质",
    "规格",
    "物料规格",
    "长",
    "长度",
    "宽",
    "宽度",
    "厚",
    "厚度",
}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def value_to_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def canonical_key(raw: Any) -> str:
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""

    text = re.sub(r"^SW[-_ ]*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"@(Part|CutList|Cut-List)$", "", text, flags=re.IGNORECASE)
    compact = (
        text.replace(" ", "")
        .replace("\t", "")
        .replace("-", "")
        .replace("_", "")
        .replace("：", "")
        .replace(":", "")
        .lower()
    )
    return CANONICAL_ALIASES.get(compact, compact)


def add_field(fields: dict[str, str], raw_key: Any, value: Any) -> bool:
    raw_value = value_to_string(value).strip()
    if raw_key is None or not str(raw_key).strip() or not raw_value:
        return False
    key = canonical_key(raw_key)
    if not key:
        return False
    fields[key] = raw_value
    return True


def extract_table_fields(fields: dict[str, str], table: list[list[Any]]) -> None:
    for row in table:
        col = 0
        while col < len(row):
            cell = value_to_string(row[col]).strip()
            if not cell:
                col += 1
                continue

            if "=" in cell and cell.index("=") > 0:
                raw_key, value = cell.split("=", 1)
                add_field(fields, raw_key, value)
                col += 1
                continue

            if col + 1 < len(row) and add_field(fields, cell, row[col + 1]):
                col += 1
            col += 1


def extract_fields(fixture: dict[str, Any]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_key, value in fixture.get("custom_properties", {}).items():
        add_field(fields, raw_key, value)

    for entry in fixture.get("cut_list_properties", []):
        for raw_key, value in entry.get("properties", {}).items():
            add_field(fields, raw_key, value)

    for table in fixture.get("tables", []):
        extract_table_fields(fields, table)
    return fields


def validate_writeback_package(fixture: dict[str, Any]) -> dict[str, str]:
    apply_fields = {
        str(key): value_to_string(value)
        for key, value in fixture.get("apply_fields", {}).items()
        if value_to_string(value).strip()
    }
    expected = {
        str(key): value_to_string(value)
        for key, value in fixture.get("expected_writeback_fields", {}).items()
    }

    require(apply_fields == expected, f"unexpected writeback fields: {apply_fields}")
    require(apply_fields, "writeback package must not be empty")

    forbidden = sorted(FORBIDDEN_AUTOCAD_PRIMARY_KEYS & set(apply_fields))
    require(not forbidden, "SolidWorks writeback package used AutoCAD keys: " + ", ".join(forbidden))

    invalid_solidworks_keys = sorted(
        key
        for key in apply_fields
        if not key.startswith("SW-") or ("@Part" not in key and "@CutList" not in key)
    )
    require(
        not invalid_solidworks_keys,
        "SolidWorks writeback keys must be SW-*@Part or SW-*@CutList: "
        + ", ".join(invalid_solidworks_keys),
    )

    extracted = extract_fields(fixture)
    undeclared = {
        "SW-HeatTreatment@Part": extracted.get("heat_treatment"),
        "SW-Coating@Part": extracted.get("finish"),
    }
    overwritten = sorted(key for key in undeclared if key in apply_fields)
    require(not overwritten, "writeback package overwrites undeclared fields: " + ", ".join(overwritten))
    return apply_fields


def verify_fixture(path: Path) -> None:
    fixture = read_json(path)
    extracted = extract_fields(fixture)
    require(extracted == fixture["expected_extract"], f"unexpected extract: {extracted}")
    validate_writeback_package(fixture)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify SDK-free SolidWorks CAD material sync fixture",
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Path to the SolidWorks fixture JSON file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        verify_fixture(args.fixture)
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("OK: SolidWorks material sync SDK-free fixture passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
