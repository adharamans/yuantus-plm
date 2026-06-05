#!/usr/bin/env python3
"""Validate CAD Material Sync Windows evidence before reviewer acceptance."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEMPLATE = (
    ROOT / "docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md"
)

PLACEHOLDER_VALUES = {
    "",
    "no",
    "pending",
    "yes | no",
    "confirm | cancel",
    "netload | bundle autoload",
    "todo",
    "tbd",
    "n/a",
    "na",
    "none",
}

SECRET_PATTERNS = (
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|token|password|secret)\s*[:=]\s*\S+", re.IGNORECASE),
)

FORBIDDEN_EVIDENCE_VALUE_TOKENS = (
    "mock fixture",
    "synthetic",
    "production customer",
)

REQUIRED_2018_FIELDS = (
    "Operator",
    "Review date",
    "Windows version",
    "AutoCAD install path",
    "Yuantus base URL",
    "Yuantus commit",
    "Test DWG description",
    "Preflight command",
    "Preflight result",
    "Preflight output path",
    "Build command",
    "Build result",
    "Compiled DLL path",
    "PackageContents path",
    "Load method",
    "Loaded DLL path",
    "AutoCAD command-line output",
    "Load result",
    "DEDUPHELP",
    "DEDUPCONFIG",
    "PLMMATPROFILES",
    "PLMMATCOMPOSE",
    "PLMMATPUSH",
    "PLMMATPULL",
    "PLMMATASSIST",
    "DWG file description",
    "Before material field value",
    "Diff preview screenshot path",
    "User action",
    "After material field value",
    "Save/reopen result",
    "Yuantus dry-run log path",
    "Yuantus real-write log path",
    "PLMMATASSIST resolve summary log path",
    "PLMMATASSIST resolve endpoint observed",
    "PLMMATASSIST resolve PLM log path",
    "PLMMATASSIST cancel result",
    "PLMMATASSIST cancel PLM item count check",
    "PLMMATASSIST cancel DWG unchanged check",
    "PLMMATASSIST Enter default No result",
    "PLMMATASSIST create confirmation result",
    "PLMMATASSIST create endpoint observed",
    "PLMMATASSIST created item id",
    "PLMMATASSIST created item number",
    "PLMMATASSIST created state",
    "PLMMATASSIST current state",
    "PLMMATASSIST draft check",
    "PLMMATASSIST create DWG write-back result",
    "PLMMATASSIST bind selected item id",
    "PLMMATASSIST bind diff preview endpoint observed",
    "PLMMATASSIST bind diff preview log path",
    "PLMMATASSIST bind cancel DWG unchanged check",
    "PLMMATASSIST bind confirm write result",
    "PLMMATASSIST bind apply-result endpoint observed",
    "PLMMATASSIST bind apply-result outcome",
    "Reviewer",
    "Decision date",
    "Reason",
)

REQUIRED_2024_FIELDS = (
    "AutoCAD 2024 ACADVER output",
    "AutoCAD 2024 build result",
    "AutoCAD 2024 load result",
    "AutoCAD 2024 command smoke result",
    "AutoCAD 2024 DWG write-back result",
)


def _normalize(value: str) -> str:
    return value.strip().strip("`").strip()


def _is_placeholder(value: str) -> bool:
    normalized = _normalize(value)
    if normalized.lower() in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith("<") and normalized.endswith(">"):
        return True
    return False


def _extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^([A-Za-z0-9 /._-]+):\s*(.*)$", line)
        if not match:
            continue
        key = match.group(1).strip()
        value = _normalize(match.group(2))
        fields[key] = value
    return fields


def _add_if(condition: bool, failures: list[str], message: str) -> None:
    if condition:
        failures.append(message)


def _field(fields: dict[str, str], name: str) -> str:
    return fields.get(name, "")


def _require_field(fields: dict[str, str], failures: list[str], name: str) -> None:
    value = _field(fields, name)
    _add_if(_is_placeholder(value), failures, f"{name} must be filled")


def _require_yes(fields: dict[str, str], failures: list[str], name: str) -> None:
    value = _field(fields, name).lower()
    _add_if(value != "yes", failures, f"{name} must be yes")


def _require_pass(fields: dict[str, str], failures: list[str], name: str) -> None:
    value = _field(fields, name).lower()
    _add_if("pass" not in value, failures, f"{name} must record a passed check")


def _validate_no_secrets(fields: dict[str, str], failures: list[str]) -> None:
    for name, value in fields.items():
        for pattern in SECRET_PATTERNS:
            if pattern.search(value):
                failures.append(f"{name} appears to contain a plaintext secret")
                break


def _validate_no_fake_evidence_values(fields: dict[str, str], failures: list[str]) -> None:
    for name, value in fields.items():
        lowered = value.lower()
        for token in FORBIDDEN_EVIDENCE_VALUE_TOKENS:
            if token in lowered:
                failures.append(f"{name} appears to contain non-real evidence: {token}")
                break


def validate(text: str, *, require_2024: bool = False) -> list[str]:
    fields = _extract_fields(text)
    failures: list[str] = []

    for name in REQUIRED_2018_FIELDS:
        _require_field(fields, failures, name)

    _add_if(
        _field(fields, "AutoCAD primary version") != "2018",
        failures,
        "AutoCAD primary version must be 2018",
    )
    _add_if(
        _field(fields, "AutoCAD ACADVER output") != "R22.0",
        failures,
        "AutoCAD ACADVER output must be R22.0",
    )
    _add_if(
        "CADDedupPlugin.dll" not in _field(fields, "Compiled DLL path"),
        failures,
        "Compiled DLL path must reference CADDedupPlugin.dll",
    )
    _add_if(
        _field(fields, "User action").lower() != "confirm",
        failures,
        "User action must be confirm for write-back acceptance",
    )
    _add_if(
        _field(fields, "Before material field value") == _field(fields, "After material field value"),
        failures,
        "Before/after material field values must differ",
    )
    _add_if(
        "/material/assistant/resolve"
        not in _field(fields, "PLMMATASSIST resolve endpoint observed"),
        failures,
        "PLMMATASSIST resolve endpoint observed must include /material/assistant/resolve",
    )
    _add_if(
        "/material/assistant/create"
        not in _field(fields, "PLMMATASSIST create endpoint observed"),
        failures,
        "PLMMATASSIST create endpoint observed must include /material/assistant/create",
    )
    for name in (
        "PLMMATASSIST cancel result",
        "PLMMATASSIST cancel PLM item count check",
        "PLMMATASSIST cancel DWG unchanged check",
        "PLMMATASSIST Enter default No result",
        "PLMMATASSIST create confirmation result",
        "PLMMATASSIST draft check",
    ):
        _require_pass(fields, failures, name)
    assistant_write_back = _field(
        fields, "PLMMATASSIST create DWG write-back result"
    ).lower()
    _add_if(
        "no dwg" not in assistant_write_back
        and "not written" not in assistant_write_back,
        failures,
        "PLMMATASSIST create DWG write-back result must record no DWG write-back",
    )
    # Phase 4 existing-item bind/write-back branch.
    _add_if(
        "/diff/preview"
        not in _field(fields, "PLMMATASSIST bind diff preview endpoint observed"),
        failures,
        "PLMMATASSIST bind diff preview endpoint observed must include /diff/preview",
    )
    _add_if(
        "/audit/apply-result"
        not in _field(fields, "PLMMATASSIST bind apply-result endpoint observed"),
        failures,
        "PLMMATASSIST bind apply-result endpoint observed must include /audit/apply-result",
    )
    _add_if(
        "ok" not in _field(fields, "PLMMATASSIST bind apply-result outcome").lower(),
        failures,
        "PLMMATASSIST bind apply-result outcome must record ok",
    )
    for name in (
        "PLMMATASSIST bind cancel DWG unchanged check",
        "PLMMATASSIST bind confirm write result",
    ):
        _require_pass(fields, failures, name)

    for name in (
        "AutoCAD 2018 support complete",
        "Real DWG write-back validated",
        "Windows client runtime accepted",
    ):
        _require_yes(fields, failures, name)

    _add_if(
        _field(fields, "Decision").lower() != "accept",
        failures,
        "Decision must be accept after reviewer approval",
    )

    regression_complete = _field(fields, "AutoCAD 2024 regression complete").lower()
    if require_2024 or regression_complete == "yes":
        _add_if(
            _field(fields, "AutoCAD regression version") != "2024",
            failures,
            "AutoCAD regression version must be 2024",
        )
        _add_if(
            regression_complete != "yes",
            failures,
            "AutoCAD 2024 regression complete must be yes",
        )
        for name in REQUIRED_2024_FIELDS:
            _require_field(fields, failures, name)

    _validate_no_secrets(fields, failures)
    _validate_no_fake_evidence_values(fields, failures)
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a filled CAD Material Sync Windows evidence markdown file. "
            "This validates evidence shape only; it does not run AutoCAD."
        )
    )
    parser.add_argument(
        "evidence",
        nargs="?",
        default=str(DEFAULT_TEMPLATE),
        help="Path to the filled evidence markdown file",
    )
    parser.add_argument(
        "--require-2024",
        action="store_true",
        help="also require the AutoCAD 2024 regression section to be complete",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit a redaction-safe machine-readable result",
    )
    args = parser.parse_args(argv)

    evidence_path = Path(args.evidence)
    if not evidence_path.is_file():
        failures = [f"evidence file does not exist: {evidence_path}"]
        if args.json:
            print(_json_report(evidence_path, failures, require_2024=args.require_2024))
        else:
            print(f"FAIL: {failures[0]}", file=sys.stderr)
        return 1

    failures = validate(
        evidence_path.read_text(encoding="utf-8", errors="replace"),
        require_2024=args.require_2024,
    )
    if args.json:
        print(_json_report(evidence_path, failures, require_2024=args.require_2024))
        return 1 if failures else 0

    if failures:
        print("FAIL: CAD material Windows evidence is not acceptable")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("OK: CAD material Windows evidence shape is acceptable")
    return 0


def _json_report(evidence_path: Path, failures: list[str], *, require_2024: bool) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "ok": not failures,
            "evidence": str(evidence_path),
            "require_2024": require_2024,
            "failure_count": len(failures),
            "failures": failures,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
