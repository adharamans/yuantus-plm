# Odoo18 Breakage Design-Loopback Durable Idempotency R1 — Development and Verification

Date: 2026-05-19

## 1. Goal

Implement Tier-B #3 §3.2 per the ratified taskbook
(`docs/DEVELOPMENT_CLAUDE_TASK_ODOO18_BREAKAGE_DESIGN_LOOPBACK_DURABLE_IDEMPOTENCY_20260519.md`,
merged 2026-05-19 as `3e5104f` / PR #603). Replace the
best-effort `ECO.description` substring-scan dedupe with a
race-safe durable link so concurrent
`create_breakage_design_loopback_eco` calls for one incident
return the same ECO instead of producing duplicates.

§3 alternative ratified: **2a — bare `BreakageIncident.eco_id`
String column + compare-and-swap link** (no FK; UNIQUE only as a
cross-incident integrity backstop; the CAS UPDATE is the
concurrency sync point).

## 2. Scope

### Modified

- `src/yuantus/meta_engine/models/parallel_tasks.py` —
  `BreakageIncident.eco_id = Column(String, nullable=True,
  unique=True, index=True)` (bare soft-link, NO ForeignKey).
- `src/yuantus/meta_engine/services/parallel_tasks_service.py` —
  `from sqlalchemy import or_/select/update`;
  `_find_breakage_design_loopback_eco_by_reference` gains
  `incident_id=` kw (durable eco_id lookup first, substring
  fallback unchanged); `create_breakage_design_loopback_eco`
  CAS link path.
- `migrations_tenant/versions/t1_initial_tenant_baseline.py` —
  `eco_id` column + `ix_meta_breakage_incidents_eco_id`
  (unique) in/after the `meta_breakage_incidents` create_table.
- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py`
  — `test_explicit_creation_does_not_mutate_breakage_incident_status`
  renamed/rewritten to
  `test_explicit_creation_preserves_content_fields_and_wires_durable_eco_link`
  (refined contract — see §4).
- `docs/DELIVERY_DOC_INDEX.md` (one index line).

### Added

- `migrations/versions/ab1c2d3e4f5a_add_breakage_design_loopback_eco_id.py`
  (new alembic head; down_revision `aa1b2c3d4e7b0`).
- `src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py`
  (6 MANDATORY + 2 alembic/baseline tests).
- `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_DURABLE_IDEMPOTENCY_R1_20260519.md`

### Unchanged by design

Merged contracts (`breakage_db_resolver_contract`,
`breakage_eco_closeout_contract`, `ecr_intake_contract`),
`ECOService.create_eco`, all routers (no new route —
`len(app.routes)` stays 677), `.claude/`, `local-dev-env/`.

## 3. Implementation

### 3.1 Schema (no FK)

`eco_id` is a **bare String** column. NO `ForeignKey` —
consistent with the table's existing soft-link columns
(`product_item_id` / `bom_id` / `version_id`) and the
version-lock baseline precedent (`document_version_id` etc.).
This sidesteps the P2 tenant-baseline FK-ordering problem
(`meta_breakage_incidents` is created before `meta_ecos`). The
UNIQUE index name `ix_meta_breakage_incidents_eco_id` matches
the codebase's `unique=True, index=True` convention as used by
the existing `incident_code` column on the same table.

Migration `ab1c2d3e4f5a` follows the `aa1b2c3d4e7b0` idempotent
inspector template (existence-guarded add_column +
create_index; mirror downgrade). Inherits the repo-wide
`alembic upgrade head --sql` offline-mode caveat (`sa.inspect`
is offline-incompatible) — live-DB upgrade is the gate.

### 3.2 Compare-and-swap link (the concurrency sync point)

After `ECOService.create_eco`, for the `allow_duplicate=False`
path:

```sql
UPDATE meta_breakage_incidents
SET eco_id = :new
WHERE id = :incident
  AND (eco_id IS NULL OR eco_id NOT IN (SELECT id FROM meta_ecos))
```

- `rowcount == 1` → this caller won; `flush()`; `created=True`.
- `rowcount == 0` → another transaction already linked a valid
  ECO; this caller lost. `session.rollback()` (undoes this
  caller's own `create_eco` INSERT — `ECOService.create_eco`
  has no internal `commit`, verified), re-read
  `incident.eco_id`, return the winner with `created=False`.

The single atomic conditional UPDATE is the sync point: the row
write lock blocks a concurrent caller's UPDATE behind the
winner's uncommitted write until the winner's caller commits,
after which the loser re-evaluates the predicate against the
post-commit state. NOT the UNIQUE index (which only prevents
two incidents sharing one ECO; two different ECO ids on the
same incident would not violate it). `get_db()`
(`database.py:238`) is `SessionLocal()` + yield + finally
close, so two concurrent requests use separate
sessions/transactions — which is why the loser's rollback
isolates to its own transaction without touching the winner's
committed row.

`allow_duplicate=True` skips the CAS entirely (explicit
detached duplicate; `eco_id` stays the canonical first ECO or
NULL — author-ratified).

### 3.3 Spec reconciliation — the dangling-link predicate widening

**This is a deliberate, reviewer-flagged deviation from the
taskbook §4.4 *illustrative snippet*.** §4.4 showed
`WHERE id=:i AND eco_id IS NULL`. But §4.1 AND §5 MANDATORY
test 6 (`test_dangling_eco_id_degrades_to_no_link`) both
ratified the *behavioral contract* that a **dangling** link
(the linked ECO was hard-deleted; no FK so no SET NULL
cascade) must degrade so a fresh create proceeds. A bare
`eco_id IS NULL` predicate does NOT deliver that — a
non-NULL-but-dead `eco_id` would permanently wedge the
incident (the CAS would forever match zero rows and the method
would forever return `created=False, eco=None`).

The implementation reconciles the snippet with the ratified
behavioral contract by widening the predicate to
`eco_id IS NULL OR eco_id NOT IN (SELECT id FROM meta_ecos)`.
This:

- Still a single atomic conditional UPDATE → race-safety
  preserved (a genuine committed winner's `eco_id` IS in
  `meta_ecos`, so the loser's predicate is false → `rowcount
  0`; the loser's own just-flushed ECO is also in `meta_ecos`,
  so a real winner is never mis-classified as dangling).
- Delivers the §4.1/§5 promised graceful degradation: a
  dangling `eco_id` matches `NOT IN (...)` → the CAS overwrites
  it with the fresh ECO → `created=True`.

The taskbook's code snippet and its behavioral contract were
internally inconsistent; this implementation honors the
behavioral contract (which is what the MANDATORY test names
pinned). Flagged here and in the PR body for explicit reviewer
ratification.

### 3.4 Refined incident-mutation contract

Pre-§3.2 (#596 substring-scan era) the dedupe never wrote to
the incident, so the merged test asserted "creation does not
mutate the incident (incl. timestamps)". §3.2's durable link
is a deliberate incident-row mutation. The refined contract,
pinned by the renamed
`test_explicit_creation_preserves_content_fields_and_wires_durable_eco_link`:

- **content fields** (`status` / `severity` / `description`)
  unchanged — the CAS only sets `eco_id`;
- **`eco_id`** is set to the created ECO's id (the new durable
  link);
- **`updated_at` bumps** — `BreakageIncident.updated_at` has
  `onupdate=datetime.utcnow`; the CAS genuinely changes the
  row, so a fresh `updated_at` is the correct, expected DB
  behavior. This is an intended behavior change vs. the #596
  "no timestamp mutation" assertion (which was only valid when
  dedupe never wrote to the incident). Flagged for reviewer.

The permission-failure test
(`test_creation_permission_error_propagates_without_status_mutation`)
is **unchanged and still green**: `ECOService.create_eco`'s
permission check (`eco_service.py:520`) precedes its
`session.add`/`flush` and the CAS, so a permission failure
never reaches the link write — no `updated_at` bump on that
path.

## 4. Test Matrix

`test_breakage_design_loopback_durable_idempotency.py` — 8
tests:

- **`test_breakage_eco_id_column_schema_pinned`** (MANDATORY) —
  bare String, nullable+unique+indexed, **NO ForeignKey**
  (re-adding an FK fails loudly — guards the P2 resolution).
- **`test_create_eco_wires_durable_link_on_first_call`**
  (MANDATORY) — first call sets `eco_id`; second call returns
  the same ECO via the durable lookup even after every ECO
  `description` envelope is destroyed (proves the substring
  scan is bypassed; no duplicate).
- **`test_create_eco_compare_and_swap_serializes_concurrent_link`**
  (MANDATORY) — two sessions on a shared StaticPool engine.
  Winner links+commits; loser's pre-check forced to miss →
  loser's CAS sees `eco_id` set → `rowcount 0` → rollback →
  returns winner; exactly one ECO committed.
- **`test_allow_duplicate_true_preserves_first_eco_id_and_creates_unlinked_duplicate`**
  (MANDATORY) — first links `eco_id=A`; `allow_duplicate=True`
  makes ECO B, `eco_id` stays A, two ECOs exist.
- **`test_substring_scan_fallback_handles_historical_incidents`**
  (MANDATORY) — `eco_id` cleared to simulate pre-migration
  data; the substring fallback still returns the historical
  ECO; no duplicate.
- **`test_dangling_eco_id_degrades_to_no_link`** (MANDATORY) —
  hard-delete the linked ECO; the lookup returns None and a
  subsequent create proceeds to a fresh ECO (validates §3.3).
- **`test_alembic_upgrade_head_creates_eco_id_column`** —
  fresh SQLite, `alembic upgrade head`, inspect column + the
  unique index.
- **`test_tenant_baseline_includes_breakage_eco_id_column`** —
  source-scan the baseline for the column + unique-index decls.

Regression unchanged & green: route, router contracts,
runtime-wiring, eco-creation-wiring (with the one renamed test),
db-resolver, closeout, ECR-intake, phase-4 route-count pin
(677, no new route), doc-index trio, R2 portfolio.

## 5. Verification Commands

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_eco_closeout_contract.py \
  src/yuantus/meta_engine/tests/test_ecr_intake_contract.py \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/models/parallel_tasks.py \
  src/yuantus/meta_engine/services/parallel_tasks_service.py \
  migrations/versions/ab1c2d3e4f5a_add_breakage_design_loopback_eco_id.py \
  migrations_tenant/versions/t1_initial_tenant_baseline.py
git diff --check
```

Observed 2026-05-19: durable-idempotency 8/8; full breakage +
phase-4 + doc-index + portfolio combined **110 passed**;
`py_compile` clean; `git diff --check` clean; new alembic head
`ab1c2d3e4f5a`.

## 6. Non-Goals (reaffirmed from taskbook §8)

- No FK / no `relationship()` — bare soft-link column only.
- No backfill of historical `eco_id` (substring fallback covers
  reads; one-shot backfill is a separate opt-in).
- No orphan-ECO cleanup path needed (loser rollback undoes its
  own INSERT before commit).
- No auto-trigger (§3.3/§3.4 separate opt-ins, now unblocked).
- No event emission (§3.6) / metrics (§3.7).
- No new route. No edit to merged contracts or
  `ECOService.create_eco`.

## 7. Inter-slice status

- §3.1 route exposure: delivered (`a02dbd0`); response shape
  unchanged (200 + `created:false` now durable-driven).
- §3.3 `update_status` auto-trigger / §3.4 helpdesk-sync
  auto-trigger: **now unblocked** by this slice's race-safety
  guarantee — each still its own separate later opt-in.
- §3.5 RBAC (recommended-reject), §3.6 event emission, §3.7
  metrics (§3.7 SQL-aggregate source can now read
  `incident.eco_id`): each its own future opt-in.
