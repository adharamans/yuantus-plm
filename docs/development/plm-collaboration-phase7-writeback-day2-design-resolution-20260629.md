# Phase-7 BOM multi-table write-back — Day-2 provider design resolution

**Status:** RATIFIED by owner 2026-06-29 — build authorized. Resolves the review-gate forks left open by the merged Day-1 taskbook (#899). Grounded against `origin/main` @ `1934cafc`. `1c0eadf0` is a spike/proof only and does **not** land.

## 0. Lineage (nailed)

- **#896 = the ECO-intent *draft* (Slice-0), now superseded.** It is retained as Slice-0 history, but the Day-2 real direction follows the later consumer pact **#3332**: synchronous `PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` → `200 {ok, bom_line_id}`. This is **not** auto-ECO and **not** an async intent — it is the **Draft / editable-state fast path**. The build PR corrects #896's doc口径 in lockstep so no future session re-derives the ECO seam.
- **#899 baseline stands** except its Fork-1 "ECO seam" (which #899 itself flagged as "Fork-1 B resurfacing"); that one fork is the superseded piece.
- **ECO route = a future, separate capability** for revising Released/locked BOMs; **not built in this slice**.

## 1. P1 — synchronous lifecycle-guarded apply (guard order nailed)

Server-side-only, on the **parent part**, in this exact order (only the 200 path is asserted by #3332; every error code is provider-side and individually tested):

```
401  unauthenticated
403  NOT entitled on the WRITE key  plm.bom_multitable_writeback   (first — no existence-leak on a write surface)
403  NOT permitted  check_permission("Part BOM", AMLAction.update)
400  malformed / empty whitelist / MISSING Idempotency-Key header   (fail-fast, fail-closed, before any object lookup)
404  part missing  OR  line ∉ part  (Item.item_type_id=="Part BOM" AND source_id==part_id)
409  parent lifecycle-locked  is_item_locked(db, part, part_type)   (Released/Review/Suspended/Obsolete = version_lock=True)
---  apply: single-use replay-cache (P2) + audit (P3) + property mutation, atomic → 200 {ok, bom_line_id}
```

The `409` reuses the `add_bom_child` precedent (`is_item_locked` → 409 "Item is locked in state '…'"). **Governance consequence:** a Released/locked BOM is not editable here; revising it is the deferred ECO route. No client version token in the body (would break #3332); `If-Match`/412 optimistic concurrency is a deferred fast-follow — **v1 is last-write-wins**, bounded by the single-use guard + lifecycle lock.

## 2. P2 — provider-side single-use / replay (key source nailed)

Redis is absent and the verifier runs on SQLite → a **DB** guard. New table **`meta_bom_writeback_audit`**, `idempotency_key String(64) NOT NULL UNIQUE`, applied via the proven MES-inbox pattern: `with db.begin_nested(): add + flush`, `except IntegrityError → fetch existing → return its cached 200 {ok, bom_line_id} WITHOUT re-applying`. Migration in **both** trees (`migrations/` + `migrations_tenant/`).

- **Key = explicit per-edit `Idempotency-Key` header.** A payload-content hash is unsound on a mutable line (a 5→10→5 revert collides with a replayed stale write); `x-request-id` is regenerated per attempt. So the consumer must supply it.
- **Missing header → 400 fail-closed** (checked at the §1 step-4 400 gate).
- **Duplicate key, same payload → cached `{ok, bom_line_id}`, no re-apply.** Duplicate key, **different** payload → **409 conflict**.
- **Functional consequence:** #3332's consumer pact needs a **functional** amendment (the header), not just a providerState text fix.

## 3. P3 — write-back domain audit (shape nailed)

The same `meta_bom_writeback_audit` row **simultaneously serves audit + idempotency/replay cache** (one insert): `actor user_id, tenant_id, org_id, part_id, bom_line_id, before(JSONB touched-cells), after(JSONB), idempotency_key, status, created_at`. Committed **atomically** with the mutation — **an audit-insert failure rolls back the property mutation** (deliberate departure from the repo's best-effort audit; a governed write must not succeed without its diff). `before` is snapshotted *before* `properties` reassignment. The global `AuditLogMiddleware` still records the HTTP row separately.

## 4. Write entitlement (nailed: NOT the read key)

Three edits that must land together: (1) register `"bom_multitable_writeback": frozenset({"plm.bom_multitable_writeback"})` in `FEATURE_APP_NAMES` (else `is_entitled` raises → 500); (2) a `WRITE_FEATURE_KEY` constant in the router/write module (not the read projection service); (3) seed the `plm.bom_multitable_writeback` license for `tenant-1` in `_seed_pact_fixtures` (else the 200 interaction → 403).

## 5. Lifecycle coverage + metasheet2 follow-ups (nailed)

- **The pact fixture cannot prove lifecycle** — the verifier's `Part` type has no `lifecycle_map_id` and P4's `state="Released"` is a bare string, so `is_item_locked` is inert there (the 200 stays green but proves nothing). **The 409 MUST be covered by a provider real/unit test**: a real `LifecycleState(version_lock=True)` parent → 409, plus a Draft parent → 200.
- **metasheet2 #3332 follow-up:** (a) providerState/fixture text `plm.bom_multitable` → `plm.bom_multitable_writeback`; (b) `PLMAdapter.updateBomMultitableLine` sends an `Idempotency-Key`; then (c) **re-add the amended pact interaction** to the broker (reverting #3337) once this provider lands.

## 6. Spike reuse boundary

From `1c0eadf0`, reuse only: the `line ∈ part` 404 shape, the verifier-interaction-proof technique, fixture isolation. The read-key gate, the no-replay, and the in-place `props.update` with no audit do **not** carry over.

## 7. Acceptance / tests (per #884 §5)

Provider unit/integration: write-entitlement 403; permission 403; malformed/empty/missing-Idempotency-Key 400; line∉part / part-missing 404; **lifecycle-locked 409 (real LifecycleState) + Draft 200**; single-use replay (same key → cached 200, no double-apply) + different-payload-same-key 409; audit before→after captured + audit-failure-rolls-back. Pact provider verifier: the PATCH success interaction **collected + 200** (interaction-level proof, not total-green); full verifier stays green; migration auto-applies in both trees.

## 8. Ratification — owner-confirmed 2026-06-29

- **R1 ✅** sync-apply supersedes #896's ECO-intent draft; Released/locked → 409; ECO route deferred.
- **R2 ✅** P2 via `Idempotency-Key` header + the #3332/PLMAdapter functional amendment; missing → 400; duplicate → cached 200.
- **R3 ✅** single combined `meta_bom_writeback_audit` (guard + audit + cached replay).
- **R4 ✅** last-write-wins v1; `If-Match`/412 deferred.

## 9. Sequence

This doc lands (docs) → governed endpoint PR on a fresh `origin/main` worktree (endpoint + governed `write_line` + `meta_bom_writeback_audit` model + migration ×2 + entitlement registration + fixture write-license seed + the §7 tests) → verifier green → land on `adharamans/yuantus-plm` after the `contracts` gate → metasheet2 #3332 follow-up (providerState text + `Idempotency-Key`) → broker re-add of the amended interaction.

## 10. Build conformance gates (G1-G5) - owner-ratified 2026-06-29

NOT new design - acceptance gates the governed-endpoint PR must pass item-by-item. Gap-checked against `origin/main` @ `73e94559`; the only delta from the §0 grounding `1934cafc` is this doc itself (via #901), so the code grounding is current.

- **G1 - guard order is NOT the read route's.** The existing BOM read route (`bom_multitable_router.py`) orders entitlement -> part lookup (404) -> type -> permission. The WRITE route MUST instead follow §1: write-key 403 -> permission 403 -> malformed / empty-whitelist / missing `Idempotency-Key` 400 -> 404 -> lifecycle 409. Permission + validation precede the object lookup so the write surface leaks no existence. **Required test:** unentitled AND missing-part -> **403, not 404**.
- **G2 - write key registered in the SAME PR.** `FEATURE_APP_NAMES` today holds only `"bom_multitable": frozenset({"plm.bom_multitable"})` (read); `is_entitled` raises `ValueError` on an unknown key -> surfaces as 500. The route MUST land with: register `"bom_multitable_writeback": frozenset({"plm.bom_multitable_writeback"})`, a `WRITE_FEATURE_KEY` constant in the write module (not the read projection service), and the `_seed_pact_fixtures` write-license seed.
- **G3 - idempotency/audit row BEFORE the mutation, one transaction.** Reuse the proven `consumption_mes_inbox_service` pattern: `begin_nested()` -> insert the `meta_bom_writeback_audit` row (UNIQUE `idempotency_key`) + flush -> on `IntegrityError` fetch existing and return its cached `200 {ok, bom_line_id}` WITHOUT re-applying; only a fresh key proceeds to mutate `properties`; `before` is snapshotted before reassignment; an audit-insert failure rolls back the mutation. **Required tests:** same-key duplicate -> cached 200 with no second write; different-payload-same-key -> 409.
- **G4 - #3332 is a FUNCTIONAL amendment, not a text rename.** The consumer (`PLMAdapter.updateBomMultitableLine`) MUST actually send an `Idempotency-Key` header (else it hits the §1 400), in addition to flipping providerState/fixture `plm.bom_multitable` -> `plm.bom_multitable_writeback`; then re-add the amended interaction to the broker (reverting #3337) once the provider lands.
- **G5 - the 409 lifecycle lock cannot be proven by the pact.** The pact `Part` ItemType has no `lifecycle_map_id` and seeds `state="Released"` as a bare string, so `is_item_locked` is inert there. The 409 MUST be covered by a provider real/unit test: real `LifecycleState(version_lock=True)` parent -> 409, plus a Draft parent -> 200. Pact-green is necessary-but-not-sufficient.

**Verified primitives (gap-check @ 73e94559):** `add_bom_child` -> `is_item_locked` -> `409 "Item is locked in state '...'"`; the MES-inbox idempotency precedent; `FEATURE_APP_NAMES` read-only key + `is_entitled` raise-on-unknown; pact-fixture inert lifecycle; BOM line containment = `Item(item_type_id="Part BOM", source_id=parent_id, related_id=child_id)` (owner-confirmed) for the §1 line-in-part 404.
