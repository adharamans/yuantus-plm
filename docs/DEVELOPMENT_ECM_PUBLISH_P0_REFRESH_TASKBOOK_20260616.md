# Development Taskbook: PLM→ECM Publish — P0 Refresh (re-gate of #694)

Date: 2026-06-16
Type: **doc-only scope-lock**. Re-gates the #694 PLM→ECM publish design (2026-06-02,
**67 commits behind** main) to current `main`, and locks the P1A–P1D implementation
slicing. Authorizes later implementation PRs; no code here.

## 1. Goal & scope

One-directional, **release-triggered** publication of released CAD versions into Athena
ECM's controlled-record repository, via a local **transactional outbox** (mirroring the
proven `erp_publication` outbox) + an async worker that calls Athena's CMIS API. Near-zero
Athena change. **The release transaction is never blocked or failed by publication.**

## 2. Why P0 Refresh (not implement #694 as-is)

#694 is a strong design *input* but stale — three load-bearing assumptions broke against
current `main`:
1. **Entitlement seam broke (not a rename).** #694's `is_enabled("plm.ecm")` dev-no-op
   does not exist; main's `is_entitled(feature_key)` **raises `ValueError`** on an
   unregistered key, and raises on missing tenant context in db-per-tenant mode — either
   would **fail a release** if called unguarded.
2. **Choke point doesn't exist / semantics differ.** There is no `release_version(version_id)`;
   the runtime release is `release(item_id)`. Crucially, the **ECO apply path never sets
   `is_released`** — so "released" ≠ "current production version."
3. **Phase 0 never executed.** The U1–U5 Athena/CMIS facts (kit ships in #694) are unrun.

## 3. Grounded facts (verified on `main`)

- **Canonical runtime `is_released` writer:** `VersionService.release(item_id, user_id)`
  (`version/service.py:555`) is the **canonical** runtime writer of `ItemVersion.is_released=True`,
  called only by lifecycle promote (`lifecycle/service.py:297`). `ChangeService._release_version`
  still exists as a **deprecated/fenced legacy surface** — `ChangeService.execute_eco` calls it
  intra-file, but `ChangeService` is deprecated, the canonical compat shim
  (`LegacyEcmCompatService`) forbids Release/Revise/New-Generation, and the B1 static guard
  (`test_version_supersede_b1`) fences external callers. **P1A does NOT extend it; it stays
  fenced/deprecated (sunset later) — do not read "canonical" as "ignore this function".**
- **ECO apply ≠ release:** `eco_service.action_apply` (`eco_service.py:1753`) sets the target
  version `is_current=True` + syncs files but **leaves `is_released=False`**. Promote-release
  and ECO-apply are distinct flows; only promote-release flips `is_released`.
- **`release()` sets neither `released_at` nor `released_by_id` today** (R3 nail #4).
- **Entitlement core:** `is_entitled(feature_key)` maps via `FEATURE_APP_NAMES`
  (`entitlement_service.py:30-42`; keys: `plm_collaboration_pro`, `approval_automation`,
  `bom_multitable`, reserved-unlit `plm`/`automation_enterprise`/`plm_offline_license`);
  unknown key → `ValueError`; tenant via `resolve_license_scope()` (raises in db-per-tenant
  without context). Reference gate = `bom_multitable_router` (auth → `is_entitled` **before**
  resource lookup → query; no existence leak; `Cache-Control: no-store` on read projections).
  **No ECM key registered.**
- **`erp_publication` outbox (the template to mirror):** model `meta_erp_publication_outbox`
  (`erp_publication/models.py`: idempotency key `(item_id, version_id, target_system,
  publication_kind)`; states `pending/dry_run_ready/sent/failed/skipped`; orthogonal `reason`
  `not_eligible/validation_error/adapter_error/remote_error`; snapshot JSON; payload
  fingerprint excl. volatile keys; `attempt_count/max_attempts/next_attempt_at/worker_id/
  claimed_at`). Service `ErpPublicationOutboxService` (enqueue idempotent + **409-conflict-if-
  already-sent**, dry_run, process [**`adapter.send()` is the only external write**], replay,
  reschedule_retry → dead-letter at `max_attempts`). Router `/plm-erp` — 5 admin-gated routes.
  Worker claims `FOR UPDATE SKIP LOCKED`. Alembic migration + migration-table coverage
  contract; tests dual-registered (ci.yml + conftest allowlist).

## 4. Locked decisions

- **D1 — Trigger & v1 scope.** ECM publish fires on the **`is_released` transition inside
  `VersionService.release()`** (the single writer). **v1 scope = promote-path releases.**
  **ECO-apply is explicitly OUT of v1** (it sets `is_current` without `is_released` — a
  different semantic, arguably its own gap); a follow-up may add it. One hook on the canonical
  writer; no divergent runtime path to unify (`ChangeService` is the deprecated/fenced legacy
  surface, left as-is).
- **D2 — Entitlement, exception-safe.** Register feature_key **`ecm_publish` →
  `frozenset({"plm.ecm_publish"})`** in `FEATURE_APP_NAMES` (+ `AppLicense` rows for lit
  tenants). The release-hook gate is **exception-safe**: `is_entitled("ecm_publish")` wrapped
  in `try/except` — **any** exception (unknown key mid-rollout, missing tenant context) →
  treat as **not entitled → no enqueue (or a `not_eligible`/`config_missing` SKIP row), NEVER
  propagate into the release txn.** Manual routes follow the `bom_multitable` order
  (auth → `is_entitled` → query; admin for config writes). **P1B also registers an
  integration-manifest descriptor** (`supported: true`, `api_version: "v1"`,
  `scenario: ["release_publish"]`, **no `actions`**) — advisory only, **not** an
  authorization source (the real gate is the hook + routes); or explicitly document
  non-broadcast.
- **D3 — Enqueue contract.** Inside `release()`, after `is_released` is set and the supersede
  hook, enqueue under **`session.begin_nested()` (SAVEPOINT)**: **no remote I/O, no file byte
  reads** (fingerprint from `FileContainer.checksum`; fallback `sha256(file_id|system_path|
  file_size|mime_type|generation|revision|file_role)`), **never throws** (outer `try/except` +
  log; SAVEPOINT rollback leaves the release committed). A fingerprint conflict vs a prior
  **SENT** row → recorded as a `CONFLICT`/SKIPPED audit row, **not raised** (R3 divergence from
  erp's `PublicationConflictError`, because the call site is `release()`).
- **D4 — Outbox model.** Mirror `meta_erp_publication_outbox` as **`meta_ecm_publication_outbox`**:
  idempotency key `(item_id, version_id, file_id, file_role, target_system)` (**per-file** —
  a released version may carry multiple controlled files); states/reason as above + ECM
  reasons (`config_missing`, `conflict`); snapshot JSON (`athena_tenant_id`,
  `athena_base_folder_id` at enqueue; `athena_folder_id`/`athena_object_id` at worker; eco
  refs, `released_by`, `fingerprint_basis`, filename/mime/size); retry/worker fields.
- **D5 — Routes / worker / replay (P1C), Null adapter.** Mirror erp's manual routes
  (status / dry-run / process / replay + a manual re-enqueue), all **admin + `ecm_publish`
  gated**; worker `FOR UPDATE SKIP LOCKED`; a **Null/fake `AthenaCmisAdapter`** pins outbox
  semantics without live Athena. Route-count: bump all pins together (+N).
- **D6 — Athena CMIS adapter (P1D) DEFERRED.** The real adapter (Keycloak realm role **U1**,
  version-producing call sequence **U2**, property key path/searchability **U3**, `X-Tenant-ID`
  routing **U4**, nested `createFolder Released/<part>` **U5**) is **frozen until Phase 0
  smoke (`scripts/ecm_publish_phase0/smoke.py`) is run against live Athena+Keycloak and U1–U5
  are recorded.** Not in this goal's "完成".
- **D7 — `release()` hardening (P1A).** **v1 adds NO public `release_version(version_id)`
  entry** — the entry stays `release(item_id)` and the publish hook lives inside it (adding a
  version-id entry would imply ECO / arbitrary-version-id release is supported, widening the
  semantic — explicitly not wanted in v1). A *private* internal alias is added **only if** the
  implementation genuinely needs an internal seam. `release()` also sets `released_at` and
  `released_by_id = user_id` (currently neither). Prove the supersede hook, lock-release, and
  history behavior are unchanged and the **canonical release writer hasn't drifted** (static +
  behavior regression; **no mis-publish**); do **not** touch `ChangeService._release_version`
  (keep it fenced).
- **D8 — CI discipline (every slice).** New migration → migration-table coverage contract +
  single Alembic head; new tests dual-registered (ci.yml + conftest allowlist); +N route-count
  pins bumped together; new `YUANTUS_ECM_*` settings declared as `Settings` fields
  (`extra="ignore"` silently drops undeclared); **prefer the outbox row as the audit** (no new
  indexed domain event, to avoid the search-indexer event-coverage contract).

## 5. Slicing / TODO

- [ ] **ECM-P0 Refresh** (this doc) — re-gated baseline on main.
- [ ] **P1A Release hook-point hardening** — `release()` sets `released_at`/`released_by_id`;
  **no public version-id alias** (D7); prove the canonical `is_released` writer hasn't drifted +
  **no mis-publish** + `ChangeService` stays fenced; static + behavior regression tests.
  *(no ECM code yet — pure release hardening)*
- [ ] **P1B Outbox + enqueue** — `ecm_publication` model + migration; `EcmPublicationOutboxService.
  enqueue_release`; the `release()` `begin_nested` hook; **exception-safe** entitlement gate +
  `ecm_publish` feature_key registration; non-blocking + conflict-as-audit + never-fail-release tests.
- [ ] **P1C Routes / worker / replay (Null adapter)** — service `process`/`replay`/`dry_run`/
  `status`; admin+entitlement routes; worker `SKIP LOCKED`; Null/fake adapter; route-count pins; tests.
- [ ] **P1D Athena CMIS adapter** — **DEFERRED** until Phase 0 U1–U5; real adapter + http +
  circuit breaker + `build_outbound_headers` + `Idempotency-Key`.

## 6. Non-goals

- No reverse (ECM→PLM) sync; no Athena schema/code change beyond CMIS calls.
- No ECO-apply publish in v1 (D1); no publish of non-released "current" versions.
- No real Athena adapter until Phase 0 (D6).
- No second entitlement system (D2 reuses `is_entitled`).

## 7. Phase 0 status

`scripts/ecm_publish_phase0/smoke.py` + results template ship with #694 (not yet on main —
fold into P1D as the kit). **U1–U5 NOT executed** → P1D gated. **P0 + P1A + P1B + P1C are
code-completable without it** — that is this goal's "完成" boundary.
