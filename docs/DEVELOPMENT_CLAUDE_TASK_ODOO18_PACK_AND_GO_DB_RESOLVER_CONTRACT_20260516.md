# Claude Taskbook: Odoo18 Pack-and-Go DB-Resolver Pure-Contract R1

Date: 2026-05-16

Type: **Doc-only taskbook.** Changes no runtime, no schema, no service.
Specifies the contract a later, separately opted-in implementation PR
will deliver. Merging this taskbook does NOT authorize that code.

## 1. Purpose

R2 closeout §4 **Tier-A** follow-up #3 (the most complex of the
pure-first tier — it must define the *row → descriptor* boundary
precisely). The merged pack-and-go version-lock bridge contract
(`pack_and_go_version_lock_contract.py`, PR #570 `c7e6fd5`) evaluates
`BundleDocumentDescriptor`s the **caller** supplies, but there is no
typed, pure mapping from the persisted rows to those descriptors. This
R1 supplies that pure mapping — **the contract still does not read the
DB**; the caller fetches rows, the pure function maps them.

## 2. Current Reality (grounded — read before implementing)

- Merged `src/yuantus/meta_engine/services/pack_and_go_version_lock_contract.py`
  `BundleDocumentDescriptor` (frozen Pydantic v2, `extra="forbid"`):
  - `document_item_id: str` (non-empty)
  - `document_version_id: Optional[str] = None`
  - `version_belongs_to_item: Optional[bool] = None`
  - `version_is_current: Optional[bool] = None`
  plus `evaluate_bundle_version_locks` / `assert_bundle_version_locks`
  (the version-lock evaluation/enforcement — **out of scope here**).
- Persisted source rows:
  - `meta_workorder_document_links` (`WorkorderDocumentLink`): columns
    `id, routing_id, operation_id, document_item_id,
    inherit_to_children, visible_in_production, created_at,
    document_version_id, version_locked_at, version_lock_source`
    (`document_version_id` etc. added by the version-lock R1 #565).
  - `meta_item_versions` (`ItemVersion`): `id, item_id, is_current,
    is_released, version_label, …`.
- The **authoritative derivation** of the descriptor's version facts is
  `WorkorderDocumentPackService.serialize_link`
  (`parallel_tasks_service.py` ~line 6155). Its exact version branch:

  ```
  version_is_current = None
  version_belongs_to_item = None
  if link.document_version_id:                     # version pinned
      version = session.get(ItemVersion, version_id)
      if version is not None:
          version_is_current     = bool(version.is_current)
          version_belongs_to_item = (str(version.item_id)
                                      == str(link.document_item_id))
      else:                                        # version row missing
          version_belongs_to_item = False
          # version_is_current stays None
  # (no document_version_id): both stay None
  ```

The R1 resolver must reproduce these **three branches bit-for-bit** —
purely, from caller-supplied rows.

## 3. Row → Descriptor Boundary (the core of this taskbook)

The contract is **pure**: it does **not** query. The caller fetches and
passes typed row views; the missing-version case is signalled by the
caller passing `version_row=None` (i.e. "I resolved `document_version_id`
and the `ItemVersion` does not exist") — this is exactly the
`session.get(...) is None` branch of `serialize_link`.

| serialize_link branch | Caller input | Resolver output |
|---|---|---|
| no `document_version_id` | `link_row.document_version_id is None` | `version_belongs_to_item=None`, `version_is_current=None` |
| version pinned, row found | `version_row` provided (and `version_row.id == link_row.document_version_id`) | `version_belongs_to_item = (str(version_row.item_id) == str(link_row.document_item_id))`, `version_is_current = bool(version_row.is_current)` |
| version pinned, row missing | `link_row.document_version_id` set **and** `version_row is None` | `version_belongs_to_item=False`, `version_is_current=None` |

**Input-shape validation — RATIFIED (2026-05-16):** if a `version_row`
is supplied, its `id` MUST equal `link_row.document_version_id`; a
mismatch is a caller bug and the resolver **raises `ValueError`**
(binding, not an open question). Rationale (ratified): silently
treating a mismatched row as "missing" would mask the caller's bug and
wrap the wrong row as the "version-pinned-but-missing" branch — a
diagnostic loss; raising surfaces the bug at its source. This is
*input-contract validation* (like the consumption/ECR contracts
rejecting malformed input), **not** version-lock enforcement (which
remains the merged `assert_bundle_version_locks`, untouched).

**`is_current` is `Optional[bool]` (mirrors the real column):** the
`ItemVersion.is_current` column is **nullable** (verified). The
shipped `serialize_link` coerces it with `bool(version.is_current)`, so
a real `NULL` row maps to `version_is_current = False`. The resolver
must reproduce this exactly: `ItemVersionRow.is_current` is
`Optional[bool] = None`, and the output computes
`version_is_current = bool(version_row.is_current)` so `None → False`.
Anything else — e.g. typing `is_current: bool` — would make a legal DB
state unrepresentable and break bit-for-bit parity.

## 4. R1 Target Output (for the later, separately opted-in impl PR)

New pure module
`src/yuantus/meta_engine/services/pack_and_go_db_resolver_contract.py`:

- `WorkorderDocLinkRow` — frozen Pydantic v2, `extra="forbid"`. The
  subset of `meta_workorder_document_links` columns the mapping needs:
  `document_item_id: str` (non-empty), `document_version_id:
  Optional[str] = None`. Field names mirror the column names
  (drift-guarded).
- `ItemVersionRow` — frozen Pydantic v2, `extra="forbid"`. The subset
  of `meta_item_versions` columns needed: `id: str` (non-empty),
  `item_id: str`, **`is_current: Optional[bool] = None`** (mirrors the
  real nullable column — see §3). The resolver outputs
  `version_is_current = bool(version_row.is_current)` so `None →
  False`, matching `serialize_link` bit-for-bit. Field names mirror
  the column names (drift-guarded).
- `resolve_bundle_document_descriptor(link_row, version_row=None)
  -> BundleDocumentDescriptor` — **pure**; reproduces the three
  serialize_link branches in §3; raises `ValueError` on the
  version_row/document_version_id id mismatch. Returns the **merged**
  `BundleDocumentDescriptor` (imported, not reimplemented).
- `resolve_bundle_document_descriptors(pairs) -> tuple[...]` — batch
  over a `Sequence[tuple[WorkorderDocLinkRow, Optional[ItemVersionRow]]]`;
  deterministic (input order preserved).

No DB read, no `session`, no `eval`, no plugin edit, no enforcement.
Imports the merged `BundleDocumentDescriptor` **only**.

## 5. Tests Required (in the later impl PR)

New `test_pack_and_go_db_resolver_contract.py`:

- Row DTOs: frozen, `extra=forbid`, non-empty `document_item_id`/`id`.
- **`test_resolver_mirrors_serialize_link_three_branches` (MANDATORY,
  exactly named)** — the three §3 branches produce exactly the
  descriptor `serialize_link` would (no version → None/None; version
  found + owned → True/`is_current`; version found + foreign →
  False/`is_current`; version missing → False/None).
- **`test_resolver_rejects_mismatched_version_row` (MANDATORY, exactly
  named)** — `version_row.id != link_row.document_version_id` →
  `ValueError`; pins the §3 input-shape decision.
- **`test_resolver_maps_null_is_current_to_false` (MANDATORY, exactly
  named)** — `ItemVersionRow(is_current=None)` (the legal nullable
  `ItemVersion.is_current` value) → `version_is_current = False`,
  matching `serialize_link`'s `bool(version.is_current)` bit-for-bit;
  pins the §3 nullable-is_current decision and prevents `is_current:
  bool` regression.
- **`test_resolver_output_is_the_merged_bundle_descriptor` (MANDATORY,
  exactly named)** — the return value is an instance of the merged
  `pack_and_go_version_lock_contract.BundleDocumentDescriptor` and the
  resolved descriptors feed `evaluate_bundle_version_locks` unchanged
  (compose proof, no DB).
- Batch: order preserved; mixed branches in one call.
- **Drift guards**: `WorkorderDocLinkRow` fields ⊆
  `WorkorderDocumentLink.__table__.columns`; `ItemVersionRow` fields ⊆
  `ItemVersion.__table__.columns`; the produced descriptor's field set
  equals `BundleDocumentDescriptor.model_fields` (reuse, not
  reimplement) — a change on either side fails loudly.
- **Purity guard** (AST): module imports nothing from
  `yuantus.database` / `sqlalchemy` / `parallel_tasks_service` / a
  router / `plugins` / any `*_service`; imports **only**
  `pack_and_go_version_lock_contract`; contains no `session`/DB call.

The R2 portfolio drift guard
(`test_odoo18_r2_portfolio_contract.py`) must stay green.

## 6. Verification Commands (for the impl PR)

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

## 7. DEV/verification MD requirements (impl PR)

Add `docs/DEV_AND_VERIFICATION_ODOO18_PACK_AND_GO_DB_RESOLVER_CONTRACT_R1_20260516.md`
+ index registration. Must document: (a) the pure row→descriptor
boundary (caller fetches; contract never queries); (b) the three
serialize_link branches reproduced bit-for-bit incl. the
version-missing→`False`/`None` case; (c) the raise-on-mismatch
input-shape rule and why it is input validation, not version-lock
enforcement; (d) the merged `BundleDocumentDescriptor` reused unchanged.

## 8. Non-Goals (hard boundaries for the impl PR)

- **No DB read / no `session`** — caller-supplied rows only; the actual
  query is a separate later opt-in.
- **No plugin wiring** — `plugins/yuantus-pack-and-go/` is not edited.
- **No version-lock enforcement** — `evaluate_bundle_version_locks` /
  `assert_bundle_version_locks` are reused **unchanged**; the resolver
  only produces descriptors, it does not decide lock-clear.
- **No edit to `pack_and_go_version_lock_contract`** or
  `parallel_tasks_service`/`serialize_link`.
- No schema / migration / tenant-baseline / feature flag / runtime
  wiring.
- No contact with the other R2 contracts beyond importing the merged
  `BundleDocumentDescriptor`.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude Code **only after this
taskbook is merged AND a separate explicit opt-in is given**, on branch
`feat/odoo18-pack-and-go-db-resolver-contract-r1-20260516`.

Follow-ups, each its own separate opt-in (explicitly NOT in R1):

- An actual DB resolver that **queries** `meta_workorder_document_links`
  + `meta_item_versions` and feeds these row DTOs (touches the DB —
  separate).
- Wiring the resolved descriptors into the pack-and-go plugin
  (plugin + runtime — separate; the merged
  `assert_bundle_version_locks` is the enforcement seam, also separate).

## 10. Reviewer Focus

- Does the resolver reproduce `serialize_link`'s **three** branches
  bit-for-bit (especially version-pinned-but-missing → `False`/`None`
  and nullable `is_current=None` → `False`)?
- Is `ItemVersionRow.is_current` typed `Optional[bool] = None` so the
  legal nullable DB value is representable, with the output computing
  `bool(version_row.is_current)`?
- Is the raise-on-mismatch rule correctly framed as **input-shape
  validation**, not version-lock enforcement?
- Is the contract pure (no DB/session/`parallel_tasks_service` import;
  caller supplies rows) and does it reuse the merged
  `BundleDocumentDescriptor` unchanged (drift-guarded)?
- Are the row DTO field sets proper subsets of the real table columns?
- Did anything add a DB read, edit the plugin / the shipped
  pack-and-go contract / serialize_link, or add enforcement? It must
  not.
