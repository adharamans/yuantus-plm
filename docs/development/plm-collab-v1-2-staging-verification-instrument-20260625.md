# V1.2 in-PLM embedded BOM-review — staging verification instrument

**Date:** 2026-06-25
**Scope:** verify the **real-iframe** end-to-end (PLM parent host → MetaSheet2 embed) in a **staging**
environment. This instrument is for whoever runs the real staging env. It deliberately separates what is
**already verified locally** (code + provider tests — do *not* re-verify) from what **requires the live
cross-origin env** (the empirical run), each as a concrete pass/fail observable.

Why an instrument and not a recorded run: a localhost rig with two ports and a self-generated keypair is
**not** staging — exact-origin enforcement and real cross-origin iframe/token behavior are only
representative across genuinely different deployed origins. So the empirical half is intentionally left to
a real env, with everything code-settleable verified up front.

## 0. Already verified locally — do NOT re-verify in staging

- **Provider mint endpoint** — `test_bom_multitable_embed_token.py` (24 passed with the router tests):
  unauthenticated → 401; unentitled → no token, part NEVER queried, permission NEVER checked
  (fail-closed); invalid signing key → 503 + zero audit; **TTL capped (≤600s)**; cross-origin → 403;
  wildcard `*` NEVER honored; success → verifiable token (`aud` == service audience, `embed_origin` ==
  requested origin) + a jti-trackable AuditLog row (never the token value).
- **Parent-host config-gate** — `test_plm_workspace_router.py`: `embed_configured` requires full config
  **and** the derived origin ∈ allowlist; 4 cases (configured / url-missing / url-invalid /
  origin-not-allowlisted) — the affordance hides when not configured.
- **Origin derivation** — server-side `_derive_origin(METASHEET_EMBED_URL)` → `{scheme}://{netloc}`
  (single source; no separate origin config that could drift).
- **Code-verified parent-host properties** — exact-origin `postMessage({type:'plm-embed:token', token},
  origin)` (not `*`); token NOT in the iframe `src` (plain embed URL, no `?token=`); token NOT logged
  (status *messages* only, never the value); 403/503 → `renderMetasheetReviewUnavailable`; re-authorize
  is **user-initiated** (button + "Use Re-authorize after expiry or replay").
- **Consumer side** — metasheet2 `/plm-embed/bom-review`: listen-only; accepts the token only from a
  strict-allowlisted + pinned parent origin; calls `/api/plm-embed/bom-review/context` with
  `X-PLM-Embed-Token` (part bound to the token claim, no part input); 401/403/503 → fail-closed; the
  embed-token interaction is pinned in the pact (#868).

## 1. Environment prerequisites

- **Two genuinely different deployed origins** (not `localhost:A` vs `localhost:B`): the PLM host
  (e.g. `https://plm-staging.example`) and the MetaSheet2 embed host
  (e.g. `https://metasheet-staging.example`).
- **Ed25519 keypair split:** Yuantus holds `YUANTUS_EMBED_TOKEN_SIGNING_KEY` (private) +
  `YUANTUS_EMBED_TOKEN_KEY_ID`; MetaSheet2 holds the matching **public** key only.
- Yuantus: `YUANTUS_METASHEET_EMBED_URL=https://metasheet-staging.example/plm-embed/bom-review`,
  `YUANTUS_EMBED_ALLOWED_ORIGINS=https://metasheet-staging.example`,
  `YUANTUS_EMBED_TOKEN_AUDIENCE=<service aud>`, `YUANTUS_ENABLE_METASHEET=true`, and the
  `bom_multitable` entitlement active for the pilot tenant.
- MetaSheet2: its `/api/plm-embed/config` parent-origin allowlist includes `https://plm-staging.example`.
- An **entitled pilot tenant** + a **Part with a BOM**.

## 2. The checks — each a concrete observable

| # | Check | How to observe | Pass |
|---|---|---|---|
| 1 | Entitlement / config gate | Open `/api/v1/plm-workspace` as the entitled tenant → BOM Navigator shows "BOM Review (MetaSheet)". As an unentitled tenant (or embed unconfigured) → affordance hidden / "unavailable". | entitled+configured → present; else hidden/unavailable |
| 2 | Real iframe opens | Select the Part, click BOM Review → an iframe loads the embed URL. | iframe `src` is the embed URL, **no `?token=`** |
| 3 | Exact-origin handoff | DevTools → parent `postMessage` targetOrigin is the exact MetaSheet2 origin (not `*`); MetaSheet2 accepts (it pins the parent origin); context renders. | targetOrigin exact; review renders |
| 4 | Token not in URL | Inspect iframe URL + address bar. | no token in any URL |
| 5 | Token not in storage | DevTools → Application → Local/Session Storage, **both** origins. | no embed-token value stored (a session bearer under a workspace key is expected; the embed JWT is not) |
| 6 | Token not in logs / request URLs | DevTools → Console + Network. | no embed-token value in console; token only in the mint POST response body, never a URL |
| 7 | **Token not in screenshot (manual)** | Take the screenshot a pilot would; inspect it. | no token value anywhere on screen — *not assertable by code; manual devtools/inspection step* |
| 8 | Expiry / replay → re-authorize | Wait past the TTL (≤600s) or force a replay → the iframe degrades internally (listen-only, no ack to parent). Click **Re-authorize**. | a fresh token mints + posts; review re-renders. Parent does **not** auto-detect expiry — re-authorize is user-initiated, by design |
| 9 | 403 degradation | Request a non-allowlisted origin (or misconfigure the allowlist) → mint 403. | parent shows "origin is not allowed"; no iframe, no crash |
| 10 | 503 degradation | Break/remove the signing key → mint 503. | parent shows the unavailable message; no iframe, no crash |

## 3. Evidence to record

Same discipline as the seats staging evidence collector: record the two origins + the version pair
(Yuantus + MetaSheet2 commits), and per check a pass/fail + the observation. **Never paste token values,
private/public keys, or bearer tokens into the evidence.** The token-not-in-screenshot check (#7) is
manual — record the inspection explicitly. Mark anything not run.

## 4. What this does NOT cover

- The provider mint internals (already exhaustively unit-tested — §0).
- A localhost rig is **not** a substitute for §2 — those checks are only representative across genuinely
  different deployed origins.
- The consumer repo (metasheet2) is owner-gated; its embed page is code-mapped + pact-pinned (#868) but
  not re-run here.
