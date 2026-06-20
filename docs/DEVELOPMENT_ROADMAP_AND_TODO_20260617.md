# YuantusPLM — Development Roadmap & TODO (current snapshot)

Date: 2026-06-17 · Original snapshot baseline `main` @ `91da3591`; execution-roadmap body
**updated through `main` @ `1eebc293`** (MES line + CAD-PDM C3 merged; the three C3 follow-ups
**#803/#804/#806** reclassified as landed on 2026-06-19 — see §3/§5, body not otherwise re-reconciled
past `1eebc293`), with a 2026-06-18
PLM x MetaSheet status pointer added after `main` @ `18389f81` (#780).
Status: **living snapshot** — the current **working roadmap** (execution entry point) as of this date.

> **PLM x MetaSheet status pointer (2026-06-18):** For the collaboration-line
> commercial/maintenance answer and the reconciliation of stale Phase 0-6
> planning docs, use
> `docs/development/plm-collaboration-current-state-commercialization-and-roadmap-20260618.md`.
> In this roadmap, the `jti revocation denylist` item means the later
> cross-repo/SSO-gated admin revocation model only; the short-TTL + single-use
> jti replay guard is already closed in the P3-D delivery line.

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

## 0.1 Progress update — 2026-06-17 autonomous run (MERGED to main)

The **MES ingestion line is end-to-end COMPLETE on main** (sync + async). Landed:

- **R2.3 source_type widening** (scrap/rework) → **#785** (adversarial verify clean). **MERGED**.
- **R2.4 uom conversion** → taskbook **#784** + impl **#786** (convert within a dimension, else 422). **MERGED**.
- **MES audit tenant-attribution follow-up** → **#787** (the R2.2 security nit). **MERGED**.
- **R2.5 async** → taskbook **#788** + inbox mechanism **#789** + wiring **#794** (default-off:
  202-mode + inbox ops + worker drain; two real defects caught by adversarial verify and fixed —
  the poison-row terminal-failure + the async uom-mismatch parity). **MERGED**.
- **Tenant-baseline generator completeness + drift guard** → **#793** (a real new-tenant
  provisioning bug surfaced by a merge-train dry-run: the generator omitted `approval_automation`,
  so a regen dropped a per-tenant table; now complete + an order-independent subprocess drift guard
  in CI). Supersedes the closed #790. **MERGED**.
- Verified **at the MES-line merge** (`main@279b44e4`): route count 716, head `mes_inbox_001`,
  full contract suite 113 passed. **Current `main@1eebc293` after C3: route count 719, head
  `c3_date_obsolete_001`.**
- **CAD-PDM C3 date-BOM auto-obsolete** → taskbook **#792** + mechanism **#797** + wiring **#798**,
  all **MERGED** — feature-complete behind a default-off flag (two adversarial-verify rounds caught
  real bugs; see §3).
- **Re-verification correction**: the **jti revocation denylist is cross-repo-blocked**, not cleanly
  Yuantus-actionable — the embed token is verified **OFFLINE by the consumer (metasheet2)**, so a
  Yuantus-side denylist enforces nothing without a metasheet2 change, and it contradicts the
  offline-verify design (which is *why* the model relies on the ≤600s TTL). It belongs with the
  not-yet-opted SSO slice, alongside MetaSheet bridge. Re-classified below.

## 1. Delivered (recent, with PR refs)

- **MES ingestion line** (the current active line) — secured end to end:
  - R1 contract (#567) → **R2** route + DB idempotency (#778) → **R2.1** uom reconciliation
    (#779) → **R2.2** dedicated credential / fixed-tenant auth boundary (#781 taskbook, #782
    impl). Each has a `DEV_AND_VERIFICATION_…` doc on main.
- **PLM → ECM publish line** — live-proven (P0 #763 … P1E worker-E2E #775; live PASS recorded).
- Broader programs already closed (verified during the survey): CAD Material Assistant Phases 1–4
  (#704/#707/#711/#721), OdooPLM parity, CAD-PDM borrow program.

## 2. MES ingestion line — ✅ END-TO-END COMPLETE on main (2026-06-17)

The active line is **done and merged** (R1 → R2.2 previously; R2.3 → R2.5 this run). Order was
owner-ratified: secure the entrypoint (R2.2) → widen sources (R2.3) → convert units (R2.4) →
async (R2.5), plus the R2.2 audit-attribution follow-up. Items below kept for the record with
their landing PRs. Verified **at the MES-line merge** (`main@279b44e4`): route count 716, head
`mes_inbox_001`, full contract suite 113 passed. (Current `main@1eebc293` after C3 is 719 routes,
head `c3_date_obsolete_001` — see §3.)

- [x] **R2.3 — `source_type` widening** · **DONE #785**. `ALLOWED_SOURCE_TYPES` is now
  `frozenset({"mes", "workorder", "scrap", "rework"})` (`services/consumption_mes_contract.py`) —
  the two new **positive-consumption** sources are `scrap` (material consumed and scrapped during
  the run) and `rework` (material consumed reprocessing a defect). The R1 contract drift tests +
  the `ALLOWED_…` assertion were updated; idempotency / uom / variance and the manual `/actuals`
  path are unaffected.
  **Stayed out of scope (still a separate future taskbook)**: `return` / 冲销 / any reversal
  (negative) semantics. The MES contract enforces `actual_quantity >= 0` (DTO
  `_finite_non_negative`) and `variance` only **sums** (no reverse offset), so a reversal source
  cannot be a plain enum value — it needs negative quantity or an explicit offset mechanism +
  variance changes.

- [x] **R2.4 — unit conversion** · **DONE #784 (taskbook) + #786 (impl)**. Converts `event.uom` →
  `plan.uom` within a dimension; an unknown / cross-dimension pair keeps the **422**
  (`consumption_mes_uom_unconvertible`). Versioned conversion table, 6-dp rounding, audit envelope.

- [x] **R2.5 — async inbox + worker** · **DONE #788 (taskbook) + #789 (inbox mechanism) + #794
  (wiring)**. Default-OFF (`MES_INGEST_ASYNC`): the route persists the event to the
  `meta_mes_consumption_inbox` and returns 202; `drain_once` processes it through the same
  `ingest_mes_consumption`; admin-gated inbox ops (list/get/replay). Two real defects caught by
  adversarial verify and fixed: the poison-row terminal-failure and the async uom-mismatch parity.

- [x] **Follow-up: MES audit attribution** · **DONE #787**. `TenantOrgContextMiddleware` skips
  header-derived tenant/org for the machine MES path (`_is_mes_ingest_path`), so the audit row no
  longer records a caller-supplied `x-tenant-id` for it. (Recording the *bound*
  `MES_INGEST_TENANT_ID` precisely remains a noted larger follow-up.)

- **Cross-cutting fix this run**: **tenant-baseline generator completeness + drift guard** →
  **DONE #793** (supersedes the closed #790). A merge-train dry-run found the generator omitted
  `approval_automation`, so a regeneration silently dropped a per-tenant table; now complete, with
  an order-independent subprocess drift guard registered in CI.

## 3. Non-MES candidates (from the survey — RE-VERIFY current state before starting)

Not freshly re-verified beyond a spot check this turn; the survey was mid-session. Confirm
against live code first.

- [ ] **PLM-COLLAB jti revocation denylist** · security · **RE-CLASSIFIED 2026-06-17:
  cross-repo-BLOCKED** (was "S, Yuantus-local"). Re-verification showed the embed token is
  **verified OFFLINE by the consumer (metasheet2)** — Yuantus only mints. A Yuantus-side denylist
  table/endpoint would enforce **nothing** (the offline consumer can't consult it), and online
  revocation contradicts the offline-verify design that the ≤600s TTL exists to bound. Real
  revocation needs a cross-repo decision (online verify, or a polled/synced revocation list) =
  the not-yet-opted SSO slice. Not a clean Yuantus-only build.

- [x] **CAD-PDM C3 — date-BOM auto-obsolete + where-used propagation** · **DONE — feature-complete
  behind a default-off flag**. Taskbook #792; **Slice 1 mechanism #797** (date-expiry scan →
  obsolete-the-Item-iff-no-effective-version + depth-1 where-used **flag, not cascade**) + **Slice 2
  wiring #798** (scan-based polling worker, no cron; two gates — global setting + a registered
  per-tenant SKU `plm.cadpdm_date_obsolete`; admin-gated impact ops routes). Two adversarial-verify
  rounds caught + fixed real bugs (false-obsolete on open-start versions; a permanently-dead
  entitlement gate; a non-converging batch). Owner-ratified decisions: obsolete = Item lifecycle
  "Obsolete" via `promote` (**not** B1 `is_superseded`, which is orthogonal), flag-not-cascade,
  depth-1, polling worker. **Originally out-of-scope follow-ups, since landed on main 2026-06-18/19**: BOM-line
  (`item_id`-scoped) effectivities → **#806**; a standalone worker-daemon CLI → **#803**; the
  pre-existing `find_effective_version` NULL-start narrowness → **#804**.

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

The **MES ingestion line** (§2) **and CAD-PDM C3** (§3) are complete and merged. With C3 done, the
**Yuantus-local buildable roadmap is essentially clear**; what remains is gated on the owner /
cross-repo, not on more solo build:

1. **jti denylist / MetaSheet bridge** — both need the **SSO / cross-repo decision** (they live
   behind metasheet2 + the not-yet-opted identity-session slice); not clean Yuantus-only builds.
2. **Optional Yuantus-local follow-ups** surfaced this cycle — **all three since MERGED to main**,
   no longer open TODO: `find_effective_version` NULL-start narrowness → **#804**; C3 BOM-line
   (`item_id`-scoped) effectivities → **#806**; standalone C3 worker-daemon CLI → **#803**. With
   these landed, **no C3-specific follow-up from this three-item §5.2 list remains open**; the next
   picked item is the owner/cross-repo SSO slice (item 1). Unrelated open tails elsewhere in this
   roadmap (e.g. the §2 MES_INGEST_TENANT_ID attribution follow-up, and #804's ratifiable
   "no-Date = always-effective?" semantic note) are not closed by this reclassification.

## 6. Maintenance

Point-in-time snapshot (`2026-06-17`, original `main@91da3591`, execution-roadmap body **updated
through `main@1eebc293`** after the MES line merged (the three C3 follow-ups #803/#804/#806
reclassified as landed 2026-06-19; body not otherwise re-reconciled past `1eebc293`); PLM x MetaSheet pointer added on
`2026-06-18` after `main@18389f81`). Update it as slices land; **re-verify any item against current
code/git before starting** — the formal plan docs went stale precisely because that step was
skipped.
