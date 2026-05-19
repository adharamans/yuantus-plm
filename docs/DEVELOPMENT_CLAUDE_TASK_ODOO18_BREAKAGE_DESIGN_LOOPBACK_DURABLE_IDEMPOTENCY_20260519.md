# Claude Taskbook: Odoo18 Breakage Design-Loopback Durable Idempotency

Date: 2026-05-19

Type: **Doc-only taskbook.** Changes no runtime, no schema, no
service. Specifies the contract a later, separately opted-in
implementation PR will deliver. Merging this taskbook does NOT
authorize that code.

## 1. Purpose

R2 closeout §4 **Tier-B follow-up #3 §3.2** (per the remainder
catalog ratified at PR #601 `7fce255`). Replace the merged
best-effort substring-scan dedupe in
`BreakageIncidentService._find_breakage_design_loopback_eco_by_reference`
with a race-safe persistence guarantee so that concurrent calls
to `create_breakage_design_loopback_eco` for the same incident
return the same ECO (or fail explicitly) rather than producing
duplicates.

This slice is the **prerequisite** for the §3.3/§3.4 auto-trigger
slices that would otherwise be race-unsafe: a UI button + a
helpdesk webhook arriving close in time can today produce 2
ECOs because the current dedupe is `ECO.description` substring
matching with no row lock, no UNIQUE constraint, and no FK
back-reference.

R1 is a **schema slice**: alembic migration + tenant baseline +
SQLAlchemy model change + service-method substitution + tests.
No edit to merged contracts, no edit to `ECOService.create_eco`,
no edit to existing routes, no auto-trigger (§3.3/§3.4 stay
gated).

## 2. Current Reality (grounded — direct file reads)

All citations verified by direct reads (per
[[feedback-verify-grounding-facts]] and its negative-grep
addendum — broad search for FK/UNIQUE precedents, alembic head,
tenant-baseline ownership of breakage tables).

### Today's dedupe path

`src/yuantus/meta_engine/services/parallel_tasks_service.py`
`BreakageIncidentService`:

- **`_find_breakage_design_loopback_eco_by_reference(self, reference)`**
  at line 4254–4273 — substring scan of `ECO.description`:
  - Normalizes the reference; computes marker
    `f"reference={normalized}"`.
  - `SELECT * FROM meta_ecos WHERE description LIKE
    %marker%` ordered by `(created_at ASC, id ASC)`.
  - Returns the earliest match where `description` ALSO contains
    `"breakage-eco-closeout"`.
  - **No row lock.** **No SELECT FOR UPDATE.** **No transaction
    isolation guarantee.**
- **`create_breakage_design_loopback_eco(self, incident_id, *, user_id, allow_duplicate=False)`**
  at line 4275+ — uses the above find-then-create pattern: if
  `allow_duplicate=False` AND no match exists, calls
  `ECOService.create_eco(**kwargs)`. Race window: two callers
  simultaneously see "no match", both call `create_eco`, two
  ECOs land in the DB with the same reference.

### Source row state

`src/yuantus/meta_engine/models/parallel_tasks.py`,
`BreakageIncident` (line 168+):

- 18 columns including `id`, `incident_code`,
  `product_item_id`, `bom_id`, `status`, `severity`,
  `description`, `created_at`, `updated_at`.
- **NO `eco_id` column today.** No back-reference from breakage
  to a derived ECO. The linkage today is purely the
  closeout-reference envelope embedded in `ECO.description`.
- `incident_code` already has a UNIQUE constraint (line 172):
  `Column(String(40), nullable=True, unique=True, index=True)`
  — precedent for UNIQUE on a nullable string column in this
  table.

### ECO destination

`src/yuantus/meta_engine/models/eco.py` `ECO` (line 135+):

- `__tablename__ = "meta_ecos"` (line 146).
- `id = Column(String, primary_key=True)` (line 148) — UUID
  string. **FK target shape: `String`, `ForeignKey("meta_ecos.id")`**.
- `product_id` FK precedent (line 152–158):
  `Column(String, ForeignKey("meta_items.id", ondelete="SET NULL"),
  nullable=True, index=True)` — pattern for adding a nullable FK
  with a SET NULL on-delete behavior.

### Alembic + tenant baseline state

- **Alembic head:** `aa1b2c3d4e7b0` (the workorder-document-link
  version-lock migration). The new §3.2 migration's
  `down_revision` will be this revision.
- **Migration pattern:** `migrations/versions/aa1b2c3d4e7b0_add_workorder_doc_version_lock.py`
  is the cleanest recent FK-column addition to mirror. Key
  pattern points:
  - `op.get_bind()` + `sa.inspect(bind)` to introspect existing
    tables/columns/indexes (defensive against repeated runs).
  - `existing_columns = {col["name"] for col in
    inspector.get_columns(_TABLE)}` then
    `if column_name not in existing_columns: op.add_column(...)`.
  - Indexes added separately, also guarded by existence check.
  - `downgrade()` mirror with `op.drop_index` → `op.drop_column`,
    each guarded.
  - **Caveat: this pattern is incompatible with `alembic upgrade
    head --sql` offline mode**, repo-wide. The new §3.2 migration
    must follow the same pattern (no alternative is possible
    without breaking existing infrastructure) and inherits the
    same caveat. Live-DB upgrade is the verification gate.
- **Tenant baseline:** `migrations_tenant/versions/t1_initial_tenant_baseline.py`
  already creates `meta_breakage_incidents` (line 77) and
  declares all 18 current columns. A new column on
  `meta_breakage_incidents` requires updating this baseline so
  fresh-tenant provisioning includes it. The version-lock
  precedent updated this baseline (lines 473, 475 for
  `document_version_id`, `version_lock_source`).

### Route layer (unchanged by §3.2 but informed by it)

`POST /breakages/{incident_id}/design-loopback/eco` (PR #602
`a02dbd0`) is a thin delegation over
`create_breakage_design_loopback_eco`. The service method's
dedupe semantics change transparently — the route's response
shape (`200 + created:false`) stays valid. No router edit
needed.

## 3. Three alternatives + author recommendation

The taskbook (PR #601 §3.2) opened three alternatives. This
sub-taskbook details each and flags author's recommended path.

### 2a — `BreakageIncident.eco_id` FK + UNIQUE (author recommendation)

**Shape.** Add nullable column
`BreakageIncident.eco_id: Optional[str]` with
`ForeignKey("meta_ecos.id", ondelete="SET NULL")` and a UNIQUE
INDEX. The service method sets `incident.eco_id = eco.id`
inside the same `session.flush()` as `create_eco`. Race-safe:
the UNIQUE constraint forces serialization at the DB level —
two concurrent transactions cannot both succeed in setting
`eco_id` to a non-NULL value on the same incident.

**Why author recommends:**

1. Surfaces the breakage↔ECO relationship as a first-class FK
   that UI / reports / audits can navigate without parsing the
   `ECO.description` envelope.
2. Cheap dedupe: one indexed PK lookup (`SELECT eco FROM ecos
   WHERE id = incident.eco_id`) replaces the substring scan.
3. Race-safety guarantee is at the DB level — works for any
   client (Python process, SQL shell, future microservice)
   without relying on application-level cooperation.
4. Provides the back-reference §3.6/§3.7 (event/metrics slices)
   could read from for "what's the loopback ECO for incident
   X?" without re-parsing descriptions.

**Trade-offs:**

- Schema change (alembic + tenant baseline).
- `allow_duplicate=True` semantics need explicit handling — the
  UNIQUE prevents setting `eco_id` twice on the same incident,
  so duplicate-allowed flow either (i) leaves `eco_id` set to
  the FIRST ECO and creates additional ECOs that are unlinked,
  or (ii) clears the link first. **Author recommends (i):
  `eco_id` is the canonical / first-created ECO; additional
  duplicates created via `allow_duplicate=True` are NOT
  back-referenced** (the operator explicitly chose to create a
  detached duplicate). The §5 tests pin this.
- Backfill question for historical breakages with existing
  loopback ECOs in `ECO.description`. Author recommends NO
  automatic backfill in R1 — `eco_id` stays NULL for
  pre-migration data, and the service method falls back to the
  substring scan only when `eco_id IS NULL` (belt-and-suspenders
  for the transition period). A separate later opt-in can write
  a one-shot backfill script.

### 2b — Separate `meta_breakage_eco_creations` audit/lock table

**Shape.** New table with `(incident_id, reference)` as a
composite UNIQUE key. The service method INSERTs into it
before calling `create_eco`; on integrity-error, it queries the
existing row and returns the linked ECO.

**Pro.** Doesn't touch `BreakageIncident`. Schema change is
isolated to a new table.

**Con.** Adds a new table that the rest of the system has to
know about. Makes "what's the loopback ECO for incident X?"
require a JOIN rather than a single FK lookup. Audit-table
proliferation drift.

**Not recommended unless 2a is blocked by an
operational/governance reason for not touching
`BreakageIncident`.**

### 2c — Application-level `SELECT FOR UPDATE`

**Shape.** Acquire a row lock on the `BreakageIncident` row via
`SELECT * FROM meta_breakage_incidents WHERE id = ? FOR UPDATE`
before the find-then-create.

**Pro.** No schema change. No migration. No tenant-baseline
update.

**Con.** The lock lives only for the transaction — it
serializes concurrent transactions but provides NO persistent
uniqueness guarantee. A committed duplicate could still appear
from another transaction that didn't take the lock (e.g., a
script with stale code, a non-cooperating microservice). Also:
SQLite (test environment) does not implement `SELECT FOR
UPDATE`; the test DB diverges from production semantics.

**Not recommended.** R1 risk profile is high enough that
durable persistence-level uniqueness is worth the schema
change.

### Reviewer ratification

**Author recommends 2a.** This taskbook describes 2a in detail
in §4–§5; the impl PR can materialise 2b or 2c instead if the
reviewer ratifies a different alternative, in which case §4–§5
contents apply only as informational background for 2b/2c.

## 4. R1 Target Output (for the impl PR — assumes 2a ratified)

### 4.1 SQLAlchemy model change

`src/yuantus/meta_engine/models/parallel_tasks.py`,
`BreakageIncident` — add ONE column after the existing 17
columns (preserving column declaration order so the migration
can append cleanly):

```python
eco_id = Column(
    String,
    ForeignKey("meta_ecos.id", ondelete="SET NULL"),
    nullable=True,
    unique=True,
    index=True,
)
```

Mirrors the existing `incident_code` UNIQUE precedent (line
172). `ondelete="SET NULL"` matches the
`product_id` precedent in `ECO` (eco.py:152–158) — if an ECO
is hard-deleted, the breakage's link drops to NULL but the
incident row is preserved.

No relationship() back-reference in R1 — the FK column alone
suffices for the dedupe lookup, and adding a relationship
ripples into more code surface than this taskbook authorizes.

### 4.2 Alembic migration

`migrations/versions/<new_rev>_add_breakage_design_loopback_eco_id.py`,
new revision after `aa1b2c3d4e7b0`. Follows the
`aa1b2c3d4e7b0` template exactly (defensive idempotent
`upgrade()` + mirror `downgrade()`):

- `_TABLE = "meta_breakage_incidents"`
- `_NEW_COLUMN = "eco_id"`
- `_NEW_INDEX = "ix_meta_breakage_incidents_eco_id"` (regular)
- `_NEW_UNIQUE = "uq_meta_breakage_incidents_eco_id"` (UNIQUE)
- `upgrade()`: inspector + existence checks → `op.add_column(
  sa.Column("eco_id", sa.String(), sa.ForeignKey("meta_ecos.id",
  ondelete="SET NULL"), nullable=True))` → `op.create_index`
  regular index → `op.create_index(unique=True)` for the
  UNIQUE constraint (the existing `incident_code` UNIQUE is
  declared inline via `unique=True` on `Column`, but at the
  migration level we use a named UNIQUE index for explicit
  drop semantics on downgrade).
- `downgrade()`: drop the UNIQUE index → regular index →
  column, each guarded.

**Offline-mode caveat inherited from `aa1b2c3d4e7b0`'s
`sa.inspect(bind)` pattern** — `alembic upgrade head --sql`
will fail repo-wide; live-DB upgrade is the verification gate.

### 4.3 Tenant baseline update

`migrations_tenant/versions/t1_initial_tenant_baseline.py`:
add the column declaration to `meta_breakage_incidents`'s
`op.create_table` call AND add `op.create_index` lines for the
two new indexes. Fresh-tenant provisioning then includes the
new column without needing to also run the alembic migration.

### 4.4 Service method changes

`src/yuantus/meta_engine/services/parallel_tasks_service.py`:

- **`_find_breakage_design_loopback_eco_by_reference`** —
  REWRITTEN to use the FK lookup as the primary path with
  substring-scan fallback for pre-migration data:

  ```python
  def _find_breakage_design_loopback_eco_by_reference(
      self, reference, *, incident_id=None,
  ):
      # Primary: durable FK lookup if we know the incident.
      if incident_id:
          incident = self.session.get(BreakageIncident, incident_id)
          if incident is not None and incident.eco_id:
              return self.session.get(ECO, incident.eco_id)
      # Fallback: substring scan for pre-migration data
      # (incidents whose `eco_id` is NULL but whose ECO was
      # created before the migration). The fallback path is
      # NOT race-safe — only the FK lookup is.
      # ...existing substring-scan body unchanged...
  ```

  The new optional `incident_id` keyword is the only signature
  change. `allow_duplicate=True` callers may want to pass
  `incident_id=None` to skip the FK lookup entirely.

- **`create_breakage_design_loopback_eco`** — wire the FK
  link AFTER `ECOService.create_eco` returns:

  ```python
  # ...existing prelude unchanged through preparation + reference...
  if not allow_duplicate:
      existing = self._find_breakage_design_loopback_eco_by_reference(
          reference, incident_id=incident_id,
      )
      if existing is not None:
          return BreakageDesignLoopbackEcoCreation(..., created=False, ...)

  kwargs = preparation.eco_draft_inputs.as_kwargs()
  kwargs["user_id"] = user_id
  eco = ECOService(self.session).create_eco(**kwargs)

  # Wire the durable link. With `allow_duplicate=False` this
  # is the canonical link. With `allow_duplicate=True` we only
  # set `eco_id` if it's currently NULL (i.e., this is the
  # FIRST loopback ECO for this incident) — author-ratified
  # semantics: `eco_id` is the canonical/first-created ECO;
  # explicit duplicates are intentionally NOT back-referenced.
  incident = self.session.get(BreakageIncident, incident_id)
  if incident is not None and incident.eco_id is None:
      incident.eco_id = eco.id
      self.session.flush()

  return BreakageDesignLoopbackEcoCreation(..., created=True, ...)
  ```

### 4.5 Race semantics

With the UNIQUE constraint, two concurrent transactions both
calling `create_breakage_design_loopback_eco(incident_id=X,
allow_duplicate=False)`:

1. Both flush their respective `ECOService.create_eco` calls
   successfully within their own transactions (no constraint on
   `meta_ecos`).
2. Both attempt `incident.eco_id = eco.id` + `flush`.
3. The first to commit wins; the second hits `IntegrityError`
   (UNIQUE violation on `eco_id`) at flush time.
4. The losing transaction catches the `IntegrityError`,
   `session.rollback()`s — which rolls back **both** the
   `eco_id` update AND the `ECOService.create_eco` flush
   (verified: `ECOService.create_eco` does NOT contain
   `session.commit` anywhere in `eco_service.py`, so its flush
   is entirely inside the losing transaction and is undone by
   the rollback). The losing process then re-queries
   `incident.eco_id` (now non-NULL from the winner's commit)
   and returns `created=False` with the winning ECO.

**No orphan ECO appears in the DB after a race loss** — the
rollback undoes the losing transaction's `ECOService.create_eco`
flush. This is why the §3.2 alt 2a design is race-safe AND
cleanup-free: the UNIQUE constraint is the synchronization
point, and the caller-owned transaction boundary (via
`ECOService.create_eco`'s no-internal-commit guarantee) means
the losing flush never lands in committed state.

R1 must implement the catch-`IntegrityError` + rollback +
re-query semantic — author recommends a single retry inside
`create_breakage_design_loopback_eco` for the
`allow_duplicate=False` path. This makes the method race-safe
from the caller's perspective.

### 4.6 Route behavior preserved

`POST /breakages/{incident_id}/design-loopback/eco` continues
to return `200 + created:false` on dedupe hits (now driven by
the durable FK rather than substring scan). The route handler
is unchanged.

### 4.7 What §3.2 R1 explicitly does NOT do

- No backfill of `BreakageIncident.eco_id` for historical
  incidents whose loopback ECOs were created pre-migration.
  The substring-scan fallback in
  `_find_breakage_design_loopback_eco_by_reference` handles
  those reads transparently; a separate later opt-in can ship
  a one-shot backfill script.
- No `relationship()` back-reference between `BreakageIncident`
  and `ECO` — the FK column alone is enough for the dedupe.
  Adding a relationship would ripple into ECOService.eco_repr,
  cascade semantics, eager-load decisions, etc.
- No auto-trigger from `update_status` or helpdesk-sync (§3.3/
  §3.4 separate later opt-ins).
- No event emission for "link wired" (§3.6 separate later opt-in).
- No metric exposure for "loopback ECO link count" (§3.7
  separate later opt-in — though §3.7 author-recommended
  source-data path uses this very column).

### 4.8 DB-engine portability note

Author claim: a `nullable=True, unique=True` column allows
multiple NULL rows (so existing breakages without an
`eco_id` can stay NULL) but blocks two non-NULL duplicates.
This is DB-engine-dependent. Verified ground truth:

- `src/yuantus/database.py:6` declares "Supports Postgres via
  `DATABASE_URL`"; `src/yuantus/database.py:132–146` adds
  SQLite-derived URLs as the dev/test default.
- Postgres + SQLite both treat NULL as "unknown" under UNIQUE,
  so multiple NULLs are allowed. The `incident_code` precedent
  (`parallel_tasks.py:172`, `Column(String(40), nullable=True,
  unique=True, index=True)`) relies on the same semantic
  successfully in production today.
- **NOT portable to MySQL or MSSQL** without an explicit
  partial/filtered index (Postgres-specific anyway). Any
  future move off Postgres+SQLite would need a different
  uniqueness strategy. R1 explicitly assumes the existing
  Postgres+SQLite invariant.

## 5. Tests Required (in the later impl PR)

### MANDATORY exactly-named tests

- **`test_breakage_eco_id_fk_unique_constraint_pinned`** —
  drift guard on the schema. Asserts the SQLAlchemy `Column`
  on `BreakageIncident.eco_id` declares
  `nullable=True, unique=True, index=True` AND has a
  `ForeignKey("meta_ecos.id", ondelete="SET NULL")`. If a
  future change weakens any of these, the test fails loudly.
- **`test_create_eco_wires_durable_link_on_first_call`** —
  end-to-end on SQLite: `create_breakage_design_loopback_eco`
  for an eligible incident sets `incident.eco_id` to the
  created ECO's id; second call with `allow_duplicate=False`
  returns the same ECO via FK lookup (NOT substring scan;
  patch the substring scan and assert it isn't called).
- **`test_create_eco_unique_constraint_blocks_concurrent_link`** —
  simulates the race: open two sessions, both prepare to
  create + link for the same incident, commit the first,
  assert the second hits `IntegrityError` on flush; verify
  the caught + retry path returns `created=False` with the
  winning ECO.
- **`test_allow_duplicate_true_preserves_first_eco_id_and_creates_unlinked_duplicate`** —
  the author-ratified `allow_duplicate=True` semantic: first
  call wires `eco_id=A`; second call with `allow_duplicate=True`
  creates `ECO B` but does NOT change `eco_id` (still A);
  `B.id != incident.eco_id`.
- **`test_substring_scan_fallback_handles_historical_incidents`** —
  simulate a pre-migration incident with `eco_id=NULL` but
  an existing ECO with the closeout envelope. The find method
  returns the historical ECO via substring scan; subsequent
  call still returns it (no new ECO is created).
- **`test_eco_hard_delete_sets_breakage_eco_id_to_null`** —
  ondelete=SET NULL behavior: delete the linked ECO row,
  verify `incident.eco_id` is NULL after the cascade.

### Alembic / tenant-baseline tests

- **`test_alembic_upgrade_head_creates_eco_id_column`** —
  fresh-DB live upgrade: spin up SQLite, run `alembic upgrade
  head`, inspect `meta_breakage_incidents` for the new
  `eco_id` column + indexes.
- **`test_tenant_baseline_includes_breakage_eco_id_column`** —
  fresh-tenant baseline: load
  `migrations_tenant/versions/t1_initial_tenant_baseline.py`,
  apply, inspect for the column. Or — easier — assert the
  baseline file's source contains the `eco_id` column
  declaration. The R2 portfolio precedent (and the existing
  `test_tenant_baseline_revision.py`) is the model.

### Existing regression suites must stay green

- `test_breakage_design_loopback_eco_creation_wiring.py` —
  service method end-to-end.
- `test_parallel_tasks_breakage_design_loopback_route.py` —
  route handler delegates unchanged.
- `test_parallel_tasks_breakage_router_contracts.py` — route
  registration unchanged.
- `test_phase4_search_closeout_contracts.py` — `len(app.routes)
  == 677` unchanged (this slice adds no route).
- R2 portfolio drift guard
  (`test_odoo18_r2_portfolio_contract.py`) green.

## 6. Verification Commands (for the impl PR)

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_durable_idempotency.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_eco_creation_wiring.py \
  src/yuantus/meta_engine/tests/test_breakage_design_loopback_runtime_wiring.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_design_loopback_route.py \
  src/yuantus/meta_engine/tests/test_parallel_tasks_breakage_router_contracts.py \
  src/yuantus/meta_engine/tests/test_breakage_db_resolver_contract.py \
  src/yuantus/meta_engine/tests/test_breakage_eco_closeout_contract.py
```

```bash
.venv/bin/python -m pytest -q \
  src/yuantus/meta_engine/tests/test_phase4_search_closeout_contracts.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_completeness.py \
  src/yuantus/meta_engine/tests/test_dev_and_verification_doc_index_sorting_contracts.py \
  src/yuantus/meta_engine/tests/test_delivery_doc_index_references.py \
  src/yuantus/meta_engine/tests/test_odoo18_r2_portfolio_contract.py \
  src/yuantus/tests/test_tenant_baseline_revision.py
```

```bash
# Live-DB alembic upgrade (offline --sql mode is broken
# repo-wide; this is the verification gate).
.venv/bin/python -m alembic upgrade head
.venv/bin/python -c "from yuantus.database import engine; \
import sqlalchemy as sa; \
print({c['name'] for c in sa.inspect(engine).get_columns('meta_breakage_incidents')})"
```

```bash
.venv/bin/python -m py_compile \
  src/yuantus/meta_engine/services/parallel_tasks_service.py \
  src/yuantus/meta_engine/models/parallel_tasks.py
git diff --check
```

## 7. DEV/verification MD requirements (impl PR)

Add `docs/DEV_AND_VERIFICATION_ODOO18_BREAKAGE_DESIGN_LOOPBACK_DURABLE_IDEMPOTENCY_R1_20260519.md`
+ index registration. Must document:

(a) Which §3 alternative ratified (2a/2b/2c).
(b) For 2a: schema migration + tenant baseline update + model
    change + service method substitution paths.
(c) Race-loss handling (catch IntegrityError, rollback, re-query,
    return `created=False`) + orphan-ECO disposition (author
    rec: leave for separate cleanup opt-in).
(d) Historical-data behavior (substring-scan fallback for
    pre-migration incidents).
(e) Drift guards added and the alembic-head-pin update.
(f) Inter-slice dependency status:
    - §3.1 route exposure: delivered (`a02dbd0`); response
      shape unchanged.
    - §3.3/§3.4 auto-trigger: NOW UNBLOCKED by this slice.
    - §3.6/§3.7 event/metrics: still each their own opt-in;
      §3.7 SQL-aggregate source-data choice is now feasible
      because `incident.eco_id` is queryable.

## 8. Non-Goals (hard boundaries for the impl PR)

- **No edit to merged contracts**: `breakage_db_resolver_contract`,
  `breakage_eco_closeout_contract`, `ecr_intake_contract` stay
  verbatim.
- **No edit to `ECOService.create_eco`** or any router beyond
  what §4.4 specifies.
- **No new route** — `len(app.routes)` stays 677.
- **No auto-trigger** in `update_status` or helpdesk-sync
  (§3.3/§3.4 separate opt-ins).
- **No backfill of historical `eco_id`** values — the fallback
  substring scan handles those reads transparently.
- **No orphan-ECO cleanup** for race-losers (separate cleanup
  opt-in if desired).
- **No `relationship()` between BreakageIncident and ECO** — FK
  column alone.
- **No event emission** for "link wired" (§3.6).
- **No metric counter** for link state (§3.7).
- **No `BreakageIncident` model edits beyond the one new
  column**.
- **No edit to existing `incident_code` UNIQUE** or any other
  pre-existing constraint.
- `.claude/` and `local-dev-env/` stay out of git.

## 9. Decision Gate / Handoff

Doc-only. Implementation owned by Claude or the project owner
**only after this taskbook is merged AND a separate explicit
opt-in is given**, on branch
`feat/odoo18-breakage-design-loopback-durable-idempotency-r1-20260519`.

Follow-ups, each its own separate opt-in (explicitly NOT in
this slice):

- Backfill script for historical `BreakageIncident.eco_id`
  values from existing ECO closeout envelopes.
- §3.3 `update_status` auto-trigger (UNBLOCKED by this slice).
- §3.4 helpdesk-sync auto-trigger (UNBLOCKED).
- §3.6/§3.7 event/metrics (their independence preserved).

## 10. Reviewer Focus

This is a schema slice — the highest-risk slice in the remainder
catalog. Reviewer focus:

- **§3 alternative choice**: 2a (FK+UNIQUE) vs. 2b (audit table)
  vs. 2c (advisory lock). Author recommends 2a; reviewer
  ratifies.
- **§4.5 race semantics**: confirm the catch-IntegrityError +
  rollback + re-query + return `created=False` pattern is the
  right shape; alternative would be transparent SELECT FOR
  UPDATE on the incident row before the find-then-create,
  which is closer to 2c semantics.
- **§4.8 portability assumption**: confirm Postgres+SQLite is
  the project's supported-DB set going forward, so the
  multi-NULL UNIQUE semantic is durable. If a future MySQL/
  MSSQL backend is on the roadmap, 2a needs a different
  uniqueness strategy.
- **§4.4 substring-scan fallback**: pin it in for the
  transition period vs. drop it entirely (forcing
  backfill-or-NULL semantics). Author keeps it for
  backwards-compatibility with historical data; reviewer can
  push back if a clean-slate is preferred + a backfill is
  bundled.
- **§4.3 tenant baseline update**: confirm the baseline file
  must be updated in the same impl PR (not deferred), so
  fresh-tenant provisioning works without depending on
  alembic-upgrade-head running first.
- **§5 test coverage**: are the 6 MANDATORY tests + 2 alembic/
  baseline tests sufficient? Notably: should there be a
  cross-tenant test verifying the UNIQUE is scoped per-tenant
  (or per-DB, depending on tenancy model)?
- **§8 non-goals**: did anything in this catalog claim
  authorization for a slice that hasn't been ratified? It
  must not — the goal is enumeration + scoping, not
  pre-decision of any §3.3+ slice.
