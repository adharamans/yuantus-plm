# PLM Collaboration Phase 7 — Governed Write-Back **Provider Endpoint** (Day-1 Taskbook / Decision Surface)

- Date: 2026-06-28
- Type: **Day-1 taskbook / gap audit + decision surface. Authorizes NO build.** Phasing: design → review gate → build.
- Line: the **YuantusPLM (provider)** governed write endpoint for the MetaSheet BOM-review write-back (Phase 7 Slice 2/3), satisfying the consumer contract landed by **metasheet2 #3332**.

## 0. What's already settled (do NOT re-decide)
- **Fork 1 = ECO change-control seam** (owner: "go ECO").
- **Fork 2 (ratified)** = write `feature_key` `bom_multitable_writeback` → app/SKU `plm.bom_multitable_writeback`; permission `"Part BOM"` / `AMLAction.update`.
- **Fork 3 / pact-first = DONE consumer-side via metasheet2 #3332.** That contract is the **source of truth**; do **NOT** revive the closed/superseded #3331 `writeBackBomMultitableLine`.
- **§0 invariant (#884):** a write uses its own **write-scoped** token (never the read embed token); "write-back" = entering **governed change control**, not mutating a row.

## 1. The contract this endpoint must satisfy (from #3332 — source of truth)
- `PATCH /api/v1/bom/multitable/{partId}/lines/{bomLineId}`; body **whitelist** `{quantity?, uom?, find_num?, refdes?}` (`null` = clear-cell, preserved; unknown keys dropped; empty body rejected — all consumer-side in #3332); success → `BomMultitableLineUpdateResult` (`{ok, bom_line_id, …}`).
- **Pact-wire auth = adapter creds + `x-tenant-id`** (the embed token is **consumer-internal**, terminates at the metasheet2 relay; it is NOT on this provider wire). So provider enforcement = `is_entitled` + `check_permission` + the chosen governed seam.
- ⚠️ #3332's consumer providerState currently documents the **read** license `plm.bom_multitable`; this slice tightens the **provider-enforced** entitlement to the **write-scoped** `plm.bom_multitable_writeback` (Fork 2) and syncs the interaction into Yuantus (see §4).

## 2. Current code facts (grounded)
- **ECO apply:** `POST /api/v1/eco/{eco_id}/apply` → `eco_service.action_apply`; gates re-enforced **in the service** (permission via `PermissionManager`; ECO **APPROVED** state; diagnostics; version-lock; rebase). `force=` only bypasses the router-level pre-check; the service still enforces APPROVED/permission/version-lock.
- **Entitlement:** `EntitlementService.is_entitled(feature_key)` **raises `ValueError` if the key is not in `FEATURE_APP_NAMES`** → Day-2 must register `bom_multitable_writeback` → `{plm.bom_multitable_writeback}` first. Unentitled-**write** precedent = HTTP **403** affordance (`approval_automation_eco_router`), not the read path's 200-affordance.
- **Permission (two 403 shapes):** the ECO path uses `PermissionManager.check_permission` (raises → 403 `{detail:{code:"PERMISSION_DENIED",…}}`); the BOM router uses `MetaPermissionService` (bool → router raises 403 `{detail:"Permission denied"}`). The chosen seam fixes which 403 body the pact asserts.
- **Write target:** `bom_multitable_projection_service` "Part BOM" relationship-Item fields (`quantity/uom/find_num/refdes`), addressed by the stable `bom_line_id`; per-line provenance (`source_version`/`source_updated_at`) is available for optimistic concurrency.
- **Replay/single-use:** **NO provider-side consumed-jti / replay guard exists today** (the shipped single-use is consumer-side + read-only) → a NEW provider build.
- **Audit:** generic middleware `AuditLog` + the **helpdesk `event_id`-in-job-payload idempotency** precedent; a write-back **domain** audit + idempotency key is NEW.
- **Pact verifier:** `test_pact_provider_yuantus_plm.py` is a **STRICT, no-pending** CI gate (+ an anti-defang meta-gate `test_ci_contracts_pact_provider_gate.py`); provider-states are a **no-op** (fixtures pre-seeded). A committed interaction whose endpoint is absent → **RED**. **Confirmed:** Yuantus `origin/main` committed pact does **NOT** yet contain the write-back interaction, so main is green today.

## 3. Decision surface (forks — owner resolves at the review gate; do NOT pre-decide)

### Fork P1 (central) — ECO routing model vs the **synchronous-PATCH ⟷ APPROVED-ECO impedance mismatch**
#3332's contract is a **synchronous** `PATCH … → {ok:true}`. ECO change-control (Fork 1) requires an **APPROVED** ECO before apply — multi-step, human-approved, effectively async. These do **not** trivially compose. Options:
- **(a) auto-create + auto-apply a one-line ECO per edit** — but `action_apply` requires APPROVED; auto-approving defeats governance, and without it the PATCH cannot return `ok:true` synchronously (contract break).
- **(b) a purpose-built governed-edit endpoint** that enforces the **same** guards ECO apply does (lifecycle/state, permission, version-lock, transactional, domain audit) **without** the full approval ceremony — i.e. Fork-1**(B)** re-surfacing precisely because the synchronous contract may be incompatible with full ECO approval.
- **(c) make the contract async** (`202` + status) — but #3332 already shipped synchronous `{ok:true}`; re-opening the consumer contract is costly.
**Decider:** owner. Does an embed BOM line edit require a **full APPROVED ECO** (⇒ the contract must go async, re-opening #3332), or a **lifecycle-guarded synchronous governed edit** (Fork-1 effectively B despite "go ECO")? **This tension is the heart of the slice — named here, not resolved.**

### Fork P2 — provider-side single-use / replay mechanism
None exists. Options: a **Redis** consumed-jti (same infra as the preview/OCR queues) vs a **DB** table. **Decider:** owner + infra (Redis availability).

### Fork P3 — audit shape + idempotency key
A write-back **domain `AuditLog`** + an `event_id`-style idempotency key (job-payload-style, as helpdesk does) so a retried relay/re-mint does not double-apply. **Decider:** owner (reuse the helpdesk pattern vs a new domain-audit record).

## 4. Pact sync + verifier (CI-critical — grounded)
Because the verifier is **strict (no pending)** and Yuantus's committed pact does **not** yet contain the write-back interaction, the sync **must land together with the working endpoint** in Day-2:
- Day-2 syncs #3332's interaction into `contracts/pacts/metasheet2-yuantus-plm.json`, **tightens the providerState to `plm.bom_multitable_writeback`** (Fork 2), extends `test_pact_provider_yuantus_plm.py` (seed fixtures for the entitled/permitted/correct-tenant write scenario), and adds the **negatives** (§5).
- **Do NOT** sync ahead of the endpoint (turns the strict gate RED). **Do NOT** add a pending mechanism (fights the anti-defang meta-gate).

## 5. Acceptance criteria (mirror #884 §5; reviewable now)
| Exit criterion | How the endpoint satisfies it |
|---|---|
| Read token cannot write | write requires a write-scoped token; a `typ:"embed"` read token → rejected (consumer-internal + provider scope check) |
| Unentitled → no write | `is_entitled("bom_multitable_writeback")` → 403, nothing mutated |
| Unpermitted → no write | `check_permission("Part BOM", update)` before mutation |
| **Wrong lifecycle → rejected** | the seam **explicitly** enforces a change-control/lifecycle guard (e.g. Released part → rejected) — verified, not assumed |
| Cross-tenant → rejected | served-tenant cross-check (token tenant ≠ served tenant → 403, pre-mutation) |
| Single-use / replay → unusable | **provider-side** consumed-jti/replay guard (NEW) consumes the write token before apply |
| Audited + idempotent | write-back domain `AuditLog` + `event_id`-style idempotency (NEW); a retried relay does not double-apply |
| Failed write → full rollback | transactional apply; a failure leaves no partial state |
| No direct-BOM bypass | routes only through the chosen governed seam; never the direct `bom/tree` routes |
| Read embed stays read-only | the default embed is unchanged |

## 6. Tests (Day-2)
- **Unit:** entitlement (write key registered + 403 when absent), permission, lifecycle guard, single-use/replay, audit/idempotency.
- **Provider pact verifier:** the synced **success** interaction passes (endpoint exists) + the **negatives**; the strict gate goes green WITH the endpoint.
- **Integration:** end-to-end governed write per the chosen Fork-P1 model.

## 7. Two-repo boundary
- **Provider (Yuantus):** owns the endpoint + all enforcement + the verifier + the synced pact.
- **Consumer (metasheet2 #3332):** already relays via `updateBomMultitableLine`; the **contract source of truth**. Do **NOT** revive #3331's `writeBackBomMultitableLine`.

## 8. Open questions for the owner
1. **Fork P1 (central):** full APPROVED-ECO (⇒ async contract, re-open #3332) **vs** synchronous lifecycle-guarded governed edit (Fork-1 B)?
2. **Fork P2:** single-use mechanism — Redis vs DB.
3. **Fork P3:** audit/idempotency shape — reuse helpdesk `event_id` vs a new domain audit.
4. **Re-confirm:** is the write endpoint the next thing to build now, or scoped-but-parked (same reassess discipline as #884 §8 Q4)?

## 9. Phasing
**Day-1 (this doc)** → **review gate** (owner resolves P1/P2/P3) → **Day-2 build** (endpoint + guards + pact sync + verifier + tests, landed **together** to keep the strict gate green) → **verification doc**.

## References (grounding)
- Phase 7 design (#884): `docs/development/plm-collaboration-phase7-writeback-governed-seam-design-20260627.md`
- Consumer contract (source of truth): metasheet2 **#3332** (`updateBomMultitableLine` + `PATCH /api/v1/bom/multitable/{partId}/lines/{bomLineId}` + whitelist/empty-body/unknown-key guards)
- Closed/superseded: metasheet2 **#3331** (do **not** revive `writeBackBomMultitableLine`)
- Seams/primitives: `eco_service.action_apply`; `entitlement_service.is_entitled` (+ `FEATURE_APP_NAMES`); `meta_permission_service` / `security/rbac/permissions.PermissionManager`; `bom_multitable_projection_service`; `bom_multitable_embed_token_service` (mint, read-only); `api/middleware/audit.py`; `test_pact_provider_yuantus_plm.py` (+ `test_ci_contracts_pact_provider_gate.py`)
