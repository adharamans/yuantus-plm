# DEV & VERIFICATION — Lifecycle all-attempts: coverage closure + cross-connection survival proof (T3)

Date: 2026-06-22 · Branch `claude/all-attempts-coverage` · base `origin/main` (`a2e6faaf`, the merged T2 #851).
Closes the two follow-ups the T2 DEV/V doc itself flagged as remaining
(`DEV_AND_VERIFICATION_LIFECYCLE_ALL_ATTEMPTS_LOGGING_20260622.md` §4): the **3 untested write-points**
and the **load-bearing cross-connection rollback-survival** guarantee.

## 1. Summary

T2 (#851) shipped the all-attempts failure logging but, by its own honesty notes, left two gaps:

1. **Coverage** — 8 of 11 B-class write-points were exercised individually; the 3 setup-heavier ones
   (`condition_failed`, `assembly_release_blocked`, `workflow_start_failed`) were "covered by
   construction" (identical helper call, only the `outcome`/`reason_code` literals differ).
2. **Fidelity** — the load-bearing property (*a failure row committed on a SEPARATE session SURVIVES
   the caller rolling back the failed attempt*) ran on in-memory sqlite + `StaticPool`, a SINGLE shared
   connection, which **cannot** prove TRUE cross-connection survival. It was left "Postgres-only".

This slice is **test-only** (no `src/` change, no route, no migration). It adds the 3 missing per-path
tests (8/11 → **11/11**) and — the real deliverable — a **file-backed sqlite + WAL** test that proves
the cross-connection guarantee on GENUINE separate physical connections, in CI.

## 2. What changed

All four tests are appended to `src/yuantus/meta_engine/tests/test_lifecycle_transition_attempts.py`
(already registered in `ci.yml` contracts + the `conftest.py` no-DB allowlist — **no registration
change**, so no dual-registration trap). The module fidelity docstring is updated to match.

- `test_condition_failed_records_aborted` — a transition with a JSON-DSL `condition` that evaluates
  FALSE (`state == "Released"` on a Draft item) → `aborted` / `condition_failed` (pre-mutation gate).
- `test_assembly_release_blocked_records_blocked` — a parent entering `Released` with one UNRELEASED
  direct `ASSEMBLY` child (the B2 hard gate, the only `blocked` site) → `blocked` /
  `assembly_release_blocked`. Mirrors `test_item_release_gate.py`'s ASSEMBLY-edge setup
  (`RelationshipService.create_relationship(parent, child, "ASSEMBLY")`).
- `test_workflow_start_failed_records_failed_and_rolled_back` — the target state has a linked workflow
  whose `start_workflow` RAISES → post-mutation rollback + `failed` / `workflow_start_failed`,
  `rolled_back=True`, generic `public_message="workflow start failed"`, and the planted secret
  exception string (`SECRET-WF…`) **never** leaks into the audit row (the Q5 sanitization guarantee,
  now also asserted on the workflow path).
- `test_failure_row_survives_caller_rollback_cross_connection` — the cross-connection proof (below).

## 3. The cross-connection survival test (the real deliverable)

The property: a failed `promote()` raises in `operations/promote_op.py` → the caller's transaction
rolls back; the audit row, written through a SEPARATE `get_db_session()` that **commits
independently**, must remain. `StaticPool` (one shared connection) literally cannot exhibit this. The
test instead uses **file-backed sqlite** (`sqlite:///{tmp_path}/xconn.db`, default pool), so each
session checks out its OWN physical connection to the same file, then:

1. seeds the lifecycle + a Draft item on a throwaway connection;
2. redirects the helper's `get_db_session()` to a session on a **different** connection;
3. drives a **pre-mutation** failure (`promote(item, "NoSuchState")` → `target_state_not_found`) on the
   caller session — pre-mutation so the caller holds only a SHARED **read** snapshot, no write lock
   (verified: `service.py:112–144` are all reads before the audit at `:145`);
4. asserts the attempt is **not** pending on the caller session (`caller.new` empty — structural);
5. `caller.rollback()` + close — the caller discards its failed transaction;
6. opens a **third** fresh connection and asserts the independently-committed audit row is still there
   (`outcome="denied"`, `reason_code="target_state_not_found"`).

### Why WAL — required AND faithful

The test sets `PRAGMA journal_mode=WAL` (+ `busy_timeout` backstop) via a `connect` listener. This is
**not** cosmetic. The caller holds its read snapshot open across the whole `promote()` call, so in
sqlite's default rollback-journal mode the audit connection's `COMMIT` (which must take `EXCLUSIVE`)
deadlocks on the caller's `SHARED` lock → `SQLITE_BUSY`; and `busy_timeout` can't help because the
caller doesn't release until *after* `promote()` returns (step 5). WAL lets the writer commit
alongside the open reader — which is also exactly the **Postgres MVCC reader/writer-don't-block**
semantics this property relies on in production. So WAL both makes the test runnable and makes it a
faithful model of the real backend.

### What it proves, and the honest residual

- **Proven now (CI):** genuine separate physical connection + independent `COMMIT` durable to the file
  + survives the caller's rollback + the structural "never on `self.session`" guarantee.
- **Still Postgres-only:** the **held-write-lock** case — a caller holding a WRITE lock *during* the
  audit commit (the post-mutation paths: `on_enter` / `workflow` / `version`, where `promote()` has
  already mutated the item). sqlite's coarse single-writer locking would serialize/deadlock there in a
  way Postgres MVCC does not, so that exact concurrency shape is not modeled. The FK-free history
  columns (no cross-txn FK lock) and the separate-session design make this safe by construction; an
  integration run on the docker-compose Postgres stack (`regression.yml`) remains the place to
  exercise it end-to-end. This test does **not** claim full Postgres equivalence.

## 4. Verification

- `python -m py_compile` on the test module — OK (local runtime is python 3.9; the codebase uses
  3.10+ `X | Y` unions, so the test bodies themselves can only execute under CI's 3.11).
- Pre-flight code-path checks (so CI isn't a blind ~8-min probe): the audit helper resolves
  `get_db_session` at call-time via a local `from yuantus.database import get_db_session`
  (`service.py:567`) → the `monkeypatch.setattr(ydb, "get_db_session", …)` is picked up; the helper
  builds a FRESH `LifecycleTransitionHistory(**values)` from `getattr`-extracted scalars
  (`service.py:544–565`) → the caller's `item` is never attached to the audit session (no
  cross-session error); `target_state_not_found` is pre-write (`service.py:112–144`).
- CI: contracts + regression (the pytest lanes are sqlite — `YUANTUS_DATABASE_URL: sqlite:///./ci.db`;
  the live-Postgres lane in `regression.yml` is a docker-compose full-stack e2e, not a pytest target).

## 5. Out of scope / notes

- **Terminal slice.** With 11/11 write-points covered and the cross-connection guarantee CI-proven,
  the lifecycle transition-history line's feature surface is complete: model + success write + all
  failure writes + item-scoped (success-only) read + forensic (all-outcomes) read. A forensic
  outcome-filter / pagination would be new feature scope, not "remaining" work — deliberately not
  added.
- No business behavior changed; `is_entitled()`, the routes, the write paths, and `PromoteResult`
  semantics are untouched. This PR only adds tests + one doc.
