# P0-8a — Method Script Sandbox — Taskbook (scope-lock)

**Status:** TASKBOOK FOR OWNER RATIFICATION — authorizes no build. The impl PR needs its own
per-slice opt-in after this taskbook is ratified (per-phase opt-in convention; claim row in
`plm-p0-capability-line-living-tracker.md` §2).
**Date:** 2026-07-02 · **Baseline:** Yuantus `origin/main@96fb201b` · **Basis:**
`docs/PLM_CAPABILITY_GAP_ANALYSIS_20260702.md` 主题 12 / P0-8a; owner 拍板 2026-07-02
(8a before 8b, separate PRs; shared adapter over BOTH exec entries).

---

## 1. Purpose

Close the server-side script-execution security debt: today Method content from the DB is run
through **two unsandboxed `exec` paths and two unrestricted `importlib` paths**, and the only
RPC entry runs **as admin by default**. One shared sandbox adapter must become the single place
that executes Method content, with import/builtins restriction, module allowlist, wall-clock
timeout, size caps, and execution audit — switching **both** entries in the same slice so no
bypass remains.

## 2. Current state (grounded — every claim verified against `origin/main@96fb201b`)

### 2.1 The four execution surfaces

| # | Surface | Where | Behavior today |
|---|---|---|---|
| S1 | Script via type hooks | `meta_engine/business_logic/executor.py:63` `exec(code, {}, local_scope)`, scope = live `session`/`item`/`payload` (`:55-60`) | Invoked from `operations/add_op.py:89` (`on_before_add_method_id`) and `operations/update_op.py:82` (`on_after_update_method_id`). Missing method id → silent pass-through (`executor.py:26-28`). Script error → `ValueError` chained, blocks transaction (`:68-70`). |
| S2 | Module via type hooks | `executor.py:79` `importlib.import_module(module_path)` + `module.run(session, item, payload)` | **Any importable module path from DB content.** `ImportError` swallowed with a bare `print` (`:84-86`) — a silent-failure + print diagnostic. Other exceptions re-raised. |
| S3 | Script via RPC | `services/method_service.py:65` `exec(code, {}, local_scope)`, arbitrary `context` dict, returns `local_scope["result"]` | Own docstring: *"Security Note: This uses exec(). In production, this must be sandboxed."* (`:41`). Reached from RPC `Method.run` (`services/engine.py:249-274`), whose comment asks *"Security: Who can run methods via RPC?"* (`:261-264`). |
| S4 | Module via RPC | `method_service.py:75-97` `importlib.import_module(mod_path)` + `getattr(mod, func_name)` + `func(**context)` | **Arbitrary `module:function` from DB content.** |

### 2.2 Entry authorization is broken at the RPC layer (adjacent pre-existing defect)

`meta_engine/web/rpc_router.py`: `DEV_MODE = os.environ.get("PLM_DEV_MODE", "true")` (`:14`) —
**defaults to true**, is read via raw `os.environ` (NOT a declared `Settings` field — the exact
undeclared-env silent trap), and when true forges `identity="admin", roles=["admin","superuser"]`
for **every** RPC request (`:46-50`). The `current_user` dependency is `Depends(lambda: None)`
(`:23`) — it never resolves a user even with DEV_MODE off. Net: any caller holding a valid bearer
token (the `AuthEnforcementMiddleware` layer) can execute `Method.run` **as admin** under default
configuration.

### 2.3 Exposure scoping (why this is still P0-severity but zero-installed-base)

- **No production write path creates Method rows**: the only reference to `meta_methods` outside
  the model itself is the initial migrations (`migrations/versions/f87ce5711ce1_initial_schema.py`,
  `migrations_tenant/versions/t1_initial_tenant_baseline.py`); no router, no RPC handler, no
  seeder authors Methods. Hook fields `on_before_add_method_id`/`on_after_update_method_id` are
  **not settable** through the schema admin API (no hits in `web/`, `schemas/`,
  `meta_schema_service.py`).
- Therefore: exploiting S1–S4 today requires a pre-existing Method row (DB-level write) — but the
  RPC-as-admin default (§2.2) plus any future Method-authoring feature (roadmap 主题 12/七 wants
  git-backed methods) makes the debt compound. **Zero installed base also means we may tighten
  execution semantics now without migration debt.**

### 2.4 Reusable in-repo assets

- `security/safe_evaluator.py` — `SafeExpressionEvaluator`: AST-validated **expression-only**
  evaluator (rejects imports/defs/attribute access, whitelisted functions). Cannot run statement
  scripts, but is the in-repo precedent for AST validation and the style anchor.
- `services/audit_service.py:17` `AuditService.log_action(user_id, action, target_type,
  target_id, details)` — structured `plm_audit` logger (log-only today, by its own comment).
- `observability/metrics.py` — Prometheus counter pattern.
- Stray test `meta_engine/business_logic/test_method_execution.py` sits outside `tests/`;
  absorb/relocate during impl (verify whether any CI enumeration collects it — likely not).

## 3. Design decisions (recommended defaults; owner ratifies via this PR's review)

**D1 — Sandbox mechanism: RestrictedPython (new pinned dependency).**
Options considered: (O1) extend the hand-rolled AST validator to statements — rejected: hand-rolled
statement-level Python sandboxes are notoriously bypassable; (O2) **RestrictedPython**
(compile-time policy + guarded runtime; mature Zope project; pure-Python wheel) — **recommended**;
(O3) subprocess isolation with rlimits — **rejected for 8a**: the execution contract passes live
`session`/`item` ORM objects into scripts (S1 scope, `executor.py:55-60`), which cannot cross a
process boundary; record as a possible future slice for untrusted-tenant scenarios.
Impl notes: `restricted_compile` + safe builtins (no `__import__`), guarded getattr/getitem,
no `exec`/`eval` inside scripts; **verify the pinned version supports the runtime matrix (3.10–3.14)
at impl time** and pin in both `pyproject.toml` (>=) and `requirements.lock` (==).

**D2 — Threat model stated honestly.** With a live DB `session` in scope, an in-process sandbox
cannot contain a determined attacker — the session itself is DB-wide power. 8a's containment
goals are: no OS/filesystem/network/import access, no interpreter escape via dunder walks,
bounded wall-clock, bounded code size, full auditability, and admin-gated entry. Replacing the
raw `session` with a least-privilege `plm` facade is **out of scope** (open question §7).

**D3 — Module allowlist, fail-closed.** New `Settings` field `METHOD_MODULE_ALLOWLIST`
(list[str] of module-path prefixes, default `[]` = **all module execution refused**), enforced in
the shared adapter for BOTH S2 and S4. Non-allowlisted or import-failing module → structured
`MethodSandboxViolation` (chained), **never** a swallowed `print` (removes the `executor.py:84-86`
silent failure; behavior change is safe per §2.3 zero installed base).

**D4 — RPC entry gate, fail-closed.** New `Settings` field `METHOD_RPC_ENABLED` (default
**False**). `rpc_run_method` (`engine.py:249`) refuses when disabled; when enabled it additionally
requires `superuser`/`admin` in the engine roles. This does NOT fix the RPC-wide forged-admin
identity (§2.2) — that is a **separately-recorded adjacent defect** (own slice: real
`get_current_user` dependency + `PLM_DEV_MODE` declared in `Settings` with default False);
with `METHOD_RPC_ENABLED=False` by default, Method execution is closed even while that defect
stands. Hook-path execution (S1/S2) remains system-triggered and is not flag-gated.

**D5 — Timeout semantics (v1 limitation stated).** `METHOD_SCRIPT_TIMEOUT_SECONDS` (default 5):
watchdog aborts the request with a violation error when exceeded. CPython cannot safely preempt a
hostile busy-loop thread; SIGALRM is main-thread-only under uvicorn workers. v1 = cooperative
trace/instruction-budget check + wall-clock watchdog; a runaway thread may persist until worker
recycle — documented limitation, revisit only if real abuse shows up (subprocess isolation is the
true fix, rejected in D1 for contract reasons).

**D6 — Audit = structured log + metric; durable table deferred.** Every execution (success,
violation, error) emits `AuditService.log_action(user_id, "method.execute", "Method", method_id,
{name, entry, outcome, duration_ms})` plus a Prometheus counter. A durable
`method_execution_audit` table is deferred (open question §7) — avoids a migration in this slice.

**D7 — No sandbox-off switch.** Deliberately no `SANDBOX_ENABLED=False` escape hatch: the
adapter is the only execution path, and the static guard (§5 test 12) pins that.

## 4. Scope

**In scope:** one new module `meta_engine/business_logic/sandbox.py` (the shared adapter:
`run_script(code, scope, *, timeout_s, size_cap, audit_ctx)` + `run_module(path, entry, args,
allowlist, audit_ctx)` + `MethodSandboxViolation`); cutover of all four surfaces S1–S4 to the
adapter (S1/S3 script → sandboxed compile+exec; S2/S4 module → allowlist + import via adapter);
`rpc_run_method` gate (D4); three new declared `Settings` fields (D3/D4/D5); audit + metric (D6);
tests + CI wiring (§5); removal of the raw `exec`/`importlib` calls and the `print` diagnostic
from `executor.py`/`method_service.py`.

**Out of scope (explicit):** P0-8b rate limiting (next PR); RPC-wide identity/DEV_MODE overhaul
(recorded adjacent defect, own slice); Method authoring API / git-backed methods; per-method ACL;
durable audit table; subprocess isolation; least-privilege `plm` facade replacing raw `session`;
any route addition (route-pin contracts unchanged); any migration (no schema change).

**Preserved semantics (regression-locked):** S1 missing-method pass-through (`executor.py:26-28`);
script error blocks the transaction with a **chained** exception (`:68-70`); S3 unknown method
name → `ValueError` (`method_service.py:27-28`); benign scripts keep receiving
`session`/`item`/`payload` (S1) and `context` (S3) and keep their return conventions (in-place
`item`; `local_scope["result"]`).

## 5. Mandatory tests (every clause below gets its own test; production seam, no fakes —
the hostile inputs must go through the REAL `MethodExecutor.execute_method` /
`MethodService.execute_method`, not a stub of the adapter)

New file `src/yuantus/meta_engine/tests/test_method_sandbox.py` (author DB-free if feasible with
an in-memory session fixture; otherwise mark DB-gated consistently with siblings):

1. `import os` inside script via **real S1** (`MethodExecutor.execute_method` with a Method row +
   hook wiring) → `MethodSandboxViolation`, transaction-blocking error, `__cause__` chained.
2. `open("/etc/passwd")` inside script via **real S3** (`MethodService.execute_method`) → violation.
3. Dunder escape attempt (`().__class__.__mro__` / `__subclasses__` walk) via adapter → violation.
4. Benign script via **real S1 hook path** (item mutated as scripted through `add_op`) — proves the
   sandbox does not break the feature.
5. Benign script via S3 returns `local_scope["result"]` unchanged semantics.
6. `Method.run` RPC: default settings → refused (`METHOD_RPC_ENABLED=False` fail-closed).
7. `Method.run` RPC: enabled + admin roles → executes; enabled + non-admin roles → refused.
   (Two asserts, two tests — split the compound.)
8. Missing method id via S1 → silent pass-through preserved.
9. Unknown method name via S3 → `ValueError` preserved.
10. Module path NOT in `METHOD_MODULE_ALLOWLIST` → fail-closed violation, for **both** S2 and S4
    (parametrized over both seams).
11. Allowlisted module executes, for both S2 and S4 (temp module fixture).
12. **Static bypass guard**: source scan pinning that `executor.py` and `method_service.py`
    contain no `exec(`, `eval(`, or `importlib.import_module(` after cutover and that both import
    the sandbox adapter; repo-wide scan for `exec(` on Method content outside
    `business_logic/sandbox.py` fails the build (S9/S10 static-verifier style).
13. Timeout: busy-loop script exceeding `METHOD_SCRIPT_TIMEOUT_SECONDS` → violation (comment
    documents the D5 runaway-thread limitation).
14. Code-size cap: oversized script refused before compile.
15. Audit: success AND violation each emit a `plm_audit` `log_action` record with
    method id/name/entry/outcome/duration (caplog) and increment the counter.
16. Settings: the three new fields exist on `Settings` with the documented defaults
    (guards the `extra=ignore` undeclared-env trap).
17. Exception chaining: every adapter-raised error carries a non-None `__cause__` when wrapping
    an underlying exception.

**CI wiring (the fan-out rule — a new test file must be registered in EVERY enumeration):**
add the new test file and `sandbox.py` to the ci.yml change-detect alternation (`.github/workflows/ci.yml`
:147 region) AND the explicit pytest target list (:268–:292 region); add to `conftest.py`
`_ALLOWLIST_NO_DB` (`conftest.py:9`) iff authored DB-free; then sweep
`grep -rn 'glob("test_' 'CI_PORTFOLIO' 'ALLOWLIST'` enumerations before pushing
(cost precedent: #678 portfolio-equality failure).

## 6. Verification (impl PR must show)

- `YUANTUS_PYTEST_DB=1 pytest -q src/yuantus/meta_engine/tests/test_method_sandbox.py` green
  (or DB-free equivalent), plus the ci.yml explicit-list invocation locally.
- Full targeted regression: the existing hook-path tests and RPC tests that touch
  `add_op`/`update_op`/`engine.py` stay green.
- `git diff --check` clean; no route-count change (`test_metrics_router_route_count_delta.py`
  and route-pin contracts untouched and green); Alembic heads unchanged (single head).
- Evidence in the impl PR description: before/after grep of `exec(`/`import_module(` in the two
  cutover files.

## 7. Open questions (owner input, non-blocking for ratification)

1. Durable `method_execution_audit` table (D6 defers) — want it in a follow-up slice?
2. Least-privilege `plm` facade instead of raw `session` in script scope (D2) — worth doing while
   installed base is still zero?
3. RPC identity overhaul slice (D4 adjacent defect: `PLM_DEV_MODE` default-true + `lambda: None`
   user dependency) — schedule as its own P0 item?
4. RestrictedPython version pin vs the 3.10–3.14 support matrix — confirm at impl and record in
   `requirements.lock`.

## 8. References

- Gap report basis: `docs/PLM_CAPABILITY_GAP_ANALYSIS_20260702.md` (主题 12, P0 表 8a, 修订记录).
- Claim row: `docs/development/plm-p0-capability-line-living-tracker.md` §2.
- Grounded sources: `meta_engine/business_logic/executor.py` (:26-28, :55-70, :79-91),
  `meta_engine/services/method_service.py` (:27-28, :37-73, :75-97),
  `meta_engine/services/engine.py` (:45, :249-274), `meta_engine/web/rpc_router.py` (:14, :23,
  :46-50), `meta_engine/operations/add_op.py` (:88-91), `operations/update_op.py` (:79-83),
  `security/safe_evaluator.py`, `services/audit_service.py` (:17),
  `business_logic/models.py` (Method / `meta_methods`), initial migrations (both trees).
