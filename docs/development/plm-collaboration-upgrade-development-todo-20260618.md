# PLM Collaboration Upgrade Development TODO

**Date:** 2026-06-18
**Status:** doc-only execution taskbook. This turns the #800 current-state ledger into a
versioned development plan for upgrading the existing Yuantus PLM deployment into a paid
PLM x MetaSheet collaboration product line. It authorizes no implementation; each slice below
requires an explicit owner opt-in and its own PR.

This taskbook is intentionally narrow about the first paid version: **BOM Review Add-on first**.
Approval execution, SSO/session spine, and controlled write-back are separate later product lines.

> **Phase ↔ draft-Stage map** (for anyone arriving from the workspace drafting docs / earlier discussion, which used "Stage"/"S0-x" names — **this taskbook's Phase numbering is canonical**): Phase 1 = V1 pilot · Phase 2 = Stage 0 hardening (S0-2 multi-kid, S0-3 pact) · Phase 3 = S2-1 parent host · Phase 4 = Stage 1 commercial-ops (S1-1…S1-4) · Phase 5 = S2-2 automation · Phase 6 = S3-1 SSO · Phase 7 = write-back.

---

## 0. Current Code Grounding

The plan below is constrained by the live code shape, not by stale checkboxes in old planning docs.

| Area | Current fact | Code / doc anchor |
|---|---|---|
| Base PLM isolation | `YUANTUS_ENABLE_METASHEET` defaults false; the MetaSheet bridge route is absent in base mode. | `src/yuantus/config/settings.py` `ENABLE_METASHEET`; `src/yuantus/api/app.py` registration boundary |
| Bridge seam | When enabled, `/api/v1/metasheet-bridge/health` is inert and returns `active:false`; it performs no MetaSheet I/O. | `src/yuantus/api/routers/metasheet_bridge.py` |
| SKU judgment | `EntitlementService.is_entitled(feature_key)` is the single feature gate. `license_data` is not an authorization source. | `src/yuantus/meta_engine/app_framework/entitlement_service.py` |
| SKU shape | `plm_collaboration_pro`, `approval_automation`, and `bom_multitable` map to independent app names. `plm.collab` must not silently unlock `plm.bom_multitable`. | `FEATURE_APP_NAMES` |
| Offline license import | `yuantus license import` verifies vendor-signed Ed25519 licenses and activates tenant-scoped `AppLicense` rows. A license can carry multiple signed `app_names`. | `src/yuantus/cli.py`; `license_import_service.py` |
| Capability handshake | `/api/v1/integrations/capabilities` is advisory only; every real feature endpoint re-checks entitlement. | `integration_capabilities_service.py`; `integration_capabilities_router.py` |
| BOM Review provider | `GET /api/v1/bom/multitable/{part_id}/context` is governed, read-only, entitlement-gated, and projects a curated full BOM snapshot. | `bom_multitable_router.py`; `bom_multitable_projection_service.py` |
| Embed token provider | `POST /api/v1/bom/multitable/{part_id}/embed-token` mints a short-lived Ed25519 token, with origin allowlist and fail-closed signing-key behavior. | `bom_multitable_router.py`; `bom_multitable_embed_token_service.py` |
| MetaSheet consumer hardening | P3-D consumer viewer, tenant cross-check, jti single-use, and #2875 all-field BOM shape guard are merged on MetaSheet2. | `plm-collaboration-phase3d-embed-delivery-and-verification-20260609.md`; metasheet2 #2875 `8d306657` |
| Approval automation | P2 is a skeleton: draft templates, ECO read projection, and NOTIFY stub. It is not a real automation execution engine. | `approval_automation_service.py`; `approval_automation_eco_service.py` |

---

## 1. Product Version Strategy

| Version | Product promise | Development stance |
|---|---|---|
| V0 Base PLM | Existing PLM remains independently deployable and testable. | Keep base profile unaware of MetaSheet; no new default dependency. |
| V1 BOM Review Add-on, controlled pilot | Paid, deployment-assisted BOM review/collaboration add-on for selected customers. | Use existing `plm.bom_multitable` entitlement, combined deployment, MetaSheet viewer, and offline license import. |
| V1.1 Contract hardening | MetaSheet can update without silent PLM integration drift. | Add modern-surface pact/golden-schema gates across both repos. |
| V1.2 In-PLM embedded experience | A PLM user opens BOM Review from a PLM page, not by manually visiting MetaSheet. | Build the deferred parent-page host: mint token, iframe, postMessage, remint on replay/expiry. |
| V2 Commercial operations | Sales/support can issue, renew, inspect, and rotate offline licenses. | Add vendor issuance tooling, admin status UX, key rotation, seat/grace policy. |
| V3 Automation / SSO / Write-back | Enterprise expansion beyond read-only BOM Review. | Separate owner-gated lines: real automation engine, identity spine, bridge activation, governed write-back. |

---

## 2. Non-Negotiable Invariants

- **One codebase, no product fork.** Base and collaboration SKUs are controlled by runtime gates and entitlements.
- **Base remains integration-unaware.** A default/base deployment must not require MetaSheet services, networks, env fallbacks, sidecars, or runtime imports.
- **Entitlement is centralized.** Do not add direct `AppLicense` reads outside the entitlement/license services.
- **Advisory is not authorization.** Capability manifests only guide UI/degradation; endpoints enforce the real gate.
- **Independent SKUs stay independent.** Bundles should be represented by signed licenses containing multiple `app_names`, not by broadening `FEATURE_APP_NAMES` casually.
- **Read-only means read-only.** V1 must not write PLM authoritative fields from MetaSheet.
- **No direct table-cell write-back.** Future writes must go through `/aml/apply` or governed `/actions`.
- **No existence leak.** Unentitled context/token calls must return upgrade/null before object lookup.
- **Offline-first for local deployments.** Do not require online payment/activation for already-purchased local features.
- **No private keys in repos.** Embed-token and license-signing private keys stay out of source control.

---

## 3. Phase 0 — Land The Planning Baseline

Purpose: make the planning docs accurate before the next code slice starts.

### TODO

- [x] Confirm #800 current-state ledger is CI-clean.
- [x] Confirm `docs/DELIVERY_DOC_INDEX.md` references the ledger.
- [x] Re-check MetaSheet2 #2875 state before finalizing this taskbook.
- [x] Update #800 current-state ledger: #2875 is now **MERGED** at metasheet2 `8d306657`, not open.
- [ ] Land #800 as the canonical PLM x MetaSheet status/monetization/maintainability entry point.
- [ ] After merge, update any active task discussion to cite #800 instead of stale Phase 0-6 checkboxes.

### Acceptance

- #800 merge state is clean.
- The current-state ledger no longer contains stale #2875 "open" language.
- No runtime code changes are included in this phase.

---

## 4. Phase 1 — BOM Review Add-on V1 Controlled Pilot

Purpose: package the mature read-only BOM Review line into the first paid, supportable add-on.

### Scope

Included:

- `plm.bom_multitable` offline license import and entitlement.
- Yuantus BOM multi-table read projection.
- Yuantus embed-token mint endpoint.
- MetaSheet token-bound BOM Review viewer.
- Combined deployment profile.
- Operator runbooks and acceptance checks.

Excluded:

- PLM parent-page host. That is Phase 3.
- Direct write-back.
- Approval automation execution.
- SSO/session exchange.
- Online self-serve billing.

### V1 Operating Constraints (interim, until Phase 4)

V1 runs on top of two verified gaps, so it must be sold and operated under explicit constraints
until Phase 4 closes them:

- **Perpetual licenses only** (`expires_at = null`). `is_entitled` is a hard, no-grace cutoff
  (grace is Phase 4) — a dated license would hard-cut the feature mid-review. Do not issue a dated
  V1 license.
- **Hand-signed, out-of-band.** The vendor issuance tool is Phase 4; for V1 a license is signed
  manually (private key in custody, never in repo) and delivered for `yuantus license import`.
- **Single tenant, no key rotation.** One controlled pilot tenant; multi-`kid` rotation is Phase 4,
  so do not rotate the embed signing key during the pilot.

### Implementation Slices

| Slice | Repo | Deliverable | Acceptance |
|---|---|---|---|
| 1A Pilot runbook | Yuantus | Deployment-assisted runbook for enabling BOM Review Add-on: profile, env, keys, origins, license import, smoke checks. | A new operator can enable V1 from a clean base deployment without reading old chat history. |
| 1B License fixture and import proof | Yuantus | Test fixture/runbook for a vendor-signed license containing `app_names:["plm.bom_multitable"]`. | Import activates only the signed tenant; `integrations/capabilities` flips `bom_multitable.entitled` true. |
| 1C Combined profile smoke | Yuantus + MetaSheet2 | Minimal local smoke for combined profile: Yuantus API, MetaSheet backend/web, PLM_BASE_URL, tenant/org headers. | Base profile stays MetaSheet-free; combined profile can query capability manifest. |
| 1D BOM Review API smoke | Yuantus | Authenticated smoke for unentitled vs entitled context and embed-token mint behavior. | Unentitled returns `context:null` / `embed_token:null`; entitled with valid part returns context/token; missing key 503; bad origin 403. |
| 1E Customer acceptance checklist | Yuantus | Customer-facing checklist for BOM Review Add-on pilot. | Checklist separates local proof, CI proof, and deferred items. |

### TODO

- [ ] Write `docs/development/plm-bom-review-addon-v1-controlled-pilot-runbook-YYYYMMDD.md`.
- [ ] Add a fixture/example signed-license payload shape, with no real private key material.
- [ ] Add a local smoke command that verifies `GET /api/v1/integrations/capabilities`.
- [ ] Add a local smoke command that verifies `GET /api/v1/bom/multitable/{part_id}/context`.
- [ ] Add a local smoke command that verifies `POST /api/v1/bom/multitable/{part_id}/embed-token`.
- [ ] Prove `docker-compose.profile-base.yml` has no MetaSheet runtime requirement.
- [ ] Prove `docker-compose.profile-combined.yml` still wires MetaSheet sidecars only in the combined overlay.
- [ ] Document required Yuantus env names: `YUANTUS_EMBED_TOKEN_SIGNING_KEY`, `YUANTUS_EMBED_TOKEN_KEY_ID`, `YUANTUS_EMBED_TOKEN_AUDIENCE`, `YUANTUS_EMBED_TOKEN_TTL_SECONDS`, `YUANTUS_EMBED_ALLOWED_ORIGINS`, `YUANTUS_LICENSE_PUBLIC_KEYS`.
- [ ] Document required MetaSheet env names: public key, key id, audience, allowed origins, data source id, Redis.
- [ ] Add explicit "not included in V1" section to the runbook.

### Verification Gate

Run locally before PR:

```bash
python3 -m pytest \
  src/yuantus/api/tests/test_metasheet_bridge_flag_contracts.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_compose_sku_profiles.py \
  src/yuantus/meta_engine/tests/test_entitlement_service.py \
  src/yuantus/meta_engine/tests/test_license_import.py \
  src/yuantus/meta_engine/tests/test_integration_capabilities.py \
  src/yuantus/meta_engine/tests/test_bom_multitable_projection.py \
  src/yuantus/meta_engine/tests/test_bom_multitable_embed_token.py
```

The PR may remain doc/runbook-only in Slice 1A; code or smoke scripts should be a separate explicit
slice if they change runtime behavior.

---

## 5. Phase 2 — Modern Surface Contract Hardening

Purpose: make MetaSheet maintenance safe by catching shape drift before release.

### Modern Surfaces To Pin

- `GET /api/v1/integrations/capabilities`
- `GET /api/v1/bom/multitable/{part_id}/context`
- `POST /api/v1/bom/multitable/{part_id}/embed-token`
- MetaSheet relay response for BOM Review.
- MetaSheet embed-token verification and jti replay behavior.
- Effective tenant header behavior.
- `bom_multitable` payload inner shape, including all declared display fields.

### Implementation Slices

| Slice | Repo | Deliverable | Acceptance |
|---|---|---|---|
| 2A Yuantus provider schema | Yuantus | **Reuse the existing pact** (provider verifier `test_pact_provider_yuantus_plm.py` + sync helper already exist) — add interactions for capability, context, and embed-token responses + their provider seed. | Removing/renaming a declared field fails `test_pact_provider_yuantus_plm.py`. |
| 2B MetaSheet consumer pact | MetaSheet2 | Consumer contract authors the 3 interactions and validates adapter/relay expectations. (Note: #2875 already gives the *runtime* drift guard — `quantity -> qty` degrades to a visible error; this adds the *CI-time* guard.) | `quantity -> qty` and similar drift fails the contract. |
| 2C Sync helper | Both | **ALREADY EXISTS** (`sync_metasheet2_pact.sh --check` + `test_ci_contracts_pact_sync_helper.py`) — route the 3 new interactions through it; do **not** build a new helper. | `--check` flags drift on the 3 surfaces. |
| 2D CI wiring | Both | **Provider gate ALREADY EXISTS** (`test_ci_contracts_pact_provider_gate.py`) — confirm its change-scope triggers cover the 3 surfaces. | No silent no-op when either repo changes the contract. |

### TODO

- [ ] Decide pact vs JSON schema vs golden fixture for the modern surfaces. **(Recommended: reuse the existing pact — the provider verifier + `sync_metasheet2_pact.sh` + CI gate already exist; a parallel golden-schema would duplicate them.)**
- [ ] Add Yuantus fixture for unentitled `context:null` and entitled context.
- [ ] Add Yuantus fixture for embed-token response envelope without committing a real token. **(Signed output is non-deterministic → match `token`/`jti` with `matchingRules` regex, never value-match; seed `YUANTUS_EMBED_TOKEN_SIGNING_KEY` + `YUANTUS_EMBED_TOKEN_KEY_ID` in the provider verifier, per `test_bom_multitable_embed_token.py`.)**
- [ ] Add MetaSheet fixture for relay error state on field drift.
- [ ] Add compatibility matrix: Yuantus commit/tag ↔ MetaSheet commit/tag.
- [ ] Add CI failure examples to the docs.
- [ ] Re-run MetaSheet2 #2875 tests against the Yuantus fixture once fixtures exist.

### Verification Gate

- Yuantus contracts green.
- MetaSheet2 consumer contracts green on Node 18 and Node 20.
- A deliberate fixture drift fails both locally and in CI.

---

## 6. Phase 3 — In-PLM Embedded BOM Review Host

Purpose: close the current user-experience gap: the provider mint and consumer viewer exist, but
the PLM page that hosts the iframe is still deferred.

### Scope

Included:

- PLM page entry point for Part/BOM Review.
- Token mint call from PLM page.
- Iframe pointed to MetaSheet `/plm-embed/bom-review`.
- Origin-pinned `postMessage` after iframe load.
- Re-mint/reopen behavior on single-use replay or expiry.

Excluded:

- SSO.
- Admin revocation.
- Direct write-back.
- General MetaSheet workbench rebuild.

### TODO

- [ ] Locate the canonical PLM Part/BOM detail UI entry point.
- [ ] Add an affordance that checks `GET /api/v1/features/bom_multitable` or `integrations/capabilities`.
- [ ] If unentitled, show upgrade affordance only; do not query the BOM.
- [ ] If entitled, call `POST /api/v1/bom/multitable/{part_id}/embed-token`.
- [ ] Host the iframe with a configured MetaSheet embed URL.
- [ ] Send `{type:"plm-embed:token", token}` to iframe `contentWindow` with exact target origin.
- [ ] Never put the token in URL, localStorage, or logs.
- [ ] On 401 replay/expired, force re-open/re-mint instead of retrying the same token.
- [ ] Add Playwright E2E covering the happy path and three failure paths: unentitled, bad origin, replay.

### Acceptance

- A user can open BOM Review from inside PLM.
- The iframe renders the read-only MetaSheet BOM table.
- Token is single-use and not visible in URL.
- Bad origin fails closed.
- Base PLM with feature disabled shows no broken MetaSheet UI.

---

## 7. Phase 4 — Offline Commercial Operations

Purpose: make the add-on sellable and supportable beyond engineering demos.

### TODO

- [ ] Build a vendor-side license issuance CLI outside the production repo or in a clearly separated private tool.
- [ ] Define key custody: private signing key location, rotation procedure, operator permissions.
- [ ] Add public-key rotation support and runbook for multiple `kid` values. **(Already half-built — the consumer verify path resolves by `kid` (`embed-token-verify.ts:66-70`) and the provider already stamps `kid`; this is a consumer-only `embedPublicKeysByKid()` multi-key env parse, size S, NOT net-new infra. Pull into Phase 2 if any rotation is anticipated before commercial ops.)**
- [ ] Add tenant-facing license status API or admin page.
- [ ] Add renewal and expiry runbook.
- [ ] Decide seat/quantity policy for `plm.bom_multitable`. **(No `seat_limit` field exists yet — design the model first. Impl caveat: active users live in the identity DB (`auth_users`), `AppLicense` in the meta DB → the count is cross-engine; reuse `QuotaService.get_usage`.)**
- [ ] Decide grace-period policy for local deployments.
- [ ] Add support bundle command that prints feature entitlement status without leaking private key material.
- [ ] Document how to issue a bundle license that contains multiple `app_names` without broadening `FEATURE_APP_NAMES`.

### Acceptance

- Sales/support can issue and import a license without engineer-only steps.
- Expired or wrong-tenant licenses do not unlock the feature.
- Key rotation is tested before the first production rotation.
- A customer can prove entitlement state from an admin screen or support bundle.

---

## 8. Phase 5 — Approval Automation Productization

Purpose: turn the existing P2 skeleton into a real product only after BOM Review V1 is stable.

### Current Boundary

The current code is intentionally a skeleton:

- templates are `draft`;
- ECO context is read-only;
- `notify` records audit and returns `dispatch_status:"stubbed"`;
- no DingTalk call;
- no ECO/approval write-back.

### TODO

- [ ] Define execution-engine contract: trigger source, schedule, idempotency key, retry envelope.
- [ ] Add real notification channel adapter behind a default-off setting.
- [ ] Add durable execution table and audit model.
- [ ] Add failure/retry/circuit-breaker policy.
- [ ] Add admin enable/disable for provisioned templates.
- [ ] Add tests proving no direct PLM approval state mutation.
- [ ] Add E2E for one scenario: ECO overdue notification.

### Acceptance

- The product is no longer a stub.
- Notification retries are idempotent.
- All writes go through governed actions or existing PLM approval endpoints.

---

## 9. Phase 6 — SSO / Identity Spine / Bridge Activation

Purpose: solve cross-system identity and long-lived session semantics.

### TODO

- [ ] Decide identity source: shared IdP, token exchange, or PLM-issued session bridge.
- [ ] Define Yuantus user ↔ MetaSheet user mapping.
- [ ] Activate MetaSheet bridge only after identity model is explicit.
- [ ] Revisit admin revocation: do not model it as a Yuantus-only denylist that offline consumers never consult.
- [ ] Add tenant/org mapping checks in both repos.
- [ ] Add browser E2E for session continuity.

### Acceptance

- Base deployment still works without SSO.
- Combined deployment has a clear identity mapping.
- Revocation model is enforceable by the consumer, not only recorded by the minting provider.

---

## 10. Phase 7 — Controlled Write-Back

Purpose: add mutation only after read-only BOM Review and identity/commercial operations are stable.

### TODO

- [ ] Define allowed write scenarios.
- [ ] Add preview endpoint before apply.
- [ ] Route every PLM authoritative write through `/aml/apply` or governed `/actions`.
- [ ] Re-run lifecycle/version/esign/approval checks on apply.
- [ ] Add stale snapshot detection using `source_version` and `source_updated_at`.
- [ ] Add audit events tying MetaSheet collaboration state to PLM action ids.

### Acceptance

- No table-cell direct write to PLM authoritative fields exists.
- Stale source snapshots block apply.
- Every mutation is reviewable and auditable.

---

## 11. Owner Decision Gates

| Gate | Decision needed |
|---|---|
| V1 pilot customer | Which deployment/customer is the first controlled pilot? |
| SKU packaging | Sell only `plm.bom_multitable`, or sell a bundle license containing multiple independent `app_names`? |
| License operations | Who owns vendor-side signing key custody? |
| MetaSheet versioning | Do we publish compatibility by commit SHA, semver tag, or release bundle id? |
| PLM parent UI | Which PLM screen owns the first BOM Review entry point? |
| SSO timing | Is identity spine required before the first paid pilot, or only before broader enterprise rollout? |
| Write-back | Which exact write scenario is valuable enough to open P5? |

---

## 12. Recommended First PR Sequence

1. **PR A — land planning baseline:** add this taskbook + update delivery index. (The #800 ledger's #2875-landed status is already fixed — see Phase 0.)
2. **PR B — V1 pilot runbook:** operator/deployment/license/acceptance runbook only.
3. **PR C — modern-surface contract fixtures:** Yuantus provider fixtures + MetaSheet consumer checks.
4. **PR D — local combined-profile smoke:** cheap, deterministic smoke for capability + BOM Review happy path.
5. **PR E — PLM parent-page embed host:** first user-facing runtime slice, after contracts are green.

Each PR should use explicit-path staging and preserve unrelated local/untracked files.
