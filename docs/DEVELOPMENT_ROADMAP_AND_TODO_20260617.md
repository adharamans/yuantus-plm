# YuantusPLM — Development Roadmap & TODO (current snapshot)

Date: 2026-06-17 · Baseline: `main` @ `91da3591`
Status: **living snapshot** — the current **working roadmap** (execution entry point) as of this date.

## 0. Why this exists (read first)

The repo's **formal** planning docs (`DEVELOPMENT_NEXT_CYCLE_TODO_PLAN_*`, the Odoo gap
analyses) have gone **stale as TODO lists** — most of their items are already merged. Acting on
them this cycle repeatedly surfaced **already-done or non-issue** work (e.g. "CAD Material
Assistant" was fully built; an "erp 401/403 parity bug" already matched the ECM production
decision). This doc is the current **working roadmap / execution entry point**, grounded in the
**current code + git history**. It does **not** supersede the existing taskbooks and design docs
— those keep their authority as historical record and design background; they just should not be
read directly as the live TODO. **Re-verify any item against live code before starting** — that
discipline is exactly what the stale TODO lists lacked.

Sizes: S = ~1 PR, M = a few PRs / a thin taskbook, L = a multi-PR program.

## 1. Delivered (recent, with PR refs)

- **MES ingestion line** (the current active line) — secured end to end:
  - R1 contract (#567) → **R2** route + DB idempotency (#778) → **R2.1** uom reconciliation
    (#779) → **R2.2** dedicated credential / fixed-tenant auth boundary (#781 taskbook, #782
    impl). Each has a `DEV_AND_VERIFICATION_…` doc on main.
- **PLM → ECM publish line** — live-proven (P0 #763 … P1E worker-E2E #775; live PASS recorded).
- Broader programs already closed (verified during the survey): CAD Material Assistant Phases 1–4
  (#704/#707/#711/#721), OdooPLM parity, CAD-PDM borrow program.

## 2. MES ingestion line — remaining (VERIFIED current; sequenced per owner decision)

The active line. Order is owner-ratified: secure the entrypoint (done, R2.2) → widen sources →
convert units → async. Each is bounded.

- [ ] **R2.3 — `source_type` widening** · size **S** · *blocked only on a product decision*.
  Widen `ALLOWED_SOURCE_TYPES` (today `frozenset({"mes","workorder"})`,
  `services/consumption_mes_contract.py:41`) to additional **positive-consumption** sources only
  (e.g. `scrap` / `rework`). **Need from owner**: which types + each one's meaning. No separate
  taskbook once the list is set. Acceptance: DTO accepts the new types; the R1 contract drift
  tests + `ALLOWED_…` assertion updated; idempotency/uom/variance unaffected; manual `/actuals`
  untouched.
  **Out of scope — do NOT fold in**: `return` / 冲销 / any reversal (negative) semantics. The
  MES contract enforces `actual_quantity >= 0` (DTO `_finite_non_negative`) and `variance` only
  **sums** (no reverse offset), so a reversal source cannot be a plain enum value — it needs its
  own taskbook (negative quantity or an explicit offset mechanism + variance changes).

- [ ] **R2.4 — unit conversion** · size **M** · *needs a thin taskbook first*. Today R2.1
  **rejects** a declared-uom mismatch (`422`); conversion would instead convert `event.uom` →
  `plan.uom`. Taskbook must lock: conversion-table source + versioning, rounding policy, which
  unit pairs are in scope, and the failure mode for an unknown pair (keep the 422). Overlaps with
  R2.1's "no unit conversion (out of scope)" note.

- [ ] **R2.5 — MES outbox/worker** · size **L** · **do last**. Async ingestion (decouple accept
  from process), mirroring the ECM/erp outbox+worker pattern. Highest runtime complexity; only
  after the synchronous ingest semantics (R2–R2.4) are fully settled.

- [ ] **Follow-up: MES audit attribution** · size **S–M**. The R2.2 security review flagged that
  the mes-actuals audit row records the request `x-tenant-id` header, not the bound
  `MES_INGEST_TENANT_ID` (attribution-only — **not** a data-isolation gap, which was verified
  correct). A precise fix is middleware-level (pin the MES tenant in the request context). Deferred
  from R2.2's narrow scope; pick up when convenient.

## 3. Non-MES candidates (from the survey — RE-VERIFY current state before starting)

Not freshly re-verified beyond a spot check this turn; the survey was mid-session. Confirm
against live code first.

- [ ] **PLM-COLLAB jti revocation denylist** · size **S** (Yuantus-side) · security. An embed
  token can replay within its ≤600s TTL — `jti` is minted/recorded but there is no revocation
  denylist. Yuantus-side denylist is local/unblocked; the consumer tenant cross-check is partly
  in **metasheet2** (cross-repo), and the doc bundles it into a **not-yet-opted SSO slice**.
  **RE-VERIFY**: the embed-token service may have changed.

- [ ] **CAD-PDM C3 — date-BOM auto-obsolete + where-used propagation** · size **M–L** ·
  decision-gated. Confirmed open (no closing commit in `--all`). A genuine new feature (date
  trigger + scheduler + upward where-used). Owner opt-in / taskbook-first when prioritized.

- [ ] **MetaSheet bridge activation** · size **M** · SSO-gated. `api/routers/metasheet_bridge.py`
  still returns a static `{"active": false, "entitlement_required": true}`. Depends on the
  SSO/identity-session decision (the same parent slice as the jti item).

## 4. Explicitly NOT actionable (blocked on external / non-code — do not pick)

- render-service **S3** (docker-compose e2e) and visual-diff **L2** — need the external VemCAD
  render image (not available locally).
- **G2** named-vendor ERP adapter — the publication spine is closed behind a registry seam; a
  concrete adapter exists only when a concrete target does.
- **G1 / G6** native-CAD signoff / production-scale evidence — hardware/operator-gated.
- erp 401/403 "parity" — **verified a non-issue**; erp already matches the ECM Transfer
  production decision (cred/scope → terminal).

## 5. Recommended next action

1. **R2.3 `source_type` widening** — the cleanest next slice; just needs the type list from owner.
2. Or **R2.4 unit-conversion thin taskbook**; or the **MES audit-attribution** follow-up; or
   **re-verify + pick** a non-MES candidate (jti denylist is the highest-value security item).

## 6. Maintenance

Point-in-time snapshot (`2026-06-17`, `main@91da3591`). Update it as slices land; **re-verify any
item against current code/git before starting** — the formal plan docs went stale precisely
because that step was skipped.
