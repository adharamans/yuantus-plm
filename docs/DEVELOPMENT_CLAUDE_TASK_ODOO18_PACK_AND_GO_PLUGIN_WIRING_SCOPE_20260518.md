# Claude Task - Odoo18 Pack-and-Go Plugin Wiring Scope Gate - 2026-05-18

## 1. Purpose

This document is the scope gate for the next pack-and-go runtime slice after:

- `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_PACK_AND_GO_VERSION_LOCK_BRIDGE_CONTRACT_20260515.md`
- `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_VERSION_LOCK_BRIDGE_CONTRACT_R1_20260515.md`
- `docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_20260516.md`
- `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_R1_20260516.md`

The next implementation MAY wire the shipped pack-and-go plugin to the merged
version-lock descriptor and resolver contracts. It MUST remain a narrow plugin
runtime seam. This scope gate does not authorize code changes by itself.

## 2. Current Baseline

As of `origin/main@fdc1fd9e3d8d413efa9b9501745b6b917963007c`:

- `plugins/yuantus-pack-and-go/main.py` owns the pack-and-go route and package
  builder.
- `build_pack_and_go_package(...)` collects BOM items, item/version files, builds
  a ZIP, and emits `manifest.json`.
- `file_scope="version"` already queries `ItemVersion` and `VersionFile` for the
  current item versions, but the plugin does not call the pack-and-go
  version-lock contracts.
- `pack_and_go_version_lock_contract.py` provides:
  - `BundleDocumentDescriptor`
  - `evaluate_bundle_version_locks`
  - `assert_bundle_version_locks`
- `pack_and_go_db_resolver_contract.py` provides pure DTOs and mapping:
  - `WorkorderDocLinkRow`
  - `ItemVersionRow`
  - `resolve_bundle_document_descriptor`
  - `resolve_bundle_document_descriptors`

The key gap is runtime composition: the plugin has the export path, and the
contracts have the version-lock evaluation, but no approved plugin seam connects
them.

## 3. Scope Decision

### Approved implementation shape

The implementation PR MAY add a guarded pack-and-go option that evaluates bundle
version-lock state during package build and records the result in the manifest.

Recommended request field:

```python
require_locked_versions: bool = Field(default=False)
```

Recommended behavior:

- Default is `False`, preserving current behavior.
- When `False`, the plugin may compute and expose a report if cheap and already
  supported by the same helper, but it MUST NOT block export.
- When `True`, the plugin MUST block package generation if
  `assert_bundle_version_locks(...)` would fail.
- Blocking error should be an HTTP 409 at route level or a `ValueError` converted
  to HTTP 409 by the existing route error handling. The error must include the
  unlocked and mismatched item ids from the contract report, not a generic
  failure.
- `stale` remains informational only. A stale but owned version must not block,
  matching the contract.

### Deliberately not approved

This slice does NOT approve:

- Mainlining the pack-and-go plugin into core services.
- Changing `pack_and_go_version_lock_contract.py` semantics.
- Changing `pack_and_go_db_resolver_contract.py` semantics.
- Any DB schema, Alembic, tenant baseline, migration, seed, or feature-flag
  change.
- Any write to workorder-document-link state.
- Any new background job type.
- Any UI change.
- Any broad refactor of `plugins/yuantus-pack-and-go/main.py`.

## 4. Expected Files For Implementation PR

Allowed files:

- `plugins/yuantus-pack-and-go/main.py`
- `src/yuantus/meta_engine/tests/test_plugin_pack_and_go.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_PLUGIN_WIRING_20260518.md`
- `docs/DELIVERY_DOC_INDEX.md`

Allowed only if the implementation proves existing tests cannot express the
route-level behavior cleanly:

- one new focused test file under `src/yuantus/meta_engine/tests/` whose name
  starts with `test_plugin_pack_and_go_`

Everything else is out of scope unless a reviewer explicitly reopens this gate.

## 5. Proposed Internal Seam

Add a small helper inside `plugins/yuantus-pack-and-go/main.py`; do not add a new
service module for this slice.

Suggested helper:

```python
def _evaluate_packgo_version_locks(
    *,
    item_rows: Sequence[Any],
    version_rows_by_id: Mapping[str, Any],
) -> BundleLockReport:
    ...
```

The exact signature may change, but the helper must:

- Build `WorkorderDocLinkRow` values from the same item/version facts the plugin
  already has for the export.
- Build `ItemVersionRow` values from already-fetched `ItemVersion` rows.
- Call `resolve_bundle_document_descriptors(...)`.
- Call `evaluate_bundle_version_locks(...)`.
- Avoid direct SQL in the helper.
- Avoid calling `assert_bundle_version_locks(...)` unless the request explicitly
  asks to enforce.

If an implementation requires additional `ItemVersion` fetches, keep them inside
the existing package-build data-loading region. Do not introduce a new repository
or service layer in this PR.

## 6. Manifest Contract

When the report is computed, `manifest.json` should include:

```json
{
  "version_lock_summary": {
    "total": 3,
    "locked": 2,
    "unlocked": ["item-unlocked"],
    "mismatched": [],
    "stale": ["item-stale"],
    "ok": false,
    "requires_lock": true
  }
}
```

Rules:

- `locked = total - len(unlocked) - len(mismatched)`.
- Mismatched is not locked.
- Stale is still locked.
- `requires_lock` mirrors the request option, not `report.ok`.
- Preserve existing manifest keys and file entries.

## 7. Semantic Risks

R1. **Default behavior drift.** Existing exports must remain successful when the
new option is omitted.

R2. **Stale semantics regression.** Stale versions are informational only. Do
not block stale-only bundles.

R3. **Mismatch vs unlocked confusion.** A version present but foreign or unknown
ownership must be mismatched, not unlocked or locked.

R4. **Duplicate derivation.** Do not reimplement lock arithmetic in the plugin.
Use the merged contract functions and assert the manifest mirrors their output.

R5. **DB resolver scope creep.** The pure resolver contract remains pure. Runtime
queries belong to the plugin's existing data-loading path only.

R6. **Cache correctness.** If `require_locked_versions` affects output or
blocking, it must be included in cache key payloads. A package created without
the requirement must not satisfy a later request that requires locks unless the
manifest/report semantics are explicitly safe.

R7. **Async parity.** The route and background job path must carry the same
request field and enforce the same behavior.

R8. **Route error mapping.** Blocking must surface as a client-actionable
conflict, not an internal server error.

## 8. Required Tests

The implementation PR must include these tests or explicitly explain why a
listed test is inapplicable.

T1. Default-off: existing pack-and-go helper tests still pass with no request
field and no blocking.

T2. Report shape: plugin manifest includes `version_lock_summary` with
`total/locked/unlocked/mismatched/stale/ok/requires_lock`.

T3. Locked bundle: `require_locked_versions=True` succeeds when every descriptor
is locked and owned.

T4. Unlocked bundle: `require_locked_versions=True` fails with 409 or mapped
client conflict and includes unlocked item ids.

T5. Mismatched bundle: `require_locked_versions=True` fails with 409 or mapped
client conflict and includes mismatched item ids.

T6. Stale-only bundle: `require_locked_versions=True` succeeds and records stale
ids without failing.

T7. Default-off violations: `require_locked_versions=False` does not block
unlocked/mismatched bundles, but the manifest report is present if the helper
computes it.

T8. Cache key: `require_locked_versions` participates in
`_build_cache_payload(...)` or the implementation proves caching cannot reuse an
unsafe artifact across different enforcement modes.

T9. Async parity: async job payload includes the field and background execution
enforces the same behavior as the direct route.

T10. Contract reuse: tests patch or inspect calls so the plugin uses
`resolve_bundle_document_descriptors(...)` and `evaluate_bundle_version_locks(...)`
or `assert_bundle_version_locks(...)`; it must not locally duplicate the lock
classification.

T11. No schema/runtime creep: no migration files, no DB model changes, no
feature-flag table/config changes.

## 9. Verification Commands

Minimum local verification for the implementation PR:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_plugin_pack_and_go.py \
  src/yuantus/meta_engine/tests/test_pack_and_go_version_lock_contract.py \
  src/yuantus/meta_engine/tests/test_pack_and_go_db_resolver_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  plugins/yuantus-pack-and-go/main.py \
  src/yuantus/meta_engine/services/pack_and_go_version_lock_contract.py \
  src/yuantus/meta_engine/services/pack_and_go_db_resolver_contract.py
git diff --check
```

No Alembic or tenant-baseline command is expected because this gate forbids
schema changes.

## 10. Review Gate

Before merge, reviewer must confirm:

- Default-off behavior is preserved.
- Plugin uses the merged contracts instead of reimplementing arithmetic.
- `require_locked_versions=True` blocks unlocked and mismatched bundles.
- Stale-only bundles do not block.
- Direct and async paths have parity.
- Cache key cannot reuse unsafe artifacts across enforcement modes.
- No schema / service / UI / mainline-plugin refactor slipped in.

## 11. Next Action

If accepted, open a separate implementation branch from latest `origin/main`.
Do not implement on this scope-gate branch. The implementation PR should be small
enough to review as one plugin seam and its tests.
