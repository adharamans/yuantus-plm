# PLM P0 capability line — TODO & development order

**Date:** 2026-07-02 · **Basis:** `docs/PLM_CAPABILITY_GAP_ANALYSIS_20260702.md` + claim board
`plm-p0-capability-line-living-tracker.md`. **Owner opened (2026-07-02):** ① notification, ⑧a Method
sandbox (**priority**), ⑧b rate limiting; ECO-route Phase 0 on the integration line. Everything else
stays gated (per-item opt-in). This TODO plans the **order** and the **parallel lanes**; it does not
widen scope.

## Development order (dependency + risk ordered)

```
LANE A (security debt)      LANE B (notification)        LANE C (change-route)
─────────────────────       ─────────────────────        ─────────────────────
⑧a Method sandbox  ◀PRIORITY  ① NotificationOutbox        ECO Phase 0
   (THIS PR)                     (taskbook first)            contract-first
      │                            │                          (taskbook first,
      ▼                            ▼                           cross-repo)
⑧b Rate limiting            ②③ workflow/inbox
   (after 8a lands)            (after ①: 催办 depends
                               on delivery)
```

**Why this order**
- **⑧a first** — owner priority; it is the only item with a ratified-shape taskbook
  (`p0-8a-method-sandbox-taskbook-20260702.md`) and closes an active security exposure (four
  unsandboxed exec/import paths + RPC-as-admin default). No dependency on anything else.
- **⑧b after ⑧a** — same "安全债" pair, but a distinct surface (inbound middleware); sequenced only
  so the two safety PRs review cleanly one at a time. Not blocked by ⑧a technically.
- **① before ②③** — unified inbox / workflow 催办 (reminders/escalation) need a real delivery
  channel; ① builds the persistent outbox that ②③ consume.
- **ECO Phase 0** — contract-first, cross-repo (Yuantus + metasheet2); independent of A/B.

## Parallelism (disjoint file sets → safe to run concurrently)

| Lane | Touches | Collides with |
|---|---|---|
| ⑧a sandbox (this PR) | `meta_engine/business_logic/sandbox.py` (new), `business_logic/executor.py`, `services/method_service.py`, `services/engine.py` (RPC gate), `config/settings.py`, new test, CI wiring | none of B/C |
| ⑧b rate limit | new `api/middleware/rate_limit.py`, `api/app.py`, `config/settings.py`¹ | ⑧a only at `settings.py` (append-only, trivial) |
| ① notification | new `meta_engine/notifications/` (models+service+worker), migration, `config/settings.py`¹ | settings.py only |
| ECO Phase 0 | `docs/` + pact fixtures (+ metasheet2 repo) | none |

¹ `settings.py` is append-only across lanes → not a real conflict; land in claim order.

## Status of THIS delivery

- [x] TODO.md (this file)
- [x] ⑧a Method sandbox — **implemented** (`sandbox.py` + 4-surface cutover + RPC gate + 4 settings
      + audit/metric); tests + CI wiring; design & verification MD.
- [ ] ⑧b rate limiting — next lane-A PR (taskbook-light: owner already scoped per-tenant token bucket).
- [ ] ① notification — **taskbook first** (persistent NotificationOutbox/Delivery + worker; the
      after-commit in-memory EventBus is trigger-only).
- [ ] ECO Phase 0 — **taskbook first** (discriminated-409 shape + line.state/eco_id + feature_key/SKU
      in one pass; does NOT wire EcoPermissionAdapter).

## Deliverables for ⑧a (this PR)

1. `docs/development/p0-8a-method-sandbox-taskbook-20260702.md` — scope-lock (already in PR #943).
2. Implementation (this branch `feat/p0-8a-method-sandbox`).
3. `docs/development/p0-8a-method-sandbox-design-and-verification-20260702.md` — design + real
   pytest evidence + adversarial escape-hunt results.
