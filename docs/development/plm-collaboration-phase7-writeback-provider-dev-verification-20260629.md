# Phase-7 BOM multi-table write-back — provider development & verification

**Status:** ✅ LANDED on `adharamans/yuantus-plm` main — PR #905, squash `7f4f99a2`, 2026-06-29. **metasheet2 consumer pact #3332 is now honored by the `adharamans/yuantus-plm` provider.**

## 0. Summary

The governed `PATCH /api/v1/bom/multitable/{part_id}/lines/{bom_line_id}` provider endpoint is built, verified, and landed per the ratified Day-2 design resolution (#901). It resolves taskbook #899's review-gate forks (P1/P2/P3) and supersedes #896's ECO-intent *draft* with the synchronous-apply direction #3332 actually shipped.

## 1. Lineage / decisions

- **Honors #3332** — synchronous `PATCH … → 200 {ok, bom_line_id}`.
- **Resolves #899** — P1 (sync lifecycle-guarded apply), P2 (provider-side single-use/replay), P3 (write-back audit + idempotency); owner-ratified in #901.
- **Supersedes #896** — the ECO-intent draft is retained as Slice-0 history; Released/locked parts → 409 (the ECO route is a deferred, separate capability).
- **Spike `1c0eadf0`** — proof-of-concept only; did **not** land (it reused the read entitlement key and had no replay/audit).

## 2. Implementation (what landed)

- **Endpoint guard order** (`bom_multitable_router.py`): `401` → `403` write-entitlement (`plm.bom_multitable_writeback`) → `403` `Part BOM`/`update` → `400` (malformed / empty whitelist / missing-or-over-long `Idempotency-Key` / non-scalar quantity, all before any object lookup) → `404` part-missing / line∉part → `409` `is_item_locked(parent)` → apply. Thin `{ok, bom_line_id}` (not the read/embed affordance envelope).
- **Write entitlement** = `plm.bom_multitable_writeback`, a distinct SKU registered in `FEATURE_APP_NAMES` (a read-only-licensed tenant gets 403 on write).
- **`meta_bom_writeback_audit`** (`idempotency_key String(64) NOT NULL UNIQUE` + before/after JSONB + provenance): **one insert simultaneously serves** the single-use replay-cache *and* the atomic write-back audit — an audit-insert failure rolls back the mutation (a governed write never succeeds without its diff). Main-tree migration (append) + tenant-tree single baseline (regenerated via `scripts/generate_tenant_baseline.py`).

## 3. Verification

- **§7 acceptance suite: 20 passed** — full guard ladder; a **real `LifecycleState(version_lock=True)` parent → 409** (+ Draft → 200) — the pact fixture's bare-string "Released" is inert and proves nothing, so the lock is proven here; **replay → cached 200 with an out-of-band 999 sentinel proving exactly-once apply**; different-payload-same-key → 409; audit before→after captured + audit-insert-failure rolls back the mutation.
- **Pact provider verifier: 3 passed** — the amended PATCH interaction (carrying `Idempotency-Key`) is **collected at 200, not skipped** (interaction-level proof, negative-control confirmed).
- **CI `contracts` (10m27s) + `plugin-tests` + `regression` + `perf-roadmap` green** on #905. Independent adversarial review: APPROVE-WITH-NITS, all 13 locks pass.

## 4. Review hardening (owner review)

- **P2 — scalar quantity:** `quantity: Optional[Any]` filtered by key only let a write-permitted caller persist an object/array into the BOM quantity cell. The 400 gate now rejects a non-scalar quantity (object/array/bool) before `write_line` — no audit row, line unchanged.
- **P2 — `Idempotency-Key` length:** a `>64`-char key passed the presence check and would throw a `VARCHAR(64)` length error at insert on Postgres (SQLite-lenient false-green). Now `400` at the gate, before any lookup.
- **Migration contracts (CI fix):** `op.create_table` used a variable where the coverage contract scans for a quoted literal → literal name; and the tenant tree was wrongly *appended* a revision where it must be a single regenerated baseline → removed the extra revision + regenerated.

## 5. Semantic note (owner-accepted — not a finding)

A same-key replay whose parent has **since** become locked returns **409 (lifecycle)**, not the cached 200, because the ratified guard order places the lifecycle gate **before** replay/apply. Governance lock takes precedence over replay idempotency — by design.

## 6. Deferred — cross-repo, provider-first ordering

The provider landed first (so the broker gate is never consumer-ahead-of-provider). Each item below is a separate owner-gated opt-in:

- **metasheet2 #3332 functional amendment** — `PLMAdapter.updateBomMultitableLine` sends `Idempotency-Key`; providerState/fixture text `plm.bom_multitable` → `plm.bom_multitable_writeback`.
- **Broker re-add** of the amended PATCH interaction (reverting #3337's depublish), so the Phase-B broker gate verifies the live interaction against the now-honoring provider.
- **Fast-follows** (design §1/§2): `Idempotency-Key` end-to-end on the consumer; `If-Match`/412 optimistic concurrency; ECO-checkout-lock 409 depth.
