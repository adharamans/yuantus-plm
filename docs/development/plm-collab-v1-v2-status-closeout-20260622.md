# PLM-Collab V1 / V2 — current status closeout (2026-06-22)

**Authority / scope.** A point-in-time snapshot of what is **landed on `main`** (observed at review
time; `main` moves fast, so the **per-PR refs below are the durable anchors**, not a main SHA), what
is **deferred** (named, owner-gated), and the **next-phase gates**. Complements
— does not replace — the broader roadmap
(`plm-collaboration-current-state-commercialization-and-roadmap-20260618.md`) and the V1 pact
boundary (`plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md`, #833). No code change.

## 1. Done — landed on `main`

**V1 — BOM-Review external trial surface**
- Two **entitlement-gated** read surfaces — capability manifest (`GET /api/v1/integrations/capabilities`,
  advisory) + BOM multi-table context (`GET /api/v1/bom/multitable/{part_id}/context`, governed,
  read-only). Unentitled → `entitled:false` / `context:null`, **no existence leak**.
- A **consumer-driven pact** (32 interactions, artifact hash `5ecbe1ee…`) pins both sides; the V1.1
  provider seed + consumer sync landed in **#805**. Pact protection boundary + operator staging
  checklist documented in **#833** (the V1 pact boundary + staging-checklist record).

**V2 — seats (Option A)**
- **S1/S2 import-time projection** (#817): a vendor-signed license's `seats` → identity-side
  `TenantQuota.max_users` (the cap the existing `QuotaService` provisioning gate enforces),
  `QUOTA_MODE`-gated (default-off → inert); `is_entitled()` stays seats-free.
- **Cap clearing** (#836): explicit `seats: null` clears `max_users` (→ unlimited); **absent** = no-op
  (backward-compatible); `seats: 0` stays illegal; dogfood signer `--clear-seats`; the audit
  distinguishes `max_users=N` from `max_users=cleared`.

**Lifecycle transition-history (audit line)**
- **Slice 1** durable write in `promote()` (#814); **Slice 2** item-scoped read (#816); **forensic
  admin route** (superuser-gated, by `item_id`, no existence gate — reaches a deleted item's retained
  FK-free history) (#827); **per-item ACL** on the item-scoped read (`check_permission` → 403,
  matching bom/impact) (#831). Two-tier model: item-scoped = per-item ACL, forensic = superuser.

**Doc alignment** — design/taskbook docs aligned to the shipped state (#835 forensic docstring; #838
transition-history taskbook + route-count; the seats design docs folded into #836).

## 2. Deferred — named, owner-gated (recorded, not dropped)

- **V2 seats:** the **B2 per-SKU seat-assignment subsystem** (the only way to sell SKU seat packs
  independently); **MetaSheet-consumer seat reconciliation**; the **vendor-private license issuance
  tool** (the dogfood signer is the in-repo dev stand-in).
- **Transition-history:** **all-attempts logging** — the `outcome` column is reserved for logging
  *rejected* transitions (D2), a future non-breaking extension.
- **V1.2 embed / SSO:** in-PLM embed host + embed-token pact + iframe/origin/CSP/Redis-jti; cross-repo
  SSO; write-back; approval-automation execution. All separate owner-gated lines; none built or
  pinned.

## 3. Next-phase gates (decide before opening the next line)

- **Pact broker.** The two repos' pact files are synced **manually** (`sync_metasheet2_pact.sh
  --check --verify-provider`); there is **no automatic cross-repo gate** (#833 §3). A pact broker
  (MetaSheet2 CI publishes the consumer pact → the Yuantus provider verifier consumes the published
  version) makes sync + version compatibility a first-class CI gate. Owner-gated infra slice.
- **V1.2 embed / SSO.** Needs an **identity-session decision** + **metasheet2 coordination** — not a
  clean Yuantus-only build. Gate this before opening the embed/iframe line.
- **B2 seat-assignment.** Open only if **SKU-independent seats** become a real commercial need; until
  then Option A (tenant-wide `max_users` cap) is sufficient and shipped.

## 4. Pointers

- Roadmap: `plm-collaboration-current-state-commercialization-and-roadmap-20260618.md`
- V1 pact boundary + staging checklist: `plm-collab-v1-pact-boundary-and-staging-checklist-20260621.md` (#833)
- Seats: `plm-collab-v2-seats-design-20260619.md` + `plm-collab-v2-seats-cap-clearing-design-20260621.md`
- Transition-history: `lifecycle-transition-history-taskbook-20260619.md` + the DEV/V docs
  (`DEV_AND_VERIFICATION_LIFECYCLE_TRANSITION_HISTORY{,_READ,_FORENSIC,_PER_ITEM_ACL}`).
