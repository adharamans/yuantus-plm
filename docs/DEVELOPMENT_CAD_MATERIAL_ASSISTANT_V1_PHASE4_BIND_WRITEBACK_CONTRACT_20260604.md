# CAD 物料助手 v1 - Phase 4 任务书：绑定已有物料 + CAD 回写 contract

## 1. Status

- Date: 2026-06-04
- Type: doc-only taskbook; no runtime changes in this PR.
- Parent plan: `docs/DEVELOPMENT_CAD_MATERIAL_ASSISTANT_V1_PLAN_20260603.md` (#701)
- Predecessors:
  - Phase 2 implementation: assistant resolve/create + similarity (#707)
  - Phase 3 SDK-free implementation: Helper Bridge + client API (#711)
  - Phase 3 manual command implementation: `PLMMATASSIST` (#715)
  - Phase 3 evidence gate: `PLMMATASSIST` Windows evidence validator/template (#716)

Phase 4 implementation requires a separate explicit authorization. This document only locks the contract and the safe implementation order.

## 2. Goal

`PLMMATASSIST` currently reads CAD fields, calls assistant `resolve`, displays exact/similar candidates, and can create a new Draft item after explicit confirmation. Existing material candidates are deliberately display-only: no bind action and no DWG write-back.

Phase 4 unlocks the deferred v1 end-state from #701: when `resolve` returns an exact or acceptable similar material, the user can select that existing PLM Item, preview CAD field differences, confirm, write only the confirmed CAD fields back to the DWG, and audit the result.

## 3. Scope

In scope:

- AutoCAD `PLMMATASSIST` existing-item flow:
  - display exact/similar candidates with enough stable identity to select an `item_id`;
  - ask the user to choose one candidate;
  - call existing helper-forwarded `DiffPreviewAsync(profile_id, item_id, current_cad_fields, include_empty:false)`;
  - show the existing `MaterialSyncDiffPreviewWindow`;
  - call `ApplyFields` only after explicit preview confirmation;
  - call `/audit/apply-result` through `ReportApplyResultSafely`, matching `PLMMATPULL`.
- Backend/plugin tests that prove the selected item preview uses the selected `Item.properties`, not the incoming assistant draft properties.
- C# SDK-free contract/static tests that pin the `PLMMATASSIST` bind/write-back order and keep direct PLM calls out of the assistant client path.
- Windows evidence template/validator update for the new existing-item bind/write-back evidence fields.

Out of scope:

- dedup_vision drawing similarity.
- LLM or natural-language intent parsing.
- SolidWorks/ZWCAD/GstarCAD UI entry.
- A new persistent server-side DWG-to-Item relationship table.
- Silent or automatic DWG write-back.
- Treating high-similar candidates as auto-bindable without user selection.

## 4. Grounding

- `AssistantResolveResponse.cad_fields` is not a selected-item write-back package. It is currently generated from the composed incoming CAD/values inside `assistant_resolve` (`cad_field_package(profile, properties, cad_system=req.cad_system)`) and returned with `exact_matches` / `similar_candidates`.
- `AssistantCreateResponse` returns `item_id`, `item_number`, `state`, `current_state`, and `draft_check`, but no `cad_fields`.
- `/diff/preview` already supports selected-item preview: when `item_id` is supplied, it loads `Item`, uses its `properties`, composes a profile package, returns `target_cad_fields`, calculates `write_cad_fields`, and sets `requires_confirmation`.
- `MaterialSyncApiClient.DiffPreviewAsync` is already helper-forwarded through `/diff/preview`, returns a `PullId`, and `ReportApplyResultAsync` already posts `/audit/apply-result`.
- `PLMMATPULL` already has the safe write-back shape: extract current fields -> `DiffPreviewAsync` -> `MaterialSyncDiffPreviewWindow` -> confirm -> `ApplyFields` -> `ReportApplyResultSafely`.
- `PLMMATASSIST` currently prints `-- 已存在物料（仅展示，不绑定/不回写）--`, then only offers confirm-gated Draft creation.

## 5. Contract Decisions

### 5.1 Reuse `/diff/preview`; do not add a bind route in v1

Phase 4 should not introduce a new PLM route just to preview binding. Existing `/diff/preview` already has the right read-only selected-item primitive and Helper audit wrapper.

The assistant flow is:

1. `PLMMATASSIST` calls `ResolveAsync`.
2. User selects one exact/similar candidate by `item_id`.
3. `PLMMATASSIST` calls `DiffPreviewAsync(profile_id, selected_item_id, currentCadFields, includeEmpty:false)`.
4. User confirms the diff window.
5. `PLMMATASSIST` applies `preview.WriteCadFields` and reports the result.

This keeps Helper route count stable and avoids duplicating diff/confirmation semantics.

### 5.2 Do not use `resolve.cad_fields` for existing-item write-back

`resolve.cad_fields` remains a draft/proposal CAD package derived from current input. It can help explain what the assistant composed, but it must not be written to the DWG for an existing selected item.

For existing-item write-back, the only accepted source of `write_cad_fields` is `/diff/preview` for the selected `item_id`.

### 5.3 "Bind" means CAD field write-back + audit unless a later phase adds persistence

There is no new server-side relationship table in this phase. The user-visible bind action means:

- selected PLM Item fields are written back to the current DWG through existing CAD field mapping;
- the write result is audited through the existing pull/apply-result path.

If product wants a durable DWG-to-PLM relationship beyond field values, that needs a separate data model and contract.

Product checkpoint before implementation: confirm that this narrower definition of
"bind" is acceptable for v1. If product expects PLM-side queryability of which DWG
is bound to which Part, stop and open a data-model phase before changing the
AutoCAD command.

### 5.4 Candidate identity must be robust

The AutoCAD command must reject candidate selection when the candidate has no usable `id`. It must not try to infer an item id from material number, description, or rendered text.

If multiple exact matches exist, the command must require explicit selection. It must not auto-select the first exact match.

### 5.5 Create remains a separate branch

Existing-item bind/write-back and new Draft creation are separate user decisions:

- exact/similar candidate selected -> diff preview/write-back branch;
- no suitable existing item -> create Draft branch;
- create-after-high-similar remains an explicit override, not the default.

Phase 4 does not need `assistant/create` to return `cad_fields`. Create-and-writeback can be a later explicit contract if needed.

## 6. Implementation Order

### Step A - Backend contract tests

- Add focused tests to `src/yuantus/meta_engine/tests/test_plugin_cad_material_sync.py` or a small assistant-specific test file:
  - `/assistant/resolve` remains business-table read-only.
  - `/diff/preview` with `item_id` uses selected `Item.properties` even when current CAD fields contain conflicting values.
  - `/diff/preview` returns `write_cad_fields` from selected item target fields, not from `AssistantResolveResponse.cad_fields`.
  - missing candidate/item id returns a clean existing error path (`404 Item not found` for backend preview; client must pre-check before call).
- Do not add backend routes or settings.

### Step B - C# DTO/client hardening

- Keep `ResolveAsync`, `DiffPreviewAsync`, and `ReportApplyResultAsync` as the only client calls needed for bind/write-back.
- Add helper methods in the AutoCAD client code to extract candidate `id` safely from `Dictionary<string, object>` / JSON-backed values.
- Add SDK-free tests in `CADDedupPlugin.Client.Tests` to prove:
  - assistant resolve still uses helper route `/material/assistant/resolve`;
  - bind/write-back branch calls `/diff/preview` after candidate selection;
  - no assistant bind path directly calls `BasePath` PLM endpoints.

### Step C - `PLMMATASSIST` command flow

Update only the `PLMMATASSIST` method body and nearby helper methods:

1. Extract current CAD fields.
2. Resolve candidates.
3. Print exact and similar candidates with stable selection numbers.
4. Prompt user for an existing candidate selection, with cancel/no-selection preserving zero DWG writes.
5. If selected:
   - call `DiffPreviewAsync(profileId, selectedItemId, cadFields, includeEmpty:false)`;
   - call `PrintMaterialDiffPreview`;
   - open `MaterialSyncDiffPreviewWindow`;
   - if confirmed, apply `window.ConfirmedWriteFields` only;
   - report `ok` or `failed` via `ReportApplyResultSafely`.
6. Only if no candidate is selected should the command offer the existing confirm-gated Draft creation branch.

### Step D - Static guards

Extend `clients/autocad-material-sync/verify_material_sync_static.py`:

- method-body slice stays scoped to `PLMMATASSIST`;
- delete/replace the existing Phase 3 assertion
  `require("ApplyFields(" not in body, ...)`; Phase 4 bind/write-back will add a
  legitimate `ApplyFields` call to `PLMMATASSIST`, so leaving the old assertion
  would make the verifier self-contradictory;
- require order: `ExtractFields` -> `ResolveAsync` -> candidate selection -> `DiffPreviewAsync` -> `MaterialSyncDiffPreviewWindow` -> `ApplyFields`;
- require `ReportApplyResultSafely` in the write branch;
- require `ApplyFields` appears only after the preview confirmation branch;
- keep `CreateAsync` confirm-gated and separate from bind/write-back;
- scope the "no DWG write-back" guarantee to the create branch only; the bind
  branch is allowed to write confirmed `write_cad_fields`;
- reject any new direct assistant PLM call bypassing Helper.

### Step E - Evidence gate update

Update the Windows evidence guide/template/validator to require:

- selected existing candidate id;
- `/diff/preview` endpoint observed;
- diff preview screenshot/log;
- cancel preview -> no DWG field changed;
- confirm preview -> only `write_cad_fields` changed;
- `/audit/apply-result` observed with `outcome=ok`;
- create branch evidence remains separate and still records no create DWG write-back unless a later create-writeback contract exists.

Implementation trap: update the whole evidence gate as one set. The validator
`REQUIRED_FIELDS`, semantic checks, blank-template assertions, and the
`_minimal_real_2018_evidence` fixture in
`src/yuantus/meta_engine/tests/test_cad_material_sync_external_validation_contracts.py`
must all receive the new bind/write-back fields together. Updating only the
validator will make the contract tests fail or, worse, leave the template and
fixture out of sync.

## 7. Acceptance

Backend:

- Existing assistant tests remain green.
- New `/diff/preview` selected-item tests prove the target package comes from the selected `Item.properties`.
- `resolve` remains zero-write.
- No new PLM route, Helper route, settings field, or router count change.

Client/Helper:

- SDK-free C# tests prove the assistant bind branch uses helper-forwarded `/diff/preview`.
- Static verifier pins the safe command-body order and confirmation boundary.
- `git diff --check` clean.

Manual:

- Windows/AutoCAD evidence cannot be marked accepted until the updated evidence validator passes with real DWG write-back proof.
- Evidence must distinguish:
  - assistant create branch: creates Draft, no DWG write-back;
  - assistant existing-item bind branch: no PLM item created, writes confirmed CAD fields, audits apply result.

## 8. Risks

- `AssistantResolveResponse.cad_fields` ambiguity: avoid by naming it only as proposal/composed fields in docs/tests and never using it as selected-item write-back source.
- Candidate payload shape is still dictionary-based in C#: add small extraction helpers and tests before changing command behavior.
- `CADDedupPlugin.csproj` is not built in CI because AutoCAD SDK is unavailable; static guards plus Windows evidence remain mandatory.
- Real DWG field mapping can still fail even when contract tests pass; audit failure reporting must remain visible to the operator.

## 9. Exit

After Phase 4 implementation merges, `PLMMATASSIST` will support the two conservative v1 outcomes:

- bind/write-back an existing selected PLM Item through diff preview and explicit confirmation;
- create a new Draft item through explicit confirmation, still without automatic DWG write-back.

dedup_vision drawing similarity and persistent server-side CAD-item relationship modeling remain later phases.
