# CAD 物料助手 v1 - Phase 3 收尾任务书：AutoCAD `PLMMATASSIST` 命令

- Date: 2026-06-04
- Status: Task (doc-only; implementation requires separate authorization)
- Parent plan: `docs/DEVELOPMENT_CAD_MATERIAL_ASSISTANT_V1_PLAN_20260603.md` (#701)
- Predecessors: Phase 1 #702/#704, Phase 2 #706/#707, Phase 3 SDK-free #709/#711
- Phase: 3 manual closeout / AutoCAD SDK path

## 1. Goal

Add the AutoCAD command entry `PLMMATASSIST` on top of the already-merged SDK-free assistant path:

`AutoCAD command -> MaterialSyncApiClient.ResolveAsync/CreateAsync -> Helper /material/assistant/* -> PLM /plugins/cad-material-sync/assistant/*`.

This task closes the native AutoCAD command gap left by #711. It does not change PLM assistant semantics, helper routes, Bridge routing, field similarity scoring, or dedup_vision.

## 2. Scope Boundary

This phase is **not CI-complete by build alone** because `clients/autocad-material-sync/CADDedupPlugin/CADDedupPlugin.csproj` requires AutoCAD managed assemblies. The implementation PR can be guarded by source/static tests in CI, but final acceptance needs Windows + AutoCAD manual evidence.

In scope:

- `clients/autocad-material-sync/CADDedupPlugin/DedupPlugin.cs`
  - add `[CommandMethod("PLMMATASSIST")]`;
  - extract current CAD fields with `CadMaterialFieldService.ExtractFields(doc)`;
  - call `MaterialSyncApiClient.ResolveAsync(...)`;
  - show an assistant result/confirmation UI;
  - call `CreateAsync(...)` only after explicit user confirmation.
- AutoCAD command discoverability:
  - `DEDUPHELP`;
  - Ribbon material-sync panel;
  - `PackageContents.xml`, `PackageContents.2018.xml`, `PackageContents.2024.xml`;
  - `README.md` and `PLM_MATERIAL_SYNC_GUIDE.md`.
- Static CI guards:
  - `clients/autocad-material-sync/verify_material_sync_static.py`;
  - optional source-reading contract in `CADDedupPlugin.Client.Tests` if useful, but do not try to build `CADDedupPlugin.csproj` in CI.
- Windows/manual evidence docs:
  - `clients/autocad-material-sync/WINDOWS_AUTOCAD2018_VALIDATION_GUIDE.md`;
  - `docs/CAD_MATERIAL_SYNC_WINDOWS_VALIDATION_EVIDENCE_TEMPLATE_20260511.md` or a focused assistant evidence section/template.

Out of scope:

- no new helper route, Bridge route, or PLM route;
- no migration of `/compose`, `/validate`, or `/profiles`;
- no dedup_vision drawing similarity;
- no LLM/NL intent parser;
- no SolidWorks/ZWCAD/GstarCAD command entry;
- no implicit DWG write-back after create unless a later task adds an explicit server response contract for CAD fields.

## 3. Grounding

- #711 already added helper-forwarded client methods in `MaterialSyncApiClient.cs`:
  - `ResolveAsync(profileId, cadFields, values)` sends `/material/assistant/resolve` and includes `cad_system = "autocad"` (:173-188).
  - `CreateAsync(profileId, properties, cadFields, values)` sends `/material/assistant/create` (:191-207).
  - Resolve response includes `composed_properties`, `exact_matches`, `similar_candidates`, `draft_suggested` (:374-402).
  - Create response includes `item_id`, `item_number`, `state`, `current_state`, `draft_check` (:404-431).
- `DedupPlugin.cs` already has the nearest workflow pattern:
  - `PLMMATPULL` prompts profile, extracts fields, calls `DiffPreviewAsync`, opens `MaterialSyncDiffPreviewWindow`, and only writes CAD after confirmation (:585-668).
  - `PromptProfileId(...)` exists (:674-692).
  - `DEDUPHELP` and Ribbon already list material commands (:416-435, :1000-1028).
- `CadMaterialFieldService` supports extraction and write-back:
  - `ExtractFields(Document doc)` reads title block/table fields (:26-44).
  - `ApplyFields(...)` exists (:46-67), but this task must not use it for assistant create unless an explicit field package is returned.
- Static guard already checks material command registration:
  - `verify_material_sync_static.py::check_commands_registered` currently pins `PLMMATPROFILES`, `PLMMATCOMPOSE`, `PLMMATPUSH`, `PLMMATPULL` across package XML, `DedupPlugin.cs`, README, and guide (:134-169).
  - `check_diff_preview_ui_contract` pins the `PLMMATPULL` confirmation pattern (:317-335).
- Windows validation already covers build/load/command evidence and `PLMMATPULL`; it needs assistant-specific steps and rejection rules.

## 4. Command Contract

`PLMMATASSIST` must be conservative and explicit:

1. Require an active AutoCAD document/editor; otherwise return quietly like existing commands.
2. Prompt `profile_id` using `PromptProfileId(ed)`.
3. Extract `cadFields = _materialFieldService.ExtractFields(doc)`.
4. Call `_materialSyncClient.ResolveAsync(profileId, cadFields, values: empty)`.
5. Print a concise command-line summary:
   - status OK/failed;
   - profile;
   - exact match count;
   - similar candidate count;
   - `draft_suggested`;
   - warnings/errors.
6. Show an assistant result window before any write:
   - composed properties/specification;
   - exact matches;
   - similar candidates with score/high-similar;
   - warnings/errors;
   - a disabled/hidden create action unless `draft_suggested == true` or the user explicitly chooses "create draft".
7. If the user cancels/closes the window, do not call `CreateAsync` and do not write DWG.
8. If the user confirms create, call `CreateAsync(profileId, properties, cadFields, values)` where `properties` comes from the confirmed composed properties/draft payload, not from arbitrary UI text.
9. After create, show `item_id`, `item_number`, `state`, `current_state`, and `draft_check`.
10. Do not call `ApplyFields(...)` from `PLMMATASSIST` in this phase. `assistant/create` does not return a CAD field package; inventing one in the client would make the command silently diverge from PLM profile mapping.

## 5. UI Contract

Prefer a small WPF dialog modeled after `MaterialSyncDiffPreviewWindow`:

- Suggested files:
  - `MaterialAssistantWindow.xaml`;
  - `MaterialAssistantWindow.xaml.cs`.
- The project file must include both (`Compile` + `Page`) like the existing diff preview window.
- The window should be inspection-first:
  - top context: profile, exact/similar counts, draft suggested;
  - candidate grid: item number/name/specification/score/high-similar;
  - draft section: composed properties and specification;
  - action buttons: `创建 Draft` and `取消`.
- The create button must be an explicit user action. No default Enter-key path should create a PLM item accidentally.
- The window must not show secrets, bearer tokens, local helper tokens, or raw Authorization headers.

If a new WPF window is too large for the implementation slice, a command-line confirmation path is acceptable for v1, but it must still satisfy the no-write/cancel/create-confirmed contracts and be covered by static guards.

## 6. Static Guards

Extend `clients/autocad-material-sync/verify_material_sync_static.py`:

- `check_commands_registered` should include `PLMMATASSIST` across:
  - `PackageContents.xml`;
  - `PackageContents.2018.xml`;
  - `PackageContents.2024.xml`;
  - `DedupPlugin.cs`;
  - `README.md`;
  - `PLM_MATERIAL_SYNC_GUIDE.md`.
- Add a focused `check_material_assistant_command_contract`:
  - `CommandMethod("PLMMATASSIST")` exists;
  - `DEDUPHELP` describes `PLMMATASSIST`;
  - Ribbon contains `CommandParameter = "PLMMATASSIST"`;
  - command calls `ExtractFields(doc)` before `ResolveAsync`;
  - command calls `ResolveAsync`;
  - command calls `CreateAsync` only in a confirmation branch/window result path;
  - command does not call `ApplyFields(doc, ...)` in the assistant flow;
  - new assistant window files are included in `CADDedupPlugin.csproj` if a WPF window is added.
- Keep the workflow guard that `verify_material_sync_static.py` runs in `cad-helper-shared-dotnet.yml`.
- Do not add a CI build of `CADDedupPlugin.csproj`.

## 7. Windows / AutoCAD Manual Acceptance

Update the Windows validation guide/template with an assistant section.

Minimum evidence:

- commit SHA and built DLL path;
- AutoCAD 2018 `ACADVER` = `R22.0`;
- preflight output;
- `NETLOAD` or bundle load output;
- `DEDUPHELP` showing `PLMMATASSIST`;
- screenshot/command log for `PLMMATASSIST` resolve result;
- PLM/helper log excerpt proving `/material/assistant/resolve` was called;
- cancel-path evidence: no PLM item created and no DWG field changed;
- create-path evidence on a test tenant/item only:
  - explicit confirmation screenshot;
  - PLM item created;
  - returned `item_id/item_number/state/current_state/draft_check`;
  - item lifecycle state follows the Phase 2 Draft/start-state contract;
  - no hidden DWG write-back unless a later contract explicitly adds it.

Reject evidence if:

- `PLMMATASSIST` is missing from `DEDUPHELP` or package commands;
- create occurs without an explicit confirmation action;
- resolve changes PLM business rows or DWG fields;
- create evidence uses production drawings or production customer data;
- token/password/local helper token appears in screenshots/logs;
- AutoCAD build/load evidence is missing.

## 8. Suggested Implementation Order

1. Static guard first: add expected `PLMMATASSIST` pins to `verify_material_sync_static.py`, confirm it fails on current code.
2. Add command registration/discoverability: package XMLs, help text, Ribbon, README/guide.
3. Add `PLMMATASSIST` command body using existing `ResolveAsync/CreateAsync`.
4. Add assistant result UI or explicit command-line confirmation.
5. Update project includes if a WPF window is added.
6. Update Windows validation guide/evidence template.
7. Run:
   - `python3 clients/autocad-material-sync/verify_material_sync_static.py`;
   - `python3 clients/cad-desktop-helper/verify_bridge_static.py`;
   - `python3 clients/cad-desktop-helper/verify_lisp_shell_static.py`;
   - `git diff --check`;
   - GitHub CI.
8. Hand off Windows/AutoCAD 2018 manual validation; do not mark manual acceptance complete until evidence is attached and reviewed.

## 9. Exit Criteria

Implementation PR can merge when:

- static guards and existing CI are green;
- `PLMMATASSIST` is registered and discoverable;
- resolve/cancel/create-confirmed contracts are locked by source/static checks;
- docs clearly state that AutoCAD manual acceptance remains pending until Windows evidence is reviewed.

Phase 3 manual closeout is complete only when the Windows evidence proves the command loads and runs inside AutoCAD 2018 against a real test DWG and test PLM tenant.
