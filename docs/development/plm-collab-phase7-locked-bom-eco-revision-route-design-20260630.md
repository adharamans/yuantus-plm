# Phase 7 — Locked-BOM ECO Revision Route — Design & Governance Decision Doc

**Status:** §3 DECISIONS RATIFIED 2026-07-01 (owner). Direction locked: **(a) A3** — explicit ECO-path opt-in (locked edit still 409s by default; explicit opt-in opens a PENDING ECO revision intent, never auto-apply); **(b) B1** — reuse the existing ECO approval workflow (apply stays the separate APPROVED-gated `/eco/{id}/apply`); **(c) C2** — pre-emptive `line.state` gating + discriminated-409 fallback. Ratified per owner instruction, chosen because A3/B1/C2 is closest to the already-landed Phase-7 write-back flow and reuses the existing consumer/provider tracks.
**RATIFICATION IS DIRECTION ONLY — STILL AUTHORIZES NO BUILD.** The §6 implementation phases remain per-phase opt-ins (none auto-starts). In particular these pre-conditions are **each separately gated** and are **not** authorized by this ratification: B1's `EcoPermissionAdapter` wiring is a **repo-wide authz change** affecting every `ECOService` caller (§3.2 blast-radius caveat) and must be scoped/tested/ratified as such; C2's discriminated-409 starts at the **provider/contract boundary** (the pact is success-only) and is a contract change the owner must authorize; and the ECO-scoped `feature_key`/SKU question (§7) stays open. Per `plm-collab-remaining-gated-development-order-and-verification-20260630.md:40` (design-first + owner-ratified before code) and the per-phase opt-in convention (`DEVELOPMENT_NEXT_CYCLE_TODO_PLAN_20260426.md:532` "No phase auto-starts").
**Date:** 2026-06-30
**Baseline:** Yuantus `origin/main` at `c5053bfb` (`#930`); MetaSheet2 consumer stack at `#3383`/`#3384`/`#3392` (on its `origin/main`).

> **Provenance.** Produced autonomously as the executable prong of the "Group B design-first" recommendation the operator ratified with "按你建议执行". It is a **decision doc for owner ratification**, not an authorization to build; it is a clean, single-revert, docs-only unit. Every code reference below was verified against `origin/main` (`git show origin/main:<path>`), not a stale local checkout. The three recommended defaults (§3) were the author's engineering recommendation; the owner **ratified all three (A3/B1/C2) on 2026-07-01** (see Status). Ratification locks the *direction* only — the §6 build and B1/C2's pre-conditions remain separately gated.

---

## 1. Purpose & Scope

### 1.1 Purpose

This is a **decision document**, not an implementation plan. Its sole job is to frame the three owner-ratify decision points enumerated verbatim at `plm-collab-remaining-gated-development-order-and-verification-20260630.md:110` — "(1) whether released/locked BOM edits create ECO revision intents, (2) how apply is authorized, and (3) how the UI distinguishes draft fast-path vs ECO path" — and to give each a concrete option set with a recommended default that **remains pending owner ratification**.

The route it governs is the **deferred locked-BOM ECO revision route**: the path that would let a user revise a lifecycle-locked BOM that today simply returns a hard `409`. This is explicitly *not* the already-shipped Draft fast-path (`plm-collaboration-phase7-writeback-day2-design-resolution-20260629.md:7` "synchronous PATCH … This is not auto-ECO and not an async intent — it is the Draft / editable-state fast path.").

### 1.2 In scope

- Framing the governance forks and their tradeoffs.
- Naming the exact existing subsystems the route would reuse (so it does not reinvent ECO).
- Recording the invariants any acceptable design must preserve.
- A design-only, phased outline **conditional on ratification**.

### 1.3 Out of scope (explicitly)

Following the precedent that the governing design "authorizes no build and does NOT spec the endpoint, pact schema, or UX" (`plm-collaboration-phase7-writeback-governed-seam-design-20260627.md:5-8`), this doc does **not**:

- Specify the HTTP endpoint, request/response schema, or pact interaction.
- Specify the UX beyond the draft-vs-ECO distinction principle.
- Authorize any code, migration, or contract change.
- Re-open shipped Day-2 behavior (the Draft fast-path is authoritative and untouched).

---

## 2. Current State

### 2.1 The 409 lifecycle-lock (grounded)

The Phase-7 governed BOM write-back is the PATCH endpoint `bom_multitable_write_line` (`bom_multitable_router.py:310-429`), backed by `BOMMultitableWritebackService.write_line` (`bom_multitable_writeback_service.py:130-236`).

- **Single-point, router-only gate.** The lifecycle-lock `409` is raised *exclusively* in the router at `bom_multitable_router.py:395-399`: it loads the parent's `ItemType`, calls `is_item_locked(db, part, part_type)`, and on a truthy `locked` raises `HTTPException(status_code=409, detail="Item is locked in state '…'")`. The write-back **service performs no lifecycle check** — it imports no lifecycle guard and only re-enforces line-in-part (`404`, service:152-161), If-Match (`412`, service:184-192), and replay-conflict (`409`, service:189/213). The service trusts the router's verdict and mutates once reached.

- **The lock signal is a single boolean.** `LifecycleState.version_lock` (Boolean, default False) at `lifecycle/models.py:59-61` is the sole discriminator, read by `is_item_locked` at `lifecycle/guard.py:30-36`. `is_released` / `is_suspended` / `is_end_state` are **not** consulted. State is resolved by `get_lifecycle_state` (`guard.py:9-27`): `item.current_state` FK first, else `item_type.lifecycle_map_id` + `item.state` name lookup.

- **Evaluated on the PARENT part**, not the BOM line and not the child component (`bom_multitable_router.py:385` `part = db.get(Item, part_id)` is the object passed to `is_item_locked` at `:395`).

- **409 set vs 200 set (data-driven).** Under the default CLI seed, `version_lock=True` → `{Released, Suspended, Obsolete}` (`cli.py:1087/1093/1100` Part, `1165/1172/1179` Document). `version_lock=False` → `{Draft, Review}`, plus any part with **no resolvable lifecycle state** (`current_state` null and no map/name match → `get_lifecycle_state` returns None → `locked=False`). Tests confirm both branches: `test_bom_multitable_writeback.py:311` (locked parent → 409, no mutation, zero audit rows) and `test_draft_parent_applies_200` (parent_current_state=None → 200).

- **Comment-vs-seed mismatch (do not rely on the comment).** The router inline comment (~line 394) lists "Released/Review/Suspended/Obsolete" as locked, but the seed does **not** set `version_lock` on Review — Review returns 200. Scope the ECO set off `version_lock`, not the comment. (Flagged as a pre-existing minor defect in §8-2.)

- **Two distinct 409s on this endpoint.** The lifecycle-lock 409 (`router:395-399`, "Item is locked in state '…'") is separate from the idempotency replay-conflict 409 (`router:420-421`, "Idempotency-Key reused for a different write", from `BomLineWritebackConflictError`). Only the former is this route's subject. Any 409-based routing must not conflate them.

- **Full guard ladder** (`bom_multitable_router.py`): 401 auth (`:315`) → 403 write-entitlement `is_entitled(WRITE_FEATURE_KEY)` (`:342-343`) → 403 permission `check_permission('Part BOM', update)` (`:347-350`) → 400 malformed/missing-key (`:355-382`) → 404 part-missing/line-not-in-part (`:385-390`) → **409 lifecycle-lock (`:395-399`)** → service phase (404 defense `:415-417`, 409 replay `:419-421`, 412 If-Match `:423-424`) → atomic apply (`service:196-235`).

### 2.2 The existing ECO subsystem available for reuse

The ECO subsystem is mature and decomposed under `src/yuantus/meta_engine`:

- **Models** (`models/eco.py`): `meta_ecos` (ECO, :135), `meta_eco_stages` (:102), `meta_eco_approvals` (:268), `meta_eco_bom_changes` (:319), `meta_eco_routing_changes` (:420). Change intent is modeled as a **version branch**: `source_version_id → target_version_id` FKs to `meta_item_versions` (`eco.py:163-168`).
- **Lifecycle** is a bespoke 7-state string machine `ECOState {draft, progress, suspended, conflict, approved, done, canceled}` (`eco.py:47-57`) on `ECO.state` (:174). Note the **vestigial** `ECO.current_state` FK (`eco.py:175-177`) — 0 references in `eco_service.py`; ECO lifecycle is *not* an instance of the generic `LifecycleService`.
- **Services** (`services/eco_service.py`): `ECOService`, `ECOStageService`, `ECOApprovalService`.
  - Create: `ECOService.create_eco` (`:504`) → DRAFT + auto-assign first stage.
  - Revision branch: `ECOService.action_new_revision` (`:708`) → `VersionService.create_branch` (`:742`), sets source/target, DRAFT→PROGRESS. Also `bind_product(create_target_revision=True)` (`:581-633`).
  - Approvals: `ECOApprovalService.approve` (`:2629`) → sets `state=APPROVED` when stage approvals complete.
  - Apply: `ECOService.action_apply` (`:1753`) requires `state==APPROVED` (`:857/:955` normalize APPROVED) + product_id + target_version_id; runs activity gate, rebase-conflict, version-lock guards; repoints `product.current_version_id`; sets state=DONE.
- **Routers** decomposed per-surface, mounted at `/api/v1` (`api/app.py:419-424`): `eco_core_router`, `eco_stage_router`, `eco_approval_workflow_router`, `eco_impact_apply_router`, `eco_change_analysis_router`, `eco_lifecycle_router`.
- **Authorization state (the real gap):** `ECOService.permission_service` is a permissive `PermissionManager` (imported aliased `MetaPermissionService`, `eco_service.py:43,57`) → allow-by-default (`security/rbac/permissions.py:36-37`); ECOService create/update/delete/execute checks are effectively **no-ops today**. A real bridge `EcoPermissionAdapter` (`services/eco_permission_adapter.py:38`, `check_permission` at :52, graceful allow-by-default when no rules) exists but is **written and NOT wired in**. Approval-side ops use real RBAC (`RBACUser.has_permission('eco.*')`, `eco_service.py:2922-2943`).
- **No "revision intent" / superseding entity exists.** The only per-affected-item action model (Change/Release/Revise/New Generation) lives in the **DEPRECATED** `ChangeService.execute_eco` (`change_service.py:98-130`, "superseded by ECOService"). Version supersession is a `VersionService.release()` concern (`version/service.py:570-593`), orthogonal to ECO apply, which only repoints the current-version pointer.

---

## 3. Decision Points Requiring Owner Ratification

The three below are the verbatim owner-ratify points from `plm-collab-remaining-gated-development-order-and-verification-20260630.md:110`. Each recommendation is **pending owner ratification**.

### 3.1 (a) Do released/locked-BOM edits create ECO revision intents?

The load-bearing fork. Upstream governing design framed this as Fork 1: (A) route through full ECO change control vs (B) a narrower lifecycle-guarded edit (`plm-collaboration-phase7-writeback-governed-seam-design-20260627.md:64-79`). The prior #884/#885 draft proposed creating a PENDING ECO change-request intent that "does NOT apply the change" (`plm-collaboration-phase7-writeback-contract-draft-20260627.md:45-55`); Day-2 #901 re-deferred it (`…day2-design-resolution-20260629.md:9`).

| Option | Description | Tradeoffs |
|---|---|---|
| **A1. Keep hard-409 (status quo)** | Locked BOM edits remain forbidden at the write-back seam; revision happens only through the native ECO UI (`POST /api/v1/eco …`), decoupled from the MetaSheet write path. | + Zero new surface, zero new governance risk. + Preserves the clean "Draft fast-path only" seam. − Leaves the operator gap the route was raised to close; the 409 stays opaque to the user. |
| **A2. Auto-create ECO revision intent on locked edit** | A locked-parent edit auto-opens/appends an ECO (source→target branch via `action_new_revision`), status DRAFT/PROGRESS, never auto-applied. | + Zero-friction capture of intent, full ECO governance inherited. − Risk of ECO sprawl (an ECO per stray edit); implicit creation blurs user intent; must guarantee non-auto-apply (`contract-draft:54-55`). |
| **A3. Explicit ECO-path opt-in (recommended)** | Locked edit still 409s by default; an **explicit, write-scoped** opt-in (distinct flag on a distinct endpoint or a discrete "raise ECO" action) creates/attaches a PENDING ECO revision intent via `action_new_revision`. Never auto-apply. | + User intent is explicit, no sprawl. + Reuses ECO branch model + approval + apply gates wholesale. + Keeps default behavior (409) intact for un-opted clients. − Requires a discriminated provider response so the UI can *offer* the opt-in (see 3.3); more surface than A1. |

**Recommended: A3 (explicit ECO-path opt-in), pending owner ratification.** It closes the operator gap without weakening the default 409, and it aligns with the non-auto-apply invariant (`contract-draft:54-55`) and the "write is a separate, write-scoped authorization" invariant (`governed-seam:15-24`). It revives the #884/#885 ECO-change-control framing behind an explicit gate rather than the implicit A2.

### 3.2 (b) How is apply authorized?

Constrained by the ADR: `/eco` is the single canonical ECO write path via `ECOService`; **ECO apply requires APPROVED state + diagnostics + version-lock** (`ADR_PLM_CORE_CONVERGENCE_ECO_WRITE_PATH.md:26-31`, `governed-seam:38`). Legacy `/ecm` is a deprecated sunset shim (`ADR:43-53`).

| Option | Description | Tradeoffs |
|---|---|---|
| **B1. Reuse existing ECO approval workflow (recommended)** | Apply stays the separate `POST /api/v1/eco/{id}/apply` (`eco_impact_apply_router.py:261` → `ECOService.action_apply:1753`), gated on `state==APPROVED` set by `ECOApprovalService.approve`, plus `get_apply_diagnostics` block-on-errors (`:279-299`), activity gate, rebase-conflict, version-lock (`:1773-1805`). The write-back never applies. | + Zero new authz machinery; honors ADR + non-auto-apply. + Full stage-role / SLA / escalation reuse. − Depends on wiring real authz (see caveat), else create/apply is effectively permissive today. |
| **B2. New authz for the locked-BOM route** | A bespoke permission/feature check specific to locked-BOM apply. | − Duplicates the ECO authz model; violates "single canonical ECO write path" (ADR:26-31); high review cost. |
| **B3. Superuser-only apply** | Restrict apply to an elevated role as an interim. | + Simple gate. − Not a governance model, just a lock; does not scale to real change control; a temporary crutch at best. |

**Recommended: B1 (reuse the existing ECO approval workflow), pending owner ratification** — with a **mandatory pre-condition to wire `EcoPermissionAdapter` into `ECOService`** so the authz is real, not the current allow-by-default `PermissionManager` (`permissions.py:36-37`; adapter written-but-unwired at `eco_permission_adapter.py:38`). B1 satisfies the ADR non-auto-apply separation and reuses the already-full-featured approval subsystem. **Blast-radius caveat:** wiring `EcoPermissionAdapter` changes authorization for *every* `ECOService` caller (create/bind/update/execute/apply), not only the locked-BOM route, since they all currently share the allow-by-default path. The adapter's "allow-by-default when no ECO rules are configured" (`eco_permission_adapter.py:80`) largely de-risks a flag-day, but this pre-condition is a repo-wide authz change that must be scoped, tested, and ratified as such — not a change local to this route (see §8-4).

### 3.3 (c) How does the UI distinguish draft fast-path vs ECO path?

Today the distinction does not exist at any layer. The provider pact is success-only (200 `{ok, bom_line_id}`, "no eco_id … leakage", `plm-adapter-yuantus.pact.test.ts:242,262-274`); the adapter envelope defers `eco_id` (`PLMAdapter.ts:741-744`); the backend flattens every 4xx to `reason:'provider-rejected'` (`plm-workbench.ts:869-874`); and the panel branches on **HTTP status only** — `PlmBomReviewPanel.vue:122-125` `writebackMessage(status)` renders one generic 409 string that *conflates lifecycle-lock with idempotency-key reuse*. Critically, the relay already threads a `reason` string end-to-end into `PlmBomMultitableLineUpdateResult` (`workbench.ts:857-860`), but the panel discards it (`PlmBomReviewPanel.vue:161` calls `writebackMessage(outcome.status)`, never reads `outcome.reason`).

| Option | Description | Tradeoffs |
|---|---|---|
| **C1. Reactive branch on discriminated 409** | Provider emits a discriminated 409 (`lifecycle_locked` / `eco_required` vs `idempotency_conflict`); backend maps it into a specific `reason` (stop flattening at `plm-workbench.ts:869-874`); panel branches on `outcome.reason` and offers a distinct "raise/attach ECO" CTA. | + Small, localized consumer change (plumbing 80% ready). − Purely reactive: user only learns the line is locked *after* submitting. |
| **C2. Pre-emptive gating on `line.state` + reactive fallback (recommended)** | Use `PlmBomMultitableLine.state` (already fetched + displayed, `workbench.ts:83` / `PlmBomReviewTable.vue:44`, but **not** used to gate editability today) to disable inline edit for locked lines up front and surface the ECO affordance, **plus** C1's discriminated-409 as a safety net. | + Clear draft-vs-ECO visual distinction before submit; no blurring (`gated-order:110`). + Reuses an existing field. − Requires provider to still emit a discriminated 409 for correctness; two-place logic. |
| **C3. Status-only (do nothing)** | Keep the single conflated 409 message. | − Fails decision point (c) outright; misroutes on idempotency conflicts (both are 409 `provider-rejected`). Not acceptable. |

**Recommended: C2 (pre-emptive `line.state` gating + discriminated-409 fallback), pending owner ratification.** It makes the draft-vs-ECO distinction visible *before* submit and does not blur the two paths, while the discriminated 409 handles the residual race. **Prerequisite:** this is not a pure consumer re-plumb — it starts at the **provider/contract boundary** (the pact is success-only and `eco_id` is deferred, `PLMAdapter.ts:741-744`), which the owner must authorize as a contract change.

---

## 4. Reuse Map

The route reuses, and must not reinvent, these existing components:

| Concern | Reuse (grounded) |
|---|---|
| Revision branch (intent) | `ECOService.action_new_revision` (`eco_service.py:708`) + `bind_product(create_target_revision=True)` (`:581`) → `VersionService.create_branch` (`:742`). Build on the `source_version_id → target_version_id` branch model (`eco.py:163-168`), **not** a new entity. |
| ECO create | `ECOService.create_eco` (`eco_service.py:504`) via `POST /api/v1/eco` (`eco_core_router.py:86`). |
| BOM change capture | `ECOBOMChange` (`meta_eco_bom_changes`, `eco.py:319`); routing via `ECORoutingChange` (`:420`). |
| Approval workflow | `ECOApprovalService.approve/reject/batch/escalate` (`eco_service.py:2629`), stage roles/SLA/auto-assign; routers `eco_approval_workflow_router`. |
| Apply gate stack | `ECOService.action_apply` (`:1753`) + `get_apply_diagnostics` (`eco_impact_apply_router.py:279-299`) + activity gate / rebase-conflict / version-lock (`:1773-1805`). Route through these, do **not** reimplement guards. |
| Authorization | Wire the existing **`EcoPermissionAdapter`** (`services/eco_permission_adapter.py:38`) into `ECOService` — do **not** invent new authz. Replaces the allow-by-default `PermissionManager` (`permissions.py:36-37`). |
| Stages | `ECOStage` (`meta_eco_stages`, `eco.py:102`); seeds in `seeder/meta/eco_stages.py`. |
| Router pattern | Add a slice following the R1..R7 decomposition, `/api/v1/eco` prefix, mounted in `api/app.py:419-424`. |
| Lock read (interpose point) | The single 409 gate at `bom_multitable_router.py:395-399` is where an ECO route interposes; `is_item_locked` (`guard.py:30-36`) / `version_lock` (`lifecycle/models.py:59-61`) remain the lock authority. |

**Explicitly not reused / not created:** the deprecated `ChangeService.execute_eco` per-affected-item action model (`change_service.py:98-130`); no new "revision intent" or "superseding" entity — supersession, if ever needed, is `VersionService.release()` (`version/service.py:570-593`), decoupled from ECO apply today.

---

## 5. Invariants Preserved

Any acceptable design MUST preserve all of the following. None may be relaxed by this route.

1. **Embed read-only / read ≠ write token.** "A write is a separate, write-scoped authorization — it is never the read-only embed token" (`governed-seam:15-24`; acceptance `:123`); re-affirmed post-ship "no write path was added to the iframe" (`gated-order:125-126`). The ECO path is still an explicit, separately-gated, audited write. The embed view stays read-only (`PlmEmbedBomReviewView.vue:29`).
2. **No private key in MetaSheet2.** "MetaSheet2 receives public keys only. No Yuantus private signing key is configured in MetaSheet2." (`gated-order:65-66`, `:126`).
3. **Edition-from-`is_entitled`.** Write is gated by `EntitlementService.is_entitled(feature_key)`, which raises `ValueError → 500` on an unregistered key, so any write capability's feature key must be registered in `FEATURE_APP_NAMES` and mapped to a SKU (`day2-design-resolution:73` G2; `provider-endpoint-taskbook:20`; shipped fast-path key `bom_multitable_writeback → plm.bom_multitable_writeback`, `taskbook:9`). **Open (see §8):** whether the locked-BOM route reuses this key or requires a distinct ECO-scoped feature_key.
4. **Per-phase opt-in before code.** "No phase auto-starts" (`NEXT_CYCLE_TODO_PLAN:532`); applied here as "design-first and owner-ratified before code" (`gated-order:40`).
5. **No direct-BOM bypass.** The write path must not route through direct `bom/tree` POST/PUT/DELETE routes; the seam must provably enforce the lifecycle/change-control guard (`governed-seam:51-59`, acceptance `:122`).
6. **No auto-apply.** Write-back must never auto-apply an ECO; apply stays the separate APPROVED-gated `POST /api/v1/eco/{id}/apply` (`contract-draft:54-55`; `ADR:26-31`).

---

## 6. Proposed Implementation Phases IF Ratified (design-only outline — not built)

Mirrors the repo's grounded → build → verify → CI cadence. Nothing below is authorized until §3 is ratified.

**Phase 0 — Contract-first (provider boundary).** Define the discriminated write-back response (e.g. a lifecycle/ECO reason code on the 409, and whether `line.state`/`eco_id` surface), since the pact is success-only and `eco_id` is deferred today (`plm-adapter-yuantus.pact.test.ts:242`; `PLMAdapter.ts:741-744`). *Verify:* new pact interaction(s) for the discriminated 409; contract test green in `core-backend/tests/contract`. *CI:* pact publish + can-i-deploy.

**Phase 1 — Provider ECO-intent seam.** Interpose at `bom_multitable_router.py:395-399`: on the ratified locked path (per 3.1), call `ECOService.action_new_revision` to open/attach a PENDING ECO revision intent; never mutate the locked BOM inline; never apply. *Verify:* unit/integration tests asserting locked parent + opt-in → PENDING ECO created, zero BOM mutation, zero premature apply; locked parent without opt-in → still 409 (regression-lock the shipped behavior via `test_lifecycle_locked_parent_is_409`). *CI:* Yuantus backend suite.

**Phase 2 — Authorization wiring.** Wire `EcoPermissionAdapter` into `ECOService`, replacing allow-by-default `PermissionManager` for the ECO write/apply path (3.2/B1). *Verify:* authz tests for create/apply denied vs admitted by ECO ItemType/state/owner; approval path RBAC unchanged. *CI:* backend suite.

**Phase 3 — Consumer discrimination + UI.** Backend: stop flattening 409 at `plm-workbench.ts:869-874`; map the discriminated reason. Consumer: branch `PlmBomReviewPanel.vue` on `outcome.reason` (already threaded, `workbench.ts:857-860`) and pre-emptively gate `PlmBomReviewTable` editability on `line.state` (3.3/C2), with an ECO CTA. *Verify:* `PlmBomReviewPanel.spec.ts` cases for `lifecycle_locked`/`eco_required` vs `idempotency_conflict`; embed view stays read-only. *CI:* web unit + Playwright smoke.

**Phase 4 — E2E + closeout.** Full draft-fast-path (200) vs ECO-path (locked → PENDING ECO → approve → apply) walkthrough; invariant regression checks (embed read-only, no direct-BOM bypass, entitlement key registered). *Verify against `serve -s build`, not local dev* (per repo e2e rule). *CI:* full gate green before closeout doc.

---

## 7. Ratified (2026-07-01) / Still Awaiting Owner Ratification

Ratified direction (owner, 2026-07-01) — see Status:
- **(a) RATIFIED: A3** — explicit ECO-path opt-in (not implicit A2, not status-quo A1).
- **(b) RATIFIED: B1** — reuse the existing ECO approval workflow. **Its pre-condition — wiring `EcoPermissionAdapter` into `ECOService` — is a repo-wide authz change and is NOT authorized by this ratification; it stays separately gated (§3.2 blast-radius).**
- **(c) RATIFIED: C2** — pre-emptive `line.state` gating + discriminated-409 fallback. **Its prerequisite — a discriminated-409 at the provider/contract boundary — is a contract change and is NOT authorized by this ratification; it stays separately gated.**

Still awaiting ratification / open:
- Whether the locked-BOM route reuses the `bom_multitable_writeback` feature_key/permission or requires a distinct ECO-scoped feature_key/SKU (the ECO variant re-opens Fork 2).
- Whether the provider (Yuantus) will emit a discriminated 409 at all — a hard prerequisite for C1/C2; the pact defines no error interaction.
- Whether write-back should be the next thing built at all (an owner open question at `governed-seam:147-152`).
- **No endpoint / pact schema / UX is specified here** (`governed-seam:5-8`).

---

## 8. Open Questions

1. **Real vs seed lock set.** Which tenants set `version_lock=True` on which states in production vs the CLI seed? A tenant marking Review (or a custom state) locked moves it into the ECO/409 set — behavior is fully data-driven off `meta_lifecycle_states`.
2. **Stale router comment (pre-existing minor defect).** The `bom_multitable_router.py` comment listing "Released/Review/Suspended/Obsolete" as locked disagrees with `cli.py` (which does not lock Review or Draft). Confirm whether this reflects an intended future seed change or is stale; if stale, a one-line comment fix is a safe, separate housekeeping change (out of scope here).
3. **Provider 409 shape.** Does Yuantus actually distinguish a lifecycle-lock 409 from an idempotency-conflict 409 on the write-back endpoint, or is it opaque? The pact defines no error interaction (`plm-adapter-yuantus.pact.test.ts`).
4. **Authz deployment default.** Is any caller constructing `PermissionManager(enforce=True)` for ECO in production, or is allow-by-default the deployed behavior? Not traced beyond the default (`permissions.py:36-37`).
5. **`ECOState.CONFLICT` writer.** The enum defines it (`eco.py:53`) but the writer was not traced; likely the rebase/compute-bom-changes path — confirm whether `ECO.state` itself is ever set to `conflict`.
6. **Audit trail.** Does the backend PATCH route emit an `operation_audit_logs` entry for a governed BOM write-back attempt/failure? Relevant if the ECO route needs an audit trail for locked-write attempts. (Note: the successful write path already writes `meta_bom_writeback_audit` rows, surfaced by the `#928` audit readout.)
7. **Unified approval bridge.** `approval_automation_eco_service.py` / `approval_automation_eco_router.py` (bridge to a generic ApprovalRequest system) were not read in depth; if the design needs the unified engine rather than ECO-native `ECOApproval`, that is the integration point.
8. **Consumer provenance.** The consumer-stack findings are read from MetaSheet2 `origin/main` (PRs `#3383`/`#3384`/`#3392`); a local MetaSheet2 working branch may not yet contain the BOM write-back code, so any local build/test must be run against `origin/main`, not a stale branch.

---

## 9. References

- Owner-ratify decision points + gated ordering: `plm-collab-remaining-gated-development-order-and-verification-20260630.md` (`:40`, `:110`).
- Line dev & verification record (current, merged as `#930`/`c5053bfb`): `plm-collab-line-dev-and-verification-20260630.md`.
- Remaining-TODO closeout: `plm-collab-remaining-todo-dev-verification-20260630.md` (`:113`).
- Day-2 design resolution (Draft fast-path vs deferred ECO route): `plm-collaboration-phase7-writeback-day2-design-resolution-20260629.md` (`:7`, `:9`).
- Governed-seam design + invariants: `plm-collaboration-phase7-writeback-governed-seam-design-20260627.md`.
- Prior (superseded) ECO-intent contract draft (#884/#885): `plm-collaboration-phase7-writeback-contract-draft-20260627.md` (`:45-55`).
- Canonical ECO write-path ADR: `ADR_PLM_CORE_CONVERGENCE_ECO_WRITE_PATH.md` (`:26-31`).
