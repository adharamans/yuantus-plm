# Claude Taskbook: Odoo18 Breakage Design-Loopback Metrics

Date: 2026-05-19

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

Tier-B #3 §3.7 (per the remainder catalog ratified at PR #601
`7fce255`) — the final §3 catalog slice. Expose **read-only**
design-loopback link metrics on the **existing** Prometheus
surface, sourced from a **SQL aggregate over the persisted
`BreakageIncident.eco_id`** (the §3.2 durable substrate) — never
from an in-memory counter. No new route, no schema, no event
subscriber, no runtime mutation.

Prerequisites (merged): §3.1 route (`a02dbd0`), §3.2 durable
idempotency (`2609bba`), §3.3 (`757c411`), §3.4 (`fb3f099`),
§3.6 event emission (`b848d66`). §3.7 reads the substrate §3.2
created; it is independent of §3.6's event (deliberately — see
§3.A/§3.C).

## 2. Current Reality (grounded — direct file reads)

All citations verified by direct read (per
[[feedback_verify_grounding_facts]]). Line numbers vs `main` @
`b848d66`.

- **Substrate:** `BreakageIncident`
  (`models/parallel_tasks.py:168`) — `status` (String(30), NOT
  NULL, default `"open"`, indexed, :184), `severity`
  (String(30), NOT NULL, default `"medium"`, indexed, :185),
  **`eco_id`** (String, **nullable**, unique, indexed, :194 —
  bare soft-link, NO FK). No persisted column distinguishes a
  CAS-created link from a dedupe-reused link.
- **Prometheus surface:**
  `ParallelOpsOverviewService.prometheus_metrics`
  (`parallel_tasks_service.py:11430`) returns a Prometheus
  **text** `str` built from `self.summary(...)` via
  `# HELP`/`# TYPE`/`self._prometheus_line(name, value,
  labels=…)`; already carries `yuantus_parallel_*` breakage
  helpdesk metrics. Exposed by the **existing** route
  `GET /parallel-ops/metrics`
  (`web/parallel_tasks_ops_router.py:1248` →
  `service.prometheus_metrics(...)`).
- **Breakage aggregate today:**
  `ParallelOpsOverviewService._breakage_summary(*, since)`
  (`:7807`) loads **all** in-window incidents
  (`created_at >= since`) and Python-`Counter`s `by_status` /
  `by_severity`. It is **load-all + windowed** and does **not**
  touch `eco_id`.
- **§3.2 dangling contract:** §3.2's CAS predicate
  (`eco_id IS NULL OR eco_id NOT IN (SELECT id FROM meta_ecos)`)
  and its MANDATORY `test_dangling_eco_id_degrades_to_no_link`
  ratified that a `eco_id` pointing at a hard-deleted ECO is
  **degraded-to-no-link** (no FK → no SET NULL cascade). This
  governs the §3.B 口径.
- **Route-count pin:** `len(app.routes) == 677`
  (`test_phase4_search_closeout_contracts.py:153`) — §3.7 adds
  no route.

## 3. Design decisions

### 3.A Source of truth — RATIFIED: SQL aggregate, new dedicated helper

The metrics are computed by a **SQL aggregate over
`BreakageIncident`** (`SQLAlchemy func.count` + `GROUP BY`),
read at scrape time. **NOT** an in-memory counter incremented
by an `event_bus` subscriber (that loses on restart / under
multi-process and is exactly the trap to avoid; §3.6's event is
fire-and-forget and explicitly NOT a metrics source).

A **new** helper `_breakage_design_loopback_metrics(self) ->
Dict[str, Any]` (NO `since` parameter) is added and called from
`summary(...)` **alongside** `_breakage_summary(...)`. It is
deliberately **not** an extension of `_breakage_summary`:
- `_breakage_summary` is load-all + Python `Counter` +
  `created_at`-windowed; the loopback link metrics are a
  **point-in-time, full-table** fact (see §3.E) computed by a
  GROUP BY — the divergence is intentional, not an oversight.
- No load-all: the helper issues `func.count` queries, never
  `query(BreakageIncident).all()`.

`summary()` gains an additive `summary["breakage_design_loopback"]
= {...}` block (no contract module exists for the summary dict;
additive extension is allowed, same framing as §3.4's
helper-extraction allowance).

### 3.B Dangling `eco_id` 口径 — RATIFIED: (b) live-link only

A "linked" incident counts **iff `eco_id IS NOT NULL AND eco_id
IN (SELECT id FROM meta_ecos)`** — the ECO must actually exist.

- **(b) live-link (RATIFIED):** consistent with the merged §3.2
  behavioral contract — a dangling `eco_id` (ECO hard-deleted;
  no FK cascade) is degraded-to-no-link, so it must **not** be
  counted as linked. Same subquery shape §3.2's CAS already
  uses (works on SQLite + Postgres — the only supported
  engines; not re-litigated).
- **(a) raw-non-null (REJECTED):** `eco_id IS NOT NULL` alone
  over-reports and **contradicts** merged §3.2 (`test_dangling_eco_id_degrades_to_no_link`).

MANDATORY test: hard-delete a linked incident's ECO and assert
`*_links_total` **decrements** (the dangling row is not
counted) and the incident drops out of `by_status` /
`by_severity`.

### 3.C `created_vs_reused` — RATIFIED: explicitly NOT done

There is **no persisted signal** on `BreakageIncident`
distinguishing a CAS-created link from a dedupe-reused link;
§3.6's `BreakageDesignLoopbackEcoEvent.created` is **transient**
(fire-and-forget, no store). A reliable `created_vs_reused`
metric would require either a new schema column (**out of
§3.7's no-schema scope**) or counting from the in-memory
`event_bus` (**the restart / multi-process loss trap — banned
by §3.A**). Therefore **`created_vs_reused` is explicitly out
of scope.** The create-vs-reuse distinction is an
**event-stream** concern: a future, separately-opted-in
consumer that materializes `BreakageDesignLoopbackEcoEvent` can
derive it — it is NOT a metric-from-state.

### 3.D Metrics + names/types — PRE-RATIFIED (exactly three)

Exactly three gauges, `yuantus_parallel_*`-prefixed, current
state:

- `yuantus_parallel_breakage_design_loopback_links_total`
  (gauge) — count of incidents with a live `eco_id` link
  (§3.B).
- `yuantus_parallel_breakage_design_loopback_links_by_status`
  (gauge, label `status`) — live-linked incidents grouped by
  `BreakageIncident.status`.
- `yuantus_parallel_breakage_design_loopback_links_by_severity`
  (gauge, label `severity`) — live-linked incidents grouped by
  `BreakageIncident.severity`.

No other label dimensions (no `by_responsibility` /
`by_product_item` / etc. — the user listed exactly these;
further dimensions are future opt-ins). `# HELP`/`# TYPE`
lines mirror the existing `prometheus_metrics` style; emitted
from the existing route only.

### 3.E Current-state, not windowed — PRE-RATIFIED

`*_links_total` / `by_status` / `by_severity` are
**point-in-time gauges over the full `meta_breakage_incidents`
table** — NOT `created_at`-windowed (unlike `_breakage_summary`).
"How many incidents are currently linked" is a state fact, not
a rate-over-window. A "links created within window" rate is a
different, future metric (recorded out of scope). MANDATORY
test: an incident created far outside any `window_days` is
still counted.

### 3.F Exposure + surface — PRE-RATIFIED (no expansion)

Reuse `ParallelOpsOverviewService.prometheus_metrics` /
`summary` / the existing `GET /parallel-ops/metrics` route. The
**only** surface change is the three additional
`# HELP`/`# TYPE`/value lines in the existing Prometheus text
body. **No new route; `len(app.routes)` stays 677**; the
ops-router contract test stays green (route set unchanged).
`prometheus_metrics`/`summary` may take no new required params
(any threshold/warn knob is out of scope — these are plain
gauges, no SLO alerting in §3.7).

## 4. R1 Target Output (for the impl PR)

- `parallel_tasks_service.py`: add
  `_breakage_design_loopback_metrics(self) -> Dict[str, Any]`
  (SQL `func.count` + `GROUP BY`, live-link §3.B predicate, no
  `since`, no load-all); call it from `summary(...)` →
  `summary["breakage_design_loopback"]`; emit the three §3.D
  gauges in `prometheus_metrics(...)` mirroring the existing
  `# HELP`/`# TYPE`/`_prometheus_line` style.
- No new route; no request/response model change; no schema /
  alembic / tenant-baseline; no event subscriber; no runtime
  mutation; no edit to §3.2's CAS or any merged contract.
- `len(app.routes)` stays 677.

## 5. Tests Required (in the impl PR)

MANDATORY exactly-named (new file
`test_breakage_design_loopback_metrics.py`):

- **`test_links_total_counts_only_live_linked_incidents`** —
  N incidents, M with a real linked ECO → `*_links_total == M`;
  unlinked (`eco_id IS NULL`) excluded.
- **`test_dangling_eco_id_is_not_counted_as_linked`** — §3.B:
  link an ECO, hard-delete it (dangling `eco_id`), assert
  `*_links_total` decrements and the row leaves
  `by_status`/`by_severity` (matches merged §3.2's
  degraded-to-no-link).
- **`test_by_status_and_by_severity_group_live_links`** —
  live-linked incidents across mixed status/severity →
  per-label gauges correct; unlinked + dangling excluded.
- **`test_empty_data_emits_zero_not_error`** — no incidents:
  `*_links_total == 0`, empty `by_*`, no exception, valid
  Prometheus text.
- **`test_metrics_are_current_state_not_window_filtered`** —
  §3.E: an incident created far outside `window_days` is still
  counted.
- **`test_no_load_all_uses_sql_aggregate`** — AST/spy guard:
  `_breakage_design_loopback_metrics` issues `func.count`
  GROUP BY and does **not** call
  `query(BreakageIncident).all()` / load all rows (mirrors the
  established AST-guard test pattern).
- **`test_prometheus_surface_exposes_three_gauges_no_new_route`**
  — the rendered text contains the three §3.D metric names with
  `# TYPE … gauge`; `len(app.routes) == 677`; the ops-router
  contract route set is unchanged.

Plus: the breakage/helpdesk/event regression
(`test_breakage_design_loopback_event_emission.py`,
`test_breakage_update_status_auto_trigger.py`,
`test_breakage_helpdesk_sync_auto_trigger.py`,
`test_breakage_design_loopback_durable_idempotency.py`),
phase-4 route-count pin, ops-router contracts, doc-index trio,
R2 portfolio — all stay green.

## 6. Verification Commands (impl PR)

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_metrics.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_event_emission.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
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

The impl PR enumerates the exact regression files it ran (the
list is indicative). No alembic / tenant-baseline — §3.7 adds
no schema.

## 7. DEV/verification MD requirements (impl PR)

`docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_METRICS_R1_20260519.md`
+ index line. Must document: the §3.A SQL-aggregate
source-of-truth (and why a separate non-windowed helper, not
`_breakage_summary`); the §3.B live-link 口径 with the §3.2
dangling-contract cross-reference + the hard-delete test proof;
the §3.C `created_vs_reused`-out rationale; the §3.D exact
metric names/types; the §3.E current-state (not windowed)
proof; the §3.F no-new-route / 677 / contract-route-set
unchanged proof; inter-slice status (§3 catalog complete after
this).

## 8. Non-Goals (hard boundaries for the impl PR)

- **No in-memory `event_bus` counter / subscriber / listener**
  for metrics — restart / multi-process loss. SQL aggregate
  over persisted `eco_id` only.
- No `created_vs_reused` metric (§3.C — no persisted signal;
  event-stream concern; future opt-in).
- No new route; no request/response model change; no schema /
  alembic / tenant-baseline; no migration.
- No edit to §3.2's CAS / `session.rollback()`, any merged
  contract, or §3.6's emission path.
- No runtime mutation — §3.7 is **read-only** at scrape time.
- No new label dimensions beyond `{status, severity}`; no SLO
  threshold/warn knobs; no windowed "links-per-window" rate.
- No `created_at`-windowing of the link gauges (§3.E).
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude or the project owner
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/odoo18-breakage-design-loopback-metrics-r1-20260519`.

This is the **final §3 catalog slice**. Post-§3.7 follow-ups
(each its own opt-in, none in scope here): an event consumer /
materialized read-model (could enable `created_vs_reused`); a
durable-delivery outbox; windowed link-rate metrics; default-ON
flips.

## 10. Reviewer Focus

- **§3.A — confirm SQL-aggregate source-of-truth + the
  separate non-windowed helper.** Not an `event_bus` counter;
  not an extension of the windowed load-all `_breakage_summary`.
- **§3.B — confirm (b) live-link 口径** (`eco_id IN (SELECT id
  FROM meta_ecos)`) as the §3.2-consistent definition; (a)
  raw-non-null recorded rejected. The hard-delete test is the
  proof.
- **§3.C — confirm `created_vs_reused` is correctly OUT** (no
  persisted signal; not an in-memory-event count) rather than
  silently dropped.
- **§3.D/§3.E/§3.F** — exactly three `yuantus_parallel_*`
  gauges; current-state not windowed; no new route /
  `len(app.routes)` stays 677 / ops-router contract route set
  unchanged.
- Did anything add a route/schema/subscriber/runtime mutation,
  introduce an in-memory metric counter, count a dangling
  `eco_id` as linked, or touch a merged contract? It must not.
