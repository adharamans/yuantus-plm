# PLM BOM Review Add-on — V1.1 Development & Verification

**Date:** 2026-06-19 · **Scope:** V1.1 = the first **external-pilot** cut on top of V1. It adds exactly
**one thing: contract hardening on the two Path-A modern surfaces** (capability manifest + BOM context) +
the **pilot-hide** of the stubbed approval-automation CTA. **embed-token pact → V1.2; multi-`kid` → V2;**
in-PLM host / SSO / automation engine / write-back remain deferred per the signed ladder.

**Why V1.1 before external customers (not protecting a frozen env — enabling safe patching):** a fixed
version pin protects a frozen deployment, but during a pilot you *will* ship fixes; the pact catches
modern-surface drift in CI **when you bump either side**. That is V1.1's whole value.

This is the build+verification record for the two owner-gated PRs:
- **metasheet2 #2918** (`claude/v1.1-bom-pact-automation-hide`) — consumer pact interactions + pilot-hide.
- **adharamans/yuantus-plm #805** (`claude/v1.1-bom-pact-seed`) — provider seed + pact sync.

---

## 1. What shipped

### metasheet2 #2918 (consumer)
- **Pact:** added the 2 Path-A interactions to `packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json` — `GET /api/v1/integrations/capabilities` and `GET /api/v1/bom/multitable/{partId}/context` (entitled tenant, type-matchers; structured to mirror the seeded P1→P2→R1 line). Updated `plm-adapter-yuantus.pact.test.ts` `PACT_PATHS` (order) + `endpointsToFind` (the anti-drift check that every pacted endpoint is called by `PLMAdapter`).
- **Pilot-hide:** in `IntegrationWorkbenchView.vue`, the `selectedPlmApprovalCapabilityEntry` computed returns `null` when `action_status === 'stubbed'` — **placed before the `'upgrade'` return, after the entitled `'enabled'` return**, so it suppresses only the *upgrade CTA shown to unentitled tenants* (the over-promise), never the honest enabled entry a buyer sees. Self-correcting: once the engine ships, `action_status` changes and the CTA returns.

### Yuantus #805 (provider)
- **Seed:** `test_pact_provider_yuantus_plm.py` seeds an ACTIVE PERPETUAL `plm.bom_multitable` license for `PACT_TENANT_ID` via the **proven import path** (ephemeral Ed25519 key signs; `import_license` takes pubkeys as an argument — no settings mutation), so the manifest/context interactions verify `entitled:true`.
- **Sync:** `contracts/pacts/metasheet2-yuantus-plm.json` synced to the 2 new interactions (byte-matches the metasheet2 pact → the `sync_metasheet2_pact.sh --check` gate passes once both land).

---

## 2. Verification — what actually ran here

**metasheet2 (in the worktree, after `pnpm install`):**
- `plm-adapter-yuantus.pact.test.ts` — **14 passed** (JSON parses as Pact v3; 32 interactions in documented order; **every endpoint — incl. the 2 new — is called by `PLMAdapter`**; provider-states present).
- `IntegrationWorkbenchView.spec.ts` — **40 passed**, including the three that pin the hide:
  - entitled + stubbed → the `'enabled'` entry still shows (guard does **not** over-hide);
  - unentitled + stubbed → entry **hidden** (the over-promise suppressed; updated from the old "shows upgrade" assertion);
  - unsupported → null.
- `vue-tsc -b` — **clean** (no errors). `eslint` — **0 errors** on the changed files (89 warnings are pre-existing test-helper `router-link` stubs).

**Yuantus:**
- `py_compile` — clean.
- The **entitled-seed mechanism is proven** (V1 round, re-runnable): a perpetual `plm.bom_multitable` license imported via this exact path → `is_entitled("bom_multitable")` **true for its tenant, false for another**, with `expires_at = None`. The seed reproduces that for `PACT_TENANT_ID`.

**Pact provider replay — ran in CI and PASSED, after a real fix.** The locally-unrunnable piece (`pact-python` absent here) is the provider replay; it ran in **Yuantus CI** and first **FAILED** — the BOM-context interaction exact-matched the **dynamic provenance** (`source_updated_at` et al.) against a fixed `2026-04-11` fixture, while the provider returns runtime time (`Expected '2026-06-19T…' to equal '2026-04-11T…'`). **Fix (in #2918, the pact source of truth — NOT by hardcoding the provider seed time):** type-match the **full context shape** (`part.*`, `lines[*].*`, top-level `source_version`/`source_updated_at`/`sync_status`) so the contract expresses field SHAPE, not seed values; re-synced to #805. Result: **`contracts` PASS (8m13s); #805 CLEAN.** The provider behaviour is also independently proven by the V1 in-process tests (`test_integration_capabilities.py`, `test_bom_multitable_projection.py`) + the seed proof. *(This corrects an earlier draft of this file that called the replay merely "deferred to CI" — it has since run, failed, been fixed, and passed.)*

---

## 3. Landing / coordination (both owner-gated)

metasheet2 is the **pact source of truth**; Yuantus syncs it. **Status:** #805 `contracts` is **green** (the Pact provider verifier passes on the corrected pact); #2918 consumer CI is **green** (`yuantus-pact-consumer` + contracts/e2e) — its `BLOCKED` is only metasheet2's strict-up-to-date rule on fast-moving `main`, a merge-time re-sync, not a failure. Recommended order: **land #2918 first**, then re-sync + land #805. Worktrees off `origin/main`; the canonical checkouts' branches were never touched.

---

## 4. Deferred (unchanged)
- **V1.2:** PLM parent-page embed host + **embed-token pact** (V1.2 is when token/iframe is actually exercised).
- **V2:** vendor-private issuance · seats (design the limits model first) · grace/renewal · admin UX · **multi-`kid`** rotation.
- Approval automation execution (Phase 5) · SSO (Phase 6) · controlled write-back (Phase 7).

*Generated by Claude (Fable). Evidence: 14 consumer pact tests + 40 workbench tests + vue-tsc + eslint (metasheet2); py_compile + the proven entitled-seed mechanism (Yuantus); and the **Pact provider replay in CI — `contracts` PASS (8m13s) after the provenance-matcher fix** (the V1.1 review caught the original exact-match gap). Boundaries stated above.*
