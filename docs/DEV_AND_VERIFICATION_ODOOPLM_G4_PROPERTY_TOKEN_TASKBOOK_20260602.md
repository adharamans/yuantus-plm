# DEV & Verification: OdooPLM Gap G4 — Category/Property Token Grounding/Scope-Lock Taskbook

Date: 2026-06-02

Records the doc-only delivery of
`DEVELOPMENT_ODOOPLM_G4_PROPERTY_TOKEN_TASKBOOK_20260602.md` — the grounding +
scope-lock for the deferred G4 category/property numbering token. Doc-only: no
code; merging it does **not** authorize the implementation. Baseline
`main = 5b73f4c3` (after G3 BOM auto-layout #685).

## 1. What changed

- New G4 property-token grounding/scope-lock taskbook: a `{prop:<key>}` token
  gated behind a **config-declared value→code map** (fail-closed), resolving the
  four §4.x deferral risks. Threading is additive/optional; no migration, no
  route; extend the regression-only numbering test.
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = 5b73f4c3`)

- **Threading gap**: `apply(item_type, properties)` calls `generate(item_type)`
  WITHOUT properties (`numbering_service.py:58/:64`); the whole render chain
  (`resolve_rule`→`_resolve_token_rule`→`_render_pattern_prefix`) is
  property-blind → a `{prop}` token requires threading `properties` through it.
- **Add-time ordering**: `add_op.py` runs auto-numbering at step 7, the validator
  `validate_and_normalize` at step 8 → a `{prop}` token sees ONLY the step-7
  incoming payload; validator-defaulted/derived properties are invisible.
- **No constrained classification field**: `models/meta_schema.py:83` `Property`
  has `data_type`/`is_required` + a `data_type="item"` reference, but **no
  enum/allowed-values column** and no standard `category` field on `Part` → an
  arbitrary string property has no cardinality bound.

## 3. Locked decisions (summary)

- **Cardinality LOCK (the crux)**: `{prop:<key>}` is valid only against a
  config-declared finite **value→code map**; render the operator-vetted code;
  unmapped / missing / empty / undeclared-map / **duplicate-output-code** /
  multi-value → **ValueError** (fail-closed). Bounds cardinality by construction;
  the counter actually sequences within each category. Rejected: raw arbitrary
  property (footgun), normalize (silently merges distinct categories), duplicate
  codes (merges categories — output side of the same bug), length-bound proxy
  (weak).
- **Add-time ordering LOCK**: step-7 payload only; validator-defaults invisible.
- **Threading**: additive/optional param; existing numbering tests green-unchanged
  = zero-regression gate.
- **Carry-over**: §6.2 (non-digit before `{seq}`) + §6.4 (≤120) + §6.1 (no
  historical scan) from numbering R1 unchanged; scalar-only. **No migration, no
  table/column, no route, no `Property`-model change** (map lives in
  `ui_layout["numbering"]`). Extend the regression-only `test_numbering_service.py`.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13,
  `verify_material_sync_static.py` — pass (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only grounding + scope-lock. Ratifying §3–§10 of the taskbook sets the
property-token implementation plan; the implementation needs its own explicit
opt-in. The remaining OdooPLM minor gaps (finishing/treatment, `plm_project`)
remain separately-opted.
