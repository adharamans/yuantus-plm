# PLM × MetaSheet integration line — LIVING tracker

> **This is the single living source of truth for the integration line. UPDATE IT IN PLACE.**
> Do not spawn a new dated snapshot doc per slice (that proliferation — #930/#935/#3442… — is what
> made "how much is left?" a recurring survey). **Claim a surface in §2 before building it** — one
> owner/branch per file — so two parallel agents never rebuild the same thing (that is what cost us
> the #3434 duplicate). Last reconciled against Yuantus `origin/main` `8959d844` (2026-07-02) and
> MetaSheet2 `main` after `#3469` (`f372cd1f`).

## 1. Status at a glance

The core feature — governed BOM multi-table **read projection + write-back + embed** — is **shipped
and live end-to-end**. The provider (Yuantus) side is **assessed complete by the #935 deep-audit**,
final-reconciled after the consumer `If-Match` same-cell lost-update fix shipped on MetaSheet2 main
(`#3469`, `f372cd1f`) and the date-obsolete DP1 light path shipped on Yuantus main (`#934`,
`423a59a5`). There is no unowned, buildable-now correctness gap left; remaining motion is a
**decision** (owner/product/governance) or an **ops/env** task.

## 2. Surfaces (the claim board)

| Surface | Repo | State | Owner / branch (claim) | Gate / next |
|---|---|---|---|---|
| Read projection (P3-A/B) | Yuantus | ✅ SHIPPED | — | — |
| Governed write-back (Phase-7) + guard ladder + audit (#928) | Yuantus | ✅ SHIPPED | — | — |
| Embed-token mint (P3-D1, Ed25519, TTL≤600, jti-audit) | Yuantus | ✅ SHIPPED | — | — |
| Consumer review UI + workbench write-back relay | MetaSheet2 | ✅ SHIPPED | — | — |
| Multi-`kid` embed verify | MetaSheet2 | ✅ SHIPPED (#3395) | — | — |
| **Consumer `If-Match` (same-cell lost-update fix)** | MetaSheet2 | ✅ SHIPPED (#3469, `f372cd1f`) | — | — |
| Date-obsolete DP1 light-path revert | Yuantus | ✅ SHIPPED (#934, `423a59a5`) | — | — |
| Locked-BOM ECO revision route — **Phase 0 (contract-first)** | Yuantus + ms2 | 🔨 OPEN (Phase 0 opted in by owner 2026-07-02; direction #933: A3/B1/C2) | claude / `docs/eco-phase0-contract-taskbook` (+ ms2-side pact claim when the interaction is authored) | Phase 0 must lock **in one pass**: the discriminated-409 shape (`lifecycle_locked`/`eco_required` vs `idempotency_conflict`); whether `line.state`/`eco_id` enter the contract; feature_key/SKU (reuse `bom_multitable_writeback` vs a new ECO-scoped key) — the §7 open points. Phase 0 does **NOT** wire `EcoPermissionAdapter` (that is Phase 2, separately gated, repo-wide authz). Phases 1–4 remain per-phase opt-ins. |
| Date-obsolete DP1-iii (child-lifecycle undo) + broader revert | Yuantus | ⛔ GATED | — | governance semantics |
| Phase-6 SSO → session → bridge | Yuantus + ms2 | ⛔ DEFERRED | — | owner: is continuous in-iframe UX the next product line? |
| Commercial ops (issuance CLI / key custody / seats / admin UX) | Yuantus | ⛔ GATED | — | commercial/owner |
| Embed `jti` revocation denylist | ms2 (verify-side) | ⛔ DEFERRED | — | TTL≤600 caps exposure today |
| Ops activation (alert webhook / owned-HTTPS V1.2 rerun / deploy `record-deployment` + consumer `can-i-deploy`) | env | 🔧 OPS (no code) | ops | secrets / domain / pipeline |

## 3. Batched decision sheet (answer these once, not per-slice)

Each **defer** keeps scope tiny; each **open** unblocks its §2 track for build.

1. **Locked-BOM ECO route** — **ANSWERED 2026-07-02: open now, Phase 0 (contract-first) only**; Phases 1–4 stay per-phase opt-ins (see §2 claim).
2. **Phase-6 SSO/bridge** — **ANSWERED 2026-07-02: stays deferred** (re-affirmed; revisit when the gap report's P1 enterprise-SSO item opens — reuse `plm-collaboration-phase6-sso-identity-session-spine-design-20260627.md`).
3. **Commercial** — **ANSWERED 2026-07-02: stays owner-timed** (no subset opened).
4. **Date-obsolete** — **ANSWERED 2026-07-02: DP1 light path is enough for now; DP1-iii stays gated.**

## 4. Faster-delivery operating rules

The bottleneck is **coordination + merge mechanics + decision latency**, not build speed. So:

1. **Claim before build** (§2) — never two agents on one surface (cost: the #3434 duplicate).
2. **Merge queue on metasheet2 main** — deletes the `BEHIND`-rebase treadmill for every PR (one-time repo setting).
3. **Green band** — owner pre-authorizes build+merge of READY, low-risk consumer PRs (green CI + no schema/migration/pact/contract change + no new product-UX decision) *without* a per-PR ask; stop only for the genuine product/governance/security calls in §3.
4. **Ceremony to risk** — design-lock + adversarial probe *only* for security/authz boundaries; small correctness plumbing is just build+test+PR.
5. **Loose cross-repo contracts** — broker-pact only the genuinely shared contract; cover consumer-only concerns with consumer-side tests (keeps a consumer change from reddening the provider gate).

## 5. Immediate action

No unowned, buildable-now correctness item remains on this line. Ops can proceed independently on
the §2 env row. **Next buildable item (opened 2026-07-02): locked-BOM ECO route Phase 0
contract-first taskbook** (claimed in §2). The new P0 capability line (notification outbox,
sandbox 8a/8b, …) is tracked separately in `plm-p0-capability-line-living-tracker.md`.
