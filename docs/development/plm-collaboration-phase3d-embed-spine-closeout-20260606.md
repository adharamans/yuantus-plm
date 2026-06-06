# PLM Collaboration P3-D — Embed-Spine Closeout / Acceptance Runbook (doc-only)

**Date:** 2026-06-06 · **Status:** doc-only closeout. This bookends the P3-D0 scope
(`plm-collaboration-phase3d-embed-spine-scope-20260605.md`, #730): it records what the embed
spine *actually landed*, pins the operational contract (the parent-page handshake + the
edge-layer CSP requirement), checks the P3-D0 acceptance list against reality (honest about the
two items that are partial), and writes the entry conditions for the **next** slice
(token-exchange / SSO). It implements nothing. The SSO slice needs its own explicit opt-in.

The canonical invariants are unchanged and not re-decided here: read-only (铁律), advisory ≠
authorization, `is_entitled` as the single gate, `license_data` is not an auth source, the
signing PRIVATE key is never committed.

---

## 1. What landed — the embed spine is LIVE end-to-end (read-only viewing)

P3-D0 framed two token-verification forks (A token-exchange, B PLM-token verifier). The spine
shipped on **Fork B** — the consumer verifies the PLM token **offline** with the Yuantus public
key; no `mst_` exchange, no per-call callback. Fork A (exchange → metasheet session) was *not*
built; it is the next slice (§5).

| Slice | What shipped | Where |
|---|---|---|
| **P3-D1** mint (provider) | `POST /api/v1/bom/multitable/{part_id}/embed-token` — mints a short-lived **Ed25519** embed token. Gate (mirrors the GET projection, then mints): `auth → is_entitled("bom_multitable") → part → Part-type → read permission → [signing key configured? else 503 fail-closed] → embed origin in allowlist (else 403)`. Unentitled → `embed_token: null` upgrade affordance, never a token (no existence leak). | YuantusPLM `main`, PR #733 (`b9585af2`) |
| **P3-D2 backend** verify (consumer) | Offline EdDSA verify via `node:crypto` + the Yuantus **public** key (Fork B). `GET /api/plm-embed/config` (public) + `GET /api/plm-embed/bom-review/context` (gated by the `X-PLM-Embed-Token` header). Server-config data source, part from the token claim, `feature_key`/`embed_origin` re-checked, never-500. | metasheet2 `main`, PR #2341 (`ae88987bd`) |
| **P3-D2 frontend** embed page (consumer) | `/plm-embed/bom-review` — a bare, no-session page that reads the allowlist from `/config`, accepts the token only from an allowlisted **parent** origin via `postMessage`, calls the context endpoint with the header (no Part input — part is token-bound), and renders the read-only table (shared with the P3-C panel). `MultitableEmbedHost` hardened: no `'*'`, outbound origin pinned. | metasheet2 `main`, PR #2347 (`c278de989`) |

**Net:** the BOM review now renders **inside the PLM UI** via a token-authenticated iframe,
re-gated server-side at every data call. The standalone P3-C workbench review still works when
embedding is unavailable.

---

## 2. The landed embed-token contract (grounded)

Standard EdDSA (Ed25519) JWT, `kid`-addressed. Claims:

| Claim | Value | Purpose |
|---|---|---|
| `sub` | user_id | identity (data side re-runs permission) |
| `tenant_id`, `org_id` | minting tenant/org | tenant binding |
| `part_id` | the Part | **the embedded part is THIS claim, never a request input** |
| `feature_key` | `"bom_multitable"` | consumer rejects a token not scoped to this feature (403) |
| `aud` | service audience (`EMBED_TOKEN_AUDIENCE`, default `metasheet2.embed`) | standard RFC-7519 audience validation |
| `embed_origin` | the PLM parent origin | validated **separately** against the consumer allowlist (NOT in `aud`) |
| `exp`, `iat` | short window | hard expiry; the consumer requires `exp` to be a finite number |
| `jti` | uuid4 | **trackable** (recorded on mint) — NOT yet a revocation denylist (§4, §5) |
| `typ` | `"embed"` | token-type guard |

- **TTL:** `EMBED_TOKEN_TTL_SECONDS` (default 120), hard-capped at `MAX_EMBED_TTL_SECONDS = 600`
  in the service — a misconfigured TTL can never mint a long-lived token.
- **Signing:** Yuantus-only private key `EMBED_TOKEN_SIGNING_KEY` (base64 raw seed, **never
  committed**; empty = minting disabled, fail-closed). `kid` = `EMBED_TOKEN_KEY_ID` (default
  `embed-1`).

**Config (the two-repo operational surface):**

| Side | Vars |
|---|---|
| Yuantus (mint) | `EMBED_TOKEN_SIGNING_KEY` (private), `EMBED_TOKEN_KEY_ID`, `EMBED_TOKEN_AUDIENCE`, `EMBED_TOKEN_TTL_SECONDS`, `EMBED_ALLOWED_ORIGINS` (CSV; empty = fail-closed; **no `'*'`**) |
| metasheet2 (verify) | `YUANTUS_EMBED_PUBLIC_KEY`, `YUANTUS_EMBED_KEY_ID` (`embed-1`), `PLM_EMBED_AUDIENCE` (`metasheet2.embed`), `PLM_EMBED_ALLOWED_ORIGINS` (CSV; strips `'*'`), `PLM_EMBED_DATA_SOURCE_ID` |

The audience must match (`EMBED_TOKEN_AUDIENCE` == `PLM_EMBED_AUDIENCE`); the key id must match
(`EMBED_TOKEN_KEY_ID` == `YUANTUS_EMBED_KEY_ID`); the public key must be the pair of the signing
key; and **all origin layers key off the same PLM parent origin(s)** (next section).

---

## 3. Operational contract (what an integrator/deployer MUST do)

### 3.1 Parent-page handshake (the PLM UI side — not yet built)
The PLM page that embeds the iframe must:
1. Request a token: `POST /api/v1/bom/multitable/{part_id}/embed-token` (gated; carries the embed
   origin). Unentitled / no-permission → no token → show the upgrade affordance, do not embed.
2. Create the iframe at the consumer's `/plm-embed/bom-review`.
3. **After the iframe `load` event, post the token** to `iframe.contentWindow`:
   `postMessage({ type: 'plm-embed:token', token }, <iframe-origin>)` — `targetOrigin` is the
   iframe origin, never `'*'`. **Retry** until acknowledged-by-render or a timeout: the embed
   buffers a token that races its `/config` fetch, but it does **not** buffer one posted before
   its `message` listener attaches, and it never posts back (listen-only), so there is no ack —
   the parent must (re)post after load.
4. Re-mint on expiry; never refresh inside the iframe.

### 3.2 Origin alignment (one origin set, three checks)
All three layers must name the **same PLM parent origin(s)**, and production must use explicit
origins (never `'*'`, never empty-which-is-fail-closed):
- the consumer postMessage allowlist (`PLM_EMBED_ALLOWED_ORIGINS`, surfaced by `/config`) — the
  iframe accepts the token only from these origins **and**, when the message carries a source,
  only from `window.parent`;
- the token's `embed_origin` claim — re-checked against the same allowlist at the data call (403);
- the mint-side origin gate (`EMBED_ALLOWED_ORIGINS`).

### 3.3 Edge-layer CSP (deploy requirement, NOT code-enforced here)
`frame-ancestors` must sit on the **embed HTML document**, which the metasheet Express app does
not serve — so it is an **edge-layer/CDN response header**, not application code. `/config`
computes and exposes the directive value (`frame-ancestors <PLM origins>`, or fail-closed
`'none'` when unconfigured) for the edge layer to apply. **Until the edge sets this header, any
page can frame the embed** — the code-enforced controls (token verify + `embed_origin` allowlist
+ frontend origin/source pin) still hold, but clickjacking-via-framing is an edge responsibility.

---

## 4. P3-D0 acceptance checklist — status

- [x] **Unentitled → no token.** Mint returns the upgrade affordance, never a token.
- [x] **Expired → unusable.** Consumer requires a finite `exp` and rejects past-TTL; the data
      call re-gates server-side.
- [x] **Cross-origin → unusable.** `embed_origin` claim + allowlist (403) + frontend origin pin
      **and** the message source, when present, must be `window.parent` (a real browser
      `postMessage` always sets the source; a null-source synthetic message still faces the origin
      allowlist); no `'*'`.
- [x] **No `'*'` in the production allowlist.** Both sides strip/forbid `'*'`; empty = fail-closed.
- [x] **Outbound postMessage origin-pinned.** `postToParent` pins the negotiated origin; the embed
      page is listen-only (no outbound at all).
- [x] **The PLM token is actually verified, not passed through.** Fork B offline Ed25519 verify in
      a dedicated `embed-token-auth` middleware — NOT the `mst_`-only `api-token-auth`.
- [x] **Graceful degradation.** Old PLM / no mint is the **PLM parent's** responsibility — it
      should not embed, or render its own fallback (§3.1 step 1: unentitled/no-permission → no
      token → do not embed). The embed page does **not** auto-detect an old PLM: if the iframe is
      loaded but no token arrives, it simply stays at **awaiting-token**. The `unavailable`/`error`
      state is the **post-token** degrade — a delivered token whose data call returns non-200 /
      malformed / a transient provider failure (and `suppressUnauthorizedRedirect` keeps a 401/403
      in-place instead of redirecting the token-only iframe to `/login`). Old metasheet (no embed
      host) → standalone P3-C still works.
- [x] **No write-back / no scope creep.** Read-only P3-A projection only.
- [~] **Cross-tenant → unusable — PARTIAL.** The token is tenant-bound at mint and the provider
      re-runs tenant-scoped `is_entitled` at the data call. The consumer relay binds
      part+origin+feature but resolves a **server-configured** data source
      (`PLM_EMBED_DATA_SOURCE_ID`), so the token-`tenant_id` ↔ data-source-tenant alignment is a
      deployment-config property today, not a consumer-side cross-check. **Hardening candidate for
      the SSO slice:** verify `claims.tenant_id` against the resolved data source's tenant.
- [~] **Single-use / revoked reuse → unusable — PARTIAL.** `jti` is recorded (trackable) but there
      is **no revocation denylist** yet, so a token can be replayed within its (≤600s) TTL. Short
      TTL is the only current mitigation. **Deferred to a later slice** (denylist / single-use).

Two of ten are partial; both are honest gaps the next slice must address (or consciously accept).

---

## 5. Next slice — token-exchange / SSO (NOT opened; entry conditions)

The owner's sequencing: do **not** open this yet. It upgrades "embedded read-only viewing" into
an **identity-session spine** — a higher risk tier (the embed token currently bootstraps one
read-only handshake; exchange/SSO would mint a *metasheet session*). Entry conditions before it
opens:

1. **Explicit opt-in** for the slice (per-phase rule).
2. **Pick the identity model:** Fork A (exchange the PLM embed token, server-side after verify,
   for a short scoped `mst_` session token that `api-token-auth` checks) vs a shared IdP
   (DingTalk) SSO. Decide session lifetime, refresh, **logout propagation**, and CSRF posture.
3. **Close the two partial acceptance items** (§4) as part of, or before, the session spine:
   the `tenant_id` ↔ data-source cross-check, and `jti` revocation / single-use.
4. **Map identity:** how a PLM `sub`/`tenant_id` becomes a metasheet principal without widening
   access beyond the read-only projection.
5. **Keep the read-only embed as the fallback** — the session spine is additive; if it is
   unavailable, the Fork-B read-only embed (and the standalone P3-C) must still work.

Until then, the spine is complete *as a read-only embedded viewer*, which is exactly its scope.

---

## References (grounding)

- P3-D0 scope (this doc bookends it): `plm-collaboration-phase3d-embed-spine-scope-20260605.md` (#730, `7d37ee9c`).
- P3 canonical package: `plm-collaboration-phase3-bom-multitable-scope-20260605.md`.
- Landed: PR #733 (`b9585af2`, mint) on YuantusPLM `main`; PR #2341 (`ae88987bd`, verify) + #2347 (`c278de989`, embed page) on metasheet2 `main`.
- Mint code: `src/yuantus/meta_engine/web/bom_multitable_router.py` (`POST …/embed-token`), `src/yuantus/meta_engine/services/bom_multitable_embed_token_service.py` (claims + `MAX_EMBED_TTL_SECONDS=600`), `src/yuantus/config/settings.py` (`EMBED_TOKEN_*` / `EMBED_ALLOWED_ORIGINS`).
- Entitlement kernel (mint gate): `EntitlementService.is_entitled`; `bom_multitable → {"plm.bom_multitable"}`.
- Signed-token discipline reused: P1-C Ed25519 (private key never in repo; `license_data` is not an auth source).
