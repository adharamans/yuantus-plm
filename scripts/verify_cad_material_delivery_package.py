#!/usr/bin/env python3
"""Verify the CAD material sync delivery package inside the Yuantus repo."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "clients" / "autocad-material-sync"
PYTHON = sys.executable

REQUIRED_PATHS = [
    ".gitignore",
    "README.md",
    "clients/autocad-material-sync/MANIFEST.md",
    "clients/autocad-material-sync/README.md",
    "clients/autocad-material-sync/PLM_MATERIAL_SYNC_GUIDE.md",
    "clients/autocad-material-sync/WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md",
    "clients/autocad-material-sync/build.bat",
    "clients/autocad-material-sync/build_simple.bat",
    "clients/autocad-material-sync/build_with_devenv.ps1",
    "clients/autocad-material-sync/quick_build.bat",
    "clients/autocad-material-sync/install.bat",
    "clients/autocad-material-sync/uninstall.bat",
    "clients/autocad-material-sync/verify_autocad2018_preflight.ps1",
    "clients/autocad-material-sync/verify_material_sync_static.py",
    "clients/autocad-material-sync/verify_material_sync_fixture.py",
    "clients/autocad-material-sync/verify_material_sync_e2e.py",
    "clients/autocad-material-sync/verify_material_sync_db_e2e.py",
    "clients/autocad-material-sync/fixtures/material_sync_mock_drawing.json",
    "clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.sln",
    "clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj",
    "clients/autocad-material-sync/CADDedupPlugin/CadMaterialFieldMapper.cs",
    "clients/autocad-material-sync/CADDedupPlugin/CadMaterialFieldService.cs",
    "clients/autocad-material-sync/CADDedupPlugin/ConfigForm.cs",
    "clients/autocad-material-sync/CADDedupPlugin/DedupApiClient.cs",
    "clients/autocad-material-sync/CADDedupPlugin/DedupConfig.cs",
    "clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs",
    "clients/autocad-material-sync/CADDedupPlugin/ICadMaterialFieldAdapter.cs",
    "clients/autocad-material-sync/CADDedupPlugin/MaterialSyncApiClient.cs",
    "clients/autocad-material-sync/CADDedupPlugin/MaterialSyncDiffPreviewWindow.xaml",
    "clients/autocad-material-sync/CADDedupPlugin/MaterialSyncDiffPreviewWindow.xaml.cs",
    "clients/autocad-material-sync/CADDedupPlugin/NotificationManager.cs",
    "clients/autocad-material-sync/CADDedupPlugin/PackageContents.xml",
    "clients/autocad-material-sync/CADDedupPlugin/PackageContents.2018.xml",
    "clients/autocad-material-sync/CADDedupPlugin/PackageContents.2024.xml",
    "clients/autocad-material-sync/CADDedupPlugin/TrendWarningManager.cs",
    "plugins/yuantus-cad-material-sync/plugin.json",
    "plugins/yuantus-cad-material-sync/main.py",
    "src/yuantus/web/workbench.html",
    "src/yuantus/api/tests/test_workbench_router.py",
    "src/yuantus/meta_engine/tests/test_plugin_cad_material_sync.py",
    "scripts/verify_cad_material_diff_confirm_contract.py",
    "docs/samples/cad_material_diff_confirm_fixture.json",
    "docs/CAD_MATERIAL_SYNC_GITHUB_HANDOFF_20260506.md",
    "scripts/verify_cad_material_delivery_package.py",
    "scripts/print_cad_material_delivery_git_commands.sh",
    "playwright/tests/cad_material_workbench_ui.spec.js",
    "docs/TODO_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
    "docs/DEVELOPMENT_CLAUDE_TASK_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
    "docs/DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
    "docs/DESIGN_AND_VERIFICATION_CAD_MATERIAL_YUANTUS_AUTOCAD_CLIENT_MIGRATION_20260506.md",
    "docs/DESIGN_AND_VERIFICATION_CAD_MATERIAL_DELIVERY_PACKAGE_20260506.md",
]

REQUIRED_GLOBS = {
    "docs/DESIGN_AND_VERIFICATION_CAD_MATERIAL_*.md": 10,
}

TEXT_SUFFIXES = {
    ".bat",
    ".cs",
    ".csproj",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sh",
    ".sln",
    ".xml",
    ".xaml",
}

OLD_PATH_TOKENS = [
    "/Users/chouhua/Downloads/Github/Yuantus",
    "/Users/chouhua/Downloads/Github/PLM/standalone-product/autocad-plugin",
    "standalone-product/autocad-plugin",
    "standalone-product\\autocad-plugin",
    "外部 AutoCAD 客户端",
]

GENERATED_NAMES = {
    "bin",
    "obj",
    "__pycache__",
}

GENERATED_SUFFIXES = {
    ".dll",
    ".exe",
    ".pdb",
    ".cache",
    ".pyc",
}


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=ROOT, env=env, check=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_required_paths() -> None:
    missing = [rel for rel in REQUIRED_PATHS if not (ROOT / rel).exists()]
    require(not missing, "missing required delivery paths: " + ", ".join(missing))
    for pattern, minimum in REQUIRED_GLOBS.items():
        matches = sorted(ROOT.glob(pattern))
        require(
            len(matches) >= minimum,
            f"required glob {pattern} matched {len(matches)} files, expected at least {minimum}",
        )
    print(f"OK required delivery paths: {len(REQUIRED_PATHS)} plus {len(REQUIRED_GLOBS)} glob checks")


def check_generated_files_absent() -> None:
    offenders: list[str] = []
    for path in CLIENT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if path.name in GENERATED_NAMES or path.suffix.lower() in GENERATED_SUFFIXES:
            offenders.append(rel)
    require(not offenders, "generated files should not be committed: " + ", ".join(offenders))
    print("OK no generated AutoCAD client outputs under clients/autocad-material-sync")


def iter_scan_files() -> list[Path]:
    roots = [
        CLIENT,
        ROOT / "plugins" / "yuantus-cad-material-sync",
    ]
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                if path == Path(__file__).resolve():
                    continue
                if path.parts[-2:] == ("docs", "DELIVERY_DOC_INDEX.md"):
                    continue
                files.append(path)

    docs = ROOT / "docs"
    for pattern in (
        "*CAD_MATERIAL*.md",
        "DEV_AND_VERIFICATION_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
        "TODO_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
        "DEVELOPMENT_CLAUDE_TASK_CAD_MATERIAL_SYNC_PLUGIN_20260506.md",
        "samples/cad_material_*.json",
    ):
        for path in docs.glob(pattern):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                files.append(path)

    scripts = ROOT / "scripts"
    for path in scripts.glob("verify_cad_material*"):
        if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
            if path == Path(__file__).resolve():
                continue
            files.append(path)
    return files


def check_old_path_tokens() -> None:
    offenders: list[str] = []
    for path in iter_scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in OLD_PATH_TOKENS:
            if token in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {token}")
    require(not offenders, "old PLM client path tokens remain: " + "; ".join(offenders))
    print("OK no old PLM AutoCAD client path tokens in scanned text files")


def check_trailing_whitespace() -> None:
    offenders: list[str] = []
    for path in iter_scan_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for index, line in enumerate(text.splitlines(), start=1):
            if line.rstrip(" \t") != line:
                offenders.append(f"{path.relative_to(ROOT)}:{index}")
    require(not offenders, "trailing whitespace found: " + ", ".join(offenders[:20]))
    print("OK no trailing whitespace in scanned delivery text files")


def check_autocad_client_package() -> None:
    run([PYTHON, "clients/autocad-material-sync/verify_material_sync_static.py"])
    run([PYTHON, "clients/autocad-material-sync/verify_material_sync_fixture.py"])
    run([PYTHON, "clients/autocad-material-sync/verify_material_sync_e2e.py"])
    run([PYTHON, "clients/autocad-material-sync/verify_material_sync_db_e2e.py"])
    run([PYTHON, "scripts/verify_cad_material_diff_confirm_contract.py"])
    run(
        [
            PYTHON,
            "-m",
            "py_compile",
            "clients/autocad-material-sync/verify_material_sync_static.py",
            "clients/autocad-material-sync/verify_material_sync_fixture.py",
            "clients/autocad-material-sync/verify_material_sync_e2e.py",
            "clients/autocad-material-sync/verify_material_sync_db_e2e.py",
            "scripts/verify_cad_material_diff_confirm_contract.py",
            "plugins/yuantus-cad-material-sync/main.py",
            "src/yuantus/meta_engine/tests/test_plugin_cad_material_sync.py",
        ]
    )

    if shutil.which("xmllint"):
        run(
            [
                "xmllint",
                "--noout",
                "clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj",
                "clients/autocad-material-sync/CADDedupPlugin/MaterialSyncDiffPreviewWindow.xaml",
                "clients/autocad-material-sync/CADDedupPlugin/PackageContents.xml",
                "clients/autocad-material-sync/CADDedupPlugin/PackageContents.2018.xml",
                "clients/autocad-material-sync/CADDedupPlugin/PackageContents.2024.xml",
            ]
        )
    else:
        print("SKIP xmllint is not installed")


def main() -> int:
    os.environ.setdefault("PYTHONPYCACHEPREFIX", str(ROOT / ".pytest_cache" / "pycache"))
    try:
        check_required_paths()
        check_generated_files_absent()
        check_old_path_tokens()
        check_trailing_whitespace()
        check_autocad_client_package()
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    print("OK: CAD material delivery package verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
