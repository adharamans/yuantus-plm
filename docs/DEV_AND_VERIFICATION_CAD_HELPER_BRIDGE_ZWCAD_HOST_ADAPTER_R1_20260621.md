# DEV & Verification: CAD Helper Bridge — ZWCAD (中望) host adapter (R1)

Date: 2026-06-21

Adds the ZWCAD (中望) managed-.NET host binding for the S9 NETLOAD Lisp bridge,
completing the domestic-CAD host pair alongside the GstarCAD adapter
(`DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_GSTARCAD_HOST_ADAPTER_R1_20260621.md`).
This is an **R1 source skeleton**: the ZWCAD adapter now exists in the bridge,
but a real-host build (against the ZWCAD .NET SDK) and operational NETLOAD
signoff remain **deferred** — see §6.

## 1. What changed

- New `clients/cad-desktop-helper/Bridge/Adapters/ZwCadHostAdapter.cs` — the
  ZWCAD counterpart of `AutoCadHostAdapter.cs` / `GstarCadHostAdapter.cs`.
  Registers the **same two** Lisp transport primitives (`yuantus-helper-call`,
  `yuantus-helper-upload`) using ZWCAD's `ZwSoft.ZwCAD.*` API, guarded by
  `#if ZWCAD_HOST`.
- This DEV/verification record + a sorted entry in `docs/DELIVERY_DOC_INDEX.md`.
- **No verifier change needed:** `verify_bridge_static.py:check_lisp_function_set`
  was already made host-aware for the GstarCAD adapter, so it accepts a third
  `*HostAdapter.cs` as long as it registers exactly the two primitives.

## 2. Scope / boundaries

- **Source-only, `#if ZWCAD_HOST`-guarded**, mirroring the checked-in state of
  the AutoCAD/GstarCAD adapters (host-bound code is excluded from the SDK-free
  CI build). **No csproj change** — the SDK-free CI build stays green.
- **Transport-only**, identical contract to S9/AutoCAD: no DWG mutation, no
  business logic, no modal UI, no direct HTTP/DPAPI (routed through the shared
  `BridgeCallService` → `SharedBridgeLocator`/`SharedBridgeTransport`).
- **Display-only on ZWCAD.** DWG field write-back stays AutoCAD-only and out of
  scope (R3 design `:724`).
- Does **not** collect native-CAD evidence; real `ZWCAD.exe` NETLOAD + the
  six-command run remain deferred to the operational signoff runbook.

## 3. API mapping (AutoCAD → ZWCAD)

ZWCAD's .NET API mirrors the AutoCAD .NET API shape (ZWCAD documents the .NET
migration as: replace the AutoCAD managed assemblies + `Autodesk.AutoCAD.*`
namespaces with the ZWCAD equivalents), so the adapter is a near-mechanical port:

| AutoCAD (`AutoCadHostAdapter.cs`) | ZWCAD (`ZwCadHostAdapter.cs`) |
|---|---|
| `Autodesk.AutoCAD.ApplicationServices` | `ZwSoft.ZwCAD.ApplicationServices` |
| `Autodesk.AutoCAD.Runtime` (`LispFunction`) | `ZwSoft.ZwCAD.Runtime` (`LispFunction`) |
| `Autodesk.AutoCAD.DatabaseServices` (`ResultBuffer`, `TypedValue`) | `ZwSoft.ZwCAD.DatabaseServices` (`ResultBuffer`, `TypedValue`) |
| `Application.DocumentManager.MdiActiveDocument.Editor.WriteMessage` | identical (under `ZwSoft.ZwCAD`) |
| Lisp string type code `5005` (`LispDataType.Text`) | identical (universal resbuf code) |
| assemblies `acmgd` / `acdbmgd` (+ `accoremgd`) | assemblies **`ZwManaged.dll`** + **`ZwDatabaseMgd.dll`** (ZWCAD install folder; set `Copy Local = False`) |

Source for the ZWCAD API names: ZWCAD .NET Developing Guide / ZWSOFT developer
docs (`ZwSoft.ZwCAD.*` namespaces; `ZwManaged.dll` + `ZwDatabaseMgd.dll`
references; `[LispFunction]` with a `ResultBuffer` parameter; `TypedValue`).

## 4. Build for a real host (deferred — needs the ZWCAD .NET SDK)

Same model as the AutoCAD/GstarCAD host builds: define the host symbol and supply
the managed assembly references at build time; the checked-in SDK-free csproj is
unchanged. Example:

```
dotnet build clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj \
  -p:DefineConstants=ZWCAD_HOST \
  -p:ZwManaged="<ZWCAD>\ZwManaged.dll" \
  -p:ZwDatabaseMgd="<ZWCAD>\ZwDatabaseMgd.dll"
```

(plus `<Reference HintPath=...>` items keyed off those properties, or a
`ZWCAD`-host build profile). The exact assembly names/paths must be confirmed
against the installed ZWCAD .NET SDK before the first real build.

## 5. Verification

- `python clients/cad-desktop-helper/verify_bridge_static.py` → **13/13 pass**
  with three host adapters (AutoCAD + GstarCAD + ZWCAD), via the host-aware guard
  *"each host adapter registers exactly {yuantus-helper-call, yuantus-helper-upload}"*.
- `python clients/cad-desktop-helper/verify_lisp_shell_static.py` → **29/29 pass**
  (unchanged; the shared LISP shell already sniffs `zwcad`).
- The adapter passes the existing bridge content guards (no business/UI/DWG
  tokens, no `new HttpClient`/`ProtectedData`/`LocalTokenStore`/`Process.Start`,
  no `.Result;`/`.Wait()`).

**Deferred (cannot run in this environment):** C# compile against the ZWCAD .NET
SDK; NETLOAD into a real `ZWCAD.exe`; the six in-CAD commands against a live
helper + PLM — per
`docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md`.
This machine has no ZWCAD .NET SDK and no real `ZWCAD.exe`.

## 6. Status

R1 source skeleton. With this and the GstarCAD adapter, both domestic-CAD .NET
host bindings now **exist** in the bridge (previously AutoCAD-only). The answer
to *"can the plugin load in ZWCAD?"* stays **no** until the DLL is built against
the ZWCAD SDK and the operational signoff runbook's **ZWCAD 2025** row is filled
on a real host.

## References
- `clients/cad-desktop-helper/Bridge/Adapters/AutoCadHostAdapter.cs` (mirrored source)
- `docs/DEV_AND_VERIFICATION_CAD_HELPER_BRIDGE_GSTARCAD_HOST_ADAPTER_R1_20260621.md` (sibling domestic-CAD adapter)
- `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` §5.7 (LISP bridge protocol; domestic-CAD adapter design)
- `docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md` (real-host signoff)
