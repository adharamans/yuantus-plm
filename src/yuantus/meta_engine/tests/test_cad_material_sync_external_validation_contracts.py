"""Contracts for CAD material sync external Windows validation handoff."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[4]
HANDOFF = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_EXTERNAL_VALIDATION_HANDOFF_20260511.md"
)
EVIDENCE_TEMPLATE = (
    ROOT / "docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md"
)
EVIDENCE_TEMPLATE_DV = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_WINDOWS_EVIDENCE_TEMPLATE_20260511.md"
)
EVIDENCE_VALIDATOR_DV = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_WINDOWS_EVIDENCE_VALIDATOR_20260511.md"
)
EVIDENCE_VALIDATOR_JSON_DV = (
    ROOT
    / "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_WINDOWS_EVIDENCE_VALIDATOR_JSON_20260511.md"
)
TODO = ROOT / "docs/TODO_CAD_MATERIAL_SYNC_PLUGIN_20260506.md"
WINDOWS_GUIDE = (
    ROOT / "clients/autocad-material-sync/WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md"
)
VALIDATOR = ROOT / "scripts/validate_cad_material_windows_evidence.py"
SCRIPTS_INDEX = ROOT / "docs/DELIVERY_SCRIPTS_INDEX_20260202.md"
INDEX = ROOT / "docs/DELIVERY_DOC_INDEX.md"
CI_YML = ROOT / ".github/workflows/ci.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_external_validation_handoff_records_post_merge_boundary() -> None:
    handoff = _text(HANDOFF)

    for phrase in (
        "PR #495 merged the CAD Material Sync delivery package on `main=8593911`.",
        "remaining validation is external: Windows + AutoCAD 2018 DLL loading and real\nDWG write-back smoke",
        "not replaced by macOS/Linux\nfixture, SQLite, Playwright, or packaging checks",
        "Windows + AutoCAD 2018 real smoke: pending.",
        "Windows + AutoCAD 2024 regression smoke: pending.",
        "It is not itself the Windows validation result.",
        "No fake or synthetic Windows evidence.",
        "No Phase 5 implementation.",
        "No P3.4 evidence creation or acceptance.",
    ):
        assert phrase in handoff

    for forbidden in (
        "AutoCAD 2018 support complete: true",
        "Real DWG write-back validated: true",
        "Windows client runtime accepted: true",
    ):
        assert forbidden not in handoff


def test_todo_keeps_external_windows_smokes_unchecked() -> None:
    todo = _text(TODO)

    assert "- [ ] 在 Windows + AutoCAD 2018 环境编译 DLL 并做真实 DWG 手工 smoke。" in todo
    assert "- [ ] 在 Windows + AutoCAD 2024 环境做回归 smoke，确保高版本路径未退化。" in todo
    assert "- [x] 在 Windows + AutoCAD 2018 环境编译 DLL 并做真实 DWG 手工 smoke。" not in todo
    assert "- [x] 在 Windows + AutoCAD 2024 环境做回归 smoke，确保高版本路径未退化。" not in todo


def test_windows_validation_guide_preserves_required_evidence_and_exit_criteria() -> None:
    guide = _text(WINDOWS_GUIDE)

    for phrase in (
        "This guide does not replace macOS-side service, fixture, or contract tests.",
        "AutoCAD `ACADVER` output showing `R22.0`.",
        "Preflight output.",
        "Build output and DLL path.",
        "Screenshot or command log for `DEDUPHELP`, `PLMMATPROFILES`, `PLMMATCOMPOSE`, `PLMMATPUSH`, `PLMMATPULL`, `PLMMATASSIST`.",
        "Before/after screenshot of the DWG material field.",
        "Preflight passes on the Windows machine.",
        "DLL builds against AutoCAD 2018 assemblies.",
        "AutoCAD 2018 loads the plugin.",
        "A real DWG title block or table field is updated and persists after save/reopen.",
    ):
        assert phrase in guide


def test_windows_evidence_template_is_blank_and_not_acceptance() -> None:
    template = _text(EVIDENCE_TEMPLATE)

    for phrase in (
        "Status: **template only; not validation evidence**",
        "Do not mark the Windows validation complete\nuntil this template is filled with real operator output and reviewed.",
        "AutoCAD primary version: `2018`",
        "AutoCAD ACADVER output: `R22.0`",
        "AutoCAD regression version: `2024`",
        "AutoCAD 2018 support complete: no",
        "Real DWG write-back validated: no",
        "Windows client runtime accepted: no",
        "AutoCAD 2024 regression complete: no",
        "Decision: pending",
        "must remain `no` and the decision must remain `pending`",
        "The DWG write-back result uses a mock fixture instead of a real DWG.",
        "The saved DWG was not reopened to confirm persistence.",
        "Any plaintext token, password, or production customer drawing content appears\n  in the evidence.",
        "python3 scripts/validate_cad_material_windows_evidence.py",
        "Windows + AutoCAD 2018 evidence is not recorded.",
        "AutoCAD 2024 regression evidence is not recorded.",
    ):
        assert phrase in template

    for forbidden in (
        "AutoCAD 2018 support complete: yes\n",
        "Real DWG write-back validated: yes\n",
        "Windows client runtime accepted: yes\n",
        "AutoCAD 2024 regression complete: yes\n",
        "Decision: accept\n",
    ):
        assert forbidden not in template


def _minimal_real_2018_evidence(*, regression_complete: bool = False) -> str:
    regression_result = "yes" if regression_complete else "no"
    regression_fields = {
        "AutoCAD 2024 ACADVER output": "R24.3",
        "AutoCAD 2024 build result": "passed",
        "AutoCAD 2024 load result": "passed",
        "AutoCAD 2024 command smoke result": "passed",
        "AutoCAD 2024 DWG write-back result": "passed",
    }
    if not regression_complete:
        regression_fields = {key: "" for key in regression_fields}

    lines = [
        "# Filled CAD Material Sync Windows Evidence",
        "Operator: reviewer-one",
        "Review date: 2026-05-11",
        "Windows version: Windows 11 x64",
        "AutoCAD primary version: 2018",
        "AutoCAD ACADVER output: R22.0",
        "AutoCAD install path: C:/Program Files/Autodesk/AutoCAD 2018",
        "Yuantus base URL: http://127.0.0.1:7910",
        "Yuantus commit: f0598c8",
        "Test DWG description: sanitized-copy",
        "Preflight command: verify_autocad2018_preflight.ps1 -RunBuild",
        "Preflight result: passed",
        "Preflight output path: evidence/preflight.txt",
        "Build command: build_simple.bat",
        "Build result: passed",
        "Compiled DLL path: evidence/CADDedupPlugin.dll",
        "PackageContents path: evidence/PackageContents.xml",
        "Load method: NETLOAD",
        "Loaded DLL path: evidence/CADDedupPlugin.dll",
        "AutoCAD command-line output: evidence/autocad-command-log.txt",
        "Load result: passed",
        "DEDUPHELP: passed",
        "DEDUPCONFIG: passed",
        "PLMMATPROFILES: passed",
        "PLMMATCOMPOSE: passed",
        "PLMMATPUSH: passed",
        "PLMMATPULL: passed",
        "DWG file description: sanitized-copy.dwg",
        "Before material field value: old-spec",
        "Diff preview screenshot path: evidence/diff-preview.png",
        "User action: confirm",
        "After material field value: 1200*600*12",
        "Save/reopen result: passed and persisted",
        "Yuantus dry-run log path: evidence/dry-run.log",
        "Yuantus real-write log path: evidence/real-write.log",
        "AutoCAD regression version: 2024",
        f"AutoCAD 2024 ACADVER output: {regression_fields['AutoCAD 2024 ACADVER output']}",
        f"AutoCAD 2024 build result: {regression_fields['AutoCAD 2024 build result']}",
        f"AutoCAD 2024 load result: {regression_fields['AutoCAD 2024 load result']}",
        f"AutoCAD 2024 command smoke result: {regression_fields['AutoCAD 2024 command smoke result']}",
        f"AutoCAD 2024 DWG write-back result: {regression_fields['AutoCAD 2024 DWG write-back result']}",
        "AutoCAD 2018 support complete: yes",
        "Real DWG write-back validated: yes",
        "Windows client runtime accepted: yes",
        f"AutoCAD 2024 regression complete: {regression_result}",
        "Reviewer: reviewer-two",
        "Decision date: 2026-05-11",
        "Decision: accept",
        "Reason: real Windows AutoCAD smoke evidence reviewed",
    ]
    return "\n".join(lines) + "\n"


def _run_validator(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(path), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_windows_evidence_validator_rejects_blank_template() -> None:
    cp = _run_validator(EVIDENCE_TEMPLATE)

    assert cp.returncode == 1
    assert "FAIL: CAD material Windows evidence is not acceptable" in cp.stdout
    assert "AutoCAD 2018 support complete must be yes" in cp.stdout
    assert "Decision must be accept after reviewer approval" in cp.stdout


def test_windows_evidence_validator_accepts_minimal_real_2018_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "filled-cad-material-windows-evidence.md"
    evidence.write_text(_minimal_real_2018_evidence(), encoding="utf-8")

    cp = _run_validator(evidence)

    assert cp.returncode == 0, cp.stdout + cp.stderr
    assert "OK: CAD material Windows evidence shape is acceptable" in cp.stdout


def test_windows_evidence_validator_rejects_incomplete_2024_claim(tmp_path: Path) -> None:
    evidence = tmp_path / "filled-cad-material-windows-evidence.md"
    evidence.write_text(
        _minimal_real_2018_evidence().replace(
            "AutoCAD 2024 regression complete: no",
            "AutoCAD 2024 regression complete: yes",
        ),
        encoding="utf-8",
    )

    cp = _run_validator(evidence)

    assert cp.returncode == 1
    assert "AutoCAD 2024 ACADVER output must be filled" in cp.stdout
    assert "AutoCAD 2024 DWG write-back result must be filled" in cp.stdout


def test_windows_evidence_validator_json_output_is_redaction_safe(tmp_path: Path) -> None:
    evidence = tmp_path / "secret-bearing-cad-material-windows-evidence.md"
    evidence.write_text(
        _minimal_real_2018_evidence().replace(
            "Yuantus real-write log path: evidence/real-write.log",
            "Yuantus real-write log path: token=SUPERSECRET123",
        ),
        encoding="utf-8",
    )

    cp = _run_validator(evidence, "--json")

    assert cp.returncode == 1
    payload = json.loads(cp.stdout)
    assert payload["schema_version"] == 1
    assert payload["ok"] is False
    assert payload["failure_count"] == 1
    assert payload["failures"] == [
        "Yuantus real-write log path appears to contain a plaintext secret"
    ]
    assert "SUPERSECRET123" not in cp.stdout
    assert "token=" not in cp.stdout


def test_external_validation_handoff_is_indexed_and_ci_wired() -> None:
    index = _text(INDEX)
    scripts_index = _text(SCRIPTS_INDEX)
    ci_yml = _text(CI_YML)
    handoff_doc = str(HANDOFF.relative_to(ROOT))
    template_doc = str(EVIDENCE_TEMPLATE.relative_to(ROOT))
    template_dv_doc = str(EVIDENCE_TEMPLATE_DV.relative_to(ROOT))
    validator_dv_doc = str(EVIDENCE_VALIDATOR_DV.relative_to(ROOT))
    validator_json_dv_doc = str(EVIDENCE_VALIDATOR_JSON_DV.relative_to(ROOT))

    assert handoff_doc in index
    assert template_doc in index
    assert template_dv_doc in index
    assert validator_dv_doc in index
    assert validator_json_dv_doc in index
    assert handoff_doc in _text(HANDOFF)
    assert template_dv_doc in _text(EVIDENCE_TEMPLATE_DV)
    assert "validate_cad_material_windows_evidence.py" in scripts_index
    assert "test_cad_material_sync_external_validation_contracts.py" in ci_yml
