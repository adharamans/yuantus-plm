# ECM Publish — P1B: Outbox + Release-Triggered Enqueue (Dev & Verification)

Date: 2026-06-16
Branch: `feat/ecm-p1b-outbox-enqueue` (off `main` after #764)
Scope owner doc: `docs/DEVELOPMENT_ECM_PUBLISH_P0_REFRESH_TASKBOOK_20260616.md`
(locked decisions D1–D8)

## 1. What P1B delivers

P1B lights the **enqueue half** of the PLM→ECM (Athena controlled-record) publish
pipeline: when a version is **released**, its controlled-record files are snapshotted
into a durable outbox table for later asynchronous publication. P1B adds **no remote
I/O and no routes** — the dispatch worker, routes, and the Null/real adapters are P1C/P1D.

Delivered surface:

- **Outbox model** `meta_ecm_publication_outbox`
  (`src/yuantus/meta_engine/ecm_publication/models.py`) — mirrors the proven
  `erp_publication` outbox: state-vs-reason orthogonal split
  (`EcmPublicationState` = pending/dry_run_ready/sent/failed/skipped;
  `EcmPublicationReason` = not_eligible/config_missing/conflict/validation_error/
  adapter_error/remote_error), content fingerprint, retry/worker columns, and a
  **per-file idempotency key** `uq_ecm_publication_outbox_identity` over
  `(item_id, version_id, file_id, file_role, target_system)`.
- **Migration** `migrations/versions/ecm_pub_outbox_001_add_ecm_publication_outbox.py`
  — `down_revision = a3_checkout_context_001` (the single head before P1B);
  `ecm_pub_outbox_001` becomes the new single head. Idempotent create-guard +
  `item_id`/`version_id` indexes, mirroring `erp_pub_outbox_001`.
- **Enqueue service** `EcmPublicationOutboxService.enqueue_release(version, …)`
  (`src/yuantus/meta_engine/ecm_publication/service.py`) — one PENDING row per
  **controlled** file (`native_cad`, `drawing`, `geometry`; previews/attachments are
  skipped). Pure DB: the content fingerprint prefers `FileContainer.checksum` and falls
  back to a **composed** SHA-256 over file/version metadata — **never a byte read**
  (D3). Idempotent (same fingerprint → reuse the row); a changed fingerprint vs an
  already-**SENT** row is **conflict-as-audit** (recorded on `properties`, never raised).
- **Release hook** `VersionService._enqueue_ecm_publication(version, user_id)`, called
  at the end of `release()` (D7). It is **exception-safe and non-blocking**:
  1. global kill-switch `settings.ECM_PUBLISH_ENABLED` (default **OFF**) short-circuits;
  2. the per-tenant license gate `EntitlementService.is_entitled("ecm_publish")` is
     wrapped so an unregistered key / missing tenant context (which `is_entitled`
     **raises** on) is treated as *not entitled*, never propagated;
  3. the enqueue runs inside a `session.begin_nested()` SAVEPOINT wrapped in try/except —
     any enqueue error rolls back **only** the enqueue; the release stays committed.
- **Feature gate** `ecm_publish` → `{plm.ecm_publish}` registered in
  `FEATURE_APP_NAMES` — its **own independent SKU** (same discipline as
  `approval_automation` / `bom_multitable`; **not** bundled into `plm.collab`).
- **Kill-switch setting** `ECM_PUBLISH_ENABLED: bool = False`
  (`src/yuantus/config/settings.py`) — ops-controlled global enable that applies **on
  top of** the per-tenant license gate.
- **Integration manifest descriptor** (D2) — `ecm_publish` advertised in
  `integration_capabilities_service._FEATURE_DESCRIPTORS` as
  `{api_version: "v1", scenarios: ["release_publish"]}`, no actions. ADVISORY only; the
  real gate stays the kill-switch + `is_entitled`, not this hint.

## 2. Decisions implemented (from the P0 taskbook)

| Decision | P1B implementation |
|---|---|
| **D2** register `ecm_publish` + advisory manifest descriptor | `FEATURE_APP_NAMES["ecm_publish"]`; `_FEATURE_DESCRIPTORS["ecm_publish"]` (`supported:true`, `api_version:"v1"`, `scenarios:["release_publish"]`, no actions) |
| **D3** enqueue contract (begin_nested, no remote I/O, no byte reads, never throws, conflict-as-audit) | `_enqueue_ecm_publication` SAVEPOINT + try/except; `_content_fingerprint_basis` checksum/composed (no reads); `_enqueue_existing` conflict-as-audit |
| **D4** outbox model | `meta_ecm_publication_outbox` + migration `ecm_pub_outbox_001` |
| **D7** hook inside `release()`, sets provenance, **no public alias** | P1A (#764) stamped `released_at`/`released_by_id`; P1B adds the enqueue call at the end of `release()` — no `release_version(version_id)` alias introduced |

Out of P1B (deferred): **P1C** routes/dispatch worker + Null adapter; **P1D** the real
Athena CMIS adapter (Phase-0-gated against a live Athena). v1 publishes on the
release/promote path only (D1); ECO-apply remains out of scope.

## 3. Verification

Test env: `.venv-wp13` (python3.11); `unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB`.

### 3.1 New unit/behavior tests — `test_ecm_publication_enqueue.py` (11, all pass)

Service (`enqueue_release`):
- one PENDING row per **controlled** file; previews/attachments skipped; the checksum-less
  file uses a `composed:` fingerprint basis, the checksummed file uses `checksum:…`.
- **idempotent** re-enqueue (no duplicate row).
- **conflict-as-audit**: changing the content after the row is SENT records
  `properties.conflict_after_sent = True` and does **not** raise / does **not** add a row.
- **re-snapshot of a non-terminal row**: a FAILED row whose content changed is reused in
  place, reset to PENDING (reason cleared, `properties.re_snapshotted = True`) — *not* a
  conflict (conflict is SENT-only).

Release hook (`_enqueue_ecm_publication`):
- `release()` **invokes** the hook with the released version + user (wiring test through
  the real `release()` with the hook stubbed).
- enabled + entitled → rows enqueued; disabled → no rows; not entitled → no rows.
- `is_entitled` **raises** (unregistered key / missing tenant) → no rows, **no exception**.
- `enqueue_release` **raises** → SAVEPOINT swallows it, **no exception**, no rows
  (release would stay committed).

End-to-end (real ORM, no ducks):
- a real `Item` + `ItemVersion` + `VersionFile` + `FileContainer` flow through the real
  `release()` (kill-switch on, entitled) → exactly one outbox row for the `native_cad`
  file (`preview` skipped), with the live attribute contract
  (`vf.file_id`/`vf.file_role`/`vf.file.checksum`) exercised and the P1A-stamped
  `released_by_id` captured in the snapshot (proves provenance is stamped *before* the
  enqueue snapshot).

### 3.2 Contract tests touched by the registration

- `test_ci_contracts_ci_yml_test_list_order` — new test added to the ci.yml contracts
  list in sorted position (`test_ecm_publication_enqueue.py`, between
  `…document_sync_router_decomposition_closeout…` and `…eco_approval_ops…`).
- `test_migration_table_coverage_contracts` — the coverage check is **dynamic**
  (scans `__tablename__` vs `op.create_table`); `meta_ecm_publication_outbox` is in both
  the model and the migration, so it is auto-covered (no allowlist edit).
- `test_integration_capabilities` — the manifest builds dynamically over
  `_FEATURE_DESCRIPTORS`; the added `ecm_publish` entry is advertised without breaking the
  contract (additive).
- Registered in `conftest.py` `_ALLOWLIST_NO_DB` (DB-less collection) alongside the ci.yml
  list (dual-registration discipline).

### 3.3 Route count

P1B adds **no routes** — the route-count pins (709) are **unchanged**.

### 3.4 Migration

`alembic upgrade head` on a fresh SQLite DB applies cleanly:
`a3_checkout_context_001 → ecm_pub_outbox_001`; the table, the
`uq_ecm_publication_outbox_identity` unique constraint, and both indexes are created.
`alembic heads` reports the single head `ecm_pub_outbox_001`.

### 3.5 How to reproduce

```bash
cd Yuantus && . .venv-wp13/bin/activate
unset YUANTUS_PYTEST_DB YUANTUS_TEST_DB PYTEST_DB
python -m pytest \
  src/yuantus/meta_engine/tests/test_ecm_publication_enqueue.py \
  src/yuantus/meta_engine/tests/test_ci_contracts_ci_yml_test_list_order.py \
  src/yuantus/meta_engine/tests/test_migration_table_coverage_contracts.py \
  src/yuantus/meta_engine/tests/test_integration_capabilities.py \
  src/yuantus/meta_engine/tests/test_release_hook_point_hardening.py -q
```

## 4. Safety properties (why release() can never be harmed)

- **Default-off**: `ECM_PUBLISH_ENABLED` is `False`, so even entitled tenants enqueue
  nothing until ops flips it. The per-tenant `is_entitled("ecm_publish")` applies on top.
- **Exception-safe gate**: `is_entitled` raises on an unknown key / missing tenant
  context; the hook treats any such error as *not entitled* and returns.
- **SAVEPOINT isolation**: the enqueue runs in `begin_nested()`; an enqueue failure rolls
  back only the enqueue and is logged at WARNING — the release transaction commits.
- **No remote I/O, no byte reads**: the fingerprint uses the stored checksum or a composed
  metadata hash, so enqueue cannot block on storage or the network.
- **Idempotent + conflict-as-audit**: re-running release (or a retry) never duplicates
  rows; a post-SENT content change is audited, never thrown.
