# PLM × MetaSheet Integration — Development Plan & TODO (2026-06-29)

Type: **reviewable development plan + TODO** for the PLM×MetaSheet integration line. Grounded
against a **review baseline** of both repos — YuantusPLM `c603f5fd` / metasheet2 `8bb4a757` — via a
four-dimension read-only code review (provider surface · consumer surface · contract chain ·
roadmap docs); **re-verified against current heads `6b97a461` / `1bc03320` (docs-only movement — no
status change)**. Items are tagged with real status, the repo that owns them, the owner gate, and
acceptance criteria. This authorizes **no build** on its own; it is the slate to review and pick from.

---

## 0. One-paragraph status

The integration is **feature-mature on the read + governance + contract spine**, and Phase 7
write-back is **fully live on the provider and contract-verified end-to-end** — but write-back is
**"plumbed, not surfaced" on the consumer**: the `PLMAdapter` method and the frozen pact exist, yet
there is **no workbench relay route and no edit UI**, so a MetaSheet user cannot perform a governed
BOM edit yet. Closing that consumer gap (T1) is the single highest-value remaining build. Everything
else is either owner-deferred (Phase 6), a cheap closure (T2), an opt-in track (lines / commercial),
or infra/ops-blocked.

---

## 1. Current state — what is shipped (grounded)

| Surface | Layer | Status |
|---|---|---|
| Entitlement / SKU spine (6 lit SKUs, tenant-scoped `is_entitled`, advisory capability manifest) | Yuantus provider | ✅ LIVE (manifest advertises write-back `"governed"`) |
| BOM multi-table **READ** projection — `GET /bom/multitable/{part}/context` | Yuantus provider | ✅ LIVE |
| Embed-token spine — Ed25519 mint → offline verify → origin + served-tenant cross-check → single-use jti | both | ✅ LIVE (fail-closed, pre-query) |
| BOM **READ** review UI — workbench panel + embedded iframe view (read-only table) | metasheet2 consumer | ✅ LIVE |
| PLM parent-page embed **host** — `plm_workspace.html` BOM-Review iframe + origin-pinned `postMessage` token + Re-authorize | Yuantus UI | ✅ LIVE (V1.2 public-staging evidence) |
| Broad PLM adapter — products, BOM tree, where-used, compare, substitutes, approvals (list/detail/history/approve/reject), CAD ×6, release-readiness, metadata | metasheet2 consumer | ✅ LIVE |
| Phase 7 BOM **WRITE-BACK** provider — `PATCH /bom/multitable/{part}/lines/{line}` + service + audit + per-tenant composite idempotency (#909) | Yuantus provider | ✅ LIVE |
| Write-back **contract** — PATCH interaction in both pacts (34 each) w/ `Idempotency-Key`; blocking broker `can-i-deploy` | both | ✅ GREEN |
| Lines 2/3/4 first slices — lifecycle filters / effectivity ops / licensing admin | Yuantus provider | ✅ LIVE (parallel) |
| Pact broker — Phase-B blocking gate on PRs + main | Yuantus | ✅ LIVE |

### Scaffolded / deferred (named, not omissions)
- **Write-back consumer relay route + edit UI** — ABSENT (see T1). `PLMAdapter.updateBomMultitableLine()` + pact exist; no `plm-workbench` PATCH route, `PlmBomReviewTable.vue` is read-only.
- **Phase 6 SSO / identity-session** — deferred-by-default (#911); sole remaining trigger is bridge activation.
- **`metasheet_bridge`** — flag-gated `/health` stub (`ENABLE_METASHEET`); inert; gated on Phase 6.
- **approval_automation actions** — advertised `"stubbed"`; execution engine deferred (Phase 2 skeleton).
- **lifecycle-locked edit → full ECO route** — deferred (locked parent → 409).
- **`uploadDrawing()`** (consumer) — mock stub.

---

## 2. Development TODO (prioritized)

### T1 — Complete the BOM write-back **consumer surface**  ·  **P0**
- **Status:** REMAINING (the one provider-live, contracted capability not reachable by a user).
- **Repo:** metasheet2 (consumer). **Owner gate:** metasheet2 build → explicit go.
- **Scope:** (a) **extend `PLMAdapter.updateBomMultitableLine` to accept an explicit `idempotencyKey`** — today it mints `randomUUID()` internally per call, so a user-retry generates a *new* key and is NOT deduped; the UI must generate one key per logical submit and reuse it on retry (fall back to `randomUUID()` when absent). Then a capability-gated `plm-workbench` relay route `PATCH /api/plm-workbench/data-sources/:id/bom/multitable/:partId/lines/:bomLineId` threads that key through; (b) an **edit affordance** in `PlmBomReviewTable.vue` for the four editable cells (quantity/uom/find_num/refdes) → optimistic submit → reflect `{ok, bom_line_id}` / surface 403/404/409/422; (c) wire the relay into the workbench (not the read-only embed view — the §0 invariant keeps the embed read-only).
- **Acceptance:** a user edits a Draft BOM line cell in the workbench → governed write-back → provider applies + audits; entitlement/permission/lifecycle/idempotency errors render as actionable UI states; the embed iframe stays read-only.
- **Refs:** provider `bom_multitable_router.py` PATCH; `PLMAdapter.updateBomMultitableLine`; `plm-workbench.ts`; `PlmBomReviewTable.vue`.

### T2 — Cross-line idempotency regression test  ·  **P1 (cheap closure)**
- **Status:** REMAINING (logic covered in the service; test missing on main).
- **Repo:** Yuantus. **Owner gate:** none (small test-only PR) — or fold into the next Phase 7 touch.
- **Scope:** add `same Idempotency-Key / different bom_line_id / same payload → 409` to `test_bom_multitable_writeback.py` (router + service level).
- **Acceptance:** the test asserts a key reused across two lines (identical cells) → 409, never the first line's cached result.
- **Refs:** `bom_multitable_writeback_service.py` (the `part_id`+`bom_line_id`+`after` cached-vs-conflict branch); `test_bom_multitable_writeback.py`.

### T3 — Phase 6 SSO fork decisions  ·  **owner decision (deferred-by-default)**
- **Status:** DEFERRED (design baseline #880 merged; #911 set defer-by-default).
- **Owner gate:** decide **is the continuous session needed now?** — i.e. is **bridge activation** the next product line? If no → stays deferred. If yes → resolve Fork 1 (A consumer `mst_` session vs **B** Yuantus-issued renewable, default B), Fork 2 (**PLM-issued** vs shared IdP, default PLM-issued), Fork 3 (~15-min renewable), Fork 4 (renewal-time denylist).
- **Refs:** `plm-collaboration-phase6-sso-identity-session-spine-design-20260627.md` (§3 forks, §7 open questions; STATUS banner records the defaults).

### T4 — Phase 6 session build  ·  **gated on T3 = yes**
- **Status:** DESIGN-GATED. **Repo:** Yuantus first (issue/verify/renew + B2 denylist), then metasheet2 (carry the served-tenant cross-check into the session path).
- **Acceptance:** the §5 criteria — session inherits served-tenant cross-check on every data call; renewal re-runs entitlement + served-tenant + revocation; read-only scope only; graceful fallback to the one-shot handshake.

### T5 — Bridge activation (`metasheet_bridge`)  ·  **gated on T4**
- **Status:** DESIGN-GATED (currently a flag-gated `/health` stub). Real bridge I/O behind the continuous session + per-tenant entitlement.

### T6 — Phase 7 write-back fast-follows  ·  **deferred v1 improvements**
- **Status:** DESIGNED, NOT BUILT. **Repo:** Yuantus (+ consumer for headers).
- **Scope:** `If-Match`/412 optimistic concurrency (v1 is last-write-wins); deeper ECO checkout-lock depth (v1 is depth-1 parent lock); the lifecycle-locked → **ECO revision route** (v1 returns 409).
- **Refs:** `plm-collaboration-phase7-writeback-day2-design-resolution-20260629.md` (§9, R4).

### T7 — Lines 2/3/4 **next** slices  ·  **owner opt-in**
- **Status:** SCOPED, first cuts shipped; next slices not built.
- **Scope:** **L2** — export + aggregates (top reasons / most-failed items/actors), cross-item search, an ops drill-down view. **L3** — fuller date-obsolete impacts view (revert, export, ad-hoc queries) beyond the shipped batch-acknowledge. **L4** — first-cut slices DONE; only commercial ops remain (→ T8).
- **Refs:** `backlog-lines-2-3-4-scoping-taskbook-20260627.md` (#882).

### T8 — Commercialization hardening  ·  **larger track, owner-prioritized**
- **Status:** DESIGNED, NOT BUILT.
- **Scope:** vendor-side license-issuance CLI + key custody/runbook; license quantity/seats/grace/renewal semantics; admin UX for import/status/feature-availability; multi-`kid` key rotation (no flag-day); cross-repo product-compatibility gates (capability/embed/multitable/relay payload-shape guardrails).
- **Refs:** `plm-collaboration-current-state-commercialization-and-roadmap-20260618.md` (gaps + recommended sequence).

### T9 — Infra / ops enablement  ·  **blocked on environment, not code**
- **Status:** BLOCKED (prerequisites outside these repos).
- **Items:** consumer-side `can-i-deploy` (needs a provider-verification **webhook** so the broker triggers Yuantus verification on consumer publish); owned-HTTPS staging re-run of the V1.2 instrument (#876, config-only, needs ops domains); `--to-environment` deploy gate (needs a pipeline that records deployments). *(The PLM parent-page embed host is **LIVE** — `plm_workspace.html` iframe host + origin-pinned `postMessage` + Re-authorize, V1.2 staging evidence — not a remaining item; see §1.)*
- **Refs:** `plm-collab-line-remaining-development-plan-20260625.md`.

---

## 3. Recommended sequencing

1. **Now (highest value):** **T1** (write-back consumer relay + edit UI) — converts the finished Phase 7 provider into actual user capability. **T2** (test nail) alongside or as a quick Yuantus PR.
2. **Owner-decision-gated:** **T3** → (if yes) **T4** → **T5**. Stays parked otherwise.
3. **Opt-in tracks:** **T7** (line next-slices), **T8** (commercialization), **T6** (Phase 7 fast-follows) — pick by product priority.
4. **Environment-blocked:** **T9** — unblock as ops/infra prerequisites land.

## 4. Owner decisions this plan needs

1. **T1 go?** (metasheet2 write-back relay + edit UI — the recommended next build.)
2. **Phase 6 (T3):** is bridge activation / continuous in-iframe UX the next line? (If no, T3–T5 stay deferred.)
3. **Priority among the opt-in tracks** (T7 lines vs T8 commercialization vs T6 fast-follows).
4. **T2** — standalone now, or folded into the next Phase 7 touch.

---

## References (grounding)
- Provider: `bom_multitable_router.py`, `bom_multitable_writeback_service.py`, `bom_multitable_projection_service.py`, `bom_multitable_embed_token_service.py`, `entitlement_service.py`, `integration_capabilities_service.py`, `metasheet_bridge.py`, `meta_bom_writeback_audit.py`.
- Consumer (metasheet2): `PLMAdapter.ts`, `embed-token-auth.ts`, `routes/plm-embed.ts`, `auth/embed-jti-store.ts`, `routes/plm-workbench.ts`, `PlmBomReviewPanel.vue` / `PlmBomReviewTable.vue` / `PlmEmbedBomReviewView.vue`, `packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json`.
- Plans/closeouts: #882 backlog taskbook; Phase 6 #880; Phase 7 day-2 #901 + provider dev/verification; `plm-collab-line-remaining-development-plan-20260625.md`; commercialization roadmap 20260618.
