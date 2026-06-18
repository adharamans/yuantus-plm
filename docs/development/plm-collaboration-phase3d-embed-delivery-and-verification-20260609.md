# PLM Collaboration P3-D — Embed Delivery & Verification (doc-only)

**Date:** 2026-06-09 · **Status:** the two backend halves — **provider mint** (Yuantus) + the
**MetaSheet token-bound, offline-verified, read-only viewer** (incl. per-call tenant + single-use
re-gating) — are COMPLETE and merged to `main` on both repos (`zensgit/yuantus-plm` +
`zensgit/metasheet2`). The **PLM parent-page embedding contract** (the PLM UI that mints, hosts the
iframe, and posts the token) is **deferred / not built** (§7), so there is **no live in-PLM
click-through yet** — what ships is the mint endpoint and a standalone token-bound viewer, not an
integrated PLM screen.

This doc bookends the embed line. It supersedes the P3-D closeout
(`plm-collaboration-phase3d-embed-spine-closeout-20260606.md`, #737) on exactly one point: the **two
`[~]` partial acceptance items** §4 left open are now **CLOSED** — by two follow-on hardening slices
that landed **independently of the SSO session spine** (so #737 §5 entry-condition 3 is already
satisfied; SSO, if ever opened, no longer carries those two as prerequisites). It records what was
built, how it is secured, and how each slice was verified. It implements nothing.

Canonical invariants unchanged and not re-decided here: read-only (铁律), advisory ≠ authorization,
`is_entitled` as the single gate, `license_data` is not an auth source, the signing PRIVATE key is
never committed, the embed data source is server-configured (never a request input), the part is the
token-bound `part_id` claim.

---

## 1. What shipped — the two backend halves

A part's BOM can be served as a **governed, read-only** table to a **MetaSheet iframe**,
authenticated by a **PLM-minted, short-lived, single-use Ed25519 token** that MetaSheet verifies
**offline**. Every data call is re-gated server-side; the surface degrades safely and never leaks
existence/identity. **What is built** is (a) the provider mint endpoint and (b) the MetaSheet
token-bound viewer; **what is not** is the PLM UI that would host the iframe and deliver the token —
that parent-page handshake is deferred (§7), so the design below describes the intended full flow
with its **PLM-page host still to be built**.

Pipeline (`[deferred]` marks the unbuilt PLM-side host): `PLM page [deferred host] → mint embed token
(gated) → iframe at MetaSheet /plm-embed/bom-review → token via postMessage (origin-pinned)
[deferred: the poster] → MetaSheet offline-verifies → renders the read-only BOM table; the embedded
data call re-checks entitlement + permission + tenant + single-use on the provider/relay.` The mint
endpoint and everything from the iframe rightward are built and merged; the `[deferred]` PLM-page
host that ties them together is not.

---

## 2. Slices (all merged)

| # | Slice | Repo | PR (squash) |
|---|---|---|---|
| P3-A | BOM governed read projection `GET /api/v1/bom/multitable/{part_id}/context` (Part-BOM rels, flattened `bom_line_id`/`level`/`path` + provenance; auth→is_entitled→part→Part-type→read perm; unentitled→`context:null`) | Yuantus | #724 `8cc6389e` |
| P3-B | `bom_multitable` independent SKU + capability manifest (`supported`/`api_version:v1`/`scenarios:[bom_review]`) | Yuantus | #727 `46a1b3a7` |
| P3-C | Consumer review UI: `PLMAdapter.getBomMultitableContext` + gated relay + `PlmBomReviewPanel.vue` + workbench entry; transient-vs-empty `error` state | metasheet2 | #2324 `e4e67d140` |
| P3-D0 | Embed-spine scope / decision surface (doc-only) | Yuantus | #730 `7d37ee9c` |
| P3-D1 | Embed-token **mint**: `POST .../embed-token`, Ed25519, entitlement+permission gated, short TTL (default 120, capped 600), `embed_origin` allowlist, signing-key fail-closed 503 | Yuantus | #733 `b9585af2` |
| P3-D2 backend | Offline EdDSA **verify** (Fork B, node:crypto, no jose); `GET /api/plm-embed/config` (public) + `/bom-review/context` (header-gated); feature/origin re-check; never-500; AUTH_WHITELIST entry | metasheet2 | #2341 `ae88987bd` |
| P3-D2 frontend | `/plm-embed/bom-review` bare no-session embed page; listen-only postMessage handshake; reuse read-only table; `MultitableEmbedHost` `'*'` removed + outbound origin pinned | metasheet2 | #2347 `c278de989` |
| P3-D closeout | Embed-spine closeout / acceptance runbook (doc-only) | Yuantus | #737 `8d4999183` |
| Slice A | **Tenant cross-check** (closes #737 §4 partial ①) — relay compares `claims.tenant_id` against the tenant the adapter actually serves (`x-tenant-id`, case-insensitive); 403 fail-closed before query | metasheet2 | #2356 `609bcfcbd` |
| Slice B (B1) | **Single-use jti** (closes #737 §4 partial ②) — shared Redis `SET NX EX` consume before query; replay→401 `EMBED_TOKEN_REPLAYED`; store-unavailable→503 fail-closed; scoped+hashed key (`JSON.stringify` material, no delimiter ambiguity) | metasheet2 | #2370 |
| Re-mint copy | Embed `error` state copy → "重新打开此嵌入视图以重新授权" (after B1 made tokens single-use, in-place retry would 401-replay) | metasheet2 | #2408 `103b27f36` |

---

## 3. Closing the two #737 §4 partials

| #737 §4 item | Was (partial) | Now (closed) | By |
|---|---|---|---|
| ① Cross-tenant → unusable | token tenant-bound at mint + provider re-runs tenant-scoped `is_entitled`, but the consumer relay resolved a server-configured data source with **no consumer-side `tenant_id` ↔ data-source-tenant cross-check** (a deployment-config property only) | relay reads the tenant the adapter **actually serves** (`connection.headers` `x-tenant-id`, case-insensitive; ambiguous casings → undefined → fail-closed) and **rejects 403 `EMBED_TENANT_MISMATCH` before the BOM query** if it is absent or ≠ `claims.tenant_id` — no cross-tenant fetch | Slice A `#2356` |
| ② Single-use / revoked reuse → unusable | `jti` recorded (trackable) but **no denylist** → replay possible within the ≤600s TTL; short TTL the only mitigation | atomic `SET key 1 EX ttl NX` on a hashed, scope-bound key **before** the query consumes the `jti`; replay within TTL → 401 `EMBED_TOKEN_REPLAYED`; shared store unavailable → 503 fail-closed (**no in-memory fallback** — it would not stop cross-instance replay). One data call per token | Slice B/B1 `#2370` |

Both closed **without** the SSO session spine. #737 §5 listed "close the two partials" as SSO
entry-condition 3 — that condition is therefore already met; an eventual SSO slice carries only its
own conditions (1 opt-in, 2 identity model, 4 identity mapping, 5 read-only fallback).

---

## 4. Security model (defense in depth)

1. **Mint gate (provider):** `is_entitled("bom_multitable")` + Part-type + read permission; unentitled → no token (no existence leak). Signing key absent/invalid → 503 fail-closed.
2. **Token:** Ed25519/EdDSA, claims `sub/tenant_id/org_id/part_id/feature_key/aud/embed_origin/exp/iat/jti/typ:"embed"`; **part is the `part_id` claim, never a request input**; short TTL capped at 600s; signing **private key never in repo**.
3. **Offline verify (consumer):** signature + `aud` (service audience, separate from `embed_origin`) + `typ` + required finite `exp` + `kid`. Fork B = no `mst_` exchange, no provider callback.
4. **Per-call re-gate:** `feature_key === bom_multitable` (else 403); `embed_origin` ∈ allowlist (else 403); data source is server-config `PLM_EMBED_DATA_SOURCE_ID` (never a request input).
5. **Tenant cross-check (Slice A):** `claims.tenant_id` must equal the **actual served** `x-tenant-id` (read from `connection.headers`, case-insensitive; ambiguous casings → fail-closed). Checked **before** the BOM query → no cross-tenant fetch.
6. **Single-use (Slice B/B1):** atomic `SET key 1 EX ttl NX` on a hashed, scope-bound key (`plm-embed:jti:<sha256(JSON.stringify([aud,feature,tenant,part,jti]))>`) **before** the query. Replay within TTL → 401; shared store unavailable → 503 fail-closed.
7. **Frontend handshake:** allowlist is single-source (`/config`), exact-match, fail-closed on empty; token accepted only from an allowlisted origin **and** (when present) `event.source === window.parent`; **listen-only** (no outbound `'*'`); token only in `X-PLM-Embed-Token` (never URL/logs); 401/403 degrade in-place (`suppressUnauthorizedRedirect`, never a login redirect of the iframe).
8. **CSP frame-ancestors:** computed + exposed via `/config` (fail-closed `'none'`), **applied at the edge layer** (Express doesn't serve the SPA HTML) — documented, not code-enforced.

---

## 5. Verification

Every code slice was gated green before merge (and on CI):
- **Yuantus** (P3-A/B/D1): route-count pins, `ci.yml` change-scope + contract enumeration, entitlement/permission unit tests, the EdDSA mint vector.
- **metasheet2** (P3-C/D2/A/B + re-mint copy): root `type-check` (vue-tsc/tsc), `eslint`, `vitest`; CI `test (18.x/20.x)` + contracts + e2e green at merge (CLEAN state).

Consumer test inventory for the embed line:
- `plm-embed-routes.test.ts` — the relay: whitelist, real-middleware-chain (embed-token-only → 200), feature/origin/exp gates, tenant match/mismatch/absent (incl. an end-to-end real-`PLMAdapter` false-closure case), **jti first-use / replay-401 / unavailable-503 / no-jti-401 + ordering (no consume on a tenant-mismatch 403)**.
- `embed-token-verify.test.ts` — offline EdDSA verify incl. a **real Python-minted cross-language vector**.
- `embed-jti-store.test.ts` — key determinism/scope/no-bare-jti + **delimiter-collision** guard; consume pins exact `SET(key,'1','EX',ttl,'NX')` args (a dropped NX would silently fail-open) via a stateful NX-honoring fake; null-client / throwing-SET both fail closed.
- `plm-adapter-effective-tenant.test.ts` — served-tenant precedence (global/env beat options) + **hand-set header wins** + ambiguous-casing → undefined.
- `plm-embed-bom-review.spec.ts` / `plm-embed-service.spec.ts` — frontend handshake states, origin/source rejection, token-bound (no Part input), `suppressUnauthorizedRedirect`, the re-mint `error` copy regression (`not.toContain('重试')`).

Cross-cutting discipline applied throughout: narrow slices; advisor-reviewed designs; **adversarial
review caught two would-be false-closures before/at build** (tenant compared against a precedence
fallback; a hand-set `x-tenant-id` diverging from the precedence const); explicit-path staging
(never `git add -A`, 0 node_modules committed); fast-main rebase-before-push/merge; no self-merge
(owner-gated, multi-round reviews).

---

## 6. Operational / deployment notes

- **Yuantus env names carry the `YUANTUS_` prefix.** Pydantic `Settings` uses `env_prefix="YUANTUS_"` with `extra="ignore"` (`src/yuantus/config/settings.py`), so an **unprefixed** `EMBED_*` var is **silently dropped** → the signing key reads empty → minting stays fail-closed (503) with no error. Always set the prefixed names: `YUANTUS_EMBED_TOKEN_SIGNING_KEY` (private), `YUANTUS_EMBED_TOKEN_KEY_ID`, `YUANTUS_EMBED_TOKEN_AUDIENCE`, `YUANTUS_EMBED_TOKEN_TTL_SECONDS`, `YUANTUS_EMBED_ALLOWED_ORIGINS` (the names the embed-token tests configure).
- **Origin allowlists (no `'*'`, empty = fail-closed):** Yuantus `YUANTUS_EMBED_ALLOWED_ORIGINS`; metasheet2 `PLM_EMBED_ALLOWED_ORIGINS`. All three checks (postMessage allowlist, `embed_origin` claim, CSP frame-ancestors) key off the same PLM parent origin(s).
- **Keys/audience must pair:** Yuantus `YUANTUS_EMBED_TOKEN_SIGNING_KEY`(private)/`YUANTUS_EMBED_TOKEN_KEY_ID`/`YUANTUS_EMBED_TOKEN_AUDIENCE` ↔ metasheet2 `YUANTUS_EMBED_PUBLIC_KEY`/`YUANTUS_EMBED_KEY_ID`/`PLM_EMBED_AUDIENCE`.
- **Embed data source:** `PLM_EMBED_DATA_SOURCE_ID`; its served tenant must be per-source unambiguous (a per-source tenant currently needs direct/internal `DataSourceConfig` — the data-sources REST schema persists neither `options.tenantId` nor a nested `connection.headers`).
- **Redis (single-use):** `REDIS_URL` required; `getRedisClient()` memoizes a *startup* failure as permanent null → embed path stays fail-closed (503) until restart if Redis is down at the first call; recovers from transient drops after a successful first connect.
- **CSP:** edge layer must apply `frame-ancestors` (value from `/config`); unconfigured → `'none'`.
- **Parent contract:** PLM page mints, injects the iframe, and posts `{type:'plm-embed:token', token}` to the iframe `contentWindow` **after load, with retry** (the embed buffers across `/config` but not before its listener attaches; it never acks). **A transient provider failure spends the single-use token → the parent must re-mint + re-post (reopen/reload), not re-call** — hence the #2408 re-mint copy.

---

## 7. What remains (all explicitly DEFERRED — each needs its own owner opt-in)

The two backend halves are complete (provider mint + the token-bound read-only viewer). The
**PLM parent-page handshake is itself the first item below** — i.e. the integrated in-PLM
click-through is deferred, not shipped. Remaining items are deferred future work, each needing its
own owner opt-in:

- **Token-exchange / SSO (identity-session spine)** — escalates read-only viewing to a session; **higher risk tier**; requires a Fork A (exchange→`mst_`) vs shared-IdP decision + identity mapping. Entry conditions in #737 §5 (now minus the two partials). *Deferred; needs explicit opt-in.*
- **B2 — jti admin-revocation denylist** — beyond B1's TTL-window single-use; only worth it if "revoke an un-expired token" is a real product need (re-introduces an online/stateful dependency). *Deferred.*
- **PLM parent-page handshake** — the PLM UI that mints + injects + posts the token. **PLM-UI-side work, outside both backend repos' current scope.** Spec'd in #737 §3.1, not built. *Deferred.*
- **Per-source tenant via REST/UI** — widen `ConnectionConfigSchema`/`options` so an embed data source's tenant is REST-configurable (today it's direct/internal or the global). *Deferred future extension.*
- **Approval-automation execution engine** — turn the Phase-2 NOTIFY stub into real dispatch/escalation; *moderate risk, separate line.*
- **Phases 4–6 (canonical plan)** — Workbench, Controlled Write-Back (铁律: writes only via `/aml/apply` or `/actions`), Enterprise Hardening. *Future, each gated.*

No item above is a ratified, ready-to-build slice: each is a deferred phase requiring an explicit
opt-in and (for SSO / write-back) a design/scope cut and a risk decision that is the owner's to make.

---

## References (grounding)

- `plm-collaboration-automation-development-plan-20260602.md` — canonical plan
- `plm-collaboration-phase3-bom-multitable-scope-20260605.md` — P3 scope
- `plm-collaboration-phase3d-embed-spine-scope-20260605.md` (#730) — embed scope
- `plm-collaboration-phase3d-embed-spine-closeout-20260606.md` (#737) — closeout / acceptance runbook (this doc closes its §4 ① ②)
- PRs/commits: `zensgit/yuantus-plm` #724/#727/#730/#733/#737; `zensgit/metasheet2` #2324/#2341/#2347/#2356/#2370/#2408
