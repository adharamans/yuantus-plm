#!/usr/bin/env python3
"""Static verification for the CAD helper bridge (S9).

The S9 bridge (`Yuantus.Cad.Bridge` / `YuantusCadHelperBridge.dll`) is a
NETLOAD transport bridge: native CAD Lisp -> Shared helper transport. The
SDK-free core can be unit-tested without AutoCAD, but the CAD-host adapter
cannot be compiled or NETLOAD'd on the GitHub Windows runner. This static
verifier catches drift in the bridge sources that does not require AutoCAD
managed assemblies. It mirrors the source/drift guards in taskbook §5 and the
mandatory tests in `Bridge.Tests/BridgeContractTests.cs`.

Usage:
    python3 clients/cad-desktop-helper/verify_bridge_static.py

Exit codes:
    0 - all guards pass
    1 - at least one guard failed (assertion message printed to stderr)
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT / "Bridge"
BRIDGE_TESTS = ROOT / "Bridge.Tests"
HELPER = ROOT / "Helper"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def gather_sources(root: Path) -> str:
    parts = []
    for cs in sorted(root.rglob("*.cs")):
        if any(part in {"bin", "obj"} for part in cs.parts):
            continue
        parts.append(read(cs))
    return "\n".join(parts)


def check_csproj_targets_net46() -> None:
    project = BRIDGE / "YuantusCadHelperBridge.csproj"
    tree = ET.parse(project)
    root = tree.getroot()
    target = None
    for elem in root.iter():
        tag = elem.tag.split("}")[-1]
        if tag in {"TargetFramework", "TargetFrameworks"}:
            target = elem.text or ""
            break
    require(
        target is not None and "net46" in target,
        f"Bridge csproj must target net46; got {target!r}",
    )

    refs = [(elem.attrib.get("Include") or "") for elem in root.iter() if elem.tag.split("}")[-1] == "ProjectReference"]
    shared_refs = [r for r in refs if r.replace("\\", "/").endswith("Shared/Yuantus.Cad.Shared.csproj")]
    require(len(shared_refs) == 1, "Bridge csproj must ProjectReference Shared exactly once")


def check_single_lisp_function(bridge_sources: str) -> None:
    occurrences = re.findall(r"\[LispFunction\(", bridge_sources)
    require(
        len(occurrences) == 1,
        f"Bridge must expose exactly one LispFunction; found {len(occurrences)}",
    )
    require(
        '[LispFunction("yuantus-helper-call")]' in bridge_sources,
        "Bridge must register exactly the literal Lisp function name yuantus-helper-call",
    )


def check_no_direct_httpclient_or_dpapi(bridge_sources: str) -> None:
    require(
        "new HttpClient(" not in bridge_sources,
        "Bridge core must not construct HttpClient; route through Shared HelperTransport",
    )
    require(
        "ProtectedData." not in bridge_sources,
        "Bridge must not access ProtectedData; route through Shared LocalTokenStore via HelperTransport",
    )
    require(
        "LocalTokenStore.ReadLocalToken" not in bridge_sources
        and "LocalTokenStore.WriteLocalToken" not in bridge_sources,
        "Bridge must not call LocalTokenStore directly; HelperTransport handles token injection",
    )
    require(
        "Process.Start" not in bridge_sources,
        "Bridge must not start the helper directly; use Shared HelperLocator/HelperSpawner",
    )


def check_no_absolute_scheme_forwarding(bridge_sources: str) -> None:
    # The endpoint validator must reject absolute schemes including
    # percent-encoded scheme-confusion attempts. Verify the validator names
    # the rejection rules.
    validator = read(BRIDGE / "EndpointValidator.cs")
    for rule in ("absolute_scheme", "network_path", "backslash", "percent", "control_char"):
        require(rule in validator, f"EndpointValidator must include rejection rule {rule!r}")
    require(
        "http://" in validator and "https://" in validator and "file://" in validator,
        "EndpointValidator must reject http/https/file absolute schemes by name",
    )


def check_wiring_reaches_shared_helper_locator_and_transport() -> None:
    # M1 convergence requires the production wiring to reach S1 Shared
    # HelperLocator and HelperTransport (see Bridge.Tests test 20).
    locator = read(BRIDGE / "SharedBridgeLocator.cs")
    require(
        "using Yuantus.Cad.Shared.Discovery;" in locator
        and "new HelperLocator()" in locator
        and ".EnsureHelperRunningAsync(" in locator,
        "SharedBridgeLocator must reach Shared HelperLocator.EnsureHelperRunningAsync",
    )

    transport = read(BRIDGE / "SharedBridgeTransport.cs")
    require(
        "using Yuantus.Cad.Shared.Transport;" in transport
        and "new HelperTransport(" in transport
        and ".PostJsonAsync<JToken>(" in transport,
        "SharedBridgeTransport must reach Shared HelperTransport.PostJsonAsync<JToken>",
    )

    service = read(BRIDGE / "BridgeCallService.cs")
    require(
        "new SharedBridgeLocator()" in service and "new SharedBridgeTransport()" in service,
        "BridgeCallService.CreateProduction must wire SharedBridgeLocator + SharedBridgeTransport",
    )


def check_no_business_logic(bridge_sources: str) -> None:
    forbidden = (
        "CadMaterialFieldService",
        "YUANTUS_DIFF_PREVIEW",
        "PLMMATPULL",
        "PLMMATPUSH",
        "PLMMATCOMPOSE",
        "PLMMATPROFILES",
        "MessageBox",
        ".ShowDialog(",
        "Transaction.Start",
        "BlockReference",
    )
    for token in forbidden:
        require(
            token not in bridge_sources,
            f"Bridge must not contain business/UI/DWG-mutation token {token!r}",
        )


def check_helper_route_count_after_g1b() -> None:
    helper_sources = gather_sources(HELPER)
    map_count = helper_sources.count("MapGet(") + helper_sources.count("MapPost(")
    require(
        map_count == 14,
        f"Helper production routes must be exactly 14 after G1-B (G1-A 13 + /document/checkin); got {map_count}",
    )
    bridge_sources = gather_sources(BRIDGE)
    for verb in ("MapGet(", "MapPost(", "MapPut(", "MapDelete("):
        require(verb not in bridge_sources, f"Bridge must not declare {verb} routes")


def check_g1b_document_checkin_does_not_read_local_filesystem() -> None:
    helper = read(HELPER / "HelperRuntime.cs")
    marker = 'app.MapPost("/document/checkin"'
    start = helper.find(marker)
    require(start >= 0, "G1-B helper route /document/checkin must be registered")
    end = helper.find("\n            });", start)
    require(end >= 0, "G1-B helper route /document/checkin block must be parseable")
    route_block = helper[start:end]

    require("ReadFormAsync" in route_block, "G1-B checkin route must read multipart form bytes")
    require("CopyToAsync" in route_block, "G1-B checkin route must copy uploaded file bytes")
    forbidden = (
        "File.OpenRead",
        "File.ReadAllBytes",
        "File.ReadAllText",
        "File.Open(",
        "new FileStream",
        "Path.GetFullPath",
        "Path.Combine",
        "Directory.",
        "FileInfo",
        'form["path"]',
        'form["file_path"]',
    )
    for token in forbidden:
        require(
            token not in route_block,
            f"G1-B /document/checkin must not read local filesystem or accept a file path; found {token!r}",
        )


def check_no_s10_lisp_command_files() -> None:
    lsp = list(BRIDGE.rglob("*.lsp"))
    require(not lsp, f"S9 must not add Lisp shell command files; found {lsp}")


def check_sync_wrapper_pattern(bridge_sources: str) -> None:
    require(
        ".GetAwaiter().GetResult()" in bridge_sources,
        "Bridge sync wrapper must use .GetAwaiter().GetResult() to avoid AggregateException",
    )
    require(
        ".Result;" not in bridge_sources,
        "Bridge must not use Task.Result (hides exceptions in AggregateException)",
    )
    require(
        ".Wait()" not in bridge_sources,
        "Bridge must not use Task.Wait() (hides exceptions in AggregateException)",
    )


def check_deferred_signoff_recorded() -> None:
    dev = ROOT.parent.parent / "docs" / "DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_S9_LISP_BRIDGE_R1_20260523.md"
    require(dev.exists(), f"DEV/Verification MD must exist at {dev}")
    text = read(dev)
    for token in ("Deferred", "NETLOAD", "operational signoff", "CAD command-line writer"):
        require(
            token in text,
            f"DEV/Verification MD must record deferred signoff token {token!r}",
        )


def main() -> int:
    bridge_sources = gather_sources(BRIDGE)
    checks = [
        ("csproj targets net46 + Shared reference", check_csproj_targets_net46),
        ("single LispFunction(yuantus-helper-call)", lambda: check_single_lisp_function(bridge_sources)),
        ("no direct HttpClient / DPAPI / LocalTokenStore / Process.Start", lambda: check_no_direct_httpclient_or_dpapi(bridge_sources)),
        ("EndpointValidator rejects absolute schemes / network paths / backslash / percent / control chars", lambda: check_no_absolute_scheme_forwarding(bridge_sources)),
        ("production wiring reaches Shared HelperLocator / HelperTransport (M1 convergence)", check_wiring_reaches_shared_helper_locator_and_transport),
        ("no business / UI / DWG-mutation logic in bridge", lambda: check_no_business_logic(bridge_sources)),
        ("helper route count is 14 after G1-B", check_helper_route_count_after_g1b),
        ("G1-B document checkin uses multipart bytes, not local file reads", check_g1b_document_checkin_does_not_read_local_filesystem),
        ("no S10 Lisp shell command files", check_no_s10_lisp_command_files),
        ("sync wrapper uses .GetAwaiter().GetResult()", lambda: check_sync_wrapper_pattern(bridge_sources)),
        ("DEV/Verification MD records deferred NETLOAD / CAD writer signoff", check_deferred_signoff_recorded),
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
        print(f"\n{len(failures)} static guard(s) failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(checks)} S9 bridge static guards passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
