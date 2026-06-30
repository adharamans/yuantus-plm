# PLM x MetaSheet Remaining Gated Development Order and Verification (2026-06-30)

Type: design and verification record for the remaining gated items after
`plm-collab-plan-completion-and-verification-20260630.md`.

This record does two things:

1. Orders the remaining work by dependency, risk, and whether it can be built
   without owner / ops / product input.
2. Records the one remaining low-risk buildable commercial-hardening slice that
   was safe to complete now: MetaSheet2 multi-`kid` embed public-key verification.

It does not pretend that environment-bound or governance-bound work is complete.

## 1. Starting Point

Current delivered baseline:

- Yuantus `origin/main` at `36132cee` (`docs(plm-collab): close out plan completion verification (#923)`).
- MetaSheet2 review baseline at `e3c2e21d5` (`test(plm): prove writeback retry key reuse (#3392)`); this pass completed at `b71d8f097` (`feat(plm): support multi-kid embed public keys (#3395)`).

The plan closeout already completed the unowned buildable remainder:

- T1 consumer write-back relay + edit UI.
- T2 cross-line idempotency regression.
- T6 `If-Match` / ETag hardening.
- T7 date-obsolete export / filtering and lifecycle forensic summary / list / export.
- MetaSheet2 retry-key false-green test hardening.

## 2. Development Order for the Remaining Items

The order below is the recommended execution order. It keeps reversible,
compatibility-improving work before high-risk governance changes, and keeps
environment-only work out of code PRs until the environment exists.

| Order | Track | Status after this pass | Why this order |
|---|---|---|---|
| 1 | Commercial hardening: multi-`kid` embed verification | Built in MetaSheet2 #3395 | Small, low-risk, removes key-rotation flag-day risk, and needs no Yuantus provider change because tokens already carry `kid`. |
| 2 | Ops activation proof: alerting, owned-HTTPS V1.2 rerun, deploy-environment gate | Environment-gated | Proves production posture without changing product semantics. Needs secrets/domains/deploy pipeline. |
| 3 | Phase 7 locked-BOM ECO revision route | Product/governance design gate | Changes write semantics for released/locked BOMs. Must be design-first and owner-ratified before code. |
| 4 | Date-obsolete revert | Governance design gate | Mutates acknowledged / worker-derived state. Needs explicit revert semantics and audit model before code. |
| 5 | Phase 6 SSO / identity-session / bridge activation | Deferred-by-default | Build only if bridge activation or continuous in-iframe UX becomes the next product line. Write-back no longer triggers Phase 6. |
| 6 | Broader commercial operations | Owner-prioritized larger track | Vendor-private issuance, key custody, admin UX, B2 per-SKU seats, and compatibility gates need product / ops decisions. |

## 3. Slice Completed Now: MetaSheet2 Multi-`kid`

### Design

MetaSheet2 previously accepted exactly one Yuantus embed public key:

- `YUANTUS_EMBED_PUBLIC_KEY`
- `YUANTUS_EMBED_KEY_ID`

That shape works for a pilot, but it creates a commercial rotation flag day: the
consumer can verify either the old key or the new key, not both.

The completed design adds:

- `YUANTUS_EMBED_PUBLIC_KEYS`: a JSON object of `kid -> base64 raw Ed25519 public key`.
- The JSON map is preferred when present.
- The legacy single-key variables remain a compatibility fallback when the map is unset.
- Malformed JSON or an empty usable map fails closed as "embed verification not configured" rather than falling back silently to a stale key.
- The production readiness runbook now documents rotation windows: keep both old and new `kid` entries until the provider has stopped minting the old `kid` and old tokens have expired.

The security invariant is unchanged: MetaSheet2 receives public keys only. No
Yuantus private signing key is configured in MetaSheet2.

### Implementation

MetaSheet2 #3395 changes:

- `packages/core-backend/src/auth/embed-config.ts`
  - Parses `YUANTUS_EMBED_PUBLIC_KEYS` as a fail-closed JSON key map.
  - Keeps `YUANTUS_EMBED_PUBLIC_KEY` / `YUANTUS_EMBED_KEY_ID` as fallback.
- `packages/core-backend/tests/unit/embed-config.test.ts`
  - Covers legacy fallback, default legacy `kid`, multi-key JSON map, trimming and invalid-entry dropping, map precedence, and malformed-map fail-closed behavior.
- `packages/core-backend/tests/unit/plm-embed-routes.test.ts`
  - Adds a real route-chain test where a token signed with `kid=embed-new` verifies through `YUANTUS_EMBED_PUBLIC_KEYS`.
  - Adds a malformed-map route-chain test that returns `503`, not a server error.
- `docs/operations/plm-bom-multitable-production-readiness-runbook-20260609.md`
  - Documents the preferred multi-key map, legacy fallback, troubleshooting, and readiness checklist.

### Verification

Local verification before PR:

- `pnpm --filter @metasheet/core-backend exec vitest run tests/unit/embed-config.test.ts tests/unit/embed-token-verify.test.ts tests/unit/plm-embed-routes.test.ts`
  - 3 test files passed.
  - 41 tests passed.
- `pnpm --filter @metasheet/core-backend run type-check`
  - `tsc --noEmit` passed.
- `git diff --check` passed.

GitHub verification:

- MetaSheet2 #3395 merged at `b71d8f097`.
- CI passed: DingTalk P4 ops regression gate, K3 WISE offline PoC, after-sales integration, contracts dashboard / openapi / strict, core-backend-cache, e2e, migration-replay, pr-validate, telemetry-plugin, coverage, and test matrices for Node 18.x and 20.x.
- Strict E2E was skipped as designed for this change.

## 4. Remaining Gates After Multi-`kid`

These are deliberately still gates, not hidden implementation leftovers.

| Track | Gate to clear before code |
|---|---|
| Alerting activation | Ops sets `ALERT_WEBHOOK_URL`, then runs a controlled failure to prove the webhook fires. |
| Owned-HTTPS V1.2 rerun | Ops provides owned HTTPS origins and runs the existing V1.2 staging instrument, including the expiry -> degrade -> re-authorize strengthen item. |
| Deploy-environment gate | A deploy pipeline must call PactFlow `record-deployment`; only then does `can-i-deploy --to-environment` become meaningful. |
| Consumer-side can-i-deploy | Needs provider-verification webhook so MetaSheet2 PRs can receive fresh Yuantus provider verification instead of racing an unknown matrix. |
| Locked-BOM ECO revision route | Owner ratifies whether released/locked BOM edits create ECO revision intents, how apply is authorized, and how the UI distinguishes draft fast-path vs ECO path. |
| Date-obsolete revert | Owner ratifies whether revert means reopen impact only, undo acknowledge only, undo child obsolete promotion, or create a superseding correction event. |
| Phase 6 session / bridge | Owner explicitly chooses bridge activation / continuous UX as the next product line; otherwise the one-shot embed handshake remains sufficient. |
| Vendor-private issuance / key custody / admin UX / B2 seats | Commercial owner chooses deployment and support model. These are outside a clean in-repo default. |

## 5. Outcome

After this pass:

- The remaining work is ordered and ready for owner / ops decisions.
- The only low-risk, unowned, buildable commercial-hardening slice was completed:
  multi-`kid` embed public-key verification in MetaSheet2.
- The rest remains explicitly gated because building it would otherwise choose
  governance, infrastructure, or commercial policy on the owner's behalf.

No read-only embed invariant was relaxed, no write path was added to the iframe,
and no private key material is introduced into MetaSheet2.
