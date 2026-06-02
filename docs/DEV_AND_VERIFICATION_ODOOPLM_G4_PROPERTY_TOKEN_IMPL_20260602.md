# DEV & Verification: OdooPLM Gap G4 — Category/Property Token R1 Implementation

Date: 2026-06-02

Records the **R1 implementation** of the G4 category/property numbering token, per
the merged scope-lock taskbook
`DEVELOPMENT_ODOOPLM_G4_PROPERTY_TOKEN_TASKBOOK_20260602.md` (#686). Baseline
`main = 562c715c`. Confined to `numbering_service.py` + its test; **no migration,
no route, no `Property`-model change** — the value→code map rides the existing
`ui_layout["numbering"]` JSON.

## 1. Step-0 grounding re-confirmed (taskbook §11)

- The render chain was **property-blind**: only the internal chain
  (`apply`→`generate`→`resolve_rule`→`_resolve_token_rule`→`_render_pattern_prefix`)
  calls these methods — no external production caller — so an **additive optional
  `properties` param** threads cleanly (existing tests pass single positional args
  → unchanged).
- `apply` passes the **step-7** `props` it already holds; the §4 add-time lock
  holds (validator-defaulted properties, step 8, are not in that dict).
- The value→code map lives in `ui_layout["numbering"].props` — no `Property`
  model change, no migration.

## 2. What changed (`services/numbering_service.py` only)

- Threaded `properties` additively: `generate(item_type, properties=None)` →
  `resolve_rule(item_type, properties=None)` → `_resolve_token_rule(raw,
  properties=None)` → `_render_pattern_prefix(pattern, *, props_config, properties)`.
  `apply` passes its `props`. Legacy / `literal+date+{seq}` callers unchanged.
- `_render_pattern_prefix` gains a `{prop:<key>}` branch delegating to a new
  `_render_prop_token(key, *, props_config, properties)` helper.
- `_render_prop_token` — **fail-closed (ValueError, never normalize)** on: empty
  key; undeclared / empty `props[key].values` map; **empty output code** (would
  silently vanish the segment); **duplicate output codes** (output-side merge bug;
  `str()` keying so int `1` and str `"1"` collapse); **missing / empty** property;
  **multi-value** (list/dict) property; or a value **not in the map**. Otherwise
  returns the operator-vetted **code**.
- The §6.1/§6.2/§6.4 + `{seq}`-required / `{seq}`-final locks carry over verbatim:
  the prop code is just more rendered-prefix content, so a code with a trailing
  digit immediately before `{seq}` (`MEC2{seq}`) is rejected by the existing §6.2
  check, and a rendered prefix > 120 by §6.4.

## 3. §3 / §4 locks realized

- **Cardinality LOCK**: `{prop}` renders only a config-declared value→code map;
  unmapped / undeclared / duplicate-code / missing / empty / multi-value →
  ValueError. Cardinality is bounded by the finite declared codes; the counter
  sequences within each category.
- **Add-time ordering LOCK**: the value is read ONLY from the `properties` dict
  `apply` received (the add_op step-7 payload). A missing key → ValueError (no
  reach-forward to the step-8 validator).

## 4. Verification

- DB-backed (`YUANTUS_PYTEST_DB=1`) — `test_numbering_service.py` **42 passed**:
  the original **27 unchanged** (zero-regression gate — the threading is additive)
  + 15 new property-token tests, one per §9 checklist clause (+ empty-code and
  int/str-collision guards):
  - mapped value → code; `apply` threads properties end-to-end;
  - value-not-in-map / missing / empty / **multi-value (list & dict)** → ValueError;
  - **undeclared / empty map**, **empty output code**, and **duplicate output
    codes** (incl. the int/str `1`/`"1"` collision) → ValueError;
  - code with a **trailing digit before `{seq}`** → §6.2 reject;
  - rendered prefix **> 120** → §6.4 reject;
  - `{prop}` with **no `{seq}`** and `{prop}` **after `{seq}`** → ValueError.
- `create_app()` builds; **691** routes (no route change); migration-coverage 4
  (no new table). All changes **extend existing files** (no new test file) → no
  conftest allowlist / ci.yml / portfolio change
  ([[feedback-test-file-ci-wiring-fanout]]).
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass.
  `git diff --check` clean.

## 5. CI shape (merge-readiness)

This PR edits `DELIVERY_DOC_INDEX.md`, so **`contracts` runs** — but the new tests
run **only under `regression`**. **Merge-readiness = `regression: pass`** (a real
run), not just aggregate CLEAN.

## 6. Non-Goals upheld

No raw-arbitrary-property token (map required); no value normalization (reject);
no reach into the step-8 validator; no enum/allowed-values addition to the
`Property` model; no multi-value support; no migration / route / route-count pins;
no GPL/AGPL.

## 7. Status

G4 category/property token R1 implemented and verified — the constrained,
fail-closed value→code-map design the taskbook scoped. The **G4 numbering line is
now complete** (literal + date + `{seq}` + category code). Remaining OdooPLM items
(each separately opted-in): the minor gaps (finishing/treatment, `plm_project`);
G1 native-signoff and G6 scale remain externally-gated.
