# Claude Taskbook: OdooPLM Gap G4 — Numbering Pattern Vocabulary Grounding + Scope-Lock

Date: 2026-05-29

Type: **Doc-only taskbook (grounding + scope-lock).** It re-verifies the G4
numbering-pattern gap against current `main`, grounds the approach on the
**existing** concurrency-safe `NumberingService` allocator, and scope-locks the
slice boundaries. It changes no code. **Merging this taskbook does NOT authorize
the implementation** — that requires its own explicit opt-in.

Origin: `DEVELOPMENT_ODOOPLM_GROUNDED_COMPARISON_20260525.md` §5 G4 ("编号 pattern
词汇：日期/分类/多段 token", impact **低** / fixability **高**, evidence
`numbering_service.py:63`) + §6.4 ("把 `numbering_service` 升级为可配置 token 规则
（日期/分类/多段），对齐 `plm_auto_engcode`/`plm_auto_internalref`"). Baseline
`main = 346b7381` (after G5 spare-parts #678).

## 0. What this is (and is not)

- The **lowest-impact** OdooPLM gap (低), high fixability — the allocator is
  already robust; only the **pattern vocabulary** is narrow.
- **Grounding first**: re-verified the gap and the approach against current code
  (below), so the scope-lock is precedent-backed, not assumed.
- **No GPL/AGPL**: aligns with odooplm `plm_auto_engcode` / `plm_auto_internalref`
  **semantics only** (composable codes from date/category/multi-segment tokens).
  No odooplm code is read, ported, or adapted.

## 1. Gap re-verified (against `main = 346b7381`)

- **The entire pattern vocabulary is one line**: `numbering_service.py:63`
  `return f"{rule.prefix}{value:0{rule.width}d}"`. The composed number is
  strictly `<prefix><zero-padded-counter>`. That is the real, narrow gap.
- **The allocator is robust and MUST NOT be touched**: `numbering_service.py`
  lines 58–233 implement a tenant/org-scoped, **concurrency-safe** sequence
  allocator — PG `on_conflict_do_update` + `func.greatest`, SQLite upsert with
  `func.max`, a generic optimistic-retry loop, and a `_floor_allocated_value`
  that bootstraps from existing item numbers (DB-side numeric extraction or a
  Python scan) for historical-data compatibility. The 2026-05-25 comparison §3
  already retracted the first-round "basic A-Z/1.2.3" misread — only the
  vocabulary is narrow.
- **Config source**: the rule is read from `ItemType.ui_layout["numbering"]`
  (a JSON object `{enabled, prefix, width, start}`) by `_raw_rule_config`
  (`:100`), plus a hardcoded `DEFAULT_NUMBERING_RULES` for `Document`/`Part`
  (`:24`). There is **no dedicated numbering-config table and no admin route**
  (grep of `web/` for `ui_layout`/`numbering` → none); the config rides on the
  ItemType.
- **Counter scope key** = `NumberingSequence.prefix` (`models/numbering.py:24`,
  `String(120)`), with a UNIQUE key on
  `(item_type_id, tenant_id, org_id, prefix)` (`uq_numbering_sequence_scope`).
- **Single production integration point**: `operations/add_op.py:98`
  `apply_auto_numbering(session, item_type, properties)` during item ADD. No
  other production caller.
- **Test contract**: `tests/test_numbering_service.py` (MagicMock unit tests of
  `apply`/`resolve_rule` + real-SQLite allocation / concurrency / floor tests);
  it is **regression-only** (not allowlisted, not in the ci.yml contracts list,
  matches no portfolio glob).

## 2. Semantic target (grounded, contract-level)

`plm_auto_engcode` / `plm_auto_internalref` = compose an item's number/code from
**ordered tokens** — literal segments, **date** components, and
**classification/category** segments — typically ending in a sequence counter
(e.g. `PART-202601-MECH-0007`). G4 = widen Yuantus's pattern rendering to a
**closed token vocabulary** producing that shape, while the counter remains
allocated by the existing concurrency-safe allocator. This is string
composition, NOT a user-facing expression language (§4 of the comparison doc
records the rule DSL is deliberately thin — keep it a closed vocabulary).

## 3. Approach (ratify) — render the non-counter portion into the existing `prefix` slot

**Recommendation: extend rendering only.** A token pattern renders to
`<rendered-scope-prefix><counter>`, where:
- the **rendered-scope-prefix** (everything before the counter: literals + date
  + category segments) is computed by an extended `resolve_rule`/`generate` and
  passed into the **UNCHANGED** allocator as today's `rule.prefix`;
- the **counter** stays the **trailing** zero-padded numeric (`{seq}`), width
  from the existing `width` config.

Why this is the grounded choice:
- Per-date / per-category **counter resets are automatic**: a distinct rendered
  prefix → a distinct `NumberingSequence` row (the unique key already includes
  `prefix`) → its own counter. No allocator change.
- **No migration**: the rendered prefix reuses the existing `prefix`
  `String(120)` column; `width` is reused as-is — no schema change.
- The allocator's concurrency safety, dialect branches, and scoping are
  inherited verbatim.

**Mode switch (backward-compatible):** add an OPTIONAL `pattern` key to
`ui_layout.numbering`. If absent → today's legacy `{prefix, width, start}`
behavior, byte-for-byte unchanged (`DEFAULT_NUMBERING_RULES` and all existing
configs keep working). If present → token mode (§4). The two modes are mutually
exclusive per config.

**Alternative considered + rejected:** a counter that can appear mid-string, or a
new per-segment scope column on `NumberingSequence`. Rejected — both break the
allocator's `<prefix><digits>` shape and/or force a migration for the lowest-
impact gap. (Revisit only if a grounded need appears.)

## 4. Token vocabulary (ratify) — closed set

Render tokens in declared order. v1 closed set:
- **literal** — fixed text (e.g. `PART-`, separators).
- **date** — UTC date components from a **whitelisted strftime subset** (e.g.
  `%Y`, `%y`, `%m`, `%d`, and a small fixed list); clock is `datetime.utcnow()`
  to match the allocator's existing `now` and give defined month-boundary
  behavior. Arbitrary strftime is NOT accepted (avoids separators/locale/length
  surprises).
- **{seq}** — the trailing counter; zero-padded by the existing `width`; start
  from existing `start`. MUST be the last token (§6).

**Unknown token → `ValueError`** (closed set; no literal-brace passthrough) —
this is what keeps it a vocabulary, not the DSL the comparison §4 warns against.

### 4.x Category / property token — DECISION: v1-DEFER

"分类" (category) maps to an item **property** value. A `{prop:<key>}` token is
where the risk concentrates, so it is called out separately, not folded in:
- **add-time ordering** — does the categorizing property exist in the
  `properties` passed to `apply` at `add_op.py:98` before numbering runs?
- **missing/empty value** behavior (ValueError vs fallback);
- **sanitization** — a raw property value may contain separators or exceed
  length, corrupting the rendered prefix / the `<prefix><digits>` shape;
- **unbounded sequence-row cardinality** — one `NumberingSequence` row per
  distinct rendered prefix.

**Decision for G4 R1:** v1 ships **literal + date + {seq}** (this already
satisfies the plain reading of "日期 / 多段" and is fully deterministic).
The category/property token is **deferred** to an explicitly-scoped follow-up
that must resolve the four risks above before inclusion. It is NOT a silent
"just another token".

## 5. Config surface (ratify)

- `ItemType.ui_layout["numbering"]` gains an optional `pattern` (token spec) +
  reuses `width`/`start` for `{seq}`. No new config table.
- **No new route**: numbering config is written wherever `ui_layout` is written
  today (ItemType admin); G4 adds no endpoint. (Hence no route-count impact.)
- Backward-compat: legacy `{prefix, width, start}` configs and
  `DEFAULT_NUMBERING_RULES` are untouched and keep their exact output.

## 6. Floor-compatibility + counter scoping (LOCK — the one correctness landmine)

The legacy floor scan (`_floor_allocated_value` / `_max_allocated_value_from_db`
/ `_numeric_item_number_expr` / `_parse_allocated_value`) assumes exactly one
`<prefix><all-digits>` shape per `item_type_id`. Token mode creates **many**
rendered prefixes per item_type, so this must be locked, not assumed:

1. **Token mode performs NO historical floor scan.** A freshly-rendered prefix
   has no legacy data in its shape, so the counter simply starts at `start`.
   This sidesteps the prefix-ambiguity edge entirely (`A-` vs `AB-` LIKE
   matching) and is less code. The floor scan stays exclusively for legacy mode.
2. **Mandatory non-digit separator immediately before `{seq}`.** Defense-in-
   depth: if a legacy-mode allocation ever runs on the **same** `item_type_id`
   that also has token-era rows, its `LIKE '<prefix>%'` scan could match a token
   number — but the non-digit separator makes the post-prefix suffix fail the
   all-digits test (`_numeric_item_number_expr` / `_parse_allocated_value` return
   None), so token rows can never be miscounted as a legacy counter. The
   implementation MUST reject a pattern whose `{seq}` is not preceded by a
   non-digit character.
3. **Per-rendered-prefix counter scoping is automatic** via the existing
   `(item_type_id, tenant_id, org_id, prefix)` unique key — no new scope column.
4. **Rendered prefix length > 120 → `ValueError`** (the `prefix` column is
   `String(120)`); never truncate (truncation would collide distinct scopes).

These four points are the load-bearing correctness contract of the slice and
must appear as explicit, tested behavior — not as "floor-compat just works".

## 7. Persistence / route-count / infra (ratify) — the surface that bit G5 is EMPTY here

- **No migration** — rendered prefix reuses `prefix String(120)`; `width` reused;
  no new column/table. Migration-table-coverage contract unaffected.
- **No new route** → **no route-count pin changes** (the four pins stay at 688)
  and **no portfolio entries** (`CI_PORTFOLIO_ENTRIES` / ci.yml router lists are
  untouched).
- **No new test file** (see §9): extend `test_numbering_service.py`, which is
  regression-only — so **no conftest allowlist / ci.yml / portfolio fan-out**
  (the exact infra that failed #678 G5 on first push). Verify locally with
  `YUANTUS_PYTEST_DB=1`.

## 8. Non-Goals

No change to the allocator's concurrency / dialect branches / scoping; no
mid-string counter; no new table or migration; no new route or config endpoint;
no general expression DSL (closed token set only); no change to revision schemes
(`meta_revision_schemes` is a separate concept from item numbering); no GPL/AGPL
reuse; no admin UI.

## 9. Step-0 to enter the IMPLEMENTATION (grounding the impl must do)

1. Deep-read `numbering_service.py` `resolve_rule` (`:65`) → `generate` (`:58`)
   → `_allocate_counter*` (`:112`+), confirming the render/allocate seam where
   the rendered prefix is produced and handed to the unchanged allocator.
2. **Confirm the §6.1 no-floor-scan decision** against `_floor_allocated_value`
   (`:235`) usage: token mode must not invoke it; legacy mode keeps it.
3. **Confirm the §6.2 separator rule** against `_numeric_item_number_expr`
   (`:294`) and `_parse_allocated_value` (`:327`) — verify a non-digit before the
   numeric tail makes both return None for token numbers.
4. Confirm the single call site `operations/add_op.py:98` needs no change (the
   widening is entirely inside the service).
5. **Test wiring (per [[feedback-test-file-ci-wiring-fanout]]):** EXTEND
   `tests/test_numbering_service.py` (regression-only) rather than add a new
   file; then run the fan-out sweep
   (`grep -rlnE 'glob\("test_.*"\)|_PORTFOLIO_|disk_contracts|ALLOWLIST' ...`)
   to CONFIRM no allowlist / ci.yml / portfolio entry is required (it is not, for
   an extended regression-only file). If the impl nonetheless adds a new
   `test_*` file, it MUST run that sweep and satisfy every match.
6. Confirm `width` reuse needs no schema change (it does not).

## 10. Preconditions to enter the IMPLEMENTATION

1. §3 approach (render-into-existing-prefix, counter trailing, mode switch via
   optional `pattern`, no allocator change) ratified;
2. §4 token vocabulary (literal + date + `{seq}`) ratified; §4.x category/property
   token **v1-DEFER** ratified;
3. §5 config surface (no new route) ratified;
4. **§6 floor-compat LOCK (no token-mode floor scan; mandatory non-digit
   separator before `{seq}`; length>120 → ValueError; unknown token →
   ValueError) ratified** — this is the gating section;
5. §7 no-migration / no-route / no-pins / extend-existing-test acknowledged;
6. §8 non-goals ratified.

A **separate explicit opt-in** then authorizes the implementation.

## 11. Reviewer Focus

1. §1 — gap still real and narrow (only `numbering_service.py:63`); allocator
   untouched?
2. §3 — render-into-`prefix` + trailing counter + backward-compatible mode
   switch (not a rewrite)?
3. §6 — the floor-compat LOCK is correct and explicit (no token-mode scan +
   mandatory separator + length/unknown-token ValueError)?
4. §4.x — category/property token risks enumerated and the v1 decision
   deliberate, not silent?
5. §7 — no migration, no route, no route-count pins, no portfolio fan-out;
   extend the regression-only numbering test?
6. §8 — allocator concurrency / DSL / revision-scheme / GPL-AGPL stay OUT?

## 12. Status

Doc-only grounding + scope-lock. Ready for review once the doc exists at the
canonical path; `DELIVERY_DOC_INDEX.md` references it + its DEV/verification
record (sorted under `## Development & Verification`); doc-index / sorting /
completeness checks pass; `git diff --check` clean. Ratifying §3–§8 sets the
numbering-pattern implementation plan; **a separate explicit opt-in authorizes
the implementation.** The other OdooPLM gaps (G3 explode, minor) remain
separately-opted.
