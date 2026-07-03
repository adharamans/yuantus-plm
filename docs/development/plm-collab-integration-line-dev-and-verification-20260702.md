# PLM × MetaSheet integration line — development & verification record (objective completion)

Objective-level dev & verification record for the governed BOM multi-table **read + write-back +
embed + optimistic-concurrency** integration between YuantusPLM (provider) and MetaSheet2 (consumer).
Status as of 2026-07-02: **COMPLETE & DELIVERED** — every buildable, ungated surface is shipped and
merged on both sides. The living status board is
`docs/development/plm-collab-integration-line-living-tracker.md`; this file is the point-in-time
completion record.

## 1. What was built (surfaces delivered)

| Surface | Side | Delivered | Verification |
|---|---|---|---|
| BOM multi-table read projection (P3-A/B) | Yuantus | merged | provider contract + route pins |
| Governed write-back (Phase-7) + guard ladder + per-tenant idempotency + audit | Yuantus | merged (incl. #928) | write-back suites; deep-audit #935 |
| Embed-token mint (Ed25519, TTL≤600, jti-audit) + multi-`kid` verify | Yuantus + ms2 | merged (#3395) | embed suites |
| Consumer review UI + workbench write-back relay | ms2 | merged | panel + relay-route specs |
| **Consumer `If-Match` optimistic concurrency (same-cell lost-update fix)** | ms2 | **merged `f372cd1fc` (#3469)** | see §2 |

## 2. Verification — the closing slice (`If-Match`, #3469)

The last correctness gap: the provider emitted a per-line `write_etag` and returned 412 on a stale
`If-Match`, but the consumer never sent it, so a same-cell concurrent write-back silently dropped the
loser's edit and reported success. Closed end-to-end (error-and-reload) and verified:

- **Automated tests:** core-backend **52** + web **19** pass on the rebased-to-main tip; `tsc` +
  `vue-tsc` clean; no new dependency. Includes the strong-ETag **byte round-trip** (quoted etag
  reaches `If-Match` verbatim), the backward-safe omit (no etag → no header → prior behavior), and the
  **412 → reload + fresh-key + no-false-success** flow.
- **Adversarial verification (3 independent lanes, refute-by-default) — all clean:** ETag
  byte-integrity (11 scratch tests through the real adapter/router/axios), regression/backward-safety
  (read guard tolerant of a missing `write_etag` but rejects a wrong-typed one; only callers are the
  relay + read-only embed), concurrency-correctness (412 reload drops the retry key → fresh
  `Idempotency-Key`, never a cached-original replay).
- Provider side independently deep-audited (#935); consumer pact green.
- Full feature record: `metasheet2:docs/development/plm-writeback-if-match-dev-and-verification-20260702.md`.

## 3. Delivery status

All surfaces **merged on both provider and consumer** (`#3469` merged `f372cd1fc`, 2026-07-02).
Nothing on this objective is pending build or merge.

## 4. Explicitly out of scope of this objective

- **Method execution sandbox (#944/#945/#946)** — a *separate* P0 capability line, reviewed on its own
  (see the security review: escape containment solid; one HIGH caller-thread `_MethodTimeout` leak on a
  C-op timeout to fix). Not part of this integration objective.
- **Decision-gated tracks** — locked-BOM ECO revision route, Phase-6 SSO, commercial ops — each needs
  its own owner opt-in (tracker §3).
- **Ops activation (T9)** — env/config, not development.
