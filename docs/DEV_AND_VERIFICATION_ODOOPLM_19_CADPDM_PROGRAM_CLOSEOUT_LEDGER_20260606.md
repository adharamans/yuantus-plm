# CAD-PDM Borrow Program — Closeout Ledger

Date: 2026-06-06; refreshed 2026-06-09 after #738/#739/#742/#744.
Status: **doc-only** stage-sealing record. Summarizes the OdooPLM-19 CAD-PDM borrow
program (#718–#735), the completed productization/read-surface follow-ups
(#738/#739/#742/#744), the live route/migration baselines, explicit non-goals, and
the remaining optional slices. No code change.

## 1. What was delivered (the borrow program)

Source: `ODOOPLM_19_CADPDM_GAP_AND_BORROW_ANALYSIS_20260604.md` (#718). Each work
package was grounded by a doc-only taskbook, then implemented, each on its own
explicit per-phase opt-in.

| WP / phase | What | Taskbook | Impl | Status |
|---|---|---|---|---|
| WP1.0 | Representation decision (Part + role-tagged Files, not a Document graph) | #718 | — | CLOSED |
| WP1.1 | CAD-PDM relationship types (ASSEMBLY / REFERENCE) seeded | — | #720 | CLOSED |
| WP1.3 | 2D/3D staleness — provenance model (`source_batch_id`) + `needs_update`, checkin alignment | #722 | #725 | CLOSED |
| WP1.2 | PDM relationship traversal (`relationship-tree` tree/flat, node budget) | #726 | #728 | CLOSED |
| WP1.2 | `stale-drawings` thin scan (reuses `needs_update`, bounded reachable-set) | #726 | #729 | CLOSED |
| B2 | Assembly release **hard gate** (`bom.children_all_released`, fail-closed on dangling edge) | — | #731 | CLOSED |
| B2 | `item_release` diagnostics surfaced in `release_readiness` (advisory, pre-promote visibility) | — | #732 | CLOSED |
| B1 | Version **Superseded** signal + concurrent-revision guard | #734 | #735 | CLOSED |

**End-to-end value now live:** an assembly with an unreleased / missing-BOM child is
**hard-blocked at promote** *and* **visible earlier in `release_readiness`**; releasing
a new version auto-**Supersedes** the prior; concurrent re-revision is blocked by an
**app guard + DB partial-unique**.

## 1b. Completed follow-ups since the original closeout

| Slice | What | Taskbook | Impl / fix | Status |
|---|---|---|---|---|
| A4-R1 | Pack-and-go `dry_run` / manifest-first plus WP1.3 drawing-staleness injection into JSON + CSV manifests | #736 | #738 | CLOSED |
| A4-R1 hardening | `dry_run` is manifest-first and does not download remote files | — | #739 | CLOSED |
| A4-R1 test hardening | temp-dir leak guard uses `monkeypatch(tempfile.mkdtemp)` instead of global glob | — | #742 | CLOSED |
| B1 read surface | `GET /versions/items/{item_id}/versions` exposes active / historical released / in-work / draft version status | #740 | #744 | CLOSED |

These are productization/read-surface follow-ups on top of the core borrow program.
They do not change the deliberate boundaries in §3.

## 2. Live baselines (as of this ledger)

- **Route count = 707** (`EXPECTED_TOTAL_ROUTES`; pins: metrics delta, phase4, breakage
  metrics, portfolio meta-contract). Core B1/B2/readiness added **no** routes; the route
  bumps in this window were WP1.2 traversal (+2 → 704), WP1.2 stale-drawings (+1 → 705),
  unrelated PLM-Collab #733 embed-token (+1 → 706), and the CAD-PDM B1 read-surface
  #744 (+1 → 707).
- **Single Alembic head = `b1_supersede_001`** (chain: `…→ p2b_appr_tmpl_001 →
  wp13_cad_stale_001 (WP1.3) → b1_supersede_001 (B1)`). WP1.1/B2/readiness added no
  migrations.
- Key surfaces: `web/pdm_relationship_router.py` (`/pdm/items/...` traversal),
  `web/cad_consistency_router.py` (`/cad/items/.../staleness`, `/stale-drawings`),
  `services/release_validation.py` (`item_release` ruleset),
  `services/item_release_service.py` (gate + diagnostics),
  `lifecycle/service.py` (promote hard gate), `version/service.py` +
  `version/models.py` (supersede hook + open-current partial-unique),
  `web/version_lifecycle_router.py` (`/versions/items/{item_id}/versions` read surface),
  and `plugins/yuantus-pack-and-go/main.py` (A4-R1 dry-run + stale manifest).

## 3. Not in scope of this program (deliberate)

- No **Document** entity / document-graph (WP1.0 D4 — Part + role-tagged Files instead).
- No new lifecycle **state on the Item** for Superseded (B1 D1 — version-level only).
- No **part-replacement** model (`bom_obsolete_service` `superseded_by` is a separate,
  disjoint concept from version supersession).
- No version-scheme change, no ECO/revision-router rework, no Item-axis `is_current`/
  config-generation change (B1 D6/D7).
- No bounded-occurrence memoized flat traversal yet (WP1.2 D3 tracked follow-up; tree +
  flat share the node-budget guard today).
- No pack-and-go `exclude_stale_drawings=true` policy yet (A4-R2 deferred).
- No automatic assembly-level cascade promote (`promote_assembly`) yet (B2b deferred).
- No workstation / client workspace checkout context yet (A3 deferred).

## 4. Separate lines interleaved in the same window (NOT CAD-PDM)

So the timeline is not misread later:
- **PLM Collaboration (cross-repo, provider+consumer):** #717 (P2.5 capability manifest),
  #723/#724 (P3-A BOM projection), #727 (P3-B SKU/manifest), #730 (P3-D0), #733 (P3-D1
  embed-token), #737 (P3-D closeout / acceptance runbook). Tracked separately (see the
  PLM-Collab memory); do not fold into CAD-PDM.
- **CAD material assistant:** #719 taskbook + #721 bind/write-back (and earlier
  #711/#713/#715) — CAD-adjacent but a distinct sub-line.

## 5. Next-candidate slices (each needs its own opt-in)

1. **A4-R2 pack-and-go policy**: `exclude_stale_drawings=true` opt-in, preserving
   manifest accounting and warning semantics. If traversal-engine consolidation is
   included, scope-lock it explicitly; otherwise keep R2 as the policy-only delta.
2. **WP1.2 bounded memoized flat**: performance / scale hardening before large assembly
   pack-and-go usage. Preserve path/occurrence semantics while avoiding repeated expansion.
3. **B2b `promote_assembly`**: cascade promote from a root assembly, with dry-run,
   bottom-up ordering, partial failure reporting, permissions, cycle guards, and reuse of
   the existing B2 hard gate. Needs taskbook-first.
4. **A3 workstation checkout** (heavier, deferred): touches CAD desktop, lock state, real
   file streams / local paths, security boundary — likely an external-environment /
   native-signoff gate like the CAD-helper line. Sequence after A4 unless CAD-workstation
   is explicitly prioritized.
5. **Small read-surface extensions** (only if a consumer asks): `?status=` filter on
   `/versions/items/{item_id}/versions`, or per-item permission tightening. Both are
   deliberately outside #744.

## 6. Verification posture

Implementation slices shipped green through the CI contracts + regression lists with
dual-registered tests where applicable (ci.yml + conftest no-DB allowlist); doc-only
taskbooks (WP1.0 #718, WP1.3 grounding #722, WP1.2 #726, B1 #734, A4-R1 #736,
Superseded read-surface #740) shipped through the doc-index / reference / sorting
contracts. All went through adversarial advisor review and owner review rounds.
Anti-drift authorities: the 4 route-count pins, `test_migration_table_coverage_contracts`,
`test_delivery_doc_index_references`, and the per-feature contract tests listed in §2.
