# PLM-Collab V1 — pinned external-trial interface surface + pact protection boundary

**Date:** 2026-06-21
**Status:** boundary record for the V1 BOM-Review external trial. Documents which Yuantus↔MetaSheet2
surfaces are pinned by contract, how the pin is enforced, the residual gap, and the operator staging
checklist. No code change.

## 1. Pinned interface surface (the V1 pilot "固定接口面")

The V1 BOM-Review trial uses exactly two read surfaces (embed-token / iframe is **V1.2** — deliberately
**not** pinned here):

| Surface | Provider route | Consumer caller |
|---|---|---|
| Capability manifest (advisory) | `GET /api/v1/integrations/capabilities` | `PLMAdapter` |
| BOM multi-table context (governed, read-only) | `GET /api/v1/bom/multitable/{part_id}/context` | `PLMAdapter` |

Both are **entitlement-gated** (`is_entitled("bom_multitable")`); unentitled callers get `entitled:false`
/ `context:null` with **no existence leak**. The capability manifest is advisory only — every real
endpoint re-checks entitlement.

## 2. How the surface is pinned (already closed on main)

A **consumer-driven pact** pins these surfaces on both sides, and at the version pair in §4 the two copies
are **byte-identical (in sync)**:

- **Consumer (MetaSheet2):** `packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json`
  (32 interactions incl. the 2 above) + the contract test `plm-adapter-yuantus.pact.test.ts`, run by the
  `yuantus-pact-consumer.yml` workflow. This is a **static** check: it pins **route usage + pact-artifact
  sanity** — the pact JSON is well-formed, its 32 interactions are in documented order with provider
  states, and `PLMAdapter.ts` still references these endpoints. It does **not** assert provider field shape.
- **Provider (Yuantus):** the synced copy `contracts/pacts/metasheet2-yuantus-plm.json` is replayed by
  `src/yuantus/api/tests/test_pact_provider_yuantus_plm.py` (in the `contracts` gate; the V1.1 seed #805
  mints a perpetual `plm.bom_multitable` license so the entitled interactions verify). A provider response
  that drops/renames a pinned field → provider CI fails.

The 2 pilot interactions were added by **#805** ("V1.1 pact provider seed + sync consumer pact"); both
mains now carry them, in sync — **no drift** between the repos' pact files at the §4 version pair.

## 3. Protection boundary + the residual gap (honest)

**What protects the surface — three layers, each catching a different thing:**
- **Consumer CI** (`plm-adapter-yuantus.pact.test.ts` via `yuantus-pact-consumer.yml`) — pins **route usage
  + pact-artifact sanity** (the endpoints are still called in documented order; the pact JSON stays
  well-formed). It does **not** catch provider field drift.
- **Provider verifier** (`test_pact_provider_yuantus_plm.py`, in `contracts`) — pins the **provider
  response shape**: a provider response that drops / renames a pinned field fails here.
- **Adapter runtime guard / unit tests** (the #2875 all-field BOM shape guard) — catches **silent field
  drift at runtime** on the consumer side (a visible degradation, not a CI-contract failure).

**What it does NOT catch automatically — the residual gap:**
- The two repos' pact files are kept in sync **manually**, via `scripts/sync_metasheet2_pact.sh --check`
  (and `--verify-provider`). There is **no automatic cross-repo gate**: nothing in either repo's CI fails
  if the consumer source and the provider copy diverge — a single-repo CI cannot gate on the other repo's
  working tree, and inventing a cross-repo CI dependency is fragile.
- **The real hardening is a pact broker** (MetaSheet2 CI publishes the consumer pact → the Yuantus
  provider verifier consumes the published version), making sync + version compatibility a first-class CI
  gate. That is a separate, owner-gated infrastructure slice — **out of scope here**.

**Operating discipline until then:** when pinning a new Yuantus↔MetaSheet2 version pair, or after any
patch to either side, run
`METASHEET2_ROOT=../metasheet2 scripts/sync_metasheet2_pact.sh --check --verify-provider` and ship only if
it reports `pact_sync=ok` + the provider verifier passes.

## 4. Pinned version pair (verified-compatible)

The real invariant is the **pact artifact hash** `5ecbe1ee…` (32 interactions): both repos' copies hash to
it, so the *commit* can move while the contract stays pinned. The commits in the table are the **baseline**
at which the pact was verified identical — **not** the current main tips, which keep advancing on both
repos. Do not trust a quoted main SHA here (it drifts the moment either repo merges); the **hash** is the
pin — re-verify with `sync_metasheet2_pact.sh --check --verify-provider` rather than a remembered tip.

| Repo | verified pact-baseline commit | pact state |
|---|---|---|
| Yuantus | `d6f17742` | provider copy = 32 interactions, hash `5ecbe1ee…`; verifier green in `contracts` |
| MetaSheet2 | `de2052bdf` | consumer source = 32 interactions, hash `5ecbe1ee…`; byte-identical to provider |

## 5. Operator staging checklist (the test line)

Run against a deployed **combined-profile** staging/dogfood instance. Importing a real vendor-signed
`plm.bom_multitable` license needs the private signing key (out of repo); the dogfood signer
`scripts/dev/sign_dogfood_license.py` produces an ephemeral-key equivalent for controlled dogfood.

1. **License import + status** (entitlement, no key leak):
   - `yuantus license import <signed-license.json>` → activates the tenant.
   - `yuantus license status --tenant-id <PILOT_TENANT>` → expect `bom_multitable: ENTITLED`, the active
     license summary (status / expiry), and **no `license_data` / key material** in the output.
2. **Capability + BOM context** (both states), via the smoke scripts:
   - `scripts/dev/smoke_combined_profile.sh` — health + `capabilities.advisory:true` +
     `bom_multitable.supported:true`; base profile stays MetaSheet-free.
   - `scripts/dev/smoke_bom_review_api.sh` — unentitled → `context:null` (existing vs missing part
     identical = no leak); entitled → `context` with `part` + `lines[]`; manifest `entitled` toggles.
     Env: `YUANTUS_BASE_URL`, `ENTITLED_TENANT`, `UNENTITLED_TENANT`, `PART_ID`, `AUTH_HEADER`.
3. **Seats** (only if testing the cap): set `YUANTUS_QUOTA_MODE=enforce`, import a license carrying
   `seats=N`, confirm the import prints `seat cap projected: TenantQuota.max_users=N`, then attempt to
   provision the (N+1)-th user → expect a quota block (soft warning header under `soft`,
   `429 QUOTA_EXCEEDED` under `enforce`). Projection + the enforcement decision are CI-covered by
   `test_seat_projection.py` (incl. `test_projected_cap_is_enforceable_at_provisioning`); the live
   create-user 429 is the manual staging confirmation.
4. **Contract pin:** before re-pinning the version pair after any patch, run §3's sync + verify.

## 6. Scope (deferred — unchanged)

In-PLM embed host + embed-token pact + iframe/origin/CSP/Redis jti = **V1.2** (separate owner-gated line).
B2 seat-assignment / multi-kid / SSO / write-back / approval-automation execution = separate owner-gated
lines. None are pinned or built here.

---

*Surfaces: `meta_engine/web/integration_capabilities_router.py`, `meta_engine/web/bom_multitable_router.py`.
Pact: provider `api/tests/test_pact_provider_yuantus_plm.py` + `contracts/pacts/metasheet2-yuantus-plm.json`;
consumer `metasheet2 packages/core-backend/tests/contract/`. Sync: `scripts/sync_metasheet2_pact.sh`.*
