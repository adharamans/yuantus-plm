# PLM Collaboration Phase 7 — Write-Back Contract DRAFT (Slice 0)

Date: 2026-06-27
Type: **Slice 0 — contract draft / spec addendum (no code).** This is **not** the
"slice-1" that #884 named — see the phasing revision below. It records the BOM
write-back interaction's request/response shape so the provider can be built against
it, and is **deliberately NOT added to the frozen consumer pact**
(`PLM_ADAPTER_PACT_PATHS`), which freezes *what PLMAdapter actually calls, never
aspiration*; this endpoint is not built or called yet.

## Phasing revision (this doc amends #884 §7)

#884 named **Slice 1 = "consumer-first write pact + provider verifier."** That is
**not achievable for an unbuilt, uncalled endpoint** — the consumer pact forbids
aspirational interactions and the local provider verifier has no pending mechanism.
So the corrected phasing is:

- **Slice 0 (this doc):** the write-back **contract draft** — spec only, not in the
  frozen pact, no code.
- **Slice 1 (revised):** the **provider write slice as ONE complete cut** (below) —
  *not* endpoint-alone.
- **Slice 2:** consumer relay (the real `PLMAdapter` call) → **then** the frozen pact
  entry + provider verifier (the pact now freezes a *real* call). This is where #884's
  original "consumer pact + verifier" actually lands.

## Confirmed fork call (the decisions this builds on)

- **Fork 1 = ECO change-control route.** A BOM edit becomes an ECO change-request and
  inherits ECO governance. No net-new write path; the lifecycle guard is enforced by ECO.
- **Fork 2 = write-intent permission = `"Part BOM"` `AMLAction.update`**, with the
  full **ECO `apply`/`execute` gate retained** — Part-BOM-update authorizes the
  *intent*; the write still passes the complete ECO gate.
- **Fork 3 = pact-first** (so this draft, then the real pact later).
- **Phase 6 = deferred** (Fork B = future lean, not a current build line).

## The contract (draft)

A consumer relays a BOM write **intent**; the provider mediates it through ECO
change-control. Field names are illustrative — pinned when the provider slice opens.

- **Request** `POST /api/v1/bom/multitable/{part_id}/write-back` (provisional path)
  - **Auth:** a **write-scoped** token (NOT the read embed token; `typ` ≠ `"embed"`),
    single-use enforced **provider-side** (a NEW guard).
  - **Body:** the BOM-line change intent — `{ bom_line_id, change: {quantity|uom|find_num|refdes|...}, reason }`.
- **Provider behavior (mediated; create-intent, NOT auto-apply):**
  `verify write-scoped token (+ provider-side single-use) → is_entitled(<write feature_key>)
   → check_permission("Part BOM", AMLAction.update)  [write-intent]
   → served-tenant cross-check (inherited, #2356)
   → create or append the BOM change as an ECO change-request **intent** (status pending)`.
  **The write-back does NOT apply the change.** Open/append-ECO and ECO **apply** are
  **separate** governed actions — they are **not** chained into a default success path.
  Applying an ECO remains the existing, separately-gated `POST /api/v1/eco/{id}/apply`,
  which requires **APPROVED** state + diagnostics + version-lock; the write-back never
  auto-promotes/auto-applies. (A separate "apply an already-approved ECO" intent could be
  a *distinct* request type later — explicitly, never the default.)
- **Response (success):** the created/updated **ECO change-request reference** (pending
  approval) + an audit id — **not** an "applied" result and **not** a raw mutated row.
- **Audit:** a **domain `AuditLog`** of the write-intent + an idempotency key (new
  provider-side build; the generic middleware audit is not a domain write record).
- **Error/guard shapes:** read token → 401/403 (no escalation); unentitled → 403;
  unpermitted → 403; wrong lifecycle state (e.g. Released part) → rejected by ECO
  semantics; cross-tenant → 403 pre-mutation; replayed write token → rejected;
  failure → full rollback (no partial ECO state).

## Acceptance for Slice 0 (reviewable now)

- [ ] Contract request/response/error shapes documented + agreed.
- [ ] **NOT** in `PLM_ADAPTER_PACT_PATHS` (no aspiration in the frozen pact).
- [ ] No provider endpoint / replay guard / domain AuditLog implemented here.
- [ ] ECO semantics explicit: write-back = **create/append a pending ECO intent**, never
      an auto-apply; apply stays the separate APPROVED-gated `eco/{id}/apply`.
- [ ] Auth chain explicit (write≠read token; Part-BOM-update + full ECO gate;
      provider-side single-use = new).
- [ ] Phasing revision to #884 §7 recorded.

## Next slices (gated, in order)

1. **Provider write slice — ONE complete cut** (not endpoint-alone): the governed write
   endpoint (ECO-route, **create/append intent only**) **+** the provider-side
   consumed-jti/replay guard **+** the domain `AuditLog`/idempotency **+** the ECO
   lifecycle/intent semantics. Landing the endpoint without the replay guard + audit
   would be an incomplete write entry-point — so they ship together.
2. Consumer relay (the real `PLMAdapter` call) → **then** the frozen pact entry +
   provider verifier (now freezing a real call, honoring the consumer repo's
   contract-first principle).

## References

- Phase 7 design baseline (this doc revises its §7 phasing) — `docs/development/plm-collaboration-phase7-writeback-governed-seam-design-20260627.md` (#884)
- ECO apply seam — `eco_impact_apply_router.py` / `eco_service.py:action_apply`
- Consumer pact (freeze-what's-used) — `metasheet2:packages/core-backend/tests/contract/plm-adapter-yuantus.pact.test.ts`; committed pact `contracts/pacts/metasheet2-yuantus-plm.json`; provider verifier `src/yuantus/api/tests/test_pact_provider_yuantus_plm.py`
