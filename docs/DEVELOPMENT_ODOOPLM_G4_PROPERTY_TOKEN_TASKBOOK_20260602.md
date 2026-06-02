# Claude Taskbook: OdooPLM Gap G4 — Category/Property Numbering Token Grounding + Scope-Lock

Date: 2026-06-02

Type: **Doc-only taskbook (grounding + scope-lock).** It grounds the **deferred**
G4 category/property numbering token against current `main`, resolves the four
risks that caused the deferral, and scope-locks a deliberately **constrained**
design. It changes no code. **Merging this taskbook does NOT authorize the
implementation** — that requires its own explicit opt-in.

Origin: `DEVELOPMENT_ODOOPLM_G4_NUMBERING_PATTERN_TASKBOOK_20260529.md` (#679)
§4.x, which ratified the category/property token as **v1-DEFER** with four named
risks (add-time ordering, missing/empty value, sanitization, unbounded
sequence-row cardinality). The G4 numbering R1 (#680) shipped `literal + UTC date
+ {seq}`. Baseline `main = 5b73f4c3` (after G3 BOM auto-layout #685).

## 0. What this is (and is not)

- The follow-up that closes the G4 numbering line: add a `{prop:<key>}` token so a
  number can carry a **classification segment** (aligning with odooplm
  `plm_auto_engcode`/`internalref`, which key a code off a category field).
- **Grounding first**: re-verified the rendering path, the add-time ordering, and
  the property-constraint model against current code (below). The result drives a
  deliberately constrained design — the safe one the §4.x deferral demanded.
- **No GPL/AGPL**: semantics-only alignment. No odooplm code read.

## 1. Pivotal grounding facts (these define the design)

1. **The token-rendering chain takes NO properties.** `apply(item_type,
   properties)` (`numbering_service.py:58`) HAS the properties but calls
   `self.generate(item_type)` (`:64`) **without** them; `generate` → `resolve_rule`
   → `_resolve_token_rule` → `_render_pattern_prefix(pattern)` (`:161`) never see
   property values. A `{prop}` token therefore requires **threading `properties`**
   through that chain (an additive, optional param — §5).
2. **Add-time ordering: numbering runs BEFORE the validator.** In
   `operations/add_op.py`, auto-numbering is **step 7**
   (`apply_auto_numbering(..., dict(new_item.properties or {}))`) and the
   validator's `validate_and_normalize` is **step 8**. So a `{prop}` token sees
   ONLY the property values present in the **incoming AML payload** at step 7 —
   **validator-defaulted / derived / computed properties (step 8+) are
   invisible** to numbering.
3. **No constrained classification field exists.** `models/meta_schema.py:83`
   `Property` has `data_type` (default `"string"`), `is_required`, and a
   `data_type="item"` + `data_source_id` item-reference mechanism — but **no
   enum / allowed-values column**, and there is **no standard
   `category`/`classification` property on `Part`**. So an arbitrary string
   property has **no server-side cardinality bound**.

## 2. The cardinality crux (read this before §3)

A `{prop:<key>}` pointed at a **high-cardinality** property (free-text, or
near-unique per item) is not an operational caveat — it is the feature
**silently not doing its job**: every item renders a distinct prefix
`<value>-0001`, the counter **never sequences past `start`**, and the
`meta_numbering_sequences` table grows one row per item. That is exactly the
"plausible-but-wrong, fail-OPEN" outcome this program has locked against
everywhere (G3 duplicate item_id → skip+warn; G3 unresolved rel_id → terminal;
G4 §6 floor-compat). Because §1.3 shows the server cannot infer eligibility from
the schema, **"which property values are numbering-eligible" must be a first-class
locked decision**, not a footnote.

## 3. Cardinality LOCK (§6-level) — config-declared value→code map, fail-closed

**`{prop:<key>}` is valid ONLY against a config-declared finite map** of source
value → emitted code, e.g.:

```json
"numbering": {
  "pattern": "PART-{prop:category}-{seq}",
  "props": { "category": { "values": { "Mechanical": "MEC", "Electrical": "ELE" } } },
  "width": 4
}
```

- Render the **operator-vetted CODE** (`MEC`), never the raw value.
- A value **not in the map** → `ValueError` (fail-closed). Missing/empty property
  → `ValueError`. The map is REQUIRED for every `{prop}` token; a `{prop}` with no
  `props[key].values` declared → `ValueError` at config resolve.
- **Duplicate output codes within one map → `ValueError` at config resolve.** Two
  source values mapping to the same code (`{"Mechanical":"MEC","Mech":"MEC"}`)
  would silently merge two categories into one counter scope — the SAME
  "silently-wrong" class as normalization (below), wearing a different hat — so it
  is rejected, not allowed. (The input side is locked above; this locks the
  output side.)
- Cardinality is therefore **bounded by construction** (finite declared codes →
  finite prefixes → the counter actually sequences within each category), and the
  number stays clean/short.

**Rejected alternatives** (all are the "silently wrong" class):
- raw arbitrary string property — unbounded cardinality footgun (§2);
- **normalize** unsafe chars instead of rejecting — distinct values collapse to
  one prefix (`A/B` and `A-B` → `A-B`), silently merging two categories into one
  counter scope; so v1 **rejects, never normalizes**;
- length-bound as a cardinality proxy — weak, doesn't prevent the footgun.

## 4. Add-time ordering LOCK (§6-level)

A `{prop}` token reads ONLY the property values present in the **step-7 incoming
payload** (`new_item.properties` at the `apply_auto_numbering` call). **Properties
defaulted / normalized / derived by the step-8 validator are invisible** to
numbering. The implementation MUST NOT reach forward to the validator to
"complete" a property; a referenced property absent from the step-7 payload →
`ValueError` (per §3). This boundary is a real surprise surface and is locked
here, not assumed.

## 5. Integration / threading (ratify)

Thread `properties` as an **additive, optional** parameter through `apply` →
`generate(item_type, properties=None)` → `resolve_rule(item_type,
properties=None)` → `_resolve_token_rule(raw, properties)` →
`_render_pattern_prefix(pattern, properties)`. Legacy and `literal+date+{seq}`
callers pass nothing and are byte-for-byte unchanged. **Zero-regression gate:**
the existing G4 numbering tests in `test_numbering_service.py` must stay green
**unchanged**.

## 6. Token syntax + value rules (ratify)

- `{prop:<key>}` renders `props[<key>].values[<payload value>]` (the code).
- **Scalar only**: a list/dict property value → `ValueError` (multi-value not
  supported in v1).
- The §6.2 / §6.4 locks of the numbering taskbook **still apply to the final
  rendered prefix, regardless of where a character came from.** A non-digit must
  precede `{seq}`: a code ending in a digit immediately before `{seq}` (e.g.
  `MEC2{seq}`) is **rejected by the existing check**, NOT waved through because the
  code is operator-chosen; and the rendered prefix must be ≤ 120 chars. The
  emitted **code** keeps segments short, but the §6.2/§6.4 guards — not the
  operator's good behavior — are the enforcement.

## 7. Historical compatibility (ratify)

The numbering R1 floor-compat locks are **unchanged**: token mode does no
historical floor scan (§6.1), the non-digit-before-`{seq}` rule (§6.2), and the
≤120 length rule (§6.4) all carry over verbatim. The `{prop}` segment is simply
more rendered-prefix content subject to the same checks — **no new historical
surface**, no migration.

## 8. Persistence / route / test wiring (ratify)

- **No migration, no new table/column** — the value→code map lives in the
  existing `ItemType.ui_layout["numbering"]` JSON (alongside `pattern`); no
  `Property`-model change (the map is numbering config, not a schema feature).
- **No new route**; **no route-count pins**.
- **Extend** the regression-only `test_numbering_service.py` (per
  [[feedback-test-file-ci-wiring-fanout]]) — no new test file → no allowlist /
  ci.yml / portfolio fan-out.

## 9. Required tests / guards for the implementation (checklist — test EACH)

Per [[feedback-taskbook-required-list-checklist]], each clause needs its own test:
- `{prop}` with a mapped value → renders the code;
- value **not in the map** → `ValueError` (fail-closed);
- **missing / empty** property → `ValueError`;
- `{prop}` token with **no `props[key].values` declared** → `ValueError`;
- **duplicate output codes** within one map → `ValueError` at config resolve (§3);
- **multi-value** (list/dict) property → `ValueError`;
- a pattern with a `{prop}` but **no `{seq}`** → `ValueError` (the numbering-R1
  "must include `{seq}`" rule still holds);
- a `{prop}` appearing **after `{seq}`** → `ValueError` (the R1 "`{seq}` must be
  the final token" rule still holds);
- a code that would put a **digit immediately before `{seq}`** → rejected by the
  §6.2 lock;
- rendered prefix **> 120** → `ValueError` (§6.4);
- **legacy + `literal+date+{seq}`** configs unchanged (zero-regression);
- the rendered prefix uses ONLY the step-7 payload value (a validator-default is
  NOT consulted).

## 10. Non-Goals

No raw-arbitrary-property token (map required); no value normalization (reject);
no reach into the step-8 validator for defaulted properties; no enum/allowed-values
addition to the `Property` model; no multi-value support; no migration / route /
route-count pins; no GPL/AGPL.

## 11. Step-0 to enter the IMPLEMENTATION

1. Re-confirm the threading chain (`apply`→`generate`→`resolve_rule`→
   `_resolve_token_rule`→`_render_pattern_prefix`) and that adding an optional
   `properties` param leaves every existing caller unchanged.
2. Re-confirm `add_op.py` step-7 numbering / step-8 validator ordering (the §4
   add-time lock).
3. Re-confirm the value→code map lives in `ui_layout["numbering"]` (no `Property`
   model change); no migration.
4. Test wiring: extend `test_numbering_service.py` (regression-only); §9 checklist.

## 12. Preconditions to enter the IMPLEMENTATION

1. §3 cardinality LOCK (config-declared value→code map; unmapped/missing/empty →
   ValueError; reject-not-normalize) ratified — gating;
2. §4 add-time ordering LOCK (step-7 payload only; validator-defaults invisible)
   ratified — gating;
3. §5 additive-threading + zero-regression gate ratified;
4. §6 token syntax + scalar-only + §6.2/§6.4 carry-over ratified;
5. §7 historical compat unchanged + §8 no-migration/route acknowledged;
6. §10 non-goals ratified.

A **separate explicit opt-in** then authorizes the implementation.

## 13. Reviewer Focus

1. §2/§3 — is the cardinality reframe right, and is the value→code-map LOCK the
   correct fail-closed design (vs raw property / normalize / length-proxy)?
2. §4 — add-time ordering lock correct (step-7 payload only)?
3. §5 — threading is additive/optional with the existing tests as the zero-regression gate?
4. §6/§7 — scalar-only + §6.2/§6.4 + floor-compat carry over verbatim?
5. §8/§10 — no migration, no route, no `Property`-model change, map-in-config; GPL/AGPL OUT?

## 14. Status

Doc-only grounding + scope-lock. Ready for review once the doc exists at the
canonical path; `DELIVERY_DOC_INDEX.md` references it + its DEV/verification
record (sorted under `## Development & Verification`); doc-index / sorting /
completeness checks pass; `git diff --check` clean. Ratifying §3–§10 sets the
property-token implementation plan; **a separate explicit opt-in authorizes the
implementation.** With this, the G4 numbering line is fully scoped; the remaining
OdooPLM items (minor gaps finishing/treatment, `plm_project`) stay
separately-opted.
