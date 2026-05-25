#!/usr/bin/env python3
"""Static verification for the CAD helper bridge installer slice.

The installer (`Installer/YuantusCadHelper.iss` + `Installer/pack.ps1`)
cannot be compiled, signed, or executed on the GitHub Windows runner: the
Inno compiler is not guaranteed present, the signing certificate is
owner-held and out of the repo, and install/uninstall/repair require a
real per-user Windows profile with a CAD host. This is the **fourth
application** of the `feedback_production_seam_tests_without_fakes` rule
(after S7 parent-ancestry, S9 bridge transport, S10 Lisp shell): the seam
is environment-prohibited on CI, so coverage is a static verifier that
source-pins the `.iss` invariants + a deferred operational signoff packet
(DEV/Verification MD §3.I).

This verifier asserts against the REAL `.iss` / `.ps1` source -- never a
mock -- exactly as `verify_lisp_shell_static.py` pins the real `.lsp`. It
implements the 10 mandatory guards from the installer taskbook §5
(#638 / 18b83d73) plus source/drift guards.

Usage:
    python3 clients/cad-desktop-helper/Installer/verify_installer_static.py

Exit codes:
    0 - all guards pass
    1 - at least one guard failed (assertion message printed to stderr)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent           # .../Installer
HELPER_ROOT = ROOT.parent                          # .../cad-desktop-helper
REPO_ROOT = HELPER_ROOT.parent.parent              # repo root
ISS_FILE = ROOT / "YuantusCadHelper.iss"
PACK_FILE = ROOT / "pack.ps1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "cad-helper-shared-dotnet.yml"
DEV_MD = REPO_ROOT / "docs" / "DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_INSTALLER_R1_20260524.md"
HELPER_RUNTIME = HELPER_ROOT / "Helper" / "HelperRuntime.cs"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def strip_iss_comments(source: str) -> str:
    """Drop Inno/Pascal comments so explanatory prose does not satisfy (or
    trip) an absence guard, while keeping real directives and Inno
    constants intact.

    The subtlety: Inno uses ``{...}`` for BOTH Pascal block comments AND
    constants such as ``{userappdata}`` / ``{app}``. We distinguish by
    whitespace: a brace group containing whitespace is a comment
    (``{ Running-helper ... }``); a brace group with no whitespace is a
    constant (``{userappdata}``) and is preserved. Full-line ``;`` comments
    (used in [Setup]/[Files]/etc. sections) are also dropped; ``;`` never
    starts a statement line in our [Code] (Pascal statements start with an
    identifier), so this is safe.
    """
    # remove { ...with whitespace... } Pascal block comments; keep {constant}
    no_block = re.sub(r"\{[^{}]*\s[^{}]*\}", " ", source, flags=re.DOTALL)
    out = []
    for line in no_block.splitlines():
        if line.lstrip().startswith(";"):
            continue
        out.append(line)
    return "\n".join(out)


def strip_ps_comments(source: str) -> str:
    """Drop PowerShell ``<# ... #>`` block comments and ``#`` line comments
    so prose does not trip an absence guard. Our pack.ps1 has no ``#`` inside
    string literals, so a simple line strip is safe."""
    no_block = re.sub(r"<#.*?#>", " ", source, flags=re.DOTALL)
    out = []
    for line in no_block.splitlines():
        idx = line.find("#")
        out.append(line[:idx] if idx >= 0 else line)
    return "\n".join(out)


# ---------- guards ----------

def check_iss_exists() -> None:
    require(ISS_FILE.exists(), f"installer script missing at {ISS_FILE}")
    require(PACK_FILE.exists(), f"pack script missing at {PACK_FILE}")


def check_privileges_required_lowest(code: str) -> None:
    # Guard 1: per-user, no-admin.
    require(
        re.search(r"(?im)^\s*PrivilegesRequired\s*=\s*lowest\s*$", code) is not None,
        "guard 1: [Setup] must declare PrivilegesRequired=lowest (per-user, no-admin)",
    )


def check_no_hklm(code_nc: str) -> None:
    # Guard 2: no machine-wide registry writes (checked against comment-stripped source).
    require(
        "HKLM" not in code_nc and "HKEY_LOCAL_MACHINE" not in code_nc,
        "guard 2: installer must not write HKLM / HKEY_LOCAL_MACHINE",
    )


def check_install_paths_pinned_to_userappdata(code: str, code_nc: str) -> None:
    # Guard 3: per-user constants, no literal %APPDATA%, no relocatable layout.
    # Positive path checks run on raw code; negative (absence) checks run on
    # the comment-stripped source so explanatory prose does not trip them.
    require(
        r"{userappdata}\YuantusPLM\helper" in code,
        r"guard 3: helper/detector DestDir must be {userappdata}\YuantusPLM\helper",
    )
    require(
        r"{userappdata}\YuantusPLM\cad-bridge" in code,
        r"guard 3: bridge/lsp DestDir must be {userappdata}\YuantusPLM\cad-bridge",
    )
    require(
        r"{userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle" in code,
        r"guard 3: bundle DestDir must be {userappdata}\Autodesk\ApplicationPlugins\CADDedup.bundle",
    )
    # DefaultDirName must be rooted at {userappdata}, and the dir page hidden
    # so there is no user-chosen relocatable install dir.
    require(
        re.search(r"(?im)^\s*DefaultDirName\s*=\s*\{userappdata\}\\YuantusPLM\s*$", code) is not None,
        r"guard 3: DefaultDirName must be {userappdata}\YuantusPLM (fixed, not relocatable)",
    )
    require(
        re.search(r"(?im)^\s*DisableDirPage\s*=\s*yes\s*$", code) is not None,
        "guard 3: DisableDirPage=yes so the helper is not relocated off its spawn path",
    )
    # reject env-var literal (Inno does not expand %APPDATA% in [Files]) and
    # reject Program Files / {app} / {commonpf}/{pf} relocatable layouts.
    require("%APPDATA%" not in code_nc, "guard 3: must not use literal %APPDATA% (Inno does not expand env vars)")
    require("Program Files" not in code_nc, "guard 3: must not install under Program Files")
    require("{commonpf" not in code_nc and "{pf}" not in code_nc and "{pf32}" not in code_nc and "{pf64}" not in code_nc,
            "guard 3: must not use {commonpf}/{pf} (machine-wide) destinations")
    # {app} must not be used as a [Files] DestDir (would be a relocatable layout)
    require(re.search(r"(?im)DestDir:\s*\"\{app\}", code) is None,
            "guard 3: [Files] DestDir must not be {app} (relocatable)")


def check_signing_present_and_guarded(code: str) -> None:
    # Guard 4: signing step present AND graceful-skip when no cert configured.
    require("SignTool=yuantussign" in code, "guard 4: a SignTool signing step must be present")
    require("[SignTools]" in code, "guard 4: a [SignTools] definition must be present")
    # both the SignTool reference and the [SignTools] block must be guarded by
    # #ifdef SignToolCmd so CI (which does not define it) builds UNSIGNED.
    require(code.count("#ifdef SignToolCmd") >= 2,
            "guard 4: SignTool usage must be guarded by #ifdef SignToolCmd (CI builds unsigned)")
    # the SignTool= line must sit inside an #ifdef SignToolCmd region
    for m in re.finditer(r"(?m)^\s*SignTool\s*=\s*yuantussign\s*$", code):
        prefix = code[:m.start()]
        last_ifdef = prefix.rfind("#ifdef SignToolCmd")
        last_endif = prefix.rfind("#endif")
        require(last_ifdef > last_endif,
                "guard 4: SignTool= must be inside an #ifdef SignToolCmd block (graceful skip)")
    # the pack script must default to UNSIGNED and only sign when SignToolCmd is supplied
    pack = read(PACK_FILE)
    require("UNSIGNED" in pack and "SignToolCmd" in pack,
            "guard 4: pack.ps1 must document the unsigned default + optional owner-local signing")
    # B1: the [Setup] SignTool only signs setup+uninstaller; the PAYLOAD
    # binaries must be signed too. pack.ps1 must Authenticode-sign the
    # first-party binaries (helper/detector/Shared/Bridge/CADDedupPlugin)
    # when SignToolCmd is supplied.
    require("first-party" in pack.lower(),
            "guard 4: pack.ps1 must sign the first-party payload binaries (not just the installer)")
    for binary in ("yuantus-cad-helper.exe", "yuantus-cad-detector.exe",
                   "YuantusCadHelperBridge.dll", "CADDedupPlugin.dll"):
        require(binary in pack,
                f"guard 4: pack.ps1 first-party signing list must include {binary}")
    # signing must be gated on SignToolCmd (CI, no cert -> unsigned payload + installer)
    require(re.search(r"if\s*\(\s*\$SignToolCmd\s*\)", pack) is not None,
            "guard 4: pack.ps1 payload signing must be gated by `if ($SignToolCmd)`")


def check_no_service_or_autostart(code: str, code_nc: str) -> None:
    # Guard 5: the helper is spawn-on-demand; no service / auto-start.
    lowered = code_nc.lower()
    require("sc create" not in lowered and "sc.exe" not in lowered,
            "guard 5: must not register a Windows Service (sc create)")
    require("serviceinstall" not in lowered and "[services]" not in lowered,
            "guard 5: must not declare a service install")
    require("currentversion\\run" not in lowered,
            "guard 5: must not add a CurrentVersion\\Run auto-start entry")
    # if a [Run] section exists, it must not auto-launch the helper exe.
    if "[Run]" in code:
        run_section = code.split("[Run]", 1)[1].split("\n[", 1)[0]
        require("yuantus-cad-helper.exe" not in run_section,
                "guard 5: [Run] must not auto-launch the helper exe")


def check_no_runtime_file_pre_creation(code: str) -> None:
    # Guard 6: never pre-create runtime-owned files.
    for name in ("local-helper-token.dat", "audit.db", "install-id.json",
                 "plm-bearer-token.bin", "config.json"):
        # may appear ONLY in the full-purge / preserve context, never created.
        require(
            not re.search(r"(?i)DestName[^\n]*" + re.escape(name), code),
            f"guard 6: installer must not lay down {name} via [Files] DestName",
        )
        # also reject a [Files] Source that would copy the runtime file in.
        require(
            not re.search(r"(?im)^\s*Source:[^\n]*" + re.escape(name), code),
            f"guard 6: installer must not stage {name} via a [Files] Source",
        )
        require(
            not re.search(r"(?i)SaveStringToFile\([^)]*" + re.escape(name), code),
            f"guard 6: installer must not create {name} in [Code]",
        )
    # helper-session-*.json must never be created or deleted by the installer
    require(
        not re.search(r"(?i)SaveStringToFile\([^)]*helper-session-", code),
        "guard 6: installer must not create helper-session-*.json (S3-owned)",
    )
    require(
        not re.search(r"(?i)DeleteFile\([^)]*helper-session-", code),
        "guard 6: installer must not delete helper-session-*.json (S3 stale-clean owns it)",
    )


def check_cad_startup_fenced_and_gated(code: str) -> None:
    # Guard 7: uniquely-fenced begin/end markers + --skip-cad-config gate.
    require("BEGIN YUANTUS CAD HELPER" in code, "guard 7: CAD-startup block must have a unique BEGIN marker")
    require("END YUANTUS CAD HELPER" in code, "guard 7: CAD-startup block must have a unique END marker")
    require('#define FenceBegin' in code and '#define FenceEnd' in code,
            "guard 7: fence markers must be #define'd constants")
    # gated behind the cadconfig task (the --skip-cad-config opt-out)
    require('Name: "cadconfig"' in code, "guard 7: a 'cadconfig' [Tasks] entry must gate auto-config")
    require("WizardIsTaskSelected('cadconfig')" in code,
            "guard 7: CAD startup write must be gated by WizardIsTaskSelected('cadconfig')")


def check_uninstall_is_allowlist_not_blanket(code: str) -> None:
    # Guard 8: uninstall removes specific subdirs, never the YuantusPLM root.
    require("[UninstallDelete]" in code, "guard 8: an [UninstallDelete] allow-list must be present")
    udel = code.split("[UninstallDelete]")[1].split("[Code]")[0]
    require(r"{userappdata}\YuantusPLM\helper" in udel, "guard 8: must remove the helper\\ subdir")
    require(r"{userappdata}\YuantusPLM\cad-bridge" in udel, "guard 8: must remove the cad-bridge\\ subdir")
    # MUST NOT blanket-delete the whole YuantusPLM root or a wildcard under it.
    require(
        not re.search(r"(?im)Type:\s*filesandordirs;\s*Name:\s*\"\{userappdata\}\\YuantusPLM\"\s*$", udel),
        "guard 8: [UninstallDelete] must not target the whole {userappdata}\\YuantusPLM root",
    )
    require(
        r"{userappdata}\YuantusPLM\*" not in udel and r"{userappdata}\YuantusPLM\\*" not in udel,
        "guard 8: [UninstallDelete] must not use a wildcard over the YuantusPLM root",
    )
    # also reject a recursive Pascal RemoveDir/DelTree of the root
    require("RemoveDir(ExpandConstant('{userappdata}\\YuantusPLM'))" not in code,
            "guard 8: must not RemoveDir the YuantusPLM root in [Code]")


def check_preserve_set_only_in_fullpurge(code: str) -> None:
    # Guard 9: preserved root files deleted ONLY in the explicit full-purge
    # opt-in branch (never in default uninstall/repair).
    preserved = ("local-helper-token.dat", "audit.db", "install-id.json",
                 "plm-bearer-token.bin", "config.json")
    for name in preserved:
        for m in re.finditer(r"(?i)DeleteFile\([^)]*" + re.escape(name), code):
            # the enclosing procedure must be the full-purge branch
            prefix = code[:m.start()]
            proc = prefix.rfind("procedure ")
            proc_name = code[proc:m.start()].split("(")[0].replace("procedure", "").strip()
            require(
                "purge" in proc_name.lower(),
                f"guard 9: {name} may only be deleted inside the explicit full-purge procedure "
                f"(found in '{proc_name}')",
            )
    # the full-purge must be an explicit opt-in (confirmation prompt), default preserve.
    require("FullPurgeUserData" in code, "guard 9: a FullPurgeUserData opt-in branch must exist")
    require("PRESERVE" in code or "keep it for a future reinstall" in code,
            "guard 9: uninstall must default to preserving user data")
    # M2: full purge MUST remove the PLM bearer token (a stale token would let
    # a reinstall stay logged in). Pin that it is deleted inside the purge proc.
    purge_body = code.split("procedure FullPurgeUserData", 1)
    require(len(purge_body) > 1, "guard 9: FullPurgeUserData procedure must exist")
    purge_body = purge_body[1].split("\nend;", 1)[0]
    require("plm-bearer-token.bin" in purge_body,
            "guard 9: full purge must delete plm-bearer-token.bin (S5 PLM bearer token)")


def check_pack_consumes_prebuilt_no_msbuild(code: str, code_nc: str) -> None:
    # Guard 10: pack consumes pre-built bin/Release; no compiler invocation.
    pack = read(PACK_FILE)
    pack_nc = strip_ps_comments(pack)
    # the .iss [Files] must source from a pre-built staging dir, not build.
    require("{#StagingDir}" in code, "guard 10: [Files] must source from the pre-built {#StagingDir}")
    require("bin/Release" in pack or "bin\\Release" in pack,
            "guard 10: pack.ps1 must stage from pre-built bin/Release output")
    # neither the .iss nor the pack step may invoke MSBuild / dotnet build/publish
    # (checked against comment-stripped source so prose describing the build
    # phase does not trip the guard).
    for blob, label in ((code_nc, ".iss"), (pack_nc, "pack.ps1")):
        low = blob.lower()
        require("msbuild" not in low, f"guard 10: {label} must not invoke MSBuild for consumed projects")
        require("dotnet build" not in low, f"guard 10: {label} must not run 'dotnet build'")
        require("dotnet publish" not in low, f"guard 10: {label} must not run 'dotnet publish'")
    # fail-fast: a release pack must throw on a missing required input rather
    # than warn-and-continue (else CI is green but the installer ships broken).
    require("Require-File" in pack and "throw" in pack,
            "guard 10: pack.ps1 must fail-fast (Require-File/throw) on missing required inputs")


def check_iss_references_all_five_artifacts(code: str) -> None:
    # drift: the installer must ship all five artifacts.
    require("yuantus-cad-helper.exe" in code, "installer must reference the helper exe")
    require(r"\helper\*" in code or r"\helper\\*" in code or "StagingDir}\\helper" in code,
            "installer must stage the helper/detector dir")
    require("YuantusCadHelperBridge.dll" in code, "installer must reference the bridge DLL")
    require("yuantus_cad_helper.lsp" in code, "installer must reference the Lisp shell")
    require("CADDedup.bundle" in code, "installer must reference the CADDedup bundle")


def check_running_helper_uses_session_pid(code: str) -> None:
    # the running-helper handling must read pid from the session file and
    # taskkill that specific pid (not a blind image-name kill).
    require("helper-session-*.json" in code, "running-helper detection must scan helper-session-*.json")
    require("JsonInt(Content, 'pid')" in code, "running-helper detection must read the session-file pid")
    require("JsonStr(Content, 'image_path')" in code,
            "running-helper detection must field-target image_path (not a loose substring match)")
    require("CompareText(ImagePath, ManagedHelperExe())" in code,
            "running-helper image_path must EQUAL the managed helper path (not endswith)")
    require("taskkill" in code.lower(), "running-helper handling must taskkill the read pid")
    require('IMAGENAME eq yuantus-cad-helper.exe' in code,
            "taskkill must add an IMAGENAME filter so a reused pid with a different image is not killed")
    require("tasklist" in code.lower() and "find /I" in code,
            "running-helper handling must verify the pid against the live process table")
    require("IsManagedHelperPidLive(Pid)" in code,
            "running-helper post-kill verification must not re-read a stale session file as still-running")
    require("still running and could not be stopped" in code,
            "install must abort (not proceed) if a managed helper cannot be stopped")
    # source-pin: the session schema really carries pid (no runtime change needed)
    if HELPER_RUNTIME.exists():
        rt = read(HELPER_RUNTIME)
        require('[JsonProperty("pid")]' in rt,
                "session schema must still serialize pid (HelperRuntime.cs) for this contract to hold")


def check_workflow_runs_verifier() -> None:
    require(WORKFLOW.exists(), "cad-helper-shared-dotnet workflow missing")
    wf = read(WORKFLOW)
    require("Installer/verify_installer_static.py" in wf,
            "workflow must run clients/cad-desktop-helper/Installer/verify_installer_static.py")
    require('"clients/cad-desktop-helper/Installer/**"' in wf,
            "workflow on.*.paths must include clients/cad-desktop-helper/Installer/**")


def check_dev_md_records_deferred_signoff() -> None:
    require(DEV_MD.exists(), f"DEV/Verification MD missing at {DEV_MD}")
    md = read(DEV_MD)
    require("deferred operational signoff" in md.lower(),
            "DEV/Verification MD must record the deferred operational signoff packet")
    require("feedback_production_seam_tests_without_fakes" in md or "without-fakes" in md.lower()
            or "without fakes" in md.lower(),
            "DEV/Verification MD must note the 4th application of the production-seam rule")
    # the 10 operator checks should be present (numbered list).
    require(md.count("\n1.") >= 1 and "10." in md,
            "DEV/Verification MD must enumerate the 10 operator-side deferred checks")


# ---------- main ----------

def main() -> int:
    code = read(ISS_FILE) if ISS_FILE.exists() else ""
    code_nc = strip_iss_comments(code) if code else ""

    checks = [
        ("installer .iss + pack.ps1 exist", check_iss_exists),
        ("guard 1: PrivilegesRequired=lowest", lambda: check_privileges_required_lowest(code)),
        ("guard 2: no HKLM write", lambda: check_no_hklm(code_nc)),
        ("guard 3: per-user {userappdata} paths, no relocatable layout", lambda: check_install_paths_pinned_to_userappdata(code, code_nc)),
        ("guard 4: signing present + #ifdef-guarded (CI unsigned)", lambda: check_signing_present_and_guarded(code)),
        ("guard 5: no Windows Service / auto-start", lambda: check_no_service_or_autostart(code, code_nc)),
        ("guard 6: no runtime-file pre-creation", lambda: check_no_runtime_file_pre_creation(code)),
        ("guard 7: CAD-startup fenced + --skip-cad-config gated", lambda: check_cad_startup_fenced_and_gated(code)),
        ("guard 8: uninstall allow-list, not blanket root delete", lambda: check_uninstall_is_allowlist_not_blanket(code)),
        ("guard 9: preserve set deleted only in full-purge opt-in", lambda: check_preserve_set_only_in_fullpurge(code)),
        ("guard 10: pack consumes pre-built bin/Release, no MSBuild", lambda: check_pack_consumes_prebuilt_no_msbuild(code, code_nc)),
        ("installer references all five artifacts", lambda: check_iss_references_all_five_artifacts(code)),
        ("running-helper handling reads session pid + taskkill /PID", lambda: check_running_helper_uses_session_pid(code)),
        ("workflow runs verify_installer_static.py + Installer/** filter", check_workflow_runs_verifier),
        ("DEV/Verification MD records deferred operational signoff", check_dev_md_records_deferred_signoff),
    ]

    failures = []
    for name, fn in checks:
        try:
            fn()
            print(f"  ok  {name}")
        except AssertionError as exc:
            print(f"  FAIL {name}: {exc}", file=sys.stderr)
            failures.append((name, str(exc)))
    if failures:
        print(f"\n{len(failures)} installer static guard(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} CAD helper installer static guards passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
