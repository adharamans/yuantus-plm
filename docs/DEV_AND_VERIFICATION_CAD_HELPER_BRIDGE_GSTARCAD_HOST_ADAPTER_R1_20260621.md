# DEV & Verification: CAD Helper Bridge — GstarCAD (浩辰) host adapter (R1)

Date: 2026-06-21

Adds the GstarCAD (浩辰) managed-.NET host binding for the S9 NETLOAD Lisp
bridge, which previously existed only for AutoCAD. This is an **R1 source
skeleton**: the GstarCAD adapter now exists in the bridge, but a real-host build
(against the GstarCAD .NET SDK) and operational NETLOAD signoff remain
**deferred** — see §6.

## 1. What changed

- New `clients/cad-desktop-helper/Bridge/Adapters/GstarCadHostAdapter.cs` — the
  GstarCAD counterpart of `AutoCadHostAdapter.cs`. Registers the **same two**
  Lisp transport primitives (`yuantus-helper-call`, `yuantus-helper-upload`)
  using GstarCAD's `Gssoft.Gscad.*` API, guarded by `#if GSTARCAD_HOST`.
- `clients/cad-desktop-helper/verify_bridge_static.py` — `check_lisp_function_set`
  made **host-aware**: instead of counting `[LispFunction(` across the
  concatenated bridge source (which would read 4 with two host adapters), it now
  asserts that **each** `*HostAdapter.cs` registers exactly
  `{yuantus-helper-call, yuantus-helper-upload}`. The two-primitive invariant is
  preserved per host.
- This DEV/verification record + a sorted entry in `docs/DELIVERY_DOC_INDEX.md`.

## 2. Scope / boundaries

- **Source-only, `#if GSTARCAD_HOST`-guarded**, mirroring the checked-in state
  of the AutoCAD adapter (the bridge csproj has **no** host build block for
  AutoCAD either; host-bound code is excluded from the SDK-free CI build). **No
  csproj change** — keeps CI's SDK-free build green.
- **Transport-only**, identical contract to S9/AutoCAD: no DWG mutation, no
  business logic, no modal UI, no direct HTTP/DPAPI (routed through the shared
  `BridgeCallService` → `SharedBridgeLocator`/`SharedBridgeTransport`).
- **Display-only on GstarCAD.** DWG field write-back stays AutoCAD-only and out
  of scope (R3 design `:724`).
- Does **not** collect native-CAD evidence; real `gscad.exe` NETLOAD + the
  six-command run remain deferred to the operational signoff runbook.

## 3. API mapping (AutoCAD → GstarCAD)

GstarCAD's .NET API mirrors the AutoCAD .NET API shape, so the adapter is a
near-mechanical namespace port:

| AutoCAD (`AutoCadHostAdapter.cs`) | GstarCAD (`GstarCadHostAdapter.cs`) |
|---|---|
| `Autodesk.AutoCAD.ApplicationServices` | `Gssoft.Gscad.ApplicationServices` |
| `Autodesk.AutoCAD.Runtime` (`LispFunction`) | `Gssoft.Gscad.Runtime` (`LispFunction`) |
| `Autodesk.AutoCAD.DatabaseServices` (`ResultBuffer`, `TypedValue`) | `Gssoft.Gscad.DatabaseServices` (`ResultBuffer`, `TypedValue`) |
| `Application.DocumentManager.MdiActiveDocument.Editor.WriteMessage` | identical (under `Gssoft.Gscad`) |
| Lisp string type code `5005` (`LispDataType.Text`) | identical (universal resbuf code) |
| assemblies `acmgd` / `accoremgd` / `acdbmgd` | assemblies `GcMgd.dll` / `GcCoreMgd.dll` / `GcDbMgd.dll` (typically `<GstarCAD>\arx\inc`) |

Source for the GstarCAD API names: GstarCAD .NET Programming Guide / Developer
docs (`Gssoft.Gscad.*` namespaces; `GcMgd`/`GcDbMgd`/`GcCoreMgd` references;
`[LispFunction]` with a `ResultBuffer` parameter; `TypedValue`/`LispDataType`).

## 4. Build for a real host (deferred — needs the GstarCAD .NET SDK)

Same model as the AutoCAD host build: define the host symbol and supply the
managed assembly references at build time; the checked-in SDK-free csproj is
unchanged. Example:

```
dotnet build clients/cad-desktop-helper/Bridge/YuantusCadHelperBridge.csproj \
  -p:DefineConstants=GSTARCAD_HOST \
  -p:GcMgd="<GstarCAD>\arx\inc\GcMgd.dll" \
  -p:GcDbMgd="<GstarCAD>\arx\inc\GcDbMgd.dll" \
  -p:GcCoreMgd="<GstarCAD>\arx\inc\GcCoreMgd.dll"
```

(plus `<Reference HintPath=...>` items keyed off those properties, or a
`GstarCAD`-host build profile). The exact assembly names/paths must be confirmed
against the installed GstarCAD .NET SDK before the first real build.

## 5. Verification

- `python clients/cad-desktop-helper/verify_bridge_static.py` → **13/13 pass**,
  including the new host-aware guard
  *"each host adapter registers exactly {yuantus-helper-call, yuantus-helper-upload}"*.
- `python clients/cad-desktop-helper/verify_lisp_shell_static.py` → **29/29 pass**
  (unchanged; the shared LISP shell already sniffs `gstarcad`/`gcad`).
- The adapter passes the existing bridge content guards (no business/UI/DWG
  tokens, no `new HttpClient`/`ProtectedData`/`LocalTokenStore`/`Process.Start`,
  no `.Result;`/`.Wait()`), so concatenated-source checks remain green.

**Deferred (cannot run in this environment):** C# compile against the GstarCAD
.NET SDK; NETLOAD into a real `gscad.exe`; the six in-CAD commands against a
live helper + PLM — per
`docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md`.
This machine has no GstarCAD .NET SDK and no real `gscad.exe`; the operational
signoff is the next, separate, real-host step.

## 6. Status

R1 source skeleton. The GstarCAD .NET binding now **exists** in the bridge
(previously AutoCAD-only), closing the structural gap identified in
`docs/devlog/2026-06-21-gstarcad-plugin-load-status.md`. The answer to *"can the
plugin load in 浩辰?"* stays **no** until the DLL is built against the GstarCAD
SDK and the operational signoff runbook's **GstarCAD 2025** row is filled on a
real host. This adapter is a step toward that, not the resolution.

## References
- `clients/cad-desktop-helper/Bridge/Adapters/AutoCadHostAdapter.cs` (mirrored source)
- `docs/CAD_DESKTOP_HELPER_BRIDGE_DESIGN_R3_20260519.md` §5.7 (LISP bridge protocol; domestic-CAD adapter design)
- `docs/CAD_HELPER_BRIDGE_NATIVE_CAD_OPERATIONAL_SIGNOFF_RUNBOOK_20260527.md` (real-host signoff)
- `docs/devlog/2026-06-21-gstarcad-plugin-load-status.md` (status assessment + load checklist)
