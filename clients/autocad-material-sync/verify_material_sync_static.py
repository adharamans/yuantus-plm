#!/usr/bin/env python3
"""Static verification for the AutoCAD PLM material sync integration.

This does not replace a Windows + AutoCAD build. It catches integration drift
that can be checked without AutoCAD assemblies.
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLUGIN = ROOT / "CADDedupPlugin"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def check_xml() -> None:
    for rel in (
        "CADDedupPlugin.csproj",
        "PackageContents.xml",
        "PackageContents.2018.xml",
        "PackageContents.2024.xml",
    ):
        ET.parse(PLUGIN / rel)


def check_autocad2018_compatibility() -> None:
    project = read(PLUGIN / "CADDedupPlugin.csproj")
    package_default = read(PLUGIN / "PackageContents.xml")
    package_2018 = read(PLUGIN / "PackageContents.2018.xml")
    package_2024 = read(PLUGIN / "PackageContents.2024.xml")
    validation_guide = ROOT / "WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md"
    preflight_script = ROOT / "verify_autocad2018_preflight.ps1"

    require(
        '<AutoCADVersion Condition="\'$(AutoCADVersion)\' == \'\'">2018</AutoCADVersion>' in project,
        "project should default AutoCADVersion to 2018",
    )
    require(
        "AutoCAD 2018" in project and "AutoCAD 2024" in project,
        "project should keep explicit AutoCAD 2018/2024 install-dir branches",
    )
    require(
        '<TargetFrameworkVersion Condition="\'$(TargetFrameworkVersion)\' == \'\' and \'$(AutoCADVersion)\' == \'2018\'">v4.6</TargetFrameworkVersion>' in project,
        "AutoCAD 2018 build should target .NET Framework v4.6",
    )
    require("<LangVersion>7.3</LangVersion>" in project, "C# language version should be pinned to 7.3")
    for assembly in ("accoremgd", "acdbmgd", "acmgd", "AcWindows", "AdWindows"):
        require(
            f"<HintPath>$(AutoCADInstallDir)\\{assembly}.dll</HintPath>" in project,
            f"project should reference {assembly} through AutoCADInstallDir",
        )
    require("PackageContents.$(AutoCADVersion).xml" in project, "post-build should select versioned PackageContents")

    for label, package, series_min, series_max in (
        ("default", package_default, "R22.0", "R22.0"),
        ("2018", package_2018, "R22.0", "R22.0"),
        ("2024", package_2024, "R24.3", "R24.3"),
    ):
        require(f'SeriesMin="{series_min}"' in package, f"{label} package should use SeriesMin={series_min}")
        require(f'SeriesMax="{series_max}"' in package, f"{label} package should use SeriesMax={series_max}")

    for package_name, package in (
        ("PackageContents.xml", package_default),
        ("PackageContents.2018.xml", package_2018),
        ("PackageContents.2024.xml", package_2024),
    ):
        require('SeriesMax="R25.0"' not in package, f"{package_name} should not claim AutoCAD 2025/.NET 8")

    for script_name in ("build.bat", "build_simple.bat", "quick_build.bat"):
        script = read(ROOT / script_name)
        require("AUTOCAD_VERSION=2018" in script, f"{script_name} should default to AutoCAD 2018")
        require("AutoCAD %AUTOCAD_VERSION%" in script, f"{script_name} should derive AutoCAD path from AUTOCAD_VERSION")
        require(f"Release\\AutoCAD%AUTOCAD_VERSION%" in script, f"{script_name} should use versioned output path")

    ps_script = read(ROOT / "build_with_devenv.ps1")
    require('"2018"' in ps_script and "AutoCAD$AutoCADVersion" in ps_script, "PowerShell build should support AutoCAD 2018 default")

    require(validation_guide.exists(), "missing Windows AutoCAD 2018 validation guide")
    require(preflight_script.exists(), "missing Windows AutoCAD 2018 preflight script")
    guide_text = read(validation_guide)
    preflight_text = read(preflight_script)
    for token in (
        "ACADVER",
        "R22.0",
        "NETLOAD",
        "PLMMATPROFILES",
        "PLMMATCOMPOSE",
        "PLMMATPUSH",
        "PLMMATPULL",
    ):
        require(token in guide_text, f"Windows AutoCAD 2018 validation guide missing {token}")
    for token in (
        "accoremgd.dll",
        ".NET Framework 4.6 targeting pack",
        "PackageContents.$AutoCADVersion.xml",
        "RunBuild",
        "AutoCAD$AutoCADVersion",
    ):
        require(token in preflight_text, f"Windows AutoCAD 2018 preflight missing {token}")


def check_project_sources() -> None:
    text = read(PLUGIN / "CADDedupPlugin.csproj")
    for source in (
        "CadMaterialFieldMapper.cs",
        "CadMaterialFieldService.cs",
        "ICadMaterialFieldAdapter.cs",
        "MaterialSyncDiffPreviewWindow.xaml.cs",
        "MaterialSyncApiClient.cs",
        "DedupPlugin.cs",
        "DedupConfig.cs",
        "ConfigForm.cs",
    ):
        require(f'Compile Include="{source}"' in text, f"missing project source: {source}")
    require(
        'Page Include="MaterialSyncDiffPreviewWindow.xaml"' in text,
        "missing MaterialSyncDiffPreviewWindow XAML page",
    )


def check_commands_registered() -> None:
    package_xml = read(PLUGIN / "PackageContents.xml")
    package_2018 = read(PLUGIN / "PackageContents.2018.xml")
    package_2024 = read(PLUGIN / "PackageContents.2024.xml")
    plugin_cs = read(PLUGIN / "DedupPlugin.cs")
    readme = read(ROOT / "README.md")
    guide = read(ROOT / "PLM_MATERIAL_SYNC_GUIDE.md")
    for command in ("PLMMATPROFILES", "PLMMATCOMPOSE", "PLMMATPUSH", "PLMMATPULL"):
        require(f'Global="{command}"' in package_xml, f"PackageContents missing {command}")
        require(f'CommandMethod("{command}")' in plugin_cs, f"DedupPlugin missing {command}")
        require(command in readme, f"README missing {command}")
        require(command in guide, f"guide missing {command}")

    for package_name, package in (
        ("PackageContents.xml", package_xml),
        ("PackageContents.2018.xml", package_2018),
        ("PackageContents.2024.xml", package_2024),
    ):
        require(
            "PLMMATPULL" in package and "差异预览确认后回填 CAD" in package,
            f"{package_name} should describe PLMMATPULL diff-preview confirmation",
        )
        require(
            "PLM Material Sync" in package and "synchronize PLM material fields" in package,
            f"{package_name} should describe PLM material sync at package level",
        )
    require(
        "PLMMATPULL     - 按 PLM Item ID 差异预览并确认回填 CAD" in plugin_cs,
        "DEDUPHELP should describe PLMMATPULL diff-preview confirmation",
    )

    for ribbon_command in ("PLMMATCOMPOSE", "PLMMATPUSH", "PLMMATPULL"):
        require(
            f'CommandParameter = "{ribbon_command}"' in plugin_cs,
            f"Ribbon missing {ribbon_command}",
        )


def check_api_contract() -> None:
    text = read(PLUGIN / "MaterialSyncApiClient.cs")
    require(
        'BasePath = "/api/v1/plugins/cad-material-sync"' in text,
        "material-sync base API path missing",
    )
    for path in (
        "/profiles",
        "/profiles/{safeProfileId}",
        "/compose",
        "/validate",
        "/diff/preview",
        "/sync/inbound",
        "/sync/outbound",
    ):
        require(path in text, f"MaterialSyncApiClient missing {path}")
    for token in (
        "DiffPreviewAsync",
        "current_cad_fields",
        "cad_system = CadSystem",
        'private const string CadSystem = "autocad"',
        "write_cad_fields",
        "requires_confirmation",
        "MaterialDiffPreviewResponse",
        "MaterialCadFieldDiff",
    ):
        require(token in text, f"MaterialSyncApiClient missing diff preview token {token}")
    for header in ("x-tenant-id", "x-org-id"):
        require(header in text, f"MaterialSyncApiClient missing {header}")


def check_s8_helper_bridge_contract() -> None:
    project = read(PLUGIN / "CADDedupPlugin.csproj")
    material = read(PLUGIN / "MaterialSyncApiClient.cs")
    dedup = read(PLUGIN / "DedupApiClient.cs")
    plugin = read(PLUGIN / "DedupPlugin.cs")
    helper = read(ROOT.parent / "cad-desktop-helper" / "Helper" / "HelperRuntime.cs")
    workflow = read(ROOT.parent.parent / ".github" / "workflows" / "cad-helper-shared-dotnet.yml")

    require(
        r"..\..\cad-desktop-helper\Shared\Yuantus.Cad.Shared.csproj" in project,
        "CADDedupPlugin should reference Yuantus.Cad.Shared",
    )
    require("Yuantus.Cad.Shared.dll" in project, "post-build should copy Yuantus.Cad.Shared.dll")
    require("IMaterialSyncHelperTransport" in material, "MaterialSyncApiClient should expose a helper transport seam")
    require("PostJsonAsync<MaterialSyncResponse>" in material and '"/sync/inbound"' in material, "SyncInboundAsync should call helper /sync/inbound")
    require("PostJsonAsync<MaterialSyncResponse>" in material and '"/sync/outbound"' in material, "SyncOutboundAsync should call helper /sync/outbound")
    require("PostJsonAsync<HelperDiffPreviewResponse>" in material and '"/diff/preview"' in material, "DiffPreviewAsync should call helper /diff/preview")
    require("/audit/apply-result" in material, "MaterialSyncApiClient should report apply-result through helper")
    require("PullId" in material and "server_response" in material, "diff preview should carry helper pull_id")

    for legacy in (
        "{BasePath}/profiles",
        "{BasePath}/profiles/{safeProfileId}",
        "{BasePath}/compose",
        "{BasePath}/validate",
    ):
        require(legacy in material, f"legacy MaterialSync direct call should remain documented in source: {legacy}")

    require("/api/dedup/check" in dedup, "DedupApiClient.CheckDuplicateAsync should remain legacy direct in S8-R1")
    require("HelperTransport" not in dedup, "DedupApiClient should not migrate to helper in S8-R1")
    require('MapPost("/dedup/check"' not in helper, "S8-R1 must not add helper /dedup/check")
    require(helper.count("MapGet(") + helper.count("MapPost(") == 17, "helper route count should be 17 after Phase 3 (15 + /material/assistant/resolve + /material/assistant/create)")
    for _doc_route in ('MapPost("/document/checkout"', 'MapPost("/document/undo-checkout"', 'MapPost("/document/status"', 'MapPost("/document/checkin"', 'MapPost("/document/bom-import"'):
        require(_doc_route in helper, f"G1-A helper must register {_doc_route}")

    require("ReportApplyResultSafely" in plugin, "PLMMATPULL should report apply-result after write attempt")
    require('"ok"' in plugin and '"failed"' in plugin, "PLMMATPULL should report both ok and failed apply-result outcomes")
    require("CreateFailedFields(writeFields, applyEx)" in plugin, "PLMMATPULL write failure should include attempted fields")
    require("throw;" in plugin, "PLMMATPULL should not swallow CAD write exceptions")

    require("clients/autocad-material-sync/**" in workflow, "cad helper workflow should include AutoCAD material-sync paths")
    require(
        "CADDedupPlugin.Client.Tests/CADDedupPlugin.Client.Tests.csproj" in workflow,
        "cad helper workflow should run SDK-free AutoCAD client tests",
    )
    require("verify_material_sync_static.py" in workflow, "cad helper workflow should run material sync static verifier")


def check_config_contract() -> None:
    config = read(PLUGIN / "DedupConfig.cs")
    form = read(PLUGIN / "ConfigForm.cs")
    for prop in (
        "TenantId",
        "OrgId",
        "MaterialProfileId",
        "MaterialSyncDryRunDefault",
    ):
        require(f"public " in config and prop in config, f"DedupConfig missing {prop}")
        require(prop in form, f"ConfigForm missing {prop}")

    require(
        '"/api/v1/health"' in read(PLUGIN / "DedupApiClient.cs"),
        "connection test should support Yuantus /api/v1/health",
    )


def check_field_service_contract() -> None:
    service = read(PLUGIN / "CadMaterialFieldService.cs")
    mapper = read(PLUGIN / "CadMaterialFieldMapper.cs")
    adapter = read(PLUGIN / "ICadMaterialFieldAdapter.cs")
    plugin_cs = read(PLUGIN / "DedupPlugin.cs")

    require(
        "public interface ICadMaterialFieldAdapter<TCadDocument>" in adapter,
        "missing generic CAD field adapter interface",
    )
    require(
        "ICadMaterialFieldAdapter<Document>" in service,
        "AutoCAD field service should implement adapter interface",
    )
    require(
        "ICadMaterialFieldAdapter<Document>" in plugin_cs,
        "DedupPlugin should depend on adapter interface",
    )

    for token in (
        "CadMaterialFieldMapper",
        "ExtractFields",
        "ApplyFields",
        "AttributeReference",
        "Table",
        "LayoutDictionaryId",
        "BlockTableRecord.ModelSpace",
    ):
        require(token in service, f"CadMaterialFieldService missing {token}")

    for token in (
        "ExtractTableCells",
        "ApplyTableCells",
        "NormalizeInputFields",
        "AddField",
        "specification",
        "material",
        "length",
        "width",
        "thickness",
    ):
        require(token in mapper, f"CadMaterialFieldMapper missing {token}")
    require(
        re.search(r"GetDrawingSpaceBlockRecordIds\s*\(", service),
        "field service should scan model and layout block records",
    )


def check_diff_preview_ui_contract() -> None:
    project = read(PLUGIN / "CADDedupPlugin.csproj")
    plugin_cs = read(PLUGIN / "DedupPlugin.cs")
    window_xaml = read(PLUGIN / "MaterialSyncDiffPreviewWindow.xaml")
    window_cs = read(PLUGIN / "MaterialSyncDiffPreviewWindow.xaml.cs")
    readme = read(ROOT / "README.md")
    guide = read(ROOT / "PLM_MATERIAL_SYNC_GUIDE.md")

    require("MaterialSyncDiffPreviewWindow.xaml" in project, "project missing diff preview window")
    require("DiffPreviewAsync" in plugin_cs, "PLMMATPULL should call diff preview before write-back")
    require("MaterialSyncDiffPreviewWindow" in plugin_cs, "PLMMATPULL should show diff preview window")
    require("ConfirmedWriteFields" in plugin_cs, "PLMMATPULL should apply confirmed write fields")
    require("ApplyFields(doc, writeFields)" in plugin_cs, "PLMMATPULL should not directly apply full cad_fields")
    require("PLM -> CAD 字段差异预览" in plugin_cs, "PLMMATPULL should print diff preview summary")

    for token in (
        "确认写回",
        "取消",
        "CAD 字段",
        "当前值",
        "目标值",
        "状态",
    ):
        require(token in window_xaml, f"diff preview XAML missing {token}")
    for token in (
        "ConfirmedWriteFields",
        "RequiresConfirmation",
        "WriteCadFields",
        "MaterialCadFieldDiffRow",
        "DialogResult = true",
    ):
        require(token in window_cs, f"diff preview window code missing {token}")
    require("/diff/preview" in guide, "material sync guide should mention diff preview API")
    require("差异预览" in readme, "README should mention diff preview confirmation")


def check_fixture_contract() -> None:
    fixture = ROOT / "fixtures" / "material_sync_mock_drawing.json"
    fixture_script = ROOT / "verify_material_sync_fixture.py"
    e2e_script = ROOT / "verify_material_sync_e2e.py"
    db_e2e_script = ROOT / "verify_material_sync_db_e2e.py"
    require(fixture.exists(), "missing mock drawing fixture")
    require(fixture_script.exists(), "missing fixture verification script")
    require(e2e_script.exists(), "missing e2e verification script")
    require(db_e2e_script.exists(), "missing DB e2e verification script")
    text = read(fixture_script)
    for token in (
        "CadMaterialFieldMapper.cs",
        "material_sync_mock_drawing.json",
        "expected_extract",
        "expected_table_after",
    ):
        require(token in text, f"fixture verification missing {token}")
    e2e_text = read(e2e_script)
    for token in (
        "/api/v1/plugins/cad-material-sync/sync/inbound",
        "/api/v1/plugins/cad-material-sync/sync/outbound",
        "YUANTUS_ROOT",
        "dry_run",
    ):
        require(token in e2e_text, f"e2e verification missing {token}")
    db_e2e_text = read(db_e2e_script)
    for token in (
        "sqlite:///:memory:",
        "ItemType",
        "Item",
        "create_if_missing",
        "overwrite",
        "sync/outbound",
    ):
        require(token in db_e2e_text, f"DB e2e verification missing {token}")


def check_brace_balance() -> None:
    for path in PLUGIN.glob("*.cs"):
        text = read(path)
        require(text.count("{") == text.count("}"), f"unbalanced braces: {path.name}")


def check_finish_treatment_aliases() -> None:
    """finishing/treatment R1 (#689): the AutoCAD field mapper canonicalizes
    surface finish / coating to ``finish``, keeps ``heat_treatment``, and
    deliberately does NOT add the bare ``表面`` alias (too broad -> would mis-map
    unrelated surface-* fields)."""
    mapper = read(PLUGIN / "CadMaterialFieldMapper.cs")
    for alias in ("表面处理", "涂层", "finish", "coating"):
        require(
            f'{{ "{alias}", "finish" }}' in mapper,
            f"finishing/treatment R1: missing finish alias {alias!r} in CadMaterialFieldMapper",
        )
    require(
        '{ "热处理", "heat_treatment" }' in mapper,
        "finishing/treatment R1: heat_treatment alias must remain",
    )
    require(
        '{ "表面", "finish" }' not in mapper,
        "finishing/treatment R1: bare 表面 alias must not be present (too broad)",
    )


def _command_body(source: str, command: str) -> str:
    """Slice the source from a command's CommandMethod attribute to the next
    CommandMethod (or EOF). Scopes per-command guards so a method-body check is
    not a whole-file grep. Returns "" if the command is absent."""
    marker = f'CommandMethod("{command}")'
    start = source.find(marker)
    if start == -1:
        return ""
    nxt = source.find("[CommandMethod(", start + len(marker))
    return source[start:nxt] if nxt != -1 else source[start:]


def check_material_assistant_command_contract() -> None:
    plugin_cs = read(PLUGIN / "DedupPlugin.cs")
    readme = read(ROOT / "README.md")
    guide = read(ROOT / "PLM_MATERIAL_SYNC_GUIDE.md")
    packages = {
        "PackageContents.xml": read(PLUGIN / "PackageContents.xml"),
        "PackageContents.2018.xml": read(PLUGIN / "PackageContents.2018.xml"),
        "PackageContents.2024.xml": read(PLUGIN / "PackageContents.2024.xml"),
    }

    # registration + discoverability
    require('CommandMethod("PLMMATASSIST")' in plugin_cs, "DedupPlugin missing PLMMATASSIST command")
    for name, package in packages.items():
        require('Global="PLMMATASSIST"' in package, f"{name} missing PLMMATASSIST command")
    require("PLMMATASSIST" in readme, "README missing PLMMATASSIST")
    require("PLMMATASSIST" in guide, "guide missing PLMMATASSIST")
    require('CommandParameter = "PLMMATASSIST"' in plugin_cs, "Ribbon missing PLMMATASSIST")
    require("PLMMATASSIST   - 物料助手" in plugin_cs, "DEDUPHELP should describe PLMMATASSIST")

    # Conservative-flow contract, SCOPED to the PLMMATASSIST method body only.
    # Phase 4: PLMMATASSIST now has two branches in one method body -- an existing-item
    # bind branch reusing the /diff/preview write-back chain, and a separate
    # confirm-gated create branch. ApplyFields IS now expected (bind branch); the old
    # "ApplyFields not in body" assertion is intentionally removed. DedupPlugin.cs also
    # calls ApplyFields in PLMMATCOMPOSE/PUSH/PULL, so this stays scoped to the slice.
    body = _command_body(plugin_cs, "PLMMATASSIST")
    require("ExtractFields(doc)" in body, "PLMMATASSIST must extract current CAD fields")
    require("ResolveAsync(" in body, "PLMMATASSIST must call ResolveAsync")
    require("PromptCandidateSelection(" in body, "PLMMATASSIST bind branch must require explicit candidate selection")
    require("DiffPreviewAsync(" in body, "PLMMATASSIST bind branch must reuse helper-forwarded DiffPreviewAsync")
    require("MaterialSyncDiffPreviewWindow" in body, "PLMMATASSIST bind branch must show the diff preview confirmation window")
    require("ApplyFields(" in body, "PLMMATASSIST bind branch must write confirmed fields via ApplyFields")
    require("ReportApplyResultSafely" in body, "PLMMATASSIST bind branch must audit via ReportApplyResultSafely (/audit/apply-result)")
    require("CreateAsync(" in body, "PLMMATASSIST must keep the confirm-gated create branch")
    require("GetKeywords(" in body, "PLMMATASSIST create branch must gate create behind an explicit keyword confirmation")
    require("AllowNone = true" in body, "PLMMATASSIST create prompt must set AllowNone = true so Enter falls back to the default (No)")
    pos_extract = body.find("ExtractFields(doc)")
    pos_resolve = body.find("ResolveAsync(")
    pos_select = body.find("PromptCandidateSelection(")
    pos_diff = body.find("DiffPreviewAsync(")
    pos_window = body.find("MaterialSyncDiffPreviewWindow")
    pos_apply = body.find("ApplyFields(")
    pos_create = body.find("CreateAsync(")
    require(
        0 <= pos_extract < pos_resolve < pos_select < pos_diff < pos_window < pos_apply,
        "PLMMATASSIST bind order must be extract -> resolve -> select -> diff preview -> window -> apply",
    )
    # "no DWG write-back" now scopes to the CREATE branch only: there must be no
    # ApplyFields from CreateAsync onward (the bind branch writes and returns first).
    require(
        "ApplyFields(" not in body[pos_create:],
        "PLMMATASSIST create branch (from CreateAsync) must not write DWG fields",
    )


def main() -> int:
    checks = [
        check_xml,
        check_autocad2018_compatibility,
        check_project_sources,
        check_commands_registered,
        check_api_contract,
        check_s8_helper_bridge_contract,
        check_config_contract,
        check_field_service_contract,
        check_diff_preview_ui_contract,
        check_material_assistant_command_contract,
        check_fixture_contract,
        check_finish_treatment_aliases,
        check_brace_balance,
    ]
    for check in checks:
        check()
    print("OK: AutoCAD material sync static verification passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
