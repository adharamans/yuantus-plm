# Development Taskbook: PLM Collaboration Phase 0 Scope/Mapping

Date: 2026-06-02

Type: **Doc-only grounding + scope-lock taskbook.** It starts the
`PLM Collaboration And Automation Edition` line from
`docs/development/plm-collaboration-automation-development-plan-20260602.md`
(#691), but authorizes **no implementation**. Phase 0 is the boundary lock
before feature work: object mapping, deployment/profile decision, entitlement
contract, and flag-OFF CI gate.

Baseline: `main = fdb25c87` after the OdooPLM closeout (#692). MetaSheet2 was
read-only grounded from `/Users/chouhua/Downloads/Github/metasheet2`; that
worktree is dirty, so this taskbook does not require or modify MetaSheet2.

## 0. Executive conclusion

The first actionable slice should **not** be approval automation, BOM
multitable, or a full entitlement UI. The correct first slice is a small
Phase-0 implementation that proves the product layering can exist without
changing base PLM behavior:

- a real Yuantus runtime flag, `ENABLE_METASHEET`, because the existing
  `YUANTUS_ENABLE_COLLAB` / `YUANTUS_DELIVERY_PROFILE` env keys are currently
  compose-only and ignored by `Settings`;
- a flag-OFF contract proving base PLM still has the same route set and no
  MetaSheet bridge side effects;
- an entitlement service contract over the existing App Store/Licensing
  scaffolding, with tenant scoping explicitly decided before any license write;
- a governed projection mapping from PLM `ItemType` / `Property` to MetaSheet
  field types, used as a later BOM/approval provisioning contract.

Everything else remains a later opt-in.

## 1. Grounding: prior art and corrected assumptions

### 1.1 Yuantus already has deployment/profile prior art

- `docker-compose.profile-base.yml` pins `YUANTUS_DELIVERY_PROFILE=base` and
  `YUANTUS_ENABLE_COLLAB=false` for `api` and `worker`.
- `docker-compose.profile-collab.yml` pins `YUANTUS_DELIVERY_PROFILE=collab`
  and `YUANTUS_ENABLE_COLLAB=true`, but is env-only.
- `docker-compose.profile-combined.yml` is already the MetaSheet sidecar shape:
  it adds `metasheet-postgres`, `metasheet-redis`, MetaSheet backend, and
  MetaSheet web, and wires `PLM_BASE_URL=http://api:7910`,
  `PLM_API_MODE=yuantus`, tenant/org defaults, and `ENABLE_PLM=true`.
- `test_ci_contracts_compose_sku_profiles.py` already treats `combined` as the
  Yuantus + MetaSheet profile.

Decision implication: Phase 0 must **reuse or deliberately alias** the existing
`combined` profile. It must not casually create a second full
`profile-metasheet` overlay with the same meaning.

### 1.2 The existing collaboration env keys are not runtime gates

`src/yuantus/config/settings.py` defines `SettingsConfigDict(env_prefix="YUANTUS_", extra="ignore")`.
There is no `ENABLE_COLLAB`, `ENABLE_METASHEET`, or `DELIVERY_PROFILE` setting
field today. Therefore the compose env keys are currently documentation and CI
shape, not executable runtime gates.

Decision implication: Phase 0 implementation must add a **real** `ENABLE_METASHEET`
setting. Until that exists, no feature can be safely "flagged off" in Yuantus.

### 1.3 App Store/Licensing is useful scaffolding, not a finished entitlement system

`meta_engine/app_framework/store_models.py` has:

- `MarketplaceAppListing` with price/category metadata;
- `AppLicense` with `license_key`, `plan_type`, `expires_at`, `status`, and
  `license_data`.

`meta_engine/app_framework/store_service.py` is explicitly mock/simulated:

- `sync_store_listings()` writes static mock listings;
- `purchase_app()` generates a UUID license;
- `install_from_store()` fetches an empty mock manifest and registers it.

Decision implication: Phase 0 should evolve this scaffold, not create a second
license system. But tenant scoping and signature/offline validation are still
unimplemented and must be locked before Phase 1.

### 1.4 Yuantus has enough PLM truth-source surfaces

- AML write surface: `POST /api/v1/aml/apply`.
- Metadata read surface: `GET /api/v1/aml/metadata/{item_type_name}`.
- Domain event surfaces: transactional events for item create/update/state
  change.
- Approval surfaces: generic approval transition and ECO approve/reject.
- ERP publication outbox and HTTP adapter provide the outbound connector
  precedent.

These are reusable boundaries. They are not permission to let MetaSheet become
the PLM source of truth.

### 1.5 MetaSheet has useful capabilities, but the direction matters

Verified MetaSheet facts:

- Approval bridge exists and can sync PLM approvals, then dispatch actions back
  to PLM. The safe route is `POST /api/approvals/:id/actions`.
- Legacy `POST /api/approvals/:id/approve|reject` still mutates local platform
  approvals directly. PLM collaboration slices must not use it.
- Automation supports record events, DingTalk actions, and `send_webhook`.
  `webhook.received` is a type, but the automation service only subscribes to
  `multitable.record.created|updated|deleted`.
- `MultitableEmbedHost.vue` exists and has `allowedOrigins`, but outbound
  `postMessage` still uses `"*"`.
- `apiTokenAuth` exists but is not mounted globally, and token scopes are
  action-wide (`records:read`, `records:write`, etc.), not base/sheet scoped.

Decision implication: approval automation can be an early feature after
entitlement, but BOM multitable depends on embed/auth/base-scope work and should
not be first.

## 2. Phase 0 scope

Phase 0 is a contract slice. It locks the exact shape of the first implementation
slice, then stops. It does not ship customer-visible collaboration capability.

In scope:

- PLM object to MetaSheet object mapping table.
- Read-only PLM snapshot field vs editable collaboration field boundary.
- Runtime flag and entitlement decision points.
- Deployment/profile decision for base, upgrade-ready, and collaboration-enabled
  modes.
- Flag-OFF CI gate definition.
- First-slice test inventory and route-count policy.

Out of scope:

- Entitlement UI, checkout, payment, remote license issuance.
- Offline license import implementation.
- MetaSheet provisioning.
- PLM workbench iframe/embed route.
- Approval automation templates.
- BOM multitable review table generation.
- Any MetaSheet code change in this Phase-0 taskbook.

## 3. Decisions to ratify

### D0-1: Deployment profile naming

Recommendation: treat current `docker-compose.profile-combined.yml` as the
existing **upgrade-ready/collaboration sidecar profile** for v1. Do not add a
second full overlay unless the product name needs it.

If the product name must be `profile-metasheet`, the implementation should make
it a thin alias/compatibility layer over `combined`, and update the existing
compose profile contracts. It should not duplicate service definitions and drift.

### D0-2: Runtime flag

Add `ENABLE_METASHEET: bool = False` to Yuantus `Settings`.

Precedence:

- `ENABLE_METASHEET=false`: kill switch. No MetaSheet bridge route, no bridge
  state, no event subscription, no MetaSheet side effect.
- `ENABLE_METASHEET=true`: collaboration layer may mount, but per-tenant
  entitlement still decides visibility/use.

The old `YUANTUS_ENABLE_COLLAB` key is not enough because it is ignored by
`Settings` today.

### D0-3: Entitlement source and tenant scoping

Reuse `AppLicense` / `MarketplaceAppListing` as the starting point.

Open tenant-scoping decision for the implementation taskbook:

- add `tenant_id` / `org_id` columns to `meta_app_licenses`, or
- rely on physical isolation under `TENANCY_MODE=db-per-tenant` /
  `db-per-tenant-org`.

Do not silently implement global licenses in `TENANCY_MODE=single`.

### D0-4: Feature key vocabulary

Phase 0 should lock a minimal vocabulary:

- `plm_collaboration_pro`
- `bom_multitable`
- `approval_automation`
- `automation_enterprise`
- `plm_offline_license`

Phase 0 implementation can expose only `plm_collaboration_pro` if keeping the
slice tiny, but the vocabulary must reserve the split keys so later work does
not invent synonyms.

### D0-5: MetaSheet feature gate

MetaSheet already has route `requiredFeature` support and a `plm` key. The
Phase 0 taskbook does **not** decide whether to reuse `plm` or add a new
`plmCollaboration` / `plm_collaboration_pro` key in MetaSheet. That requires a
MetaSheet-side grounding slice because it touches route metadata, feature store,
and entitlement propagation.

## 4. Governed projection mapping

The projection rule is:

> PLM fields may be copied into MetaSheet only as read-only snapshots with
> source metadata. MetaSheet fields may add collaboration state. Any write-back
> to PLM must go through governed Yuantus endpoints.

### 4.1 PLM metadata source

Use `ItemType` / `Property` from `meta_engine/models/meta_schema.py` as the
source of truth. The existing `/aml/metadata/{itemType}` route is useful, but it
drops `ui_type`, `ui_options`, and `data_source_id`, so a future projection
service should read the model or expose a richer governed metadata endpoint.

`Property.data_type` is a string convention, not a hard enum. Phase 0 treats the
current documented values as expected inputs and requires fail-closed handling
for unknown values.

### 4.2 Field mapping table

| PLM `Property` | MetaSheet field | Boundary |
|---|---|---|
| `string` + plain `ui_type` | `string` | Read-only if PLM-sourced. |
| `string` + multiline hint | `longText` | Read-only if PLM-sourced. |
| `string` + select options | `select` / `multiSelect` | Options come from `ui_options`; unknown shape blocks projection. |
| `integer` / `float` | `number` | Currency/percent only if explicitly hinted. |
| `boolean` | `boolean` | No checkbox synonym in the PLM contract. |
| `date` | `date` / `dateTime` | Must preserve timezone/date-only semantics. |
| `item` + `data_source_id` | `link` | Link target must be explicit. No free text relation guessing. |
| `list` | `multiSelect` or `longText` | Depends on element contract. Unknown element shape blocks projection. |
| `json` | `longText` | Snapshot only, not editable authority. |

Required snapshot metadata for every PLM-owned projection row/field:

- `sourceSystem = "yuantus"`
- `sourceObjectType`
- `sourceObjectId`
- `sourceVersion`
- `sourceUpdatedAt`
- `syncStatus`

Editable collaboration fields must be separate fields, not overloads of PLM
source fields.

## 5. First implementation slice shape (future, not authorized here)

Name proposal:

`PLM-COLLAB-P0-A runtime flag + base-green contract`

Scope:

- add `ENABLE_METASHEET` to `Settings`;
- add a minimal bridge registration seam that is absent by default and present
  only when `ENABLE_METASHEET=true`;
- do not add database migrations;
- do not add MetaSheet provisioning;
- do not add customer-visible UI;
- update compose/profile contracts only after D0-1 is ratified;
- add tests that default flag-OFF preserves base route count `691`.

Expected tests:

- settings env parsing: `YUANTUS_ENABLE_METASHEET=true` toggles the setting;
- flag-OFF route/state guard: default create_app has no MetaSheet bridge route
  and no MetaSheet bridge state;
- flag-ON route/state guard: toggled create_app exposes only the minimal bridge
  health/status route, if that route is part of the slice;
- route-count residual scan: default route pins remain `691`;
- compose profile contract: `combined` remains the single MetaSheet sidecar
  profile unless D0-1 ratifies an alias;
- CI wiring: any new test file must be added to the explicit CI contracts list.

## 6. Verification gates for Phase 0 implementation

The implementation PR must prove both sides of the gate:

- **Flag OFF**: base PLM route count remains `691`; no MetaSheet bridge route,
  no bridge state, no event subscription, no worker side effect.
- **Flag ON**: the minimal bridge seam exists and is still inert unless
  entitlement passes.

Do not rely on a test file merely existing. This repo's CI uses explicit test
lists for several contract jobs; update the list and run the portfolio guards
when adding a new contract test.

## 7. Reviewer focus

1. Are `combined` vs `profile-metasheet` and the no-duplicate-profile rule the
   right deployment boundary?
2. Is `ENABLE_METASHEET` the right real runtime flag name, replacing the current
   compose-only `YUANTUS_ENABLE_COLLAB` for executable gating?
3. Should tenant scoping for `AppLicense` be a migration or a db-per-tenant
   assumption?
4. Is the governed projection mapping strict enough, especially for `item` links
   and unknown `data_type` values?
5. Is the first implementation slice small enough: flag + base-green contract,
   not feature entitlement UI or MetaSheet provisioning?

## 8. Status

Doc-only taskbook. No implementation is authorized. After merge, the next step
is a separate opt-in for `PLM-COLLAB-P0-A runtime flag + base-green contract`.
