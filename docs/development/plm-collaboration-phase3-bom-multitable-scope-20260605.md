# PLM Collaboration — Phase 3 (BOM Multi-table) Scope & Design Package

**Date:** 2026-06-05
**Status:** SCOPE/DECISION PACKAGE — doc-only. NO implementation. Converges BOM multi-table
from an idea into per-slice product/technical boundaries so P3-A…P3-D can each be cut narrow.
**Repos:** YuantusPLM (`zensgit/yuantus-plm`, provider) + metasheet2 (`zensgit/metasheet2`, consumer).

This consolidates and sharpens the BOM-multi-table design already framed in the canonical
plan (`docs/development/plm-collaboration-automation-development-plan-20260602.md`, 铁律 5/6 +
the identity/embed spine) against the current code, and fixes the owner-ratified scope
decisions (F-A/F-B/F-C below). It does not re-decide the canonical invariants — it grounds them.

---

## 0. Owner-ratified scope decisions (2026-06-05)

- **F-A — Minimal sellable = a READ-ONLY BOM review table.** Sell the "BOM multi-dimensional
  review / collaboration view" first: project the BOM read-only into a multi-table review
  surface with MetaSheet-local collaboration fields. **No write-back, no PLM-embed in the
  minimal cut.** Write-back (`/aml/apply`) and the iframe spine are later slices.
- **F-B — The authoritative/collaboration red line (confirmed).** PLM authoritative fields are
  **read-only snapshot** (`item_number`/`name`/`state`/`revision`/`generation`/`current_version`/
  `quantity` …). MetaSheet-editable collaboration fields live ONLY in MetaSheet (owner, note,
  tags, review-status, due date, review comments …) and are NEVER written back to PLM
  authoritative fields. Any future write-back MUST go through a governed endpoint.
- **F-C — The identity/SSO/embed spine is in this design, but is NOT a P3-A/B/C dependency.**
  It must be specified here so Phase 3 doesn't scatter, but P3-A (projection), P3-B
  (SKU/capabilities) and P3-C (metasheet2 consume) ship without iframe embedding. P3-D owns the
  short-token / iframe / `apiTokenAuth` base-scope heavy lifting.

---

## 1. Why a scope package first (not a feature)

Approval automation already has the full ladder (sellable gate → draft templates → ECO governed
projection → scenario entry → cross-repo capability discovery). BOM multi-table is higher risk:
the core is **not a button**, it is governed projection of BOM/Part data + a field boundary +
identity/embed + the reserved-key→SKU timing + how PLM keeps up via capabilities/pact. Cutting
those boundaries on paper first is what lets P3-A…P3-D each stay narrow.

The proven foundation to EXTEND (this session, on `main`):
- **Governed read-only projection** — P2-C `GET /api/v1/approvals/automation/eco/{eco_id}/context`
  (read-only ECO snapshot, auth → is_entitled → then query, no existence leak). BOM is the same
  shape with the object swapped to BOM/Part.
- **Independent-SKU entitlement** — P2-A `is_entitled` single judgment; `FEATURE_APP_NAMES` lights
  a key to an app_name.
- **Advisory capability handshake** — P2.5 `GET /api/v1/integrations/capabilities` (provider) +
  metasheet2 C1/C2/C3 (consumer fetch → relay → UI degrade). BOM extends the SAME manifest.

---

## 2. Object mapping (PLM → multi-table)

PLM already exposes BOM as a graph; metasheet2 already consumes the BOM read surface (pact-pinned).

| PLM (Yuantus) | grounding | Multi-table |
|---|---|---|
| `Item` (`meta_items`) | `src/yuantus/meta_engine/models/item.py:20` (id, item_type_id, config_id, generation, is_current, state, current_version_id, **properties JSON**) | a **record** (a Part / a BOM-line target) |
| BOM **"Part BOM" relationship-Item** (an `Item`, NOT the legacy `Relationship` class) | `BOMService.add_child` (`services/bom_service.py:777`) creates `Item(item_type_id="Part BOM", source_id, related_id, properties)`; `_build_tree` (:293) reads it via `Item.source_id` | a **BOM line** (parent→child; line attributes in the relationship-Item's `properties`) |
| `ItemType` (+ `property`) | AML metadata: `GET /api/v1/aml/metadata/{itemType}` (name/label/type/required/length/default) | a **table + its columns** |

This `ItemType≈table / property≈field / Item≈record` isomorphism is the canonical plan's anchor
(dev-plan §line 104). The BOM read endpoints MetaSheet ALREADY consumes (pact-pinned in
`contracts/pacts/metasheet2-yuantus-plm.json`):
`/api/v1/bom/{id}/tree`, `/where-used`, `/substitutes`, `/bom/compare`, `/bom/compare/schema`.

**Minimal-cut object set (F-A):** the **BOM tree of one Part** (`/bom/{id}/tree`) projected into a
review table — parent Part as context, each child BOM line (a "Part BOM" relationship-Item) as a row. `where-used`,
`substitutes`, `compare` are review aids, deferred past the minimal cut unless trivially additive.

---

## 3. Field boundary — the F-B red line (governed projection, not authoritative mirror)

Per 铁律 5 (dev-plan §line 65): long-tail data lives in MetaSheet referencing PLM part numbers;
PLM fields are an allowed **read-only snapshot** (a review table needs comparable field values —
pure "link, not mirror" is engineering-infeasible) but the snapshot is read-only and carries
provenance, and MetaSheet is NEVER the authority for them.

**Read-only authoritative snapshot (projected, never editable in MetaSheet):**
- Part identity/state: `item_number`, `name`, `state`/`current_state`, `revision`/`generation`,
  `current_version`, `is_current`.
- BOM-line authoritative: a BOM line is a **"Part BOM" relationship-Item** (an `Item` with
  `item_type_id="Part BOM"`, `source_id`/`related_id`), NOT the legacy `Relationship` class. The
  per-line attributes live in that relationship-Item's `properties` — `quantity`/`uom`/`find_num`/
  `refdes`, written by `BOMService.add_child` (`services/bom_service.py:777`; keys at
  `LINE_FIELD_KEYS` :25) — plus the child `item_number`. (NOTE: the legacy `Relationship` class
  (`relationship/legacy_models.py`) is NOT the current BOM write/read source; `max_quantity` there
  is a type-level constraint on `RelationshipType`, not the per-line quantity.)
- Curated `properties` (the AML-declared read-only Part attributes relevant to review).
- **Provenance markers on every snapshot row** (铁律 5): `source_version` / `source_updated_at` /
  `sync_status` so MetaSheet can detect staleness (mirrors P2-C's `source_updated_at` +
  `sync_status:"snapshot"`).

**MetaSheet-local collaboration fields (editable, MetaSheet-authoritative, NOT projected back):**
- `owner` / assignee, review `status`, `tags`, `note`/comments, `due_date`, review opinion.

**Hard rule:** the minimal cut writes NOTHING back to PLM. When write-back is added (a later slice,
not P3-A…D's minimal scope), it goes ONLY through a governed endpoint (`POST /api/v1/aml/apply` for
field writes, or the approval `/actions` path) so version/release/esign/permission re-apply (铁律 6,
dev-plan §line 66/216) — never a direct table-cell→PLM write.

---

## 4. Permission / identity / SSO / embed spine (F-C — design here, P3-D implements)

The canonical plan calls this the "identity/embed spine — the prerequisite for 'inside the PLM UI'"
(dev-plan §line 98) and a "one-time up-front investment." Grounded current state:

- **Existing assets (reusable):** `apps/web/src/multitable/views/MultitableEmbedHost.vue` (single
  base/sheet/view + `embedded` flag + postMessage + `allowedOrigins` allowlist, dev-plan §line 113);
  `packages/core-backend/src/auth/dingtalk-oauth.ts` (common-IdP candidate; the canonical plan §line
  116 abbreviates it as `auth/dingtalk-oauth.ts`).
- **Gaps (net-new for P3-D):** `packages/core-backend/src/middleware/api-token-auth.ts` is **defined
  but mounted nowhere**; API tokens are **global-scoped, not base-scoped** (`api-tokens.ts:20`); the
  embed routes are `requiresAuth:true` (§line 125). So cross-origin + base-scoped embed auth is
  net-new.
- **Spine options (P3-D decision, framed now):** (i) **DingTalk as common IdP** (PLM also logs in via
  DingTalk) — lowest friction if both sides already do DingTalk; (ii) **PLM→MetaSheet short-token
  exchange** + `apiTokenAuth` mounted with a **base-scoped** token + a `workbench.html`
  auth-gated iframe slot.

**Why P3-A/B/C don't need it:** the read-only projection (P3-A), the SKU/capabilities (P3-B) and the
metasheet2 backend/UI consumption (P3-C) all work with the EXISTING per-data-source auth (the same
auth C1/C2 already use); the embed spine only becomes a hard dependency for P3-D ("BOM table actually
inside the PLM BOM screen").

---

## 5. Capability manifest + entitlement (extend, don't reinvent)

- **SKU lighting (P3-B):** `bom_multitable` is currently a RESERVED key →
  `FEATURE_APP_NAMES["bom_multitable"] = frozenset()`
  (`src/yuantus/meta_engine/app_framework/entitlement_service.py:36`, always False). P3-B lights it
  to an **independent SKU** app_name (e.g. `plm.bom_multitable`), exactly as P2-A lit
  `approval_automation → {"plm.approval_automation"}`. Timing: light it when P3-A's projection
  endpoint exists (so an entitled tenant has something to consume) — not before.
- **Capability manifest (P2.5 extension):** the integration manifest
  (`GET /api/v1/integrations/capabilities`) already advertises every `FEATURE_APP_NAMES` key with
  `supported` derived from lit-ness. Lighting `bom_multitable` automatically flips its `supported`;
  P3-B adds its descriptor (`api_version`, `scenarios:["bom_review"]`, no `actions` in the read-only
  cut). The metasheet2 consumer (C1/C2/C3) then discovers it **with no consumer change** beyond
  rendering a new feature block — the handshake we built is the reason this is cheap.

---

## 6. Minimal sellable scope (F-A = the "BOM review table" skeleton)

**In (the sellable skeleton):**
- Yuantus: a governed READ-ONLY BOM-context projection endpoint (P3-A) — one Part's BOM tree as a
  review snapshot with provenance markers, entitlement-gated by `is_entitled("bom_multitable")`.
- Yuantus: light `bom_multitable` + advertise it in the capability manifest (P3-B).
- metasheet2: consume the BOM capability + render a read-only BOM review table with MetaSheet-local
  collaboration fields (P3-C), degrading by `supported`/`entitled` (reuse C1/C2/C3).

**Out (explicitly later, not the minimal cut):**
- Write-back to PLM (any field) — deferred; only ever via `/aml/apply` or approval `/actions`.
- The iframe embed / "inside the PLM screen" (P3-D + the identity spine).
- `where-used` / `substitutes` / multi-Part compare tables (review aids; additive later).
- Kanban / grouping / filtering beyond a basic review table.

This mirrors the approval line's "skeleton first, capabilities later" and keeps the execution/embed
risk out of the first sellable cut.

---

## 7. Cross-repo follow strategy (how PLM keeps up)

- **Additive pact:** when metasheet2 starts consuming a NEW Yuantus BOM endpoint, the consumer pact
  (`packages/core-backend/tests/contract/pacts/metasheet2-yuantus-plm.json`) gains an interaction →
  Yuantus provider verification (`test_pact_provider_yuantus_plm.py`) goes red until P3-A implements
  it → contract-first, the broken test names the gap. (Note: the P3-A projection IS a required
  capability once consumed, unlike the advisory `/integrations/capabilities` which is deliberately
  un-pacted.)
- **Advisory capabilities for feature discovery:** the P2.5 manifest stays the soft channel — PLM
  advertises `bom_multitable` supported/version; MetaSheet degrades if a PLM is older/unlit.
- **Schema additive-only:** the manifest + the projection payload evolve additively (a v1 consumer
  must tolerate a forward-compatible future shape), as established for the capability manifest.

---

## 8. Slice sequence (entry/exit criteria — each its own opt-in)

| Slice | Scope | Entry | Exit |
|---|---|---|---|
| **P3-A** Yuantus BOM governed projection | read-only BOM-context snapshot endpoint (like P2-C, object = BOM/Part); provenance markers; entitlement-gated; NO write-back, NO embed | this package ratified | endpoint + endpoint/service tests on main; the EXISTING provider pact stays green (the new projection has NO consumer pact interaction yet — P3-C adds it, after which provider verification pins the projection contract) |
| **P3-B** `bom_multitable` SKU + capabilities | light the reserved key to an independent SKU; manifest descriptor (supported/api_version/scenarios) | P3-A endpoint exists | lit + advertised; entitlement tests; route/pin updated |
| **P3-C** metasheet2 consume BOM capability | backend adapter method + relay route + frontend read-only review table with collaboration fields; degrade by supported/entitled (reuse C1/C2/C3) | P3-A/B on main | review table renders; vitest specs; CI green |
| **P3-D** embed / collaboration surface | identity spine (short-token / DingTalk IdP), `apiTokenAuth` base-scope, iframe slot; BOM table inside the PLM BOM screen; PLM fields still read-only, write-back only via governed endpoint | P3-C shipped + spine decision | embedded review surface; auth-gated iframe |

Each slice needs its own explicit opt-in (per the standing per-phase discipline). Write-back to PLM
authoritative fields is a deliberate non-goal of P3-A…P3-D's minimal sellable scope.

---

## 9. References (grounding)

- Canonical plan: `docs/development/plm-collaboration-automation-development-plan-20260602.md` (铁律 5/6 §65/66; spine §98; assets §104/113/116; gaps §120/125; sequence §227).
- BOM/Part models: `src/yuantus/meta_engine/models/item.py:20` (Item; a BOM line = a "Part BOM" relationship-Item created at `services/bom_service.py:777`, read via `Item.source_id` in `_build_tree` :293). The legacy `Relationship` class (`relationship/legacy_models.py`) is NOT the current BOM source.
- Pact BOM endpoints: `contracts/pacts/metasheet2-yuantus-plm.json` (`/bom/{id}/tree`, `/where-used`, `/substitutes`, `/bom/compare`).
- Entitlement: `src/yuantus/meta_engine/app_framework/entitlement_service.py:36` (`bom_multitable` reserved).
- Proven patterns to extend: P2-C ECO projection (`approval_automation_eco_service.py`), P2.5 manifest (`integration_capabilities_service.py`), metasheet2 C1/C2/C3 (`PLMAdapter.getIntegrationCapabilities`, `plm-workbench` capabilities route, `IntegrationWorkbenchView`).
- metasheet2 spine assets: `apps/web/src/multitable/views/MultitableEmbedHost.vue`, `packages/core-backend/src/auth/dingtalk-oauth.ts`, `packages/core-backend/src/middleware/api-token-auth.ts` (unmounted).
