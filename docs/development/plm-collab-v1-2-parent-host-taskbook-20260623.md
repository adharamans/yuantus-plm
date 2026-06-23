# PLM-Collab V1.2 — In-PLM Embedded BOM Review Parent Host Taskbook

**Date:** 2026-06-23
**Status:** READY-TO-START taskbook, but **runtime implementation is gated** on the
PactFlow real-run and the real staging evidence run. This document locks scope and
acceptance for the next code slice; it does not claim V1.2 is built.

## 0. Start Gates

Do not open the V1.2 runtime implementation until all gates are true:

1. PactFlow is provisioned, `PACT_BROKER_BASE_URL` / `PACT_BROKER_TOKEN` are set in both
   repositories, and the broker run proves:
   - MetaSheet2 publishes `Metasheet2@<sha>` on branch `main`;
   - Yuantus verifies the broker-sourced `Metasheet2` pact and publishes
     `YuantusPLM@<sha>` verification results;
   - `can-i-deploy` returns a real matrix answer;
   - a deliberate drift breaks the advisory broker gate.
2. Staging evidence exists for the V1/V1.1/V2-seats trial baseline:
   - version pair and pact hash;
   - real vendor-signed license import and `yuantus license status`;
   - capability manifest and BOM context;
   - seats set/enforce and explicit `seats:null` clear.
3. The implementation branch starts from fresh `origin/main`; no runtime work is layered
   onto the broker/staging docs branches.

## 1. Code-Grounded Current State

| Surface | Current evidence | V1.2 meaning |
|---|---|---|
| Parent page | `src/yuantus/api/routers/plm_workspace.py` serves `src/yuantus/web/plm_workspace.html` at `GET /api/v1/plm-workspace` and already performs server-side setting replacement via `_SETTING_REPLACEMENTS`. | This is the parent host. Reuse the existing injection seam for any server-computed public config. |
| Mount point | `src/yuantus/web/plm_workspace.html` has `#workspace-bom`, the `BOM Navigator` tab, and legacy `loadBom()`/tree rendering. It does not call `/bom/multitable/{part_id}/context`, mint embed tokens, create an iframe, or use `postMessage`. | Mount the MetaSheet BOM Review affordance inside the existing BOM Navigator surface. |
| Provider mint | `src/yuantus/meta_engine/web/bom_multitable_router.py` exposes `POST /api/v1/bom/multitable/{part_id}/embed-token` with request body `{ "origin": "..." }`. It gates auth -> entitlement -> part -> Part-type -> read permission -> signing-key configured -> origin allowlist -> mint + jti audit. | Parent host calls this route; do not duplicate entitlement logic in the frontend. |
| Token service | `src/yuantus/meta_engine/services/bom_multitable_embed_token_service.py` signs EdDSA JWTs, caps TTL at 600 seconds, carries `aud`, `embed_origin`, `tenant_id`, `org_id`, `part_id`, `feature_key`, `jti`, and `typ:"embed"`. | V1.2 must keep the token out of URL, localStorage, logs, and query strings. |
| Existing settings | `src/yuantus/config/settings.py` has `EMBED_TOKEN_SIGNING_KEY`, `EMBED_TOKEN_KEY_ID`, `EMBED_TOKEN_AUDIENCE`, `EMBED_TOKEN_TTL_SECONDS`, and `EMBED_ALLOWED_ORIGINS`. Pydantic uses the `YUANTUS_` env prefix. | Existing env names remain `YUANTUS_EMBED_*`. |
| Missing setting | There is no MetaSheet iframe URL setting. | Add `Settings.METASHEET_EMBED_URL`, configured by `YUANTUS_METASHEET_EMBED_URL`. |
| Capability manifest | `GET /api/v1/integrations/capabilities` returns advisory `features.bom_multitable.supported` and tenant-scoped `entitled`. | Frontend affordance gating uses this advisory hint plus server-computed embed config readiness; endpoint gates remain authoritative. |
| Bridge flag | `ENABLE_METASHEET` controls whether the MetaSheet bridge route mounts; the capability manifest and `bom_multitable` routes are registered separately. | Do not use `ENABLE_METASHEET` as the V1.2 affordance gate. Gate on capability + entitlement + embed config readiness. |
| Consumer page | MetaSheet2 already has `/plm-embed/bom-review`: listen-only `postMessage` receiver, strict parent-origin allowlist, `X-PLM-Embed-Token` data call, token-bound part, fail-closed 401/403/503 degradation. | Consumer is out of scope for this Yuantus V1.2 parent-host slice, except for the new embed-token pact interaction. |

## 2. V1.2 Scope

### In Scope

1. **One new server setting**
   - Add `Settings.METASHEET_EMBED_URL`.
   - Env name is `YUANTUS_METASHEET_EMBED_URL`.
   - This is the iframe `src` for the MetaSheet embed page, expected to point at
     MetaSheet2 `/plm-embed/bom-review`.

2. **Single-source origin derivation**
   - Derive the embed-token `origin` from `YUANTUS_METASHEET_EMBED_URL` with a real URL
     parser. In browser code that is `new URL(metasheetEmbedUrl).origin`; in Python use
     the standard URL parser rather than string slicing.
   - Do not add a second `METASHEET_EMBED_ORIGIN` setting.
   - Add a boot-time or request-time fail-closed validation that the derived origin is
     present in `YUANTUS_EMBED_ALLOWED_ORIGINS`. The mint route already enforces the
     allowlist; this validation makes deployment misconfiguration visible before the
     first user click.

3. **Explicit parent affordance gating**
   - Gate the visible "BOM Review (MetaSheet)" affordance on:
     - capability manifest `features.bom_multitable.supported === true`;
     - capability manifest `features.bom_multitable.entitled === true`;
     - server-computed embed config readiness.
   - The frontend must not infer signing-key or allowlist completeness from public
     settings. Preferred shape: inject a boolean such as `embedConfigured` via
     `plm_workspace.py` using the existing `_SETTING_REPLACEMENTS` pattern.
   - Still handle mint failures reactively:
     - 503 -> "BOM Review unavailable";
     - 403 -> "BOM Review origin not allowed";
     - unentitled `embed_token:null` -> upgrade/unavailable state, not an iframe.

4. **Parent host flow**
   - In `#workspace-bom`, add a "BOM Review (MetaSheet)" affordance for the selected Part.
   - On open / re-authorize:
     1. fetch capability manifest;
     2. if supported + entitled + configured, call
        `POST /api/v1/bom/multitable/{part_id}/embed-token` with
        `{ "origin": derivedOrigin }`;
     3. create or refresh the iframe with `src = YUANTUS_METASHEET_EMBED_URL`;
     4. after iframe load, call
        `iframe.contentWindow.postMessage({ type: "plm-embed:token", token }, derivedOrigin)`.
   - Keep the token out of URL, localStorage, DOM text, console logs, and analytics.

5. **Parent-controlled re-authorize**
   - The MetaSheet iframe is listen-only and does not ack failures to the parent.
   - Replay/expiry degrades inside the iframe; the parent cannot auto-detect it.
   - Provide a parent-side "Re-authorize" or reload path that mints a fresh token and
     re-posts it. Do not build an ack-loop.

6. **Embed-token pact interaction**
   - Add the V1.2 interaction to the cross-repo pact after broker Phase A is real-run
     proven.
   - The token and `jti` are non-deterministic; use matchers/regex for shape, never
     exact token values.
   - Seed `YUANTUS_EMBED_TOKEN_SIGNING_KEY` / `YUANTUS_EMBED_TOKEN_KEY_ID` in the provider
     verifier, following `test_bom_multitable_embed_token.py`.

### Out Of Scope

- MetaSheet2 consumer page changes; P3-D2 is already built.
- SSO and PLM identity federation.
- Write-back from MetaSheet to PLM.
- Approval automation execution.
- Multi-`kid` rotation and jti revocation denylist.
- V2 commercial issuance, seats UX, billing, or per-SKU assignment.

## 3. Implementation Checklist

### Slice V1.2-A — Server Config And Injection

- [ ] Add `METASHEET_EMBED_URL` to `Settings`.
- [ ] Add tests that the env name is `YUANTUS_METASHEET_EMBED_URL`.
- [ ] Derive `metasheetEmbedOrigin` from `METASHEET_EMBED_URL` with a URL parser.
- [ ] Validate derived origin against `EMBED_ALLOWED_ORIGINS`.
- [ ] Inject public parent config into `plm_workspace.html`, including:
  - [ ] `metasheetEmbedUrl`;
  - [ ] `metasheetEmbedOrigin`;
  - [ ] `embedConfigured`.
- [ ] Add `plm_workspace` router tests for configured, missing URL, invalid URL, and
  origin-not-allowlisted cases.

### Slice V1.2-B — Parent UI Host

- [ ] Add a compact "BOM Review (MetaSheet)" affordance inside the existing BOM Navigator
  tab.
- [ ] Fetch the capability manifest with the existing auth/tenant/org header helpers.
- [ ] Use only the selected Part id already held by the workspace state.
- [ ] Mint via `POST /api/v1/bom/multitable/{part_id}/embed-token`.
- [ ] Mount iframe only after supported + entitled + configured + token returned.
- [ ] `postMessage` only to the derived exact origin.
- [ ] Add parent-controlled "Re-authorize" flow.
- [ ] Render visible degradation for unsupported, unentitled, 403, 503, and network error.
- [ ] Add Playwright coverage for:
  - [ ] affordance hidden or disabled when not configured;
  - [ ] capability unentitled path;
  - [ ] successful mint -> iframe -> exact-origin postMessage;
  - [ ] 403/503 visible degradation;
  - [ ] re-authorize mints again and re-posts.

### Slice V1.2-C — Pact

- [ ] Extend the consumer pact with the embed-token interaction.
- [ ] Publish through the broker, not by manual-only sync.
- [ ] Verify Yuantus provider via broker and committed fallback.
- [ ] Confirm a drifted embed-token envelope breaks the broker verification.

## 4. Acceptance Gates

V1.2 is not complete until all of these are true:

- [ ] PactFlow Phase A is active, and the embed-token interaction is broker-verified.
- [ ] `YUANTUS_METASHEET_EMBED_URL` is configured in staging and its derived origin is in
  `YUANTUS_EMBED_ALLOWED_ORIGINS`.
- [ ] A real browser run opens `/api/v1/plm-workspace`, selects a Part, opens BOM Review,
  mints an embed token, loads MetaSheet2 `/plm-embed/bom-review`, and posts the token to
  the exact derived origin.
- [ ] The token is absent from URL, localStorage, visible DOM text, logs, and screenshots.
- [ ] Replay/expiry is handled by parent re-authorize, not automatic ack-loop.
- [ ] Unsupported/unentitled/config-missing/origin-denied/mint-unavailable paths degrade
  visibly without leaking part existence.
- [ ] No V2 commercial scope is included.

## 5. Verification Commands For The Code Slice

When the runtime slice is opened, run at minimum:

```bash
python -m pytest \
  src/yuantus/api/tests/test_plm_workspace_router.py \
  src/yuantus/meta_engine/tests/test_bom_multitable_embed_token.py \
  src/yuantus/meta_engine/tests/test_integration_capabilities.py \
  src/yuantus/api/tests/test_pact_provider_yuantus_plm.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_pact_provider_gate.py

npx playwright test playwright/tests/plm_workspace_*.spec.js --reporter=line
```

Add any new tests to the explicit CI lists that Yuantus uses; do not rely on broad glob
collection for new coverage.

## 6. Open Questions To Resolve At Implementation Time

1. Should `embedConfigured` hide the affordance completely, or show a disabled control with
   unavailable copy? Either is acceptable; the implementation must be explicit.
2. Should the iframe be recreated on every re-authorize, or can the parent re-post to an
   existing frame after reload? Pick the simplest behavior the browser smoke can prove.
3. What staging MetaSheet2 URL is canonical for `YUANTUS_METASHEET_EMBED_URL`? It should be
   a full `/plm-embed/bom-review` URL, not merely an origin.

## 7. Deferred Commercial Scope

Keep V2 behind separate owner opt-in:

- vendor-private issuance tooling;
- admin billing/license UX;
- per-SKU assignment or consumer-side seat reconciliation;
- multi-`kid` rotation;
- SSO;
- write-back.
