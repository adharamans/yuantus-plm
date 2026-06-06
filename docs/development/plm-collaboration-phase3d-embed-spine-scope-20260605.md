# PLM Collaboration P3-D0 — Embed Spine Scope / P3-A·B·C Closeout (doc-only)

**Date:** 2026-06-05 · **Status:** doc-only scope/closeout. This package decides nothing it
does not have to: it records what shipped (P3-A/B/C), frames P3-D (the identity/SSO/embed
spine) and its security contract + two-repo boundary, and pins the acceptance checklist. It
does **not** implement P3-D. Every P3-D implementation slice needs its own explicit opt-in.

It does not re-decide the canonical invariants (铁律 read-only / advisory-not-auth /
is_entitled single gate) — it grounds the next slice against them. The mechanics of the spine
were framed earlier in §4 of `plm-collaboration-phase3-bom-multitable-scope-20260605.md`
("Permission / identity / SSO / embed spine (F-C)"); this package concentrates the decision
surface for when P3-D is opened.

---

## 1. Shipped status — P3-A / P3-B / P3-C are LIVE end-to-end (except embedding)

The BOM multi-table review feature is live across both repos as a governed, read-only,
advisory-degrading surface. What remains is *embedding it inside the PLM UI* — that is P3-D.

| Slice | What shipped | Where |
|---|---|---|
| **P3-A** provider projection | `GET /api/v1/bom/multitable/{part_id}/context` — governed READ-ONLY projection of a Part's full BOM tree (restricted to `"Part BOM"` rels, flattened with `bom_line_id`/`part_id`/`level`/`path` + per-row provenance). Order auth → `is_entitled` → part → Part-type → read perm; unentitled → `context:null` (no existence leak). NO write-back/embed. | YuantusPLM `main`, PR #724 (squash `8cc6389e`) |
| **P3-B** SKU + manifest | `FEATURE_APP_NAMES["bom_multitable"] = {"plm.bom_multitable"}` — its own independent SKU (NOT bundled into plm.collab). The P2.5 integration manifest now advertises `bom_multitable` (`supported`, `api_version:"v1"`, `scenarios:["bom_review"]`, no actions; `entitled` per tenant). | YuantusPLM `main`, PR #727 (squash `46a1b3a7`) |
| **P3-C** metasheet2 consumer | `PLMAdapter.getBomMultitableContext` + a gated relay route + `PlmBomReviewPanel.vue` (read-only review table) + a workbench capability entry. Advisory: discovers `bom_multitable` via the C1/C2/C3 handshake, renders the read-only table when supported+entitled, degrades gracefully otherwise; the relay does NOT query the resource when the manifest says unentitled. | metasheet2 `main`, PR #2324 (squash `e4e67d140`) |

**Net:** provider read surface + sellable SKU/advertisement + consumer review UI are all on
`main`. The feature is usable **standalone** (the MetaSheet workbench renders the review). It is
**not yet embedded** "inside the PLM screen".

---

## 2. P3-D goal (and explicit non-goals)

**Goal:** show the BOM review (and, later, other collaboration surfaces) **inside the PLM UI**
via an embedded MetaSheet iframe, authenticated by a **PLM-issued short-lived embed token** —
the identity/SSO/embed spine. This is the "inside the PLM UI" prerequisite the canonical plan
calls the embed spine.

**Non-goals (hard red lines, unchanged from P3-A/B/C):**
- **No write-back.** The embedded surface stays read-only; any future write still goes ONLY
  through a governed endpoint (`/aml/apply` or an approval `/actions`), never a table-cell→PLM
  write. P3-D adds embedding, not mutation.
- **No new BOM functionality / no new PLM data surface.** P3-D embeds the *existing* P3-A
  projection; it does not widen the curated field set, add depth/where-used, or expose new data.
- **No standalone regression.** Embedding is additive; the standalone P3-C workbench review must
  keep working when embedding is unavailable.
- **Advisory ≠ authorization still holds.** The manifest only decides whether to *attempt*
  embedding; the embed token is the real gate.

---

## 3. Security contract for the embed token (the decision surface)

P3-D's embed token is the spine's core. The contract (each clause is a P3-D acceptance pin):

- **Short-lived.** Minted on demand with a short hard TTL (target ≤ a few minutes), never a
  long-lived/session token. It exists only to bootstrap one embed handshake.
- **Tenant- AND user-bound.** The token carries `tenant_id` + `user_id` (+ `org_id`); it is
  valid ONLY for that tenant/user. A token minted for tenant A is unusable for tenant B (the
  same no-cross-tenant rule as the entitlement kernel — `tenant_id IS NULL` never unlocks).
- **Entitlement-gated at mint.** PLM issues the token ONLY if `is_entitled("bom_multitable")`
  AND the user holds the P3-A read permission. **Unentitled → no token** (return an upgrade
  affordance, mirroring P3-A's "未授权不查资源" — do not mint, do not leak).
- **Hard expiry, no grace.** An expired token is rejected outright; no silent extension/refresh
  inside the embed surface (re-mint instead).
- **Single-use or short-TTL + revocable.** The token is consumed on the embed handshake (or is
  short-TTL with a server-side revocation check); a reused / revoked token is rejected.
- **Cryptographically verifiable, NOT `license_data`.** Minted and signed by PLM (signing
  mechanism is a P3-D decision — reuse the P1-C Ed25519 discipline or a scoped server secret;
  never guessable, never derived from `license_data`, which is not an authorization source).
- **Origin-pinned (cross-origin allowlist), and the allowlist must be a real allowlist.** The
  JWT `aud` is the recipient SERVICE (P3-D1 ships it as `EMBED_TOKEN_AUDIENCE`, default
  `metasheet2.embed`, so P3-D2 can do STANDARD RFC-7519 audience validation); the embed origin
  is a SEPARATE `embed_origin` claim, validated against the allowlist independently (do NOT put
  the origin in `aud`). The iframe host also enforces the `allowedOrigins` allowlist + postMessage
  origin checks (the existing `MultitableEmbedHost.vue` mechanism); a token / message from a
  non-allowlisted origin is rejected. **Two concrete traps
  in the current host that P3-D MUST close:** (i) `MultitableEmbedHost.isOriginAllowed`
  short-circuits to `true` when `allowedOrigins` contains `'*'` — the **production embed config
  MUST forbid `'*'`** (an explicit PLM origin only); (ii) `postToParent` today does
  `window.parent.postMessage(payload, '*')` — every outbound message MUST instead use the
  **negotiated PLM origin** as `targetOrigin`, never `'*'`, so a payload is never delivered to a
  foreign parent frame.
- **Re-checked at every embedded data call.** The embedded surface still calls the P3-A
  projection, which re-runs `is_entitled` + permission server-side. The token bootstraps the
  embed; it does not replace the per-call gate (defense in depth).
- **Fail closed + degrade, never leak.** Any token failure (absent / expired / cross-tenant /
  revoked / origin-mismatch) → the iframe does not load / shows a neutral degraded state, never
  an error that leaks existence or identity.

---

## 4. Two-repo boundary

| Repo | Owns | Does NOT |
|---|---|---|
| **YuantusPLM (provider)** | a NEW governed endpoint to **mint** the embed token (entitlement + permission gated, short-lived, signed, tenant/user-bound) and to **verify** its own token at the embedded data calls; the embedded data stays the P3-A read-only projection. | does not host the iframe; does not trust a MetaSheet-minted token. |
| **metasheet2 (consumer)** | **hosts** the iframe (`apps/web/src/multitable/views/MultitableEmbedHost.vue`, `embedded` flag + a real-allowlist `allowedOrigins`), **verifies the embed identity** via one of the two forks below, and **consumes** the embed config to render the embedded read-only review. | does not mint embed tokens; does not write back. |

**Token-verification fork (P3-D MUST pick one — do NOT assume the existing middleware verifies a
PLM token).** `packages/core-backend/src/middleware/api-token-auth.ts` is defined-but-unmounted
**and it only validates MetaSheet's own `mst_`-prefixed Bearer tokens — a non-`mst_` token (e.g.
a raw PLM-issued token) is passed straight through to normal auth, NOT verified.** So mounting it
as-is does not authenticate a PLM token. P3-D chooses:
- **(A) Token exchange.** Yuantus mints the short embed token; metasheet2 exchanges it (server
  side, after verifying it against Yuantus) for its OWN short-lived, scoped `mst_` token, which
  `api-token-auth` then verifies on the embedded calls. Reuses the existing middleware unchanged.
- **(B) PLM embed-token verifier.** metasheet2 adds/adapts a verifier for the PLM token directly
  — offline via a Yuantus **public key** (the P1-C Ed25519 discipline) or online via a **callback
  to Yuantus**. New verification path; no reliance on `mst_`.

**Flow (framed, not fixed):** PLM UI → request an embed token from Yuantus (gated) → hand it to
the MetaSheet iframe via the embed config / postMessage (origin-checked, fixed `targetOrigin`) →
MetaSheet verifies it (fork A or B) → renders the embedded P3-A review, every data call re-verified
by Yuantus. **Identity/SSO** is the open P3-D decision (the scope doc §4 frames the options:
DingTalk as a common IdP vs the PLM-issued token as the bridge) — pick one when P3-D opens.

---

## 5. Acceptance checklist (P3-D exit criteria)

- [ ] **Unentitled → no token.** An unentitled tenant's mint request returns an upgrade
      affordance, never a usable token; the iframe is not offered.
- [ ] **Expired token → unusable.** A token past its TTL is rejected at the handshake and at
      the data call; no grace, no silent refresh.
- [ ] **Cross-tenant → unusable.** A tenant-A token used for tenant B (or with a mismatched
      user) is rejected.
- [ ] **Cross-origin → unusable.** A token / iframe from a non-allowlisted origin is rejected
      (allowlist + postMessage origin check).
- [ ] **No `'*'` in the production allowlist.** The production embed config's `allowedOrigins`
      contains explicit PLM origin(s) only, never `'*'` (which makes `isOriginAllowed`
      short-circuit to true).
- [ ] **Outbound postMessage is origin-pinned.** Every message the embed host sends to the parent
      uses the negotiated PLM origin as `targetOrigin`, never `'*'`.
- [ ] **The PLM token is actually verified, not passed through.** metasheet2 verifies the embed
      identity via fork A (exchange → `mst_`) or fork B (PLM-token verifier) — NOT by relying on
      the existing `api-token-auth`, which ignores non-`mst_` tokens.
- [ ] **Single-use / revoked reuse → unusable.** A consumed or revoked token cannot be replayed.
- [ ] **Graceful degradation.** An old PLM (no mint endpoint) or old MetaSheet (no embed host)
      degrades cleanly: the standalone P3-C review still works; no hard failure, no leak.
- [ ] **No write-back, no scope creep.** The embedded surface is read-only and exposes nothing
      beyond the P3-A curated projection.

---

## References (grounding)

- P3 scope (the canonical Phase 3 package, F-A/F-B/F-C + spine §4): `plm-collaboration-phase3-bom-multitable-scope-20260605.md`.
- P3-A/B/C: PR #724 (`8cc6389e`), #727 (`46a1b3a7`) on YuantusPLM `main`; PR #2324 (`e4e67d140`) on metasheet2 `main`.
- Entitlement kernel (mint gate): `src/yuantus/meta_engine/app_framework/entitlement_service.py` (`is_entitled`; `bom_multitable → {"plm.bom_multitable"}`).
- Signed-token discipline to reuse: P1-C Ed25519 offline license import (`license_import_service`; private key never in repo; `license_data` is NOT an auth source).
- Embed spine assets (metasheet2): `apps/web/src/multitable/views/MultitableEmbedHost.vue` (allowlist + postMessage — NOTE `isOriginAllowed` honors `'*'`, and `postToParent` posts with `targetOrigin '*'`; both must be tightened for prod — §3/§5), `packages/core-backend/src/middleware/api-token-auth.ts` (defined, unmounted, **and validates only `mst_`-prefixed tokens — passes non-`mst_` through; it does NOT verify a PLM token** — §4 fork), `packages/core-backend/src/auth/dingtalk-oauth.ts` (common-IdP candidate).
