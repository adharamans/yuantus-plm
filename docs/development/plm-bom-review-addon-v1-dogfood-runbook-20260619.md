# PLM BOM Review Add-on — V1 Dogfood / Controlled-Demo Runbook

**Date:** 2026-06-19 · **Scope:** stand up the **V1** BOM Review Add-on for internal dogfood or a
very-close-quarters controlled demo. **Path A only** (BOM review inside the MetaSheet workbench);
**no in-PLM iframe** (that is V1.2), **no automation execution**, **no write-back**.

This is an **operator runbook**, not a feature build. The V1 feature code is already on `main`
(provider projection + capability manifest + entitlement + offline license import; consumer BOM-review
panel). "Ready" is not "docs done" — it is the **acceptance conditions in §6** actually passing.

---

## 1. V1 operating constraints (must hold)

- **Perpetual license only** (`expires_at = null`). `is_entitled` is a hard, no-grace cutoff (grace is
  Phase 4); a dated license would hard-cut mid-review.
- **Hand-signed, out-of-band.** Use the one-time dev signer (`scripts/dev/sign_dogfood_license.py`),
  **not** a shipped CLI command (the product `cli.py` has only `license import`). Vendor productized
  issuance is V2.
- **Single tenant.** A per-source tenant on a data source currently needs direct/internal
  `DataSourceConfig`; multi-tenant trial is deferred. One pilot tenant only.
- **Fixed Yuantus↔MetaSheet version pair.** The 3 modern surfaces are not pact-pinned until V1.1, so
  record the exact commit pair for this deployment and do not update one side alone.
- **No key rotation** during the dogfood (multi-`kid` is V2).
- Users log into **MetaSheet** directly (no PLM SSO until Phase 6).

---

## 2. Deploy the combined profile

The runnable overlays (`YUANTUS_DELIVERY_PROFILE`):

| Overlay file | `YUANTUS_DELIVERY_PROFILE` | `YUANTUS_ENABLE_METASHEET` | Use |
|---|---|---|---|
| `docker-compose.profile-base.yml` | `base` | `false` | pure PLM, no MetaSheet |
| `docker-compose.profile-collab.yml` | `collab` | `true` | collaboration overlay |
| `docker-compose.profile-combined.yml` | `combined` | `true` | **PLM + MetaSheet bundled — use this** |

Also set `YUANTUS_AUTH_MODE=required` (never `optional` on an internet-reachable deploy).
Path A (workbench BOM review) does **not** need the embed-token env (`YUANTUS_EMBED_*`) — that is Path B / V1.2.

---

## 3. Sign + install the dogfood license

```bash
# 1) sign a perpetual plm.bom_multitable license for the pilot tenant (ephemeral key, discarded)
.venv/bin/python scripts/dev/sign_dogfood_license.py \
    --tenant-id <PILOT_TENANT> --subject "<Customer/Org>" --out dogfood-license.json
# the script prints the public key to trust + self-verifies the signature
```

It prints a line like:

```
YUANTUS_LICENSE_PUBLIC_KEYS={"dogfood-1": "<base64 raw Ed25519 public key>"}
```

2. Put that `YUANTUS_LICENSE_PUBLIC_KEYS` value in the deployment env (so the offline verify trusts the
   `dogfood-1` kid).
3. Import on the deployment:

```bash
yuantus license import dogfood-license.json
```

After import, `GET /api/v1/integrations/capabilities` flips `bom_multitable.entitled` to `true` for the
pilot tenant only (verified: a perpetual `plm.bom_multitable` license entitles its tenant and no other).

---

## 4. Configure the MetaSheet PLM data source

In MetaSheet, add a PLM data source pointing at this Yuantus deployment (single tenant). The workbench
BOM-review panel calls, via the relay:

- `GET /api/v1/integrations/capabilities` — advisory manifest (is `bom_multitable` supported + entitled?)
- `GET /api/v1/bom/multitable/{part_id}/context` — the governed, read-only BOM projection.

The adapter forwards the served tenant as `x-tenant-id` on these calls. For single-tenant dogfood, set
**`PLM_TENANT_ID=<PILOT_TENANT>`** on the MetaSheet backend — the PLM adapter resolves the effective tenant
(`configService plm.tenantId` → `PLM_TENANT_ID` env → per-source `options.tenantId`) and applies it as
`x-tenant-id`, so `/context` resolves the **entitled** tenant. (Per-source `options.tenantId` is the
multi-tenant route, but the data-source REST schema does not persist it today — which is *why* V1 is
single-tenant. Verified against metasheet2 `origin/main`: `PLMAdapter.getEffectiveTenantId` /
`applyTenantOrgHeaders`.) Authoritative BOM fields are read-only; collaboration fields are MetaSheet-local.

---

## 5. Internal-dogfood UI note

The capability manifest advertises `approval_automation` with `action_status: "stubbed"`; the workbench
shows an "升级审批自动化" entry when `approval_automation.supported = true`. For **internal dogfood this is
harmless** (your own people). **Before any external pilot (V1.1)** this entry must be hidden / pilot-filtered
so you do not over-promise an automation product — that is a V1.1 MetaSheet (owner-gated) change, not V1.

---

## 6. Acceptance conditions (these passing = V1 ready, not "docs written")

1. **Real license:** `scripts/dev/sign_dogfood_license.py` produces a license whose self-verify passes
   and which `yuantus license import` accepts; `integrations/capabilities` then shows
   `bom_multitable.entitled = true` for the pilot tenant.
2. **Combined profile boots**; `base` profile stays MetaSheet-free (route surface unchanged with
   `YUANTUS_ENABLE_METASHEET=false`).
3. **BOM Review Path A states** (use `scripts/dev/smoke_bom_review_api.sh`) — **Path A = manifest + context only**:
   - unentitled tenant → `context: null` (an existing vs missing part return identical responses — no
     existence leak);
   - entitled tenant + valid part → a context with `part` + `lines[]`;
   - capability manifest → `bom_multitable.entitled` toggles `true` (entitled tenant) / `false` (unentitled).
   - *(embed-token mint bad-origin `403` / missing-key `503` is **V1.2**, not required for V1 dogfood.)*
4. **Single tenant** only; users authenticate to MetaSheet directly.
5. **No** automation execution, **no** write-back, **no** in-PLM iframe is presented.

---

## 7. Explicitly NOT in V1

In-PLM iframe host (V1.2) · embed-token click-through (V1.2) · approval automation execution (Phase 5) ·
controlled write-back (Phase 7) · SSO (Phase 6) · vendor-private issuance / seats / grace / admin UX (V2) ·
multi-tenant on one deployment · key rotation.

---

*Companion: `scripts/dev/sign_dogfood_license.py` (signer), `scripts/dev/smoke_bom_review_api.sh` (§6.3 三态),
`scripts/dev/smoke_combined_profile.sh` (§6.2). Canonical plan: the in-repo taskbook
`docs/development/plm-collaboration-upgrade-development-todo-20260618.md` (Phase 1).*
