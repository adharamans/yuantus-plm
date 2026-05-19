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
  string. The `eco_id` link column stores this value as a bare
  `String` (no FK — see §4.1 for why). For grounding context:
  `ECO.product_id` (line 152–158) DOES use
  `ForeignKey("meta_items.id", ondelete="SET NULL")`, but
  `BreakageIncident` deliberately does NOT follow that pattern
  for its cross-table references — `product_item_id`/`bom_id`/
  `version_id` are bare `String` soft-links
  (`parallel_tasks.py:173–177`). `eco_id` joins that
  soft-link convention.

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

### 2a — `BreakageIncident.eco_id` column + compare-and-swap link (author recommendation)

**CORRECTION (review round 1, P1):** an earlier draft claimed a
single-column `UNIQUE(eco_id)` serializes two concurrent links
to the *same incident*. That is **wrong**. `UNIQUE(eco_id)`
only guarantees no two incident rows reference the *same* ECO;
two transactions writing two *different* ECO ids (A and B) to
the *same* `incident.eco_id` do NOT violate it (A ≠ B, both
unique) — the failure mode is silent last-writer-wins, not an
`IntegrityError`. The race-safety mechanism in 2a is therefore
**not** the UNIQUE constraint; it is an explicit
**compare-and-swap (CAS)** update. The taskbook's design and
the §5 race test are written around CAS, not UNIQUE.

**Shape.**

- Add nullable column `BreakageIncident.eco_id: Optional[str]`.
  Type is a bare `String` (NO `ForeignKey`) — consistent with
  `BreakageIncident`'s existing soft-link convention:
  `product_item_id`, `bom_id`, `version_id` are all bare
  `Column(String, nullable=True, index=True)` with no FK
  (`parallel_tasks.py:173–177`). Making `eco_id` a bare string
  matches the table and sidesteps the P2 baseline-FK-ordering
  problem entirely (see §4.3). Loss of `ON DELETE SET NULL`:
  the read path `self.session.get(ECO, incident.eco_id)`
  already returns `None` for a since-deleted ECO, so a dangling
  `eco_id` degrades gracefully to "no link" — acceptable.
- Add a UNIQUE index on `eco_id` (NULLs excluded — see §4.8).
  Its job is **data integrity, not race serialization**: it
  prevents the same ECO being claimed by two different
  incidents (which would be a genuine bug). It is explicitly
  NOT the concurrency sync point.
- The concurrency sync point is a **conditional UPDATE**:
  `UPDATE meta_breakage_incidents SET eco_id = :eco
   WHERE id = :incident_id AND eco_id IS NULL`, then inspect
  `rowcount`. `rowcount == 1` → this caller won the link.
  `rowcount == 0` → another transaction already linked an ECO;
  this caller lost the race, must roll back its own
  `ECOService.create_eco` (undone by the rollback per the
  no-internal-commit guarantee), re-query `incident.eco_id`,
  and return the winner's ECO with `created=False`.

**Why author recommends:**

1. Surfaces the breakage↔ECO relationship as a navigable
   column for UI / reports / §3.6 / §3.7 without parsing the
   `ECO.description` envelope.
2. Cheap dedupe: one indexed lookup
   (`SELECT * FROM meta_ecos WHERE id = incident.eco_id`)
   replaces the substring scan.
3. Race-safe via the CAS UPDATE — a single atomic
   `UPDATE ... WHERE eco_id IS NULL` is evaluated under the
   row's write lock by Postgres and SQLite, so exactly one
   concurrent caller gets `rowcount == 1`. (This is an
   application-issued conditional update, NOT a pure DB
   constraint — callers that bypass this code path and do a
   blind `UPDATE ... SET eco_id` could still clobber the link;
   the UNIQUE index is the backstop that turns a cross-incident
   clobber into an error, but a same-incident blind overwrite
   is out of scope — every write goes through the one service
   method.)
4. Bare-string column matches the table's existing soft-link
   convention; no new FK, no baseline table-ordering risk.

**Trade-offs:**

- Schema change (alembic + tenant baseline) — column + indexes
  only, no FK constraint.
- `allow_duplicate=True` semantics: the CAS only fires for the
  `allow_duplicate=False` path. With `allow_duplicate=True`,
  the caller explicitly wants a detached duplicate, so the
  service creates the ECO and does **not** attempt the CAS
  link (leaving `eco_id` pointing at whatever the canonical
  first ECO is, or NULL if none yet). **Author-ratified: `eco_id`
  is the canonical first-linked ECO; `allow_duplicate=True`
  ECOs are intentionally NOT back-referenced.** §5 pins this.
- Backfill: NO automatic backfill in R1. `eco_id` stays NULL
  for pre-migration incidents; the service falls back to the
  substring scan only when `eco_id IS NULL`
  (belt-and-suspenders for the transition). A one-shot backfill
  script is a separate later opt-in.

### 2b — Separate `meta_breakage_eco_creations` audit/lock table

**Shape.** New table with `(incident_id, reference)` as a
composite UNIQUE key. The service method INSERTs into it
before calling `create_eco`; on integrity-error, it queries the
existing row and returns the linked ECO.

**Pro.** Doesn't touch `BreakageIncident`. Schema change is
isolated to a new table.

**Con.** Adds a new table that the rest of the system has to
know about. Makes "what's the loopback ECO for incident X?"
require a JOIN rather than a single-column lookup on
`BreakageIncident`. Audit-table proliferation drift.

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
eco_id = Column(String, nullable=True, unique=True, index=True)
```

**Bare `String`, NO `ForeignKey`** — consistent with
`BreakageIncident`'s existing soft-link columns
`product_item_id` / `bom_id` / `version_id`
(`parallel_tasks.py:173–177`), all of which are
`Column(String, nullable=True, index=True)` with no FK. The
`unique=True` mirrors the `incident_code` precedent
(`parallel_tasks.py:172`, also a bare `String` with
`unique=True`, no FK). Dropping the FK:

- Sidesteps P2 (the tenant-baseline FK-ordering problem — see
  §4.3): `meta_breakage_incidents` is created early in the
  baseline, `meta_ecos` late; an inline cross-table FK in the
  early `create_table` would fail on Postgres fresh-tenant.
- Loses `ON DELETE SET NULL`. Acceptable: the read path
  `self.session.get(ECO, incident.eco_id)` returns `None` for
  a since-deleted ECO, so a dangling `eco_id` degrades to "no
  link" — and a §5 test pins this graceful-degradation
  behavior.

No `relationship()` back-reference in R1 — the bare column
suffices for the dedupe lookup; a relationship ripples into
more code surface than this taskbook authorizes.

### 4.2 Alembic migration

`migrations/versions/<new_rev>_add_breakage_design_loopback_eco_id.py`,
new revision after `aa1b2c3d4e7b0`. Follows the
`aa1b2c3d4e7b0` template exactly (defensive idempotent
`upgrade()` + mirror `downgrade()`):

- `_TABLE = "meta_breakage_incidents"`
- `_NEW_COLUMN = "eco_id"`
- `_NEW_UNIQUE = "uq_meta_breakage_incidents_eco_id"` (a UNIQUE
  index — serves as both the uniqueness guard and the lookup
  index; no separate regular index needed).
- `upgrade()`: inspector + existence checks →
  `op.add_column(sa.Column("eco_id", sa.String(),
  nullable=True))` (no `ForeignKey`) →
  `op.create_index(op.f(_NEW_UNIQUE), _TABLE, ["eco_id"],
  unique=True)`.
- `downgrade()`: drop the UNIQUE index → drop the column, each
  guarded.

**Offline-mode caveat inherited from `aa1b2c3d4e7b0`'s
`sa.inspect(bind)` pattern** — `alembic upgrade head --sql`
will fail repo-wide; live-DB upgrade is the verification gate.

### 4.3 Tenant baseline update + the P2 FK-ordering resolution

**P2 (review round 1):** `meta_breakage_incidents` is created
at `migrations_tenant/versions/t1_initial_tenant_baseline.py:77`;
`meta_ecos` is created later at the same file's line 1339. If
the impl PR put an inline `ForeignKey("meta_ecos.id")` into the
early `create_table`, Postgres fresh-tenant provisioning would
fail (referenced table doesn't exist yet). SQLite (test env)
is lax about FK ordering, so the test suite would NOT catch the
break — exactly the kind of prod-only failure §4.8/[[feedback-runtime-pr-semantics]]
warns about.

**Resolution: no FK at all (§4.1).** Because `eco_id` is a bare
`String` column with no `ForeignKey`, there is no
table-ordering constraint. The baseline update is simply:

- Add `sa.Column("eco_id", sa.String(), nullable=True)` to the
  `meta_breakage_incidents` `op.create_table(...)` call (line
  77 region).
- Add one `op.create_index(op.f("uq_meta_breakage_incidents_eco_id"),
  "meta_breakage_incidents", ["eco_id"], unique=True)` after
  that table's other index creations.

This keeps fresh-tenant schema in lockstep with the model +
the alembic migration with **zero table-ordering risk**. A
drift test (§5
`test_tenant_baseline_includes_breakage_eco_id_column`) pins
that the baseline declares the column so model/migration/
baseline can't silently diverge. The version-lock precedent
(`document_version_id` / `version_lock_source` at baseline
lines 473/475) is the exact pattern — those were also bare
`sa.String()` columns with no FK, added to an early
`create_table`, with no ordering problem.

### 4.4 Service method changes

`src/yuantus/meta_engine/services/parallel_tasks_service.py`:

- **`_find_breakage_design_loopback_eco_by_reference`** —
  REWRITTEN to use the durable `eco_id`-column lookup as the
  primary path with substring-scan fallback for pre-migration
  data:

  ```python
  def _find_breakage_design_loopback_eco_by_reference(
      self, reference, *, incident_id=None,
  ):
      # Primary: durable eco_id-column lookup if we know the
      # incident. (Bare String column, no FK — see §4.1.)
      if incident_id:
          incident = self.session.get(BreakageIncident, incident_id)
          if incident is not None and incident.eco_id:
              return self.session.get(ECO, incident.eco_id)
      # Fallback: substring scan for pre-migration data
      # (incidents whose `eco_id` is NULL but whose ECO was
      # created before the migration). The fallback path is
      # NOT race-safe — only the CAS link path is.
      # ...existing substring-scan body unchanged...
  ```

  The new optional `incident_id` keyword is the only signature
  change. `allow_duplicate=True` callers may pass
  `incident_id=None` to skip the eco_id lookup entirely.

- **`create_breakage_design_loopback_eco`** — link via
  compare-and-swap AFTER `ECOService.create_eco` returns:

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

  if allow_duplicate:
      # Explicit detached duplicate — do NOT attempt the CAS
      # link. eco_id stays whatever it was (NULL or the
      # canonical first ECO). Author-ratified semantics.
      return BreakageDesignLoopbackEcoCreation(..., created=True, ...)

  # Compare-and-swap link. A single atomic conditional UPDATE
  # is the concurrency sync point — NOT the unique index.
  result = self.session.execute(
      sa.update(BreakageIncident)
      .where(
          BreakageIncident.id == incident_id,
          BreakageIncident.eco_id.is_(None),
      )
      .values(eco_id=eco.id)
  )
  if result.rowcount == 1:
      self.session.flush()
      return BreakageDesignLoopbackEcoCreation(..., created=True, ...)

  # rowcount == 0 → another transaction already linked an ECO
  # to this incident. We lost the race. Roll back our own
  # create_eco (undone because ECOService.create_eco has no
  # internal commit — verified: no `session.commit` anywhere in
  # eco_service.py), re-read the winner's link, return it.
  self.session.rollback()
  incident = self.session.get(BreakageIncident, incident_id)
  winner = (
      self.session.get(ECO, incident.eco_id)
      if incident is not None and incident.eco_id
      else None
  )
  return BreakageDesignLoopbackEcoCreation(
      ..., eco=winner, created=False, ...
  )
  ```

### 4.5 Race semantics (compare-and-swap, NOT UNIQUE-driven)

Two concurrent transactions both calling
`create_breakage_design_loopback_eco(incident_id=X,
allow_duplicate=False)`:

1. Both pass the pre-create dedupe check (neither sees a
   linked ECO yet).
2. Both flush their own `ECOService.create_eco` inside their
   own transactions (no constraint on `meta_ecos` — both ECO
   inserts are valid; A ≠ B).
3. Both issue
   `UPDATE meta_breakage_incidents SET eco_id = :eco
    WHERE id = X AND eco_id IS NULL`.

   **Serialization mechanism (precise):** the two requests run
   in **separate SQLAlchemy sessions** — `get_db()`
   (`database.py:238`) does `db = SessionLocal()` then
   `yield db` / `finally: db.close()`, and FastAPI invokes the
   dependency once per request, so two concurrent requests get
   two Sessions = two transactions. When the winner's UPDATE
   matches the row, **Postgres takes a row-level write lock**
   (SQLite takes a whole-DB write lock — even stronger). The
   loser's UPDATE on the same row **blocks behind the winner's
   uncommitted write** until the winner's caller commits or
   rolls back (the service method itself never commits — the
   route/caller owns the boundary, see §4.6). After the winner
   commits, the loser's UPDATE re-evaluates its `WHERE
   eco_id IS NULL` against the post-commit state, finds
   `eco_id` already set, and matches zero rows → `rowcount
   == 0`. It is NOT "both evaluate the WHERE in parallel and
   one happens to win" — the write lock is the serialization
   point. Standard atomic-conditional-update idiom; holds on
   Postgres + SQLite.
4. The `rowcount == 0` loser `session.rollback()`s. Because
   the loser is in its own per-request session (confirmed
   above), the rollback undoes ONLY the loser's transaction:
   its `eco_id` UPDATE attempt AND its `ECOService.create_eco`
   INSERT (verified: `ECOService.create_eco` has NO
   `session.commit` anywhere in `eco_service.py`, so its
   INSERT is entirely inside the loser's transaction and is
   undone). It does NOT touch the winner's already-committed
   row. The loser then re-reads the winner's
   `incident.eco_id` and returns `created=False` with the
   winning ECO.

**Why NOT the UNIQUE index:** `UNIQUE(eco_id)` only prevents
two *different incidents* pointing at the *same* ECO. It does
**not** stop two transactions writing two *different* ECO ids
to the *same* incident — `A ≠ B`, no uniqueness violation, the
failure mode would be silent last-writer-wins. The UNIQUE
index is retained purely as a data-integrity backstop (catch a
cross-incident double-claim bug loudly); the **CAS UPDATE is
the actual race serialization point**.

**No orphan ECO** appears after a race loss — the loser's
rollback undoes its `create_eco` INSERT (no internal commit in
`ECOService.create_eco`). Race-safe AND cleanup-free, but the
guarantee comes from the conditional UPDATE + the
caller-owned-transaction (no-internal-commit) invariant, NOT
from the unique index.

R1 must implement the CAS + rowcount-branch + rollback +
re-query path for `allow_duplicate=False`. No blind
`incident.eco_id = eco.id` assignment — every link write goes
through the conditional UPDATE.

### 4.6 Route behavior preserved

`POST /breakages/{incident_id}/design-loopback/eco` continues
to return `200 + created:false` on dedupe hits (now driven by
the durable `eco_id` column / CAS path rather than the
substring scan). The route handler is unchanged.

### 4.7 What §3.2 R1 explicitly does NOT do

- No backfill of `BreakageIncident.eco_id` for historical
  incidents whose loopback ECOs were created pre-migration.
  The substring-scan fallback in
  `_find_breakage_design_loopback_eco_by_reference` handles
  those reads transparently; a separate later opt-in can ship
  a one-shot backfill script.
- No `relationship()` back-reference between `BreakageIncident`
  and `ECO` — the bare `eco_id` column alone is enough for the
  dedupe. Adding a relationship would ripple into
  ECOService.eco_repr, cascade semantics, eager-load decisions,
  etc.
- No auto-trigger from `update_status` or helpdesk-sync (§3.3/
  §3.4 separate later opt-ins).
- No event emission for "link wired" (§3.6 separate later opt-in).
- No metric exposure for "loopback ECO link count" (§3.7
  separate later opt-in — though §3.7 author-recommended
  source-data path uses this very column).

### 4.8 DB-engine portability note

Two DB-engine-dependent assumptions, both verified:

1. **Multi-NULL UNIQUE** (the `unique=True` data-integrity
   backstop): a `nullable=True, unique=True` column allows
   multiple NULL rows (so pre-migration incidents stay NULL)
   but blocks two non-NULL rows sharing an `eco_id`. Postgres
   + SQLite both treat NULL as "unknown" under UNIQUE, so
   multiple NULLs are allowed. The `incident_code` precedent
   (`parallel_tasks.py:172`, bare `String`, `unique=True`, no
   FK) relies on the same semantic in production today. NOT
   portable to MySQL/MSSQL without a partial index.
2. **Atomic conditional UPDATE** (the §4.5 CAS sync point):
   `UPDATE ... SET eco_id=:e WHERE id=:i AND eco_id IS NULL`
   evaluated under the row write lock so exactly one concurrent
   caller gets `rowcount==1`. Holds on Postgres (row-level
   write lock) and SQLite (whole-DB write lock — even stronger
   serialization). This is the actual race-safety mechanism;
   assumption (1)'s UNIQUE index is only the cross-incident
   backstop.

`src/yuantus/database.py:6` declares Postgres support;
`database.py:132–146` adds SQLite-derived URLs as the dev/test
default. R1 explicitly assumes the existing Postgres+SQLite
invariant for both; a future MySQL/MSSQL backend would need a
different uniqueness + serialization strategy and is out of
scope.

## 5. Tests Required (in the later impl PR)

### MANDATORY exactly-named tests

- **`test_breakage_eco_id_column_schema_pinned`** — drift
  guard on the schema. Asserts the SQLAlchemy `Column` on
  `BreakageIncident.eco_id` declares `nullable=True,
  unique=True, index=True` AND has **NO `ForeignKey`** (the
  column is a bare soft-link, consistent with
  `product_item_id`/`bom_id`/`version_id`). If a future change
  adds an FK (re-introducing the P2 baseline-ordering risk) or
  weakens nullable/unique, the test fails loudly.
- **`test_create_eco_wires_durable_link_on_first_call`** —
  end-to-end on SQLite: `create_breakage_design_loopback_eco`
  for an eligible incident sets `incident.eco_id` to the
  created ECO's id via the CAS UPDATE (`rowcount == 1`);
  second call with `allow_duplicate=False` returns the same
  ECO via the `eco_id`-column lookup (patch the substring scan
  and assert it is NOT called on the second call).
- **`test_create_eco_compare_and_swap_serializes_concurrent_link`** —
  the race test (rewritten — does NOT expect a UNIQUE
  IntegrityError). Two sessions both reach the
  `allow_duplicate=False` link step for the same incident.
  Commit the first (its CAS UPDATE returns `rowcount == 1`).
  The second's CAS UPDATE
  (`WHERE id=:i AND eco_id IS NULL`) returns `rowcount == 0`
  because the winner already set `eco_id`; verify the loser
  branch rolls back its `create_eco`, re-reads, and returns
  `created=False` with the winning ECO. Assert NO second ECO
  is committed (loser's INSERT rolled back). Assert the
  incident still has exactly the winner's `eco_id`.
- **`test_allow_duplicate_true_preserves_first_eco_id_and_creates_unlinked_duplicate`** —
  the author-ratified `allow_duplicate=True` semantic: first
  call wires `eco_id=A` via CAS; second call with
  `allow_duplicate=True` creates `ECO B`, does NOT attempt the
  CAS, leaves `eco_id` == A; `B.id != incident.eco_id`.
- **`test_substring_scan_fallback_handles_historical_incidents`** —
  pre-migration incident with `eco_id=NULL` but an existing
  ECO carrying the closeout envelope. The find method returns
  the historical ECO via substring scan; subsequent call still
  returns it (no new ECO created).
- **`test_dangling_eco_id_degrades_to_no_link`** — the
  no-FK graceful-degradation pin (replaces the old
  ondelete=SET NULL test, which no longer applies since there
  is no FK). Set `incident.eco_id` to an ECO id, hard-delete
  that ECO row, then call the find method: it returns `None`
  (`self.session.get(ECO, dangling_id)` → None) and a
  subsequent `create_breakage_design_loopback_eco` proceeds to
  create a fresh ECO rather than crashing on the dangling id.

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

- **§3 alternative choice**: 2a (bare `eco_id` column + CAS
  link, UNIQUE only as a cross-incident backstop) vs. 2b
  (audit/lock table with `incident_id` UNIQUE as the sync
  point) vs. 2c (advisory lock). Author recommends 2a (now
  corrected — see the §3 P1 CORRECTION block); reviewer
  ratifies. **Note:** the round-1 review correctly rejected
  the original "UNIQUE(eco_id) serializes same-incident
  links" claim; §3/§4.5 are rewritten so the CAS UPDATE is
  the sync point, not the unique index.
- **§4.5 race semantics**: confirm the CAS
  (`UPDATE ... WHERE id=:i AND eco_id IS NULL` → `rowcount`
  branch → loser rolls back + re-queries) is the right shape.
  Alternative: `SELECT FOR UPDATE` on the incident row before
  find-then-create (closer to 2c; rejected for SQLite-test-env
  divergence). Confirm the loser's `session.rollback()`
  correctly undoes its `ECOService.create_eco` INSERT (relies
  on the verified no-internal-commit invariant in
  `eco_service.py`).
- **§4.8 portability assumptions** (two): (1) multi-NULL
  UNIQUE backstop and (2) atomic conditional UPDATE both hold
  on Postgres+SQLite. Confirm Postgres+SQLite is the
  supported-DB set going forward; a MySQL/MSSQL backend would
  need a different uniqueness + serialization strategy.
- **P2 resolution (§4.3)**: confirm the no-FK decision is the
  right way to sidestep the baseline table-ordering problem
  (vs. reordering baseline `create_table` calls or
  `op.create_foreign_key` after `meta_ecos`). The no-FK
  approach matches the table's existing soft-link convention
  and the version-lock baseline precedent.
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
