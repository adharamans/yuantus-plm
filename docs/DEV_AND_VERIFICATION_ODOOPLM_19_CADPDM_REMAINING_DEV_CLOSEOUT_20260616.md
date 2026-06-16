# DEV & Verification: CAD-PDM remaining-development closeout

Date: 2026-06-16

Consolidated closeout for the final CAD-PDM remaining-development pass: map the
remaining work, adversarially re-verify prior taskbook-review findings against the
now-merged code, implement the safe code-completable remainder, and verify completeness.
Companion to the per-slice DEV/V docs (each indexed separately) and the program closeout
ledger. `main` baseline: route count **709**, single Alembic head `a3_checkout_context_001`.

## 0. Method

Parallel workflows mapped remaining work across three lines (CAD-PDM borrow, render
S-line, visual-diff L-line) + an open-TODO sweep, adversarially re-verified the two
findings raised at the #746/#747 taskbook reviews, and audited the WP3.4 C-group. A final
4-agent audit verified completeness/coherence on `main`. Every finding was re-verified by
hand before action.

## 1. Prior-finding verification (against merged code)

| Finding (taskbook review) | Merged impl | Verdict |
|---|---|---|
| **#747 D2** — B2b bottom-up order must be reverse-topological, not `min_depth` (else diamonds fail the hard gate) | #749 `AssemblyPromotionService._topological_child_first` uses **Kahn's algorithm** + a shortcut-diamond test | **Correctly handled** — no bug |
| **#746 D4/D6** — bounded-flat must not be exponential path-enumeration | #748 `_build_flat_projection` enumerated every path **and** dropped the budget guard → unbounded (OOM/DoS) | **Real HIGH bug → fixed (#757)** |

## 2. Shipped this pass — all merged

| PR | Slice | Squash | Verification |
|---|---|---|---|
| **#757** | Bounded-flat regression fix — memoized topological DP replaces exponential enumeration; restores boundedness | `fb42fb16` | 21 prior tests preserved + 2²⁵-path guard; blast-radius 50; route 709; CI green |
| **#758** | WP3.2 (B3) `item_number` immutable once assigned (admin/superuser override + audit log) | `5e75e982` | 7 tests; full contracts 1392; route 709; CI green |
| **#759** | WP3.4 C2 opt-in `released_only` search filter (non-breaking; `GetOperation` untouched) | `b495b6bd` | 3 tests; full contracts 1400; route 709; CI green |
| **#760** | Register 3 orphaned `latest_released_guard*` tests in CI (silent-no-op gap from #294) | `975116b3` | 20 tests now gated; CI green |
| **#761** | C2 review-fix: ES-failure fail-open + require `version.is_current` | `2f3ba56a` | +2 regression tests; full contracts 1425; route 709; CI green |

Details on the two bugfixes:
- **#757 (HIGH):** `_build_flat_projection` stored a full path tuple per BFS entry → a
  25-deep stacked diamond is 2²⁵ ≈ 33.5M paths, and #748 had removed the
  `max_nodes`/`TraversalBudgetError` guard. Rewritten as three linear passes (shortest-
  first BFS for metadata → DFS back-edge drop → depth-stratified topological DP), O(V·
  max_depth + E); flat now *returns* where the duplicate tree raises — the #746 goal.
- **#761 (from owner review of #759):** P1 — the ES-failure `except` fallback dropped
  `released_only` (an earlier `replace_all` matched only the shallower-indented call), so
  an ES outage failed **open** and leaked drafts; P2 — the filter checked only
  `version.is_released`, not `version.is_current`, mismatching `LatestReleasedGuardService`.

## 3. WP3.4 C-group — audited & dispositioned

| Item | Disposition |
|---|---|
| **C1** BOM compare→apply roundtrip | **by-design (verified) — no change.** BOM is product-scoped + date-effective (`get_bom_for_version` → `get_bom_structure(item_id)` + effectivity date; no version-scoped rows), so ECO BOM edits are already live; `ECOBOMChange`/`compute_bom_changes` are preview/audit (only `eco_change_analysis_router`), `merge_bom` is a tests-only utility. `action_apply` correctly leaves the live BOM alone — wiring `merge_bom`/`ECOBOMChange` into apply (the audit's suggested fix) would **double-apply and corrupt** the BOM. |
| **C2** latest-released on search | **shipped** (#759) + **review-fixed** (#761). |
| **C3** date-BOM auto-obsolete + upward propagation | **deferred per owner** — a genuine new feature (date-triggered auto-obsolete + scheduler + where-used propagation), P2, taskbook-first when prioritized. |
| **C4** category auto-coding | **already done** — `numbering_service` `{prop:<key>}` token + value→code map wired into `add_op` step-7, 15 tests. |

## 4. Closeout verification (final 4-agent adversarial audit on `main`)

- **Consistency — clean:** `create_app()` = 709 routes matching all pins; version-router
  `EXPECTED_OWNERS` resolves (34 `/versions` routes / 5 split routers); single Alembic
  head `a3_checkout_context_001`; doc-index references + sorting contracts pass.
- **Remaining-work hunt — clean:** adversarially searched every WP/gap for a missed
  code-completable item — **none found**.
- **Session-PR coherence — clean:** the fixes are correctly on `main`, no cross-PR
  conflict, the C2 opt-in respects `GetOperation` + backward-compat. (Two audit
  "blockers" were false positives — agents ran on `main` and flagged the then-unmerged
  green PRs.)
- **One genuine pre-existing finding** (orphaned `latest_released_guard*` tests from
  #294) — **closed by #760**.

## 5. Not done (with rationale — environment/decision-gated)

- **C3** date-BOM auto-obsolete — deferred per owner (taskbook-first when prioritized).
- **REM-1** `?status=` read-surface filter — speculative ("only if a consumer asks").
- **render-service S3** (docker-compose e2e) / **visual-diff L2** (DWG, version-store) —
  implementation needs the external VemCAD render image.
- **SolidWorks COM** (Windows/SDK), **Phase 3.4 / Phase 5** (external operator evidence),
  **A1 document-graph** (deprioritized by WP1.0), **#756** (a deploy/backport action).

## 6. Verdict

**The CAD-PDM borrow program is complete and `main` is coherent** (route 709, single
Alembic head, doc-index clean, no missed buildable item). The five PRs above are all
merged. Remaining items are environment- or decision-gated, not code-completable here.
