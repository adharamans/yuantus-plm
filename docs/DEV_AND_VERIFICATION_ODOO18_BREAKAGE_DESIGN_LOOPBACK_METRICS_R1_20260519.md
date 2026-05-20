# Odoo18 Breakage Design-Loopback Metrics R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement Tier-B #3 §3.7 per the ratified taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_20260519.md`,
merged 2026-05-20 as `c2e404f` / PR #611). Expose three
read-only design-loopback link gauges on the existing
Prometheus scrape surface, sourced from a SQL aggregate over
the persisted `BreakageIncident.eco_id` (the §3.2 durable
substrate) — never an in-memory counter. **Final §3 catalog
slice.**

## 2. Scope

### Modified

- `src/yuantus/meta_engine/services/parallel_tasks_service.py`
  — add `from sqlalchemy import func as sa_func`; add
  `ParallelOpsOverviewService._breakage_design_loopback_metrics`
  (SQL `func.count` + `GROUP BY`, no `since`, no load-all);
  `prometheus_metrics(...)` calls it and appends three new
  gauges + `# HELP`/`# TYPE` lines. **`summary()` is NOT
  touched** (§3.A (b) / Medium 1).
- `src/yuantus/meta_engine/tests/test_parallel_tasks_services.py`
  — additive harness extension: `ECO`/`ECOStage` added to the
  `create_all(tables=…)` list because `prometheus_metrics` now
  reads `eco_id IN (SELECT id FROM meta_ecos)`. No assertion /
  behavior change (see §4).
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py`
  (9 MANDATORY tests).
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_R1_20260519.md`

### Unchanged by design

`summary()` (and therefore `GET /parallel-ops/summary` /
`/parallel-ops/summary/export` JSON responses — byte-identical;
Medium 1 closed), §3.2's CAS / `session.rollback()`, every
merged contract, every route (no new route — `len(app.routes)`
stays 677), no schema/alembic, no event subscriber, no runtime
mutation, the three protected test files
(`test_breakage_update_status_auto_trigger.py`,
`test_breakage_helpdesk_sync_auto_trigger.py`,
`test_breakage_design_loopback_event_emission.py` — all stay
green with zero-line diff), `.claude/`, `local-dev-env/`.

## 3. Implementation

### 3.1 §3.A SQL aggregate — `_breakage_design_loopback_metrics`

New helper on `ParallelOpsOverviewService`. Three round-trips
(`func.count` + `GROUP BY`), live-link 口径
`BreakageIncident.eco_id.in_(sa_select(ECO.id))` — SQL NULL
semantics make this also exclude `eco_id IS NULL`, and the
IN-subquery shape matches §3.2's CAS predicate so a dangling
`eco_id` (the linked ECO was hard-deleted; no FK cascade) is
correctly excluded. Returns
`{"links_total": int, "by_status": {str: int}, "by_severity":
{str: int}}`. No load-all
(`query(BreakageIncident).all()` is forbidden by an AST guard
in the §5 test). No `since` parameter — current-state, not
windowed (§3.E).

### 3.2 §3.A (b) surface containment (Medium 1)

The helper is called **only from `prometheus_metrics(...)`**,
right before the existing `return "\n".join(lines) + "\n"`.
`summary()` is **not** touched, so the
`/parallel-ops/summary[/export]` JSON responses stay
byte-identical (no `breakage_design_loopback` key). The
Prometheus scrape text is the single, exhaustive surface
change. Pinned by `test_summary_json_surface_unchanged` —
targeted "no `breakage_design_loopback` key" plus a robustness
check that the `summary()` keyset is identical with and
without a linked incident (so a future drift that conditionally
injects the key under any name would still fail).

### 3.3 §3.D three gauges + no `common_labels` (Medium 2)

The three lines emitted from `prometheus_metrics`:

- `yuantus_parallel_breakage_design_loopback_links_total` —
  gauge, **no labels** (`_prometheus_line(name, value)` with
  `labels=None` renders bare `name value`, no `{}` block);
- `yuantus_parallel_breakage_design_loopback_links_by_status`
  — gauge, **only `{status}`** label (`labels={"status": s}`);
- `yuantus_parallel_breakage_design_loopback_links_by_severity`
  — gauge, **only `{severity}`** label.

The existing renderer's `common_labels`
(`{window_days, site_id, target_object, template_key}`,
`parallel_tasks_service.py:11498`) are NOT applied — emitting
a current-state value under window/filter labels would be a
lie. Pinned by `test_loopback_gauges_have_no_common_labels`
with line-shape regexes per the advisor's spec.

### 3.4 Pre-existing test harness extension (disclosed)

`test_parallel_tasks_services.py::test_parallel_ops_overview_summary_and_window_validation`
exercises `prometheus_metrics()` against a SQLite harness whose
`create_all(tables=…)` list **predates §3.7** and lacked
`meta_ecos` / `meta_eco_stages`. With §3.7 the renderer now
reads `eco_id IN (SELECT id FROM meta_ecos)` → that table must
exist or SQLite errors `no such table: meta_ecos`. The minimal
fix is to add the two ECO tables to the existing `create_all`
list. **No assertion / behavior change**; same class as the
§3.4 `test_parallel_tasks_router.py` update and the §3.6
`_unrecoverable_creation` `**_kw` widening — a pre-existing
test legitimately updated for a taskbook-ratified additive
behavior change. The three §3.3/§3.4/§3.6 protected test files
are **untouched** (zero-line diffs).

### 3.5 §3.E current-state, not windowed

`_breakage_design_loopback_metrics` takes no `since` /
`window_days` argument; pinned by
`test_metrics_are_current_state_not_window_filtered` — an
incident with `created_at` 400 days ago is still counted even
when `prometheus_metrics(window_days=1)`.

## 4. Test Matrix

`test_breakage_design_loopback_metrics.py` — 9 MANDATORY tests
(harness: StaticPool shared in-memory + the proven
`test_parallel_ops_router_e2e.py` curated table list **plus**
`ECO`/`ECOStage`):

- **`test_links_total_counts_only_live_linked_incidents`**
  (MANDATORY).
- **`test_dangling_eco_id_is_not_counted_as_linked`** (MANDATORY)
  — §3.B: hard-delete the linked ECO, assert `*_links_total`
  decrements and the row leaves `by_status`/`by_severity`.
- **`test_by_status_and_by_severity_group_live_links`**
  (MANDATORY) — mixed status/severity, unlinked + dangling
  excluded.
- **`test_empty_data_emits_zero_not_error`** (MANDATORY) — no
  incidents: zeros, valid Prometheus text, `links_total` line
  has no `{}` block.
- **`test_metrics_are_current_state_not_window_filtered`**
  (MANDATORY) — §3.E.
- **`test_no_load_all_uses_sql_aggregate`** (MANDATORY) — AST
  guard per the advisor's spec: forbid any
  `Call(func=Attribute(attr="query"), args[0]=Name("BreakageIncident"))`
  in the helper body; positive sanity that `sa_func.count` +
  `group_by` are present.
- **`test_prometheus_surface_exposes_three_gauges_no_new_route`**
  (MANDATORY) — three `# TYPE … gauge` lines present;
  `len(app.routes) == 677` via `create_app()`.
- **`test_summary_json_surface_unchanged`** (MANDATORY,
  Medium 1) — no `breakage_design_loopback` key, plus keyset
  equality with/without a linked incident.
- **`test_loopback_gauges_have_no_common_labels`** (MANDATORY,
  Medium 2) — line-shape regexes per metric; none of
  `window_days=` / `site_id=` / `target_object=` /
  `template_key=` on any of the three metric lines.

Regression unchanged & green: the breakage/helpdesk/event slice
files (10/10 each, zero-line diff); ops-router contracts;
phase-4 route count pin (677); doc-index trio; R2 portfolio.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_event_emission.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_breakage_update_status_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_breakage_helpdesk_sync_auto_trigger.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_ops_router_contracts.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/parallel_tasks_service.py
git diff --check
```

Observed 2026-05-20: §3.7 9/9; full combined §6 suite **74
passed**; broader breakage/parallel/phase4/event/eco/ops
**201 passed**; protected §3.3/§3.4/§3.6 test files zero-line
diff; `py_compile` clean; `git diff --check` clean;
`len(app.routes)` unchanged at 677 (no route/request-model
change).

## 6. Non-Goals (reaffirmed from taskbook §8)

- No in-memory `event_bus` counter / subscriber / listener for
  metrics.
- No `created_vs_reused` metric.
- No new route; no request/response model change; no schema /
  alembic / tenant-baseline; no migration.
- No edit to §3.2's CAS / `session.rollback()`, any merged
  contract, or §3.6's emission path.
- No runtime mutation — §3.7 is read-only at scrape time.
- No new label dimensions beyond `{status, severity}`; no SLO
  thresholds; no windowed link-rate.
- No `created_at`-windowing of the link gauges.
- `summary()` is not touched (§3.A (b) / Medium 1).
- `.claude/` and `local-dev-env/` stay out of git.

## 7. Inter-slice status

- §3.1 / §3.2 / §3.3 / §3.4 / §3.6: merged; unchanged by §3.7
  (the protected test files have zero-line diffs).
- §3.7 metrics: delivered (this slice). **Tier-B #3 §3 catalog
  is now complete** (§3.1 → §3.7 all merged after this).
- Future follow-ups (each its own opt-in, none in scope):
  windowed link-rate / created-vs-reused via an event-stream
  consumer / durable-delivery outbox / default-ON flips /
  additional label dimensions.
