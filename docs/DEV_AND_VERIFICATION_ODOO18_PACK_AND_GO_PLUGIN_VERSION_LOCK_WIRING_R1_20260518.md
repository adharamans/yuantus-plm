# Odoo18 Pack-and-Go Plugin Version-Lock Runtime Wiring R1 — Development and Verification

Date: 2026-05-18

## 1. Goal

Implement the runtime slice approved by the pack-and-go plugin
wiring scope gate (`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_PACK_AND_GO_PLUGIN_WIRING_SCOPE_20260518.md`,
PR #590 `c5ec820`). Adds a default-OFF `require_locked_versions:
bool = False` option to the pack-and-go export request; when
`True`, the plugin enforces source-item version-lock by reusing the
merged 3a DB-resolver (`pack_and_go_db_resolver_contract`, #588
`fdc1fd9`) and the merged version-lock evaluator
(`pack_and_go_version_lock_contract`, #570 `c7e6fd5`). Sync route
blocks with HTTP 409 carrying `unlocked` + `mismatched` ids; async
job propagates the same error message into the job failure result.
`stale` items remain informational only — they never block, matching
the merged contract.

This is **the first Claude-authored Tier-B runtime wiring** in the
R2 closeout follow-up sequence. The owner's `8814fbc` (maintenance
manufacturing-side wiring) and `063e3de` (breakage design loopback
preparation) preceded it as owner-authored runtime work.

## 2. Scope

### Modified

- `plugins/yuantus-pack-and-go/main.py` — the runtime seam.
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_plugin_pack_and_go_version_lock_wiring.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_PLUGIN_VERSION_LOCK_WIRING_R1_20260518.md`

The merged 3a resolver
(`src/yuantus/meta_engine/services/pack_and_go_db_resolver_contract.py`),
the merged version-lock evaluator
(`src/yuantus/meta_engine/services/pack_and_go_version_lock_contract.py`),
the `BundleDocumentDescriptor` / `BundleLockReport` types, the
`WorkorderDocumentLink` / `ItemVersion` schemas, and the prior R2
contracts are **unchanged** — proven by drift + AST guards.

## 3. Contract

### Request schema

- New optional field `require_locked_versions: bool =
  Field(default=False)` on `PackAndGoRequest`.
- When `False` (default), no enforcement is performed and no
  exception is raised. **Default-OFF is not identical to the
  pre-PR behavior**: the plugin now does one extra indexed
  `ItemVersion.id.in_(...)` query per export after final file
  inclusion filters (on the source versions actually entering the
  package — typically a small set) and the manifest gains a
  `version_lock_summary` block. This is the caller-requested
  "report-only preview" path; if the extra lookup or manifest field
  is unwanted, the caller may simply ignore the field. The extra
  query is on an indexed PK and is scoped to the unique
  `source_version_id`s in the final bundle.
- When `True`, the plugin **raises `BundleVersionLockError`** if
  any source item is `unlocked` or `mismatched`. The exception
  carries the `unlocked` and `mismatched` id lists for precise
  diagnostics; `stale` is informational only and never blocks.

### Helper boundary

A new module-level helper `_evaluate_bundle_version_lock(session,
*, file_links, require_locked_versions=False) -> BundleLockReport`
encapsulates the version-lock evaluation:

- Loads `ItemVersion` rows for every unique `source_version_id` in
  the filtered, included file links (one query).
- Dedupes by source `item_id` — one descriptor per source item
  even when multiple files come from the same item (e.g. multiple
  CAD file roles for one part).
- For item-scope exports, uses the same effective version the final
  manifest uses: `link.source_version_id` when present, otherwise
  `source_item.current_version_id`.
- Reuses the merged 3a `WorkorderDocLinkRow` / `ItemVersionRow` row
  DTOs (the resolver doesn't care that the rows come from a
  pack-and-go bundle rather than a workorder document link — the
  row DTOs are pure data carriers).
- Calls `resolve_bundle_document_descriptors(pairs)` →
  `evaluate_bundle_version_locks(descriptors)`.
- Raises `BundleVersionLockError` only when
  `require_locked_versions=True` and `not report.ok`.
- Always returns the `BundleLockReport` so the caller can write
  the `version_lock_summary` into the manifest, even on the
  default-OFF path.

### Sync-vs-async parity

Both `build_pack_and_go_package(...)` and the async
`handle_pack_and_go_job(...)` call the helper with the same
`require_locked_versions` value (sourced from `req.require_locked_versions`
in sync; from `payload.get("require_locked_versions", False)` in
async). The sync route catches `BundleVersionLockError` **before**
the existing `except ValueError → HTTP 404` block and maps to HTTP
409 with a structured detail. The async path lets the exception
propagate up so the same error message ends up in the job failure
result — `BundleVersionLockError` is **not** a `ValueError` subclass
(deliberate, to avoid the existing route's 404 mapping).

### Cache safety

`require_locked_versions` is added to `_build_cache_payload(...)` so
the resulting `_build_cache_key` differs between locked-required
and not-required requests. This prevents an unlocked-bundle cache
zip from satisfying a future locked-required request.

### Manifest summary

The manifest gains a `version_lock_summary` block with:

```
{
  "total":         int,         # report.total
  "locked":        int,         # report.locked
  "unlocked":      list[str],   # source item ids
  "mismatched":    list[str],   # source item ids
  "stale":         list[str],   # source item ids (informational)
  "ok":            bool,        # report.ok
  "requires_lock": bool,        # the caller-supplied flag
}
```

This is written even on `require_locked_versions=False` so a
caller running a "report-only" preview can see the lock state
without enforcing it.

### Hard boundary (#590 scope-gate §3 "Deliberately not approved")

- No mainlining of the pack-and-go plugin into core services.
- No semantic changes to
  `pack_and_go_version_lock_contract.py` or
  `pack_and_go_db_resolver_contract.py`.
- No DB schema / Alembic / tenant-baseline / migration / seed /
  feature-flag changes.
- No write to `meta_workorder_document_links` state.
- No new background-job type (the existing `pack_and_go` job type
  is reused; the payload merely gains an extra field).
- No UI changes.
- No broad refactor of `plugins/yuantus-pack-and-go/main.py` —
  only the additions strictly needed for the wiring (request
  field, helper, sync-route catch block, async passthrough, cache
  payload field, manifest field).

## 4. Test Matrix

`src/yuantus/meta_engine/tests/test_plugin_pack_and_go_version_lock_wiring.py`
— 18 tests (counts a point-in-time snapshot):

- **Layer 1 (no session)**:
  - Schema: `require_locked_versions` field, default False, type bool.
  - Cache payload: flag in payload dict; `_build_cache_key`
    produces different keys for the two flag values (cache safety
    pin).
  - Async payload: `req.model_dump(by_alias=True)` round-trips
    the new field (so `_build_job_payload` automatically picks it
    up — no special update line required).
  - `BundleVersionLockError`: carries `unlocked` + `mismatched`
    id lists; `str(exc)` includes both; **not** a `ValueError`
    subclass (HTTP 409 mapping safety).
  - AST contract reuse: helper imports the merged
    `resolve_bundle_document_descriptors` and
    `evaluate_bundle_version_locks`, and uses the merged row DTOs
    `WorkorderDocLinkRow` / `ItemVersionRow`.
  - AST anti-redefinition: plugin module does NOT define functions
    named like the merged contract surface
    (`evaluate_bundle_version_locks` / `assert_bundle_version_locks`
    / `resolve_bundle_document_descriptor` / `resolve_bundle_document_descriptors`)
    — no shadow re-implementation.
- **Layer 2 (SQLite session, helper end-to-end)**:
  - Default OFF: helper returns the `BundleLockReport` and does
    NOT raise even when the report's `ok=False`.
  - Locked & owned: all source items pinned to current owned
    versions → `ok=True`, no unlocked/mismatched/stale, helper
    succeeds whether or not enforcement is requested.
  - Unlocked: source item without `source_version_id` → helper
    raises `BundleVersionLockError(unlocked=[item_id],
    mismatched=[])`.
  - Mismatched: source item pinned to a version that belongs to a
    different item → helper raises `BundleVersionLockError(
    mismatched=[item_id], unlocked=[])`.
  - Stale-only: source item pinned to an owned but non-current
    version → `ok=True`, `stale=[item_id]`, helper does NOT raise
    even under `require_locked_versions=True`.
  - Dedupe: multiple files from the same source item produce ONE
    descriptor in the report (`total=1`).
  - Missing version row: pinned `source_version_id` whose row does
    not exist (caller resolved a now-deleted version) → resolver's
    Branch C maps to `mismatched`, helper raises.
  - Item-scope source link: lock input uses
    `source_item.current_version_id`, matching the final manifest
    version field.
  - Final-bundle ordering: lock links are appended only after
    item/file-role/type/extension/missing-file filters; helper
    receives the filtered list.
- **Layer 2 (async passthrough)**: `handle_pack_and_go_job`
  source contains `require_locked_versions=...` in its call to
  `build_pack_and_go_package` (pinned by source inspection — the
  job-service plumbing is too coupled to drive end-to-end here,
  but the kwarg pass-through is the test that matters).
- **Layer 2 (async fallback cache key)**: fallback cache-key
  recomputation includes `require_locked_versions`, matching the
  sync route namespace.

The existing `src/yuantus/meta_engine/tests/test_plugin_pack_and_go.py`
remains the regression net for unchanged plugin behavior (19
helper tests, all green post-wiring).

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_plugin_pack_and_go_version_lock_wiring.py \
  src/yuantus/meta_engine/tests/test_plugin_pack_and_go.py \
  src/yuantus/meta_engine/tests/test_pack_and_go_version_lock_contract.py \
  src/yuantus/meta_engine/tests/test_pack_and_go_db_resolver_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile plugins/yuantus-pack-and-go/main.py
git diff --check
```

No alembic / tenant-baseline — the plugin wiring adds no schema
and no migration.

Observed after rebasing onto `origin/main` on 2026-05-18:

- pack-and-go wiring + plugin regression + version-lock/db-resolver
  contracts: `73 passed`
- doc-index/R2 portfolio tests: `14 passed`
- `py_compile plugins/yuantus-pack-and-go/main.py`: clean
- `git diff --check`: clean

## 6. Non-Goals (reaffirmed from #590 §3 "Deliberately not approved")

- No DB-querying resolver — descriptors are built from data the
  plugin already loads (existing `file_scope="version"` query). A
  dedicated DB-querying resolver is a separate later opt-in.
- No mainlining / schema / Alembic / migration / tenant-baseline
  / feature-flag / UI / new background-job type.
- No write to `WorkorderDocumentLink` state.
- No broad refactor of the plugin.
- No edit to the merged 3a resolver or version-lock contracts.
- No change to existing default-OFF behavior — every existing
  test in `test_plugin_pack_and_go.py` passes unchanged.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Follow-ups (each its own separate opt-in)

- A dedicated DB-querying resolver for pack-and-go (queries
  `meta_workorder_document_links` directly rather than going
  through the plugin's existing item/version loading) — separate
  later opt-in, lower priority per the 2026-05-18 owner priority
  ranking (#4 in the queue).
- UI surface for `require_locked_versions` (e.g. an export-dialog
  checkbox + 409 error rendering) — separate UI work, out of
  scope.
- Automation engine substitution — #2 in the owner's priority
  queue, separate session + opt-in, 21-case parity matrix as the
  regression net.
- Breakage state-machine / ECR-creation wiring — #3 in the queue;
  needs to be split into finer taskbooks before implementation.

## 8. R2 Closeout Status

Tier-A pure-contract tier — CLOSED (3a/3b/3c all delivered). This
PR is the first Claude-authored Tier-B runtime wiring; the
owner-authored `8814fbc` (maintenance) and `063e3de` (breakage
intake preparation) preceded it. Per the owner's serialization
rule, only one Tier-B runtime follow-up should be in-flight at a
time; further runtime follow-ups need their own explicit opt-ins.
