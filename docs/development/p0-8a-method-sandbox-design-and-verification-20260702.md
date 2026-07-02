# P0-8a Method Sandbox — Design & Verification

**Date:** 2026-07-02
**Scope:** P0-8a only, per `p0-8a-method-sandbox-taskbook-20260702.md` (#943, merged).
**Branch:** `feat/p0-8a-method-sandbox`.

## 1. Landed design

This slice removes the four Method execution bypasses the taskbook called out and adds a
single shared adapter `meta_engine/business_logic/sandbox.py` that every path routes through:

| Surface | Before | After |
|---|---|---|
| S1 hook script (`executor.py:63`) | raw `exec` | `sandbox.run_script` |
| S2 hook module (`executor.py:79`) | unrestricted `importlib` + silent `print` on ImportError | `sandbox.run_module` (fail-closed allowlist) |
| S3 RPC script (`method_service.py:65`) | raw `exec` | `sandbox.run_script` |
| S4 RPC module (`method_service.py:75`) | unrestricted `importlib` | `sandbox.run_module` (fail-closed allowlist) |

- **Scripts** compile under RestrictedPython 8.3 (compile-time rejection of `import`, dunder
  attribute access, `exec`/`eval`) and run with `safe_builtins` (no `open`/`getattr`/`__import__`),
  guard hooks (`safer_getattr`, guarded getitem/write/iter/inplace, `PrintCollector`), a code-size
  cap, and a best-effort wall-clock watchdog (see §3). A reserved-scope-key guard stops
  attacker-supplied context (e.g. an RPC `context` dict) from replacing the guard hooks.
- **Modules are NOT RestrictedPython-sandboxed** — imported Python runs with full process
  privileges. Containment is the fail-closed `METHOD_MODULE_ALLOWLIST` (empty ⇒ all module
  execution refused; exact-or-dotted-prefix match so `plm.hooks` does not admit `plm.hooks_evil`).
  This is stated plainly because it is a different, weaker containment model than scripts.
- **RPC entry is fail-closed:** `Method.run` is refused unless `METHOD_RPC_ENABLED=true` (default
  false) AND the caller holds an admin/superuser role, raising a 403 `PLMException`. This slice does
  **not** fix the broader `rpc_router` forged-admin default (`PLM_DEV_MODE` true by default) — that
  is a separately-gated adjacent slice; with `METHOD_RPC_ENABLED` off, Method execution stays closed
  while that stands.
- **Audit + metric** on every execution (success / violation / error) via `AuditService.log_action`
  and `yuantus_jobs_total{task_type="method_execute"}`.

## 2. Settings (all declared on `Settings`, `YUANTUS_` prefix)

| Field | Default | Meaning |
|---|---|---|
| `METHOD_SCRIPT_TIMEOUT_SECONDS` | `5.0` | best-effort wall-clock budget (§3); `0` disables |
| `METHOD_SCRIPT_MAX_BYTES` | `100000` | script size cap, refused before compile; `0` disables |
| `METHOD_MODULE_ALLOWLIST` | `""` | comma-separated module prefixes; empty ⇒ all refused |
| `METHOD_RPC_ENABLED` | `false` | allow `Method.run` RPC (still needs admin role) |

Dependency: `RestrictedPython` pinned `>=7.0` (pyproject) / `==8.3` (requirements.lock). CI runs
Python 3.11, where RP 8.3 was validated.

## 3. The watchdog — honest limits (revised after the escape hunt)

The wall-clock watchdog is **best-effort**, not a hard CPU/memory bound. A hard bound needs process
isolation, which the taskbook (D1) deferred because the live `session`/`item` contract cannot cross
a process boundary.

Mechanism: a supervising daemon thread injects a `BaseException`-derived deadline signal
(`_MethodTimeout`) into the executing thread once the deadline passes and **re-injects** until exec
returns. It reliably interrupts:
- ordinary infinite loops (`while True: pass`, with or without large ranges);
- loops using `except Exception` (the signal is a `BaseException`, so it escapes `except Exception`);
- the "outer `try/except BaseException` then a bare loop" pattern (the re-injection catches the
  second loop — the exact exploit the escape hunt found against the earlier design).

It does **NOT** interrupt (documented residual DoS limitations, not containment breaches):
- a single long C-level op (`[0]*(10**9)`, `sum(range(10**12))`) — async injection only fires at
  Python bytecode boundaries;
- a loop that catches `BaseException` / bare `except:` on **every** iteration;
- memory/allocation exhaustion — there is no allocation cap in v1.

These are DoS-shaped and consistent with the threat model: a script already holds a live `session`
(DB-wide power), so the sandbox contains OS/filesystem/network/import access and interpreter escapes
— which hold unconditionally — not a determined attacker's resource use.

The watchdog targets **sync / threadpool** call paths (the Method hook fires inside `add_op`/
`update_op` and the RPC dispatch, both run synchronously under Starlette's threadpool); it is not
intended to run on the asyncio event-loop thread, where injecting an async exception into the loop
would be inappropriate.

One deliberate semantic change: `run_script` wraps **every** script-raised error as a
`MethodSandboxViolation` (the original exception is preserved via `__cause__`), whereas the old RPC
path re-raised the original type. This makes "any Method failure blocks the transaction as a sandbox
violation" uniform across both seams; callers that need the original type read `__cause__`.

## 4. Adversarial escape hunt (6-lens red-team against the real adapter)

A multi-agent hunt attacked the finished adapter through six independent lenses, running real inputs
against the real `run_script`/`run_module`:

| Lens | Verdict |
|---|---|
| attribute-walk & builtins-recovery | contained |
| import & codec tricks | contained |
| scope & guard-hook override | contained |
| generator / decorator / comprehension | contained |
| module allowlist bypass | contained |
| resource-exhaustion & watchdog bypass | **breached (fixed, see below)** |

**The one real finding (now fixed).** The earlier watchdog used a one-shot `sys.settrace` tracer
that raised on the deadline. CPython unsets the trace function whenever a trace callback raises, so a
script wrapping a loop in `try/except BaseException` swallowed the single raise and disarmed the
watchdog — proven airtight by `run_script` returning normally at 1.18s under a 1.0s budget. This was
a real violation of the documented "pure-Python loops are interrupted" claim (not the C-level
limitation).

**Fix:** replaced the one-shot tracer with the re-injecting supervising-thread mechanism in §3, and
corrected the over-claim in the module docstring, the settings description, and this document. The
exact exploit is now interrupted at the budget (regression-locked by
`test_13b_watchdog_reinjects_and_survives_swallowed_exception`), and an `except Exception` loop is
interrupted too (`test_13c`). The residual `except BaseException`-every-iteration and memory cases
are documented, not claimed contained.

All other lenses reported zero breach attempts; the containment goals for
OS/filesystem/network/import/interpreter-escape held across every attack.

## 5. Verification (commands actually run on this branch)

```text
$ .venv-wp13/bin/python -m pytest src/yuantus/meta_engine/tests/test_method_sandbox.py -q
27 passed in 1.62s

$ YUANTUS_PYTEST_DB=1 .venv-wp13/bin/python -m pytest \
    src/yuantus/meta_engine/operations/tests/test_add_op.py \
    src/yuantus/meta_engine/operations/tests/test_update_op.py -q
7 passed

$ .venv-wp13/bin/python -c "from yuantus.api.app import create_app; create_app()"
import+create_app OK
```

The 27 tests drive the **real** `MethodExecutor` / `MethodService` seams (only the DB `session` is a
MagicMock returning a real `Method`; the sandbox is never stubbed) and cover:

- import / open / dunder-walk escapes rejected through both real seams;
- attacker-supplied `__builtins__` / reserved keys cannot replace the guards;
- benign hook mutation, `print`, and `result` return preserved; missing-method pass-through and
  unknown-name `ValueError` preserved;
- RPC gate: disabled-by-default 403, enabled+non-admin 403, enabled+admin reaches execution;
- module allowlist fail-closed for S2 and S4, dotted-prefix (no sibling leak), missing-entry;
- static bypass guards — no raw `exec`/`eval`/`importlib.import_module` in the two cutover files,
  and `importlib.import_module` exists only in `sandbox.py`;
- **wall-clock watchdog** — plain loop, the swallowed-exception exploit, and the `except Exception`
  loop all interrupted; size cap; audit success+violation emission; settings defaults; runtime-error
  chaining;
- **real wiring** — `add_op.execute` → real `MethodExecutor` → sandbox blocks a hostile onBeforeAdd
  hook.

## 6. CI wiring

- `test_method_sandbox.py` added to the ci.yml "Contract checks" explicit pytest list (CI runs no
  bare full-suite pytest, so an allowlisted-only test would otherwise never run) and to the
  `conftest.py` no-DB allowlist.
- A dedicated detect-changes branch routes changes to `sandbox.py` / `executor.py` /
  `method_service.py` / `test_method_sandbox.py` into the contracts job; `pyproject.toml` +
  `requirements.lock` changes already trigger it.

## 7. Deliberately out of scope (adjacent, separately gated)

RPC-wide identity overhaul (`PLM_DEV_MODE` default-true + `lambda: None` user dependency); durable
`method_execution_audit` table; least-privilege `plm` facade replacing the raw `session`; process
isolation / hard CPU+memory bounds; P0-8b inbound rate limiting (next lane-A PR).
