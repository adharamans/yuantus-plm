# DEV & Verification: OdooPLM Gap G4 — Numbering Pattern Grounding/Scope-Lock Taskbook

Date: 2026-05-29

Records the doc-only delivery of
`DEVELOPMENT_ODOOPLM_G4_NUMBERING_PATTERN_TASKBOOK_20260529.md` — the grounding +
scope-lock for the G4 numbering-pattern-vocabulary gap. Doc-only: no code;
merging it does **not** authorize the implementation. Baseline `main = 346b7381`
(after G5 spare-parts #678).

## 1. What changed

- New G4 grounding/scope-lock taskbook (gap re-verified; render-into-existing-
  `prefix` approach with the counter as the trailing zero-pad; closed token
  vocabulary; category/property token **v1-DEFER**; the floor-compat correctness
  LOCK; no migration / no route / no
  pins; extend the regression-only numbering test; non-goals; step-0 +
  preconditions).
- This DEV/verification record.
- Two sorted `DELIVERY_DOC_INDEX.md` entries (under `## Development &
  Verification`).

## 2. Grounding (against `main = 346b7381`)

- **Gap real and narrow**: the entire pattern vocabulary is
  `numbering_service.py:63` `f"{rule.prefix}{value:0{rule.width}d}"`. The
  allocator (`:58`–`:233`) is tenant/org-scoped + concurrency-safe (PG
  `on_conflict_do_update`+`greatest`, SQLite upsert, generic optimistic retry,
  floor-compat) and is explicitly OUT of scope to modify.
- **Config** rides on `ItemType.ui_layout["numbering"]` (`{enabled, prefix,
  width, start}`, `_raw_rule_config:100`) + `DEFAULT_NUMBERING_RULES:24`; no
  config table, no admin route.
- **Counter scope key** = `NumberingSequence.prefix` (`models/numbering.py:24`,
  `String(120)`); UNIQUE `(item_type_id, tenant_id, org_id, prefix)`.
- **Single production caller**: `operations/add_op.py:98`.
- **Test** `tests/test_numbering_service.py` is regression-only (not allowlisted,
  not in ci.yml, no portfolio glob) → extending it needs no CI-wiring fan-out.

## 3. Locked decisions (summary)

Render a token pattern's non-counter portion into the existing `prefix` slot and
feed the UNCHANGED allocator; counter stays the trailing zero-padded `{seq}`;
optional `pattern` key switches token mode (legacy `{prefix,width,start}`
unchanged). Closed token set v1 = literal + UTC date (whitelisted strftime
subset) + `{seq}`; category/property token is **deferred** to a separate
follow-up (four risks enumerated). **Floor-compat LOCK**: token mode
does NO historical scan (counter starts at `start`); mandatory non-digit
separator immediately before `{seq}`; rendered length > 120 → ValueError;
unknown token → ValueError. **No migration, no new route, no route-count pins,
no portfolio entries**; extend the regression-only numbering test. Non-goals: no
allocator/concurrency change, no mid-string counter, no DSL, no revision-scheme
change, no GPL/AGPL, no UI.

## 4. Verification (this doc-only PR)

- doc-contract pytests — delivery-doc-index references; `## Development &
  Verification` sorting + completeness; doc-index sorting — pass.
- `verify_lisp_shell_static.py` 28, `verify_bridge_static.py` 13,
  `verify_material_sync_static.py` — pass (unchanged; no client/helper change).
- `git diff --check` clean.

## 5. Status

Doc-only grounding + scope-lock. Ratifying §3–§8 of the taskbook sets the
implementation plan; the implementation needs its own explicit opt-in. The other
OdooPLM gaps (G3 explode, minor) remain separately-opted.
