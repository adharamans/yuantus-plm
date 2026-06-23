# DEV & VERIFICATION — Lifecycle all-attempts (failure) transition logging (T2)

Date: 2026-06-22 · Branch `claude/all-attempts-impl` · base `origin/main`.
Implements the **T1 taskbook** (`docs/development/lifecycle-transition-history-all-attempts-taskbook-20260622.md`,
#846): `promote()` now records **failed / denied / blocked / aborted** attempts, not just successes,
using the reserved `outcome` column.

## 1. Summary

The success write was same-session (`begin_nested` savepoint) — correct, because a successful
promote **commits**. Failures are the opposite: `operations/promote_op.py:56–57` **raises**
`ValidationError` on a failed `PromoteResult`, so the AML apply transaction **never commits** — a
same-session failure row would vanish. T2 therefore writes failure rows through a **separate,
independently-committing `get_db_session()`** that survives the caller's rollback. The item-scoped
read stays **success-only**; failures surface only on the **forensic** (superuser) route.

## 2. What changed (no new route, no migration)

- **`lifecycle/service.py`**
  - `_record_transition_attempt(...)` — the separate-session failure writer: opens
    `get_db_session()` (tenant-aware — binds the tenant schema via its `after_begin`
    `SET LOCAL search_path` listener), `add` + `commit`. Best-effort: never touches / flushes /
    commits `self.session`; never raises; never changes the `PromoteResult`. No tenant context →
    `get_db_session` raises → swallowed (no row, promote unaffected). Records `outcome` +
    `properties.reason_code` + an optional **bounded** `public_message`/`rolled_back` — **never** a
    raw exception.
  - **11 write-points** at the B-class failure returns in `promote()`: `target_state_not_found`,
    `transition_missing`, `actor_missing`, `permission_denied` (`denied`); `assembly_release_blocked`
    (`blocked`); `before/on_exit/on_enter` aborts + `condition_failed` (`aborted`);
    `workflow_start_failed`, `version_release_failed` (`failed`). The 3 post-mutation paths record
    `rolled_back=True` after restoring state. A-class config errors (no map / current state missing)
    are **not** recorded.
  - `get_transition_history(..., success_only=False)` — new filter.
- **`web/lifecycle_transition_history_router.py`** — the item-scoped read passes `success_only=True`
  (must not leak denials); the forensic route passes `success_only=False` (all outcomes).
- Route count unchanged; the `outcome` / `properties` columns already exist (no migration).

## 3. Design notes

- **The asymmetry is the load-bearing point** (verified: `promote_op.py:56–57` raises). Successes
  commit → same-session is fine; failures roll back → they MUST use a separate, independently
  committing session, or nothing is recorded on the main AML path.
- **Tenant schema correctness** — `get_db_session` is tenant-aware, so the independent write lands in
  the same tenant schema as the success rows.
- **No cross-session FK lock** — every reference column (`item_id` / `from_state_id` / `to_state_id`
  / `transition_id` / `lifecycle_map_id`) is plain `String` (FK-free), so the independent INSERT can
  never block on an FK the caller's uncommitted transaction is holding.
- **No raw exception in the audit (Q5)** — the `workflow`/`version` paths embed `str(e)` in
  `PromoteResult.error`, but the audit call passes a *generic* `public_message` ("workflow start
  failed" / "version release failed"); by construction the helper never receives the raw exception.

## 4. Verification

`test_lifecycle_transition_attempts.py`:
- **Per-path** — `target_state_not_found`→`denied`, `transition_missing`→`denied`,
  before/on_exit/on_enter aborts→`aborted` (on_enter asserts `rolled_back`), `version_release_failed`
  →`failed`. The version test also asserts a planted secret exception string **never** appears in the
  audit row (the Q5 guarantee).
- **Structural (the honest rollback proof)** — the attempt row is written via `get_db_session()` and
  is **not** queued on `self.session` (`s.new` empty); after `self.session.rollback()` the
  independently-committed row **remains**.
- **Best-effort** — a raising `get_db_session` never changes the `PromoteResult` or raises.
- **Read filter** — `success_only=True` excludes non-success; `success_only=False` returns all.
- The Slice-1 `test_rolled_back_transition_writes_no_row` is reconciled (no-ops the audit session so
  it stays focused on the caller-session invariant).

**Fidelity note (important, per review):** these tests run on **in-memory sqlite + `StaticPool`** (a
single shared connection), so they **cannot** prove TRUE cross-connection rollback-survival — that is
**Postgres-only / integration-verified**. What they prove faithfully is the *structural* guarantee
(failure rows ride the separate session, never `self.session`), the per-path outcomes, best-effort,
the read filter, and the no-`str(e)` guarantee. CI: contracts + regression (can't run locally —
system python 3.9 vs codebase 3.10+).

**Coverage honesty:** 8 of the 11 write-points are exercised individually — both pre-transition
`denied` paths, both **role-gate** `denied` paths (`actor_missing`, `permission_denied`), the three
hook aborts, and `version_release_failed` — covering `denied` / `aborted` / `failed` + `rolled_back`
+ the sanitization. The other 3 (`assembly_release_blocked` [the only `blocked` site, needs BOM
children], `condition_failed`, `workflow_start_failed`) use the **identical** helper call (only the
`outcome`/`reason_code` strings differ) and are covered by construction; the BOM/workflow-setup ones
are recommended for an integration pass.

## 5. Out of scope / notes

- **Connection churn** — each failed promote opens+commits a second connection (fine for an audit
  path; a denial-probing loop generates churn — noted for future rate-awareness).
- The forensic read surface itself is unchanged in shape (it already returned all rows; it now
  simply also carries non-success rows). `is_entitled()` and the write business behavior are
  untouched.
- **CI / main-health note (at PR time):** this PR's `contracts` inherited a **recurring ECM-line
  main-health gap** (a separate development line, merged red — the repo has no required checks): the
  ECM line keeps merging `DEV_AND_VERIFICATION` docs *without indexing them*, red-ing the shared
  doc-index completeness gate for **every** doc-touching PR. The currently-unindexed ECM docs
  (`ECM_PUBLISH_A1_DISPOSITION`, `ECM_PUBLISH_LINE`) are folded into the index here as a one-line
  main-health fix. (An earlier ECM datetime-format test failure was fixed on the ECM line in the
  interim.) **This PR's own changes are green** — the fold-ins are only to clear the shared gate so
  this PR can; the durable fix is the ECM line indexing its own docs.
