# DEV & Verification: PLM→ERP Publication Contract — R4 Export Taskbook

Date: 2026-05-29

Records the doc-only delivery of
`DEVELOPMENT_PLM_TO_ERP_PUBLICATION_CONTRACT_R4_EXPORT_TASKBOOK_20260529.md`
— the scope-lock for `/publication/export`, the read-only pull surface that
returns an item's publishable package. Doc-only: no code; merging it does **not**
authorize the export implementation. Baseline `main = d3372f69` (after the R3
connector impl #674). This is the last planned G2 boundary (#666 §9).

## 1. What changed

- New export-slice scope-lock taskbook (route + inputs; admin auth; output format
  = verdict + canonical snapshot; fresh-current snapshot not the outbox;
  read-only/no-side-effect; ineligible → 200 + null snapshot; PULL-vs-PUSH
  relationship to R3; non-goals).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = d3372f69`)

- Reuses the merged R1-B `build_publication_readiness` (shared, HTTP-agnostic
  verdict builder) + the R2 `build_snapshot` (canonical publishable package) — so
  export adds **no** new publication semantics.
- Distinct from `/publication-readiness` (verdict, with diagnostics) — export
  returns the *artifact* (snapshot) when eligible.
- Distinct from R3 (push) — export is read-only PULL, touching **no** adapter /
  registry / outbox and performing **no** external I/O.
- One new route → `len(app.routes)` 683 → 684 at impl (full-tree residual scan
  first).

## 3. Locked decisions (summary)

`GET /plm-erp/items/{item_id}/publication/export`, admin-gated; inputs = item_id +
R1-B read params + `publication_kind` (no `target_system` — target-agnostic);
output = `{item_id, eligible, blocking_reasons, generated_at, snapshot|null}` where
`snapshot` is the canonical (not adapter-specific) package, present only when
eligible; **fresh** snapshot from current readiness (not a stored outbox row);
**read-only, no side effect** (no enqueue/POST/adapter/write); ineligible → 200 +
`snapshot=null` (not 4xx); complementary to (not coupled with) the R3 connector.
Non-goals: no write, no adapter, no target-specific wire payload, no by-outbox-id
export.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only scope-lock. Ratifying §2–§8 of the taskbook sets the export
implementation plan; the export implementation needs its own explicit opt-in.
With R4 landed, the planned G2 line is complete; any vendor-specific adapter or a
by-outbox-id / target-specific export remain later, separately-opted slices.
