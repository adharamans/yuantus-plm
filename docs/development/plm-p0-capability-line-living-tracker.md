# PLM P0 capability line — LIVING tracker (claim board)

> **Single living source of truth for the P0 capability line. UPDATE IT IN PLACE** — do not spawn
> dated snapshot docs per slice. **Claim a surface in §2 before building it** (one owner/branch per
> surface). Operating rules mirror `plm-collab-integration-line-living-tracker.md` §4.
> Formal basis: `docs/PLM_CAPABILITY_GAP_ANALYSIS_20260702.md` (conclusion baseline
> `origin/main@8959d844`). Opened/gated status below records the owner's 拍板 of **2026-07-02**.

## 1. Owner decisions (2026-07-02)

- **OPEN**: P0-① notification delivery + subscriptions; P0-⑧a Method sandbox (**priority**),
  P0-⑧b inbound rate limiting (after 8a; **two separate PRs**, never bundled).
- **OPEN on the integration line** (claimed THERE, not here): locked-BOM ECO revision route
  **Phase 0 (contract-first)** — see `plm-collab-integration-line-living-tracker.md` §2.
- **GATED** (each needs its own explicit opt-in before any taskbook/code): P0-② unified task
  inbox, P0-③ workflow semantics + REST, P0-④ document full-text search, P0-⑤ bulk import,
  P0-⑥ mass-replace wizard, P0-⑦ ECR/ECN objects, P0-⑨ form/grid admin API, P0-⑩ GraphQL
  mount-or-delete decision; all P1/P2 report items.
- Phase-6 SSO: stays **deferred**. Date-obsolete DP1-iii and the commercial subset: stay **gated**.

## 2. Surfaces (the claim board)

| Surface | State | Owner / branch (claim) | Hard technical premises (owner findings 2026-07-02) & gate |
|---|---|---|---|
| P0-⑧a Method script sandbox | 🔨 OPEN — taskbook next (first in line) | claude / `docs/p0-8a-method-sandbox-taskbook` | There are **two** raw `exec` paths: `business_logic/executor.py:44` **and** `services/method_service.py:65` (its own comment: "In production, this must be sandboxed"). Build a **shared sandbox adapter and switch both entries in the same slice** — fixing one leaves a bypass. Resource limits + execution audit; admin-only until landed (report §六.5). |
| P0-① Notification delivery + subscriptions | 🔨 OPEN — taskbook after 8a starts | claude / `docs/p0-1-notification-outbox-taskbook` | The generic event layer is an **after-commit in-memory EventBus** (`events/transactional.py:48` → `events/event_bus.py`): no persistence, retry, dead-letter, or delivery state — trigger source ONLY. First slice MUST add persistent **NotificationOutbox/Delivery tables + a dedicated worker** (pattern: `erp_publication`/`ecm_publication` outbox: state machine, retry, dead-letter, idempotency fingerprint). Delivery adapters: SMTP first, IM/webhook later. Subscription model + digest coalescing. |
| P0-⑧b Inbound rate limiting | 🔨 OPEN — after 8a | — (claim when 8a taskbook lands) | Per-tenant token-bucket middleware; new envs declared in `Settings` (extra=ignore swallows undeclared); separate PR from 8a. |
| P0-②③④⑤⑥⑦⑨⑩ | ⛔ GATED | — | Per-item opt-in. Sequencing guidance in the report §"三批次" plan: ②③ after ① (催办/分发 depend on delivery); ⑦ after the ECO-route Phases 1–2 (same change domain — claim-before-build). |
| P1 / P2 report items | ⛔ GATED | — | See report §五. |

## 3. Delivery shape (every slice, no exceptions)

1. taskbook (scope-lock, source-grounded) → impl PR → external review; **one slice, one PR**.
2. New feature keys registered in `FEATURE_APP_NAMES` + SKU mapping; gate via `is_entitled` only
   (writes: `require_admin_user` before `is_entitled`; reads: auth → entitlement → query).
3. New routers update the route-pin contract tests; new test files added to the ci.yml explicit
   list AND every other CI enumeration (conftest allowlist, portfolio globs); new env vars
   declared in `Settings`.
4. Alembic stays single-head; new tables always via migration trees.
5. Consumer pact stays green; outward payload changes run dry-run + contract validation first.
