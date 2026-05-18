# Odoo18 Pack-and-Go DB-Resolver Pure-Contract R1 — Development and Verification

Date: 2026-05-16

## 1. Goal

Implement R1 of the pack-and-go DB-resolver taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_20260516.md`,
merged 2026-05-18 as `27f58ae` / PR #587). R2 closeout §4 Tier-A
follow-up #3 — the row→descriptor boundary the merged pack-and-go
version-lock bridge (`pack_and_go_version_lock_contract.py`, PR #570
`c7e6fd5`) was missing.

R1 is **pure, contract-only**: a module + tests + this MD. The caller
fetches rows; the pure function maps them. No DB / no `session` / no
plugin wiring / no version-lock enforcement / no edit to the shipped
pack-and-go contract or `parallel_tasks_service`.

## 2. Scope

### Added

- `src/yuantus/meta_engine/services/pack_and_go_db_resolver_contract.py`
- `src/yuantus/meta_engine/tests/test_pack_and_go_db_resolver_contract.py`
- `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_R1_20260516.md`

### Modified

- `docs/DELIVERY_DOC_INDEX.md` (one index line)

The shipped `pack_and_go_version_lock_contract` (incl.
`BundleDocumentDescriptor`, `evaluate_bundle_version_locks`,
`assert_bundle_version_locks`), `parallel_tasks_service`
(`serialize_link`), the pack-and-go plugin, and the prior R2
contracts are **unchanged** — proven by drift + AST guards.

## 3. Contract

The contract is pure: it does **not** query. The caller fetches and
passes typed row views; the missing-version case is signalled by the
caller passing `version_row=None`.

### Row DTOs

- `WorkorderDocLinkRow` — frozen Pydantic v2, `extra="forbid"`. Subset
  of `meta_workorder_document_links` columns the mapping needs:
  `document_item_id: str` (non-empty), `document_version_id:
  Optional[str] = None`. Field names mirror the column names (drift
  guard pins `WorkorderDocLinkRow` fields ⊆
  `WorkorderDocumentLink.__table__.columns`).
- `ItemVersionRow` — frozen Pydantic v2, `extra="forbid"`. Subset of
  `meta_item_versions` columns: `id: str` (non-empty), `item_id: str`
  (non-empty), `is_current: Optional[bool] = None`. Field names mirror
  the column names (drift guard pins fields ⊆
  `ItemVersion.__table__.columns`).

### Three branches (bit-for-bit `serialize_link` parity)

`resolve_bundle_document_descriptor(link_row, version_row=None) ->
BundleDocumentDescriptor` reproduces
`WorkorderDocumentPackService.serialize_link`
(`parallel_tasks_service.py` ~line 6155) exactly:

| serialize_link branch | Resolver behavior |
|---|---|
| no `document_version_id` (falsy) | `version_belongs_to_item=None`, `version_is_current=None` |
| version pinned, row found | `version_belongs_to_item = (str(version_row.item_id) == str(link_row.document_item_id))`, `version_is_current = bool(version_row.is_current)` |
| version pinned, row missing | `version_belongs_to_item=False`, `version_is_current=None` |

### Two RATIFIED policies (taskbook §3, binding)

1. **Raise on id mismatch (unconditional on `version_id` state).** If
   `version_row` is supplied, its `id` MUST equal
   `link_row.document_version_id`; a mismatch is a caller bug and the
   resolver **raises `ValueError`**. The rule is unconditional — it
   catches both kinds of caller bug:
   - **(a)** both set, ids differ — wrong row paired with the link;
   - **(b)** `document_version_id` falsy but `version_row` still
     supplied — stray row whose link has no pinned version
     (`version_row.id != None` so the rule still rejects).

   This is *input-shape validation* (like the consumption/ECR
   contracts rejecting malformed input), **not** version-lock
   enforcement (which remains the merged
   `assert_bundle_version_locks`, untouched). Silently treating (a)
   as "missing" or silently dropping (b) into Branch A would mask the
   caller's bug at the diagnostic boundary; raising surfaces it.
2. **Nullable `is_current` mirrored.** The real
   `ItemVersion.is_current` column is nullable; `serialize_link`
   coerces with `bool(version.is_current)` so a real `NULL` row maps
   to `version_is_current = False`. The resolver reproduces this
   exactly: `ItemVersionRow.is_current: Optional[bool] = None`, output
   computes `bool(version_row.is_current)` so `None → False`. Typing
   `is_current: bool` would make a legal DB state unrepresentable.

### Reuse, not reimplementation

The resolver **imports** the merged `BundleDocumentDescriptor` and
returns instances of it directly — there is no shadow descriptor. The
descriptors plug into `evaluate_bundle_version_locks` unchanged (the
compose-proof test pins this).

### Batch

`resolve_bundle_document_descriptors(pairs) -> tuple[...]` — batch
over `Sequence[Tuple[WorkorderDocLinkRow, Optional[ItemVersionRow]]]`;
deterministic (input order preserved); the §3 input-shape rule
applies per-pair (a mismatch in any pair raises from that pair's
call).

### Boundary (owner-scoped, taskbook §8)

No DB read, no `session`, no plugin edit
(`plugins/yuantus-pack-and-go/` untouched), no version-lock
enforcement, no edit to `pack_and_go_version_lock_contract` or
`parallel_tasks_service`/`serialize_link`, no schema / migration /
tenant-baseline / feature flag / runtime wiring. The only
cross-contract import is the merged
`pack_and_go_version_lock_contract`.

## 4. Test Matrix

`src/yuantus/meta_engine/tests/test_pack_and_go_db_resolver_contract.py`
(15 tests; group counts a point-in-time snapshot):

- Row DTOs: frozen, `extra=forbid`, non-empty `document_item_id`/`id`/
  `item_id`; `ItemVersionRow` accepts `True`/`False`/`None` for
  `is_current` (the §3 nullable policy).
- **`test_resolver_mirrors_serialize_link_three_branches` (MANDATORY,
  exactly named)** — Branch A (no version), Branch B-owned-current,
  Branch B-foreign-not-current, Branch C (missing). Direct equality
  vs. expected `BundleDocumentDescriptor` instances.
- **`test_resolver_rejects_mismatched_version_row` (MANDATORY,
  exactly named)** — `version_row.id != link_row.document_version_id`
  → `ValueError`, parametrized across the two RATIFIED sub-cases:
  **(a)** both set, ids differ; **(b)** `document_version_id` falsy
  but `version_row` supplied. The rule is unconditional on
  `version_id` state.
- **`test_resolver_maps_null_is_current_to_false` (MANDATORY, exactly
  named)** — `ItemVersionRow(is_current=None) →
  version_is_current=False` (bit-for-bit `bool(None)`). Prevents
  `is_current: bool` regression.
- **`test_resolver_output_is_the_merged_bundle_descriptor`
  (MANDATORY, exactly named)** — every returned descriptor is the
  merged `BundleDocumentDescriptor` (`isinstance` and field-set
  equality), and the resolved descriptors feed
  `evaluate_bundle_version_locks` unchanged producing the expected
  unlocked/mismatched/stale/locked counts.
- Batch: order preserved across 5 unlocked pairs; mismatch in a batch
  pair propagates as `ValueError`.
- Drift guards: `WorkorderDocLinkRow` fields ⊆
  `WorkorderDocumentLink.__table__.columns`; `ItemVersionRow` fields
  ⊆ `ItemVersion.__table__.columns`; `ItemVersion.is_current.nullable
  is True` (pins the assumption the §3 nullable policy is built on);
  the resolver returns exactly `BundleDocumentDescriptor` (no shadow
  type).
- Purity guard (AST): imports nothing matching `yuantus.database` /
  `sqlalchemy` / `parallel_tasks_service` / `_router` / `plugins` /
  `_service`; **does** import `pack_and_go_version_lock_contract`; no
  `session` reference (name or attribute).
- No-evaluate/no-assert (AST): module does not call
  `evaluate_bundle_version_locks` or `assert_bundle_version_locks` —
  the resolver only produces descriptors; the gate stays the caller's
  responsibility.
- No-`assert_*` callable: the resolver is a mapper, not an enforcer;
  the §3 raise on mismatch is input-shape validation inside the
  mapper, not a separate enforcement seam.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_pack_and_go_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_pack_and_go_version_lock_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/pack_and_go_db_resolver_contract.py
git diff --check
```

No alembic / tenant-baseline — the contract adds no schema.

Observed as of 2026-05-18: 15 resolver-contract tests passed; merged
pack-and-go version-lock contract regression passed (unchanged);
doc-index trio + R2 portfolio drift guard passed; `py_compile` clean;
`git diff --check` clean.

## 6. Non-Goals (reaffirmed from taskbook §8)

- No DB read / no `session` — caller-supplied rows only; the actual
  query is a separate later opt-in.
- No plugin wiring — `plugins/yuantus-pack-and-go/` is not edited.
- No version-lock enforcement — `evaluate_bundle_version_locks` /
  `assert_bundle_version_locks` are reused **unchanged**; the
  resolver only produces descriptors, it does not decide
  lock-clear.
- No edit to `pack_and_go_version_lock_contract` or
  `parallel_tasks_service`/`serialize_link`.
- No schema / migration / tenant-baseline / feature flag / runtime
  wiring.
- No contact with the other R2 contracts beyond importing the merged
  `BundleDocumentDescriptor`.
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Follow-ups (each its own separate opt-in)

- An actual DB resolver that **queries**
  `meta_workorder_document_links` + `meta_item_versions` and feeds
  these row DTOs (touches the DB — separate).
- Wiring the resolved descriptors into the pack-and-go plugin
  (plugin + runtime — separate; the merged
  `assert_bundle_version_locks` is the enforcement seam, also
  separate).
- Maintenance and breakage DB-resolver pure contracts (each its own
  taskbook + opt-in implementation PR).
