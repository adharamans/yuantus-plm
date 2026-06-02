# DEV & Verification: PLM Collaboration Phase 0 Scope/Mapping Taskbook

Date: 2026-06-02

Records the doc-only delivery of
`DEVELOPMENT_PLM_COLLABORATION_PHASE0_SCOPE_MAPPING_TASKBOOK_20260602.md`.
This is the first scope-lock artifact after the canonical
`docs/development/plm-collaboration-automation-development-plan-20260602.md`
(#691). It authorizes no implementation.

## 1. What changed

- Added a formal Phase 0 taskbook for PLM Collaboration / MetaSheet integration.
- Locked the recommended first implementation slice as a runtime flag plus
  base-green contract, not approval automation or BOM multitable.
- Grounded and corrected the deployment profile assumption: existing
  `docker-compose.profile-combined.yml` already carries the MetaSheet sidecar,
  so `profile-metasheet` must be a deliberate alias/rename decision, not a
  duplicate overlay.
- Grounded that `YUANTUS_ENABLE_COLLAB` / `YUANTUS_DELIVERY_PROFILE` are
  compose-only today because no corresponding `Settings` fields exist and
  unknown `YUANTUS_*` env vars are ignored.
- Locked the governed projection mapping from Yuantus `ItemType` / `Property`
  to MetaSheet field types.
- Added sorted `DELIVERY_DOC_INDEX.md` entries for this taskbook and this
  verification record.

## 2. Grounding summary

Yuantus:

- `docker-compose.profile-combined.yml` already defines MetaSheet sidecar
  services and Yuantus-to-MetaSheet federation env.
- `Settings` uses `env_prefix="YUANTUS_"` with `extra="ignore"` and has no
  `ENABLE_COLLAB`, `ENABLE_METASHEET`, or `DELIVERY_PROFILE` field today.
- `AppLicense` / `MarketplaceAppListing` exist, but `store_service.py` is mock
  App Store behavior.
- `create_app()` includes all regular routers unconditionally today; current
  route-count pins are at `691`.

MetaSheet2:

- approval bridge exists; safe dispatch route is `/api/approvals/:id/actions`.
- legacy `/approve` and `/reject` routes mutate local platform approvals.
- `webhook.received` is defined but not subscribed by the automation service.
- `apiTokenAuth` exists but is not mounted globally, and token scopes are not
  base/sheet scoped.
- `MultitableEmbedHost.vue` exists but still has an outbound `postMessage("*")`
  hardening gap.

## 3. Verification

Doc-only local verification passed:

- doc-index / reference / discipline tests: 25 passed.
- `verify_lisp_shell_static.py`: 28 static guards passed.
- `verify_bridge_static.py`: 13 static guards passed.
- `verify_material_sync_static.py`: passed.
- `git diff --check`.

No Yuantus code, migration, route, or MetaSheet file was changed.

## 4. Status

Doc-only. Ready for review. Next work, if ratified, is a separate implementation
opt-in for the Phase 0 runtime flag and base-green contract.
