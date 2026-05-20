# Phase 3.4 Readiness Taskbook - Development and Verification

Date: 2026-05-19

## 1. Summary

Formalized the owner-staged, Codex-reviewed Phase 3.4 readiness inventory as a
doc-only taskbook:

- `docs/PHASE3_4_READINESS_TASKBOOK_20260519.md`
- this development and verification note
- `docs/DELIVERY_DOC_INDEX.md`

This PR does not start Phase 5, does not accept P3.4 evidence, does not connect
to PostgreSQL, and does not authorize any implementation work.

## 2. Design

The taskbook consolidates the current Phase 3.4 cutover-readiness state from the
existing tenant-import, provisioning, and rehearsal artifacts.

The canonical conclusion is unchanged:

- Local Phase 3.4 tooling and local safety hardening are ready.
- Provisioning prerequisites from Phase 3.3 are in place.
- Synthetic drill output remains explicitly non-evidence.
- The only blocking item is external operator-run non-production PostgreSQL
  rehearsal evidence plus reviewer acceptance.
- `ready_for_cutover=false` remains a hard contract.

The taskbook also records the decision boundary for future work:

- Option A: wait for external operator rehearsal evidence.
- Option B: continue local safety hardening, explicitly marked as local-only.
- Option C: separately decide P3.3.3 tenant baseline revision timing.
- Option D: other owner-directed work.

Each option remains a separate opt-in. This formalization does not choose or
start any of them.

## 3. Authorship and Scope

The taskbook content was owner-staged before this formalization step. This PR
preserves that content and adds only repository discoverability plus this
verification note.

Hard boundaries:

- No `src/` edits.
- No `migrations*/` edits.
- No `scripts/` edits.
- No `alembic*.ini` edits.
- No runtime, schema, operator-pack, or evidence artifact changes.
- No secret, DSN, token, or password material in tracked files.
- `local-dev-env/` remains untracked and out of scope.

## 4. Verification

Focused stop-gate validation:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/tests/test_tenant_import_rehearsal_stop_gate_contracts.py
```

Doc-index validation:

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py
```

Whitespace check:

```bash
git diff --check
```

Observed locally:

- Stop-gate contracts: `10 passed`.
- Doc-index trio: `4 passed`.
- `git diff --check`: clean.

## 5. Reviewer Checklist

- Confirm the taskbook is inventory-only and does not authorize Phase 5.
- Confirm the external evidence blocker remains explicit and unchecked.
- Confirm `ready_for_cutover=false` remains a hard acceptance boundary.
- Confirm no runtime, schema, migration, script, or operator-pack files changed.
- Confirm the taskbook and this verification note are indexed.
