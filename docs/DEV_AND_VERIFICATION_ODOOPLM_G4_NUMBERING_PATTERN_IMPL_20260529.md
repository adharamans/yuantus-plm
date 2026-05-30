# DEV & Verification: OdooPLM Gap G4 — Numbering Pattern Implementation

Date: 2026-05-29

Records the **implementation** of the G4 numbering-pattern-vocabulary gap, per the
merged grounding/scope-lock taskbook
`DEVELOPMENT_ODOOPLM_G4_NUMBERING_PATTERN_TASKBOOK_20260529.md` (#679, with the
§4.x category/property token ratified **v1-DEFER**). Baseline `main = a78d71cb`.
Code change confined to `numbering_service.py` + its test; **no migration, no
route, no schema change** — the rendered token prefix reuses the existing
`NumberingSequence.prefix` slot and the unchanged concurrency-safe allocator.

## 1. What changed (`src/yuantus/meta_engine/services/numbering_service.py`)

- `NumberingRule` gains `token_mode: bool = False` (frozen-dataclass default keeps
  every existing construction valid).
- `resolve_rule`: when `ui_layout.numbering.pattern` is present, delegates to
  `_resolve_token_rule` (mutually exclusive with the legacy
  `{prefix,width,start}` path, which is byte-for-byte unchanged).
- `_resolve_token_rule` / `_render_pattern_prefix` / `_reject_stray_braces` /
  `_validate_date_fmt`: render a closed token set — **literal**, `{date:FMT}`
  (UTC, whitelisted locale-independent codes `%Y%y%m%d%H%M%S%j`), and a single
  **trailing `{seq}`** — into the scope-prefix. The counter stays the trailing
  zero-pad produced by the unchanged allocator (`generate` line 63 untouched).
- `_floor_allocated_value`: early-returns `rule.start` when `token_mode` (§6.1 —
  no historical scan).

The allocator (`_allocate_counter*`, dialect branches, scoping, retries) and the
single call site `operations/add_op.py:98` are **untouched**.

## 2. §6 floor-compat locks — realized & tested

- **§6.1 no token-mode floor scan**: `_floor_allocated_value` returns `rule.start`
  for token rules. Proven by `test_token_mode_skips_floor_scan_and_starts_at_start`
  (seeds a high legacy number `PART-000500`, asserts the token counter still
  starts at `000001`, not `000501`).
- **§6.2 non-digit separator before `{seq}`**: `_resolve_token_rule` rejects a
  rendered prefix ending in a digit
  (`test_token_pattern_rejects_digit_immediately_before_seq`).
- **§6.4 length ≤ 120**: rendered prefix > 120 → ValueError
  (`test_token_pattern_rejects_rendered_length_over_120`).
- **Unknown token / non-final `{seq}` / missing `{seq}` / stray brace / bad date
  code → ValueError** (`test_token_pattern_validation_errors`, parametrized). No
  literal-brace passthrough.

## 3. Decisions realized

- Token mode = optional `pattern` key; legacy mode unchanged and is the default.
  v1 tokens = literal + UTC date + `{seq}` (category/property token **deferred**
  per the taskbook §4.x ratification).
- Per-rendered-prefix counter scoping is automatic via the existing
  `(item_type_id, tenant_id, org_id, prefix)` unique key — proven by
  `test_token_mode_scopes_counter_per_rendered_prefix` (Jan → 1,2 ; Feb → 1).
- `width`/`start` are reused for `{seq}`; no schema change.

## 4. Verification

- `test_numbering_service.py` — **27 passed** (`YUANTUS_PYTEST_DB=1`): the
  original 15 **unchanged** (zero-regression gate) + 12 new token tests (render,
  alias, validation matrix, §6.1 no-floor-scan, per-prefix scope).
- `create_app()` builds; **688** routes (no route change). Migration-table-
  coverage — 4 passed (no new table).
- CI-wiring fan-out (per [[feedback-test-file-ci-wiring-fanout]]): the test file
  was **extended, not added**; it matches no portfolio glob and is regression-
  only → no conftest allowlist / ci.yml / portfolio change.
- CI shape: this PR edits `DELIVERY_DOC_INDEX.md`, so **`contracts` runs**
  (exercising the doc-index family + app build) — but it does NOT run
  `test_numbering_service.py`. **`regression` is the gate** for the token logic;
  merge-readiness requires `regression: pass` (a real run), not just aggregate
  CLEAN.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13 — pass
  (unchanged). `git diff --check` clean.

## 5. Non-Goals upheld

No allocator/concurrency change; no mid-string counter; no new table/migration;
no new route; no general expression DSL (closed token set, unknown → ValueError);
no revision-scheme change; no GPL/AGPL; no UI; category/property token deferred.

## 6. Status

G4 numbering-pattern implemented and verified (regression-gated). Follow-ups
(each separately opted-in): the deferred category/property token (must resolve the
§4.x risks first); other OdooPLM gaps (G3 explode, minor).
