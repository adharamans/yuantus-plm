# PLM Collaboration Phase 6 — SSO / Identity-Session Spine (design-first)

Date: 2026-06-27 (rev 2 — corrected against the **live** metasheet2 consumer; see
"Correction" note below)
Type: **design-only**. Authorizes no build. It frames the decision surface for the
identity-**session** spine and ties it to reviewable exit criteria, so the owner
resolves the forks at a review gate **before** any implementation slice. Scope:
the **MetaSheet embed spine only** — NOT the separate CAD Helper Bridge line.

> **Correction (rev 2).** Rev 1 mis-stated the current state: it treated consumer
> token-verification, single-use replay, and the cross-tenant check as open. They
> are **shipped** on metasheet2 `main` (verified below). The one-shot embed
> handshake is **complete**; Phase 6's real decision is the **continuous session**
> (ownership / renewal / admin-revocation), not whether the PLM token is verified.

## 0. The load-bearing invariant (read this first)

**SSO here means identity *continuity*, never new *capability*.** The embedded
surface stays the P3-A governed, read-only BOM Review projection; advisory-not-auth
is unchanged. The session spine must not become a write-back or privilege-escalation
path — any future write still goes **only** through the governed seam (Phase 7,
`/aml/apply` or `/actions`), never the embed session.

## 1. What is already DONE — the one-shot embed handshake (do not redesign)

The P3-D1 (provider) + P3-D2 (consumer) one-shot handshake is live end-to-end and
**already satisfies the P3-D0 §5 exit criteria**. The new session layer must
**inherit** these, not replace or redo them:

| Shipped property | Mechanism (live) |
|---|---|
| Provider mint, gated + short-lived + fail-closed | Ed25519 EdDSA JWT; entitlement/permission/origin gated; TTL ≤ 600 s; `jti` recorded; missing/invalid key → 503. `bom_multitable_embed_token_service.py` (#733) |
| **Consumer offline verification** (Fork B, **resolved**) | `X-PLM-Embed-Token` verified OFFLINE with the Yuantus public key; no token → 401, no key → 503, bad/expired/wrong-aud/wrong-type → 401. `metasheet2 …/middleware/embed-token-auth.ts` |
| Feature + origin scope | `EMBED_FEATURE_MISMATCH` (not `bom_multitable`) / `EMBED_ORIGIN_NOT_ALLOWED`, before query. `metasheet2 …/routes/plm-embed.ts` |
| **Served-tenant cross-check** (the real cross-tenant defense) | compares `claims.tenant_id` to `adapter.getEffectiveTenantId()` (the tenant whose data is *actually* served) → `403 EMBED_TENANT_MISMATCH`, **before** querying, fail-closed; deliberately the served tenant, not a config fallback, to avoid false closure. `plm-embed.ts` |
| **Single-use replay prevention (B1, DONE)** | `consumeEmbedJti` = Redis `SET key 1 EX <ttl> NX` → first-use vs replay; replay rejected (`EMBED_TOKEN_REPLAYED`), store-unavailable → 503 fail-closed; consumed **before** query. `…/auth/embed-jti-store.ts` |
| Provider record | "provider mint + MetaSheet offline-verified viewer complete; #737 partials closed" — #780 |

**So these P3-D0 §5 criteria are already met:** unentitled→no token, expired→unusable,
cross-tenant→unusable, cross-origin→unusable, no `'*'`, token-actually-verified,
single-use replay→unusable, graceful degradation, no write-back. Phase 6 does **not**
reopen them.

## 2. What Phase 6 actually is — the continuous session layer

The handshake is **one-shot**: one token = one data call (single-use jti), re-mint on
refresh. That is sufficient for the read-only context load. A **continuous identity
session** is needed only as the prerequisite for (a) bridge activation (the flag-gated
`metasheet_bridge` router, `ENABLE_METASHEET`, today a `/health` stub) and (b) Phase 7
write-back. Phase 6 delivers **identity continuity**, not those capabilities.

**First open question for the gate:** *is the session needed yet?* If bridge activation
/ write-back are not the next thing the owner intends to build, the one-shot handshake
is complete and Phase 6 can be **deferred** until that need is concrete (the same
reassess discipline applied elsewhere). The forks below matter only once the answer is
"yes, build the session now."

## 3. Decision surface — forks and their *deciders*

Each fork is decided by a **deployment/intent fact this repo cannot observe**; the
design states the deciding fact and stops.

### Fork 1 — Session ownership (and therefore revocation latency)

This is the A/B fork, now correctly about the **session**, not one-shot verification.

- **(A) Exchange → `mst_` session.** The consumer exchanges a verified embed token for
  its own short-lived scoped `mst_` session (its `api-token-auth` already validates
  `mst_`). **Consumer owns the session ⇒ admin/logout revocation is real-time.** Cost: a
  MetaSheet session store + exchange endpoint. **Risk to close:** the `mst_` session
  MUST re-apply the **served-tenant cross-check** (§1) on every data call — verifying
  only the session's own tenant would miss data-source tenant drift after exchange.
- **(B) Renewable PLM session token.** Extend the shipped offline-verified model with a
  Yuantus-issued **renewable** session token, still verified offline by `embed-token-auth`.
  **Yuantus owns identity, offline-scalable ⇒ revocation is bounded-window** (short TTL
  + a renewal-time denylist), never instant without a callback that defeats the offline
  property.

**Decider:** the operational **admin/logout-revocation SLA** + whether MetaSheet should
own a session store. Near-instant revocation + consumer session acceptable → A;
offline / single-identity-source + bounded revocation acceptable → B.

### Fork 2 — Identity model (who is the IdP)

PLM-issued-token-as-bridge (extend the shipped Ed25519 issuance; Yuantus stays the
single identity source) **vs** a shared external IdP (e.g. DingTalk; both repos become
relying parties). **Decider:** *is a shared IdP already the identity provider across
**both** repos in the target deployment?* If no, do not introduce one for this feature.

### Fork 3 — Session shape (once Fork 1 is chosen)

Short-lived + **renewable** (proposed ~15 min; renewal **re-runs** `is_entitled` + the
served-tenant cross-check + the revocation check), tenant/org/user/`embed_origin`-bound
(identical to the P3-D1 claim set), **read-only `feature_key` scope only** (the §0
invariant, enforced in the scope claim). Owner sets the numbers.

### Fork 4 — Revocation B2 (NOT single-use, which is B1-DONE)

Single-use replay is already solved (§1). B2 is the **remaining** revocation surface:
**admin/session revocation + logout propagation + renewal-time denylist** — the ability
to kill an *active continuous session* (not just block one token's replay). Under A:
drop the consumer session (real-time). Under B: publish to a denylist checked at
**renewal** (bounded by the short TTL).

## 4. The proposed spine (builds ON the complete handshake)

1. **Layer 1 — handshake (DONE, unchanged):** mint → offline verify → feature/origin →
   **served-tenant cross-check** → **single-use jti** → query. All fail-closed, all
   before the query (§1).
2. **Layer 2 — session (NEW, Phase 6):** on a verified handshake, establish a
   short-lived, renewable, tenant/org/user/origin-bound, **read-only-scoped,
   admin-revocable** session (owned per Fork 1). **It MUST inherit every Layer-1
   fail-closed check** — most importantly the served-tenant cross-check on every data
   call — and renewal re-runs entitlement + served-tenant + revocation. Yuantus stays
   the authority for *what the user may see* (every data call re-verified against the
   P3-A projection); Phase 6 adds only *who the user is, continuously*.

## 5. Phase 6 acceptance criteria (NEW — the session layer; P3-D0 §5 is met by §1)

Design-first ⇒ each is reviewable now.

| Phase 6 exit criterion | How the session satisfies it |
|---|---|
| Session inherits served-tenant cross-check | Every `mst_` (A) / renewable-PLM (B) data call re-applies `claims.tenant_id == adapter.getEffectiveTenantId()` before query — explicitly closing the exchange→session drift risk (Fork 1 A). |
| Renewal re-checks, no silent escalation | Renewal re-runs `is_entitled` + served-tenant + revocation; entitlement lost mid-session → next renewal fails; no refresh past expiry/revocation. |
| Admin/logout revocation works (B2) | A: drop session (real-time). B: jti denylist at renewal (bounded-window) — the owner accepts the SLA at the gate. |
| Read-only scope preserved (invariant) | Session scope claim = read-only `feature_key`; no write scope is mintable; write-back stays the governed seam. |
| Graceful degradation unchanged | Old consumer (no session endpoint) → falls back to the one-shot handshake (§1), which still works. |

## 6. Phasing (design → review gate → build; build NOT authorized here)

1. **This doc** — design + decision surface. **Review gate:** owner answers §2 ("is the
   session needed yet?") and, if yes, resolves Forks 1–4 by their deciders.
2. *(gated)* Session-layer slices on the chosen fork — Yuantus side first (issue / verify
   / renew + B2 denylist), each with its own dev & verification doc and the §5 mapping as
   acceptance; then the consumer slice, which MUST carry the served-tenant cross-check
   into the session path.
3. *(separately gated)* Bridge activation + Phase 7 write-back — out of scope here.

## 7. Open questions for the owner

1. **Is the continuous session needed now**, or is the complete one-shot handshake
   sufficient until bridge/write-back is scoped? (§2)
2. **Admin/logout revocation SLA + consumer session infra** → Fork 1 (A vs B) and Fork 4.
3. **Is a shared IdP (DingTalk) already the IdP across both repos?** → Fork 2.
4. **Session lifetime / renewal window** (proposed ~15 min) → Fork 3.
5. Confirm the invariant: Phase 6 ships identity continuity only; bridge activation and
   write-back stay separately gated.

## References (grounding)

- **Consumer (live, metasheet2 `main`):** `packages/core-backend/src/middleware/embed-token-auth.ts` (offline EdDSA verify); `packages/core-backend/src/routes/plm-embed.ts` (mount + feature/origin + served-tenant cross-check + single-use consume, all pre-query); `packages/core-backend/src/auth/embed-jti-store.ts` (`consumeEmbedJti` SET EX NX single-use)
- **Provider (Yuantus):** `src/yuantus/meta_engine/services/bom_multitable_embed_token_service.py` (#733 mint); `src/yuantus/api/routers/metasheet_bridge.py` (flag-gated bridge stub); `src/yuantus/security/auth/{jwt,service}.py`, `api/middleware/auth_enforce.py` (base identity)
- **Records:** P3-D embed delivery & verification — #780 (provider mint + offline-verified viewer complete; #737 partials closed); P3-D0 embed-spine scope / §5 exit criteria — #730; V1/V2 status closeout — #841
