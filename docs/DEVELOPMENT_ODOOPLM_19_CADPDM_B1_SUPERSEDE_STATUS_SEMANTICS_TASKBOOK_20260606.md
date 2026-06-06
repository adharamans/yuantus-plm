# B1 Grounding Taskbook — Supersede / status semantics (version replacement signal)

Date: 2026-06-06
Status: **doc-only grounding**. Locks semantics before any code. No implementation,
no migration, no route. Successor to the CAD-PDM borrow program (WP1.0–1.3, B2
hard gate #731, B2 readiness aggregation #732).

## 1. What B1 is (borrow source)

From `ODOOPLM_19_CADPDM_GAP_AND_BORROW_ANALYSIS_20260604.md` §B1 (lines 176–191).
OdooPLM (`plm/models/plm_mixin.py`):
- `action_release()` → `_mark_obsolete_previous()`: releasing vN+1 sets the **prior**
  version to `obsoleted`.
- `new_version()` → `_mark_under_modifie_previous()`: opening a revision marks the
  **source** version `undermodify` ("being revised", UI-visible).
- Unique `(engineering_code, engineering_revision)` + `is_releaseble()` guard: only a
  released version may spawn a revision; same code+rev is unique.

**Borrow scope (explicit, line 191):** Yuantus's `is_current` + ECO model is *cleaner*
and is NOT being overturned. B1 borrows only the **externally-visible status signal**
and the **concurrent-revision guard** — additive, not a model rewrite.

## 2. As-is (grounded gap)

- `ItemVersion` (`version/models.py:40–123`) owns: `state` (default "Draft",
  `:68`), `is_current` (`:71`, indexed), `is_released` (`:72`, indexed),
  `predecessor_id` (`:83`), `generation`/`revision`/`version_label`, plus
  **branching** (`branch_name`/`branched_from_id`, `:86–87`). **No `is_superseded`
  or under-modification field.**
- `revise()` / `new_generation()` (`version/service.py:355–464`): create a new
  `Draft` `ItemVersion` (`is_current=True`), set the source `is_current=False`, and
  repoint `item.current_version_id`. **The source version keeps `state="Released"`
  and `is_released=True`** — no replacement signal.
- `release()` (`:466–506`): marks the *current* version released; **never touches
  the predecessor**. So after releasing vN+1, vN stays `is_released=True,
  state="Released"`, distinguishable only by `is_current=False`.
- Existing revise guard `_assert_source_version_checkout_available` checks **only the
  checkout lock**, not `is_released` and not "is a revision already open" — so two
  revisions can be opened off the same line with no guard.
- Separate axis: Item-level generations (`meta_items.config_id`+`generation`+
  `is_current`) drive WP1.2 relationship traversal / B2. **`ItemVersion` ≠ Item
  generation.** B1 must stay on the `ItemVersion` axis and not perturb the Item axis.
- The revise router (`web/version_lifecycle_router.py:216–226`) commits the new
  `Draft` version but **does not reset `item.state` or call `LifecycleService`** — so
  after revising a `Released` item, `item.state` stays `Released` while
  `item.current_version.state` is `Draft`: **the Item and version axes diverge
  today.** Consequence: "being revised" is NOT encoded in `item.state` → it must be
  derived (D3), and Superseded living on the version (D1) leaves this pre-existing
  divergence untouched (D6).

**Gap:** "active released" vs "historical/replaced released" is only an `is_current`
flag, not a status; there is no "being revised" signal and no concurrent-revision
guard.

## 3. Decisions to lock (ratify before any code)

- **D1 — Locus: version-level, not Item lifecycle.** "Superseded" is a value of
  `ItemVersion.state`, NOT a new `LifecycleState` on the Item's lifecycle map.
  Rationale: when vN+1 releases, the *Item* is still released (the new version is
  current+released); only the *prior version* is superseded. → **No
  `LifecycleService` / `LifecycleState` / transition / seeder change.** (This
  reconciles the borrow plan's loose "新增 Superseded 转移": it is a version-state
  set, not a lifecycle-map transition.)
- **D2 — Supersede trigger (choke-point confirmed).** `VersionService.release()`
  (`version/service.py:496`) is the **sole runtime** setter of
  `ItemVersion.is_released`, called only from `LifecycleService.promote`→Released
  (`lifecycle/service.py:297`). ECO uses its own `ECOStage` system and does **not**
  release versions directly (`eco_service.py:650`); the only other `is_released=True`
  sites are CLI bootstrap/seed (`cli.py:711/789`) and the deprecated, test-only
  `change_service` shim — both non-runtime. So a supersede hook in `release()` covers
  promote- AND ECO-originated releases (no bypass). After marking the current version
  released, follow `predecessor_id` and mark the immediate prior version `Superseded`
  **iff it is `is_released` and not already `is_superseded`** — the not-superseded
  clause stops the seeder's Suspend→Release re-cycle (Released→Suspended→Released)
  from re-superseding an already-superseded version. Only the immediate predecessor
  is touched per release (older ancestors were superseded inductively).
- **D3 — Under-Modification: derived, not stored.** Expose "this line is being
  revised" as a **read-time predicate** (e.g. the line's current `ItemVersion` is
  `is_released=False` while a predecessor was released), not a stored state. Matches
  the borrow note's "或由'存在更高代 Draft'派生"; avoids a write + migration.
- **D4 — Concurrent-revision guard: precondition.** `revise()` / `new_generation()`
  gain an entry guard (the `is_releaseble` idea): refuse to open a revision unless the
  source is released / no open (unreleased) successor exists. Fail-closed:
  `VersionError` → 400 at the router. ADDS a precondition the current code lacks
  (today only the checkout lock is checked, `_assert_source_version_checkout_available`).
- **D4b — Guard ENFORCEMENT (LOCKED): app guard + DB partial-unique.** An app-level
  precondition alone is **TOCTOU-racy** (two concurrent `revise()` both read vN
  released, both spawn vN+1). Locked enforcement = **both**: the D4 app-guard for a
  friendly early 400, AND a **DB partial-unique index** that makes the race
  impossible. **Chosen constraint (NULL-safe form):**
  `UNIQUE (item_id, COALESCE(branch_name,'main')) WHERE is_current IS TRUE AND
  is_released IS NOT TRUE` — "at most one open (unreleased) current version per item
  line + branch." Chosen over the `predecessor_id` dimension ("one open successor per
  source version") because `branch_name` in the key cleanly scopes branches (D5) and
  composes with `merge_branch` (which must inherit `branch_name` — see D5), whereas a
  `predecessor_id`-unique would bind every `merge_branch`-created version.
  **NULL safety (load-bearing):** `branch_name`/`is_current`/`is_released` carry only
  a Python-side `default=`, NOT `nullable=False`/server defaults
  (`version/models.py:71,72,86`). Raw NULLs are therefore possible, and a NULL
  `branch_name` defeats uniqueness while a NULL boolean falls out of a bare
  `WHERE is_current AND NOT is_released` predicate. So the migration MUST first
  **normalize existing NULLs** (`branch_name←'main'`, booleans←concrete) and the index
  MUST use the `COALESCE` + `IS TRUE`/`IS NOT TRUE` form above; add
  `server_default`/`NOT NULL` to these columns in the same migration.
  **This also settles Q-B:** a line may hold only one open draft, so `revise()` /
  `new_generation()` are valid only from a **closed (released) current version** —
  "iterating a draft" is check-out/check-in, not a new revision.
  **Test enforcement:** impl tests must prove the **DB constraint itself** rejects a
  second open current draft — via a real migration run OR an equivalent ORM `Index`
  declaration so `create_all` builds it (SQLite *does* support partial unique
  indexes; the real risk is the test path not creating the index) — not by the
  app-guard alone.
- **D5 — Branch scope + `merge_branch` boundary.** Supersede (D2) and the guard (D4)
  target the **mainline** (`branch_name="main"`); branches are an explicit parallel
  mechanism and are **out of scope**. **`merge_branch()` (`version/service.py:268`) is
  a third version-creation path** — it mints a `Draft` `ItemVersion`
  (`is_current=target_ver.is_current`, `is_released=False`) and can repoint
  `item.current_version_id`, but D4 only names `revise()`/`new_generation()`.
  **Critical gap: `merge_branch()` does NOT set `branch_name` on the new version
  (`version/service.py:321`), so it defaults to `"main"`** (`version/models.py:86`) —
  a branch-target merge would therefore land as a *mainline* open draft and collide
  with / pollute the D4b index. **B1 impl MUST make the merged version inherit
  `target_ver.branch_name`**, with a test that a branch-target merge stays on its
  branch and is NOT caught by the mainline open-draft index. B1 otherwise does not
  special-case `merge_branch`: once `branch_name` is inherited, the uniform
  `(item_id, COALESCE(branch_name,'main'))` invariant scopes branch-local merges
  independently, and a merge creating a *second* open mainline draft is correctly
  refused (resolve the open draft first).
- **D6 — Dual-model boundary.** B1 operates strictly on `ItemVersion`. It does NOT
  change `Item.state`, `Item.is_current`, or the config-generation/relationship
  `is_current` semantics that B2 / WP1.2 traversal depend on.
- **D7 — Additive only.** `is_current` stays the active-version flag; ECO stays the
  higher-level change mechanism; the B2 release gate (children-released, reads Item
  state) is unaffected. No behavior change to any of them.
- **D8 — Migration / backfill (RATIFIED: forward-only).** Add an indexed boolean
  `ItemVersion.is_superseded` (mirrors `is_released`/`is_current`) so "active
  released" = `is_released and not is_superseded` is an indexed query; the
  `Superseded` string state needs no schema. **Migration base: chain off the CURRENT
  Alembic head — today `wp13_cad_stale_001` (verified head). Do NOT pin
  `p2b_appr_tmpl_001`: WP1.3 already consumed it (`wp13_cad_stale_001.down_revision =
  p2b_appr_tmpl_001`), so a second migration off `p2b` would create a parallel
  head/fork. Re-verify the head at impl time** (main moves fast). The same migration
  adds the D4b partial-unique index. Column+index add → no migration-table
  `create_table`, no contract bump. Backfill: **forward-only** (do not retro-supersede
  existing released-not-current versions).
- **D9 — No new route.** The signal lives in existing version reads (`get_history`,
  version queries). Route-count stays at **705**; no pin bump. Impl touches
  `version/models.py` + `version/service.py` (+ migration) + tests only.
- **D10 — Non-goals.** No UI/frontend; no version-scheme change; no ECO/revision-
  router rework; **no part-replacement** (the `bom_obsolete_service`
  `superseded_by`/`replacement_id` is a *different* concept — one part replaced by
  another, not version supersession — explicitly disjoint); no Item lifecycle state.

## 4. Ratified (this review round)

- **Q-A (D2): RATIFIED — keep `is_released=True`** on a superseded version (it *was*
  released; `is_superseded`/`is_current` distinguish active vs historical).
- **Q-B (D4): RESOLVED by D4b** — the "one open draft per line+branch" invariant means
  `revise()`/`new_generation()` are valid only from a closed (released) current
  version; in-place draft iteration is check-out/check-in, not a new revision.
- **Q-C (D5): RATIFIED — branches out**, with the **`merge_branch()` boundary made
  explicit** (D5) + a required impl test that it is not falsely caught.
- **Q-D (D8): RATIFIED — forward-only**, no backfill.
- **Q-E (D1): RATIFIED — `is_superseded` boolean + `state="Superseded"`** (boolean for
  indexed queries, string for display/history).
- **Q-F (D6): RATIFIED — leave the Item/version axis divergence as-is** (B1 =
  version-axis only; surfacing it is enough).
- **D4b enforcement: LOCKED** — app-guard + `UNIQUE (item_id,
  COALESCE(branch_name,'main')) WHERE is_current IS TRUE AND is_released IS NOT TRUE`
  (NULL-safe; see D4b).

## 5. Verification plan (for the eventual impl PR, not this doc)

- New tests (dual-registered: `ci.yml` contracts list + `conftest.py` no-DB
  allowlist; hermetic if they touch release rulesets): (1) release vN+1 → vN
  `Superseded` + immediate-predecessor scope + the `is_released and not is_superseded`
  predicate (Suspend→Release re-cycle does NOT re-supersede); (2) guard refuses a
  second open revision — **both** the app-guard (400) **and** the DB partial-unique
  rejecting a 2nd open current draft (asserted against a real migration / ORM `Index`,
  not via the app-guard); (3) under-modification predicate; (4) **`merge_branch()`
  inherits `target_ver.branch_name`; a branch-target merge stays on its branch and is
  NOT caught by the mainline open-draft index** (D5); (5) B2 gate + WP1.2 traversal
  still green (no Item-axis perturbation).
- Alembic migration adding `is_superseded` + the D4b partial-unique index (driven by
  `YUANTUS_DATABASE_URL`; **chain off the current head — today `wp13_cad_stale_001` —
  re-verify at impl time**, NOT `p2b_appr_tmpl_001`).
- **Static guard:** confirm no runtime import/use of the deprecated `ChangeService`
  release path (`change_service.py:132` still sets `is_released=True` directly but is
  test-only/deprecated per its header + the `LegacyEcmCompatService` shim) — keep it
  out of the runtime release path so the D2 hook in `release()` stays the sole runtime
  release.
- Route-count **705 unchanged** (no route) → no 4-pin bump.
- DEV/V doc + index; full CI contracts list green.

## 6. Sequencing

B1 is the last item of the borrow Phase 2 (governance close-out; B2 done). Suggest
this taskbook lands doc-only first (ratify §3 + §4), then a single impl PR scoped to
§3/§5 — mirroring the WP1.x and B2 cadence.
