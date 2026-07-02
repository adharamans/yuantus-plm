# P0-8a Method Sandbox — Design & Verification

**Date:** 2026-07-02  
**Scope:** P0-8a only, following `p0-8a-method-sandbox-taskbook-20260702.md`.

## Landed Design

This slice removes the four Method execution bypasses called out by the taskbook:

- `business_logic/executor.py` script execution now routes through `run_script`.
- `services/method_service.py` script execution now routes through `run_script`.
- `business_logic/executor.py` module execution now routes through `run_module`.
- `services/method_service.py` module execution now routes through `run_module`.

Scripts are compiled with RestrictedPython and run with safe builtins, guard hooks,
a script-size cap, and an opcode-level wall-clock watchdog. Module hooks are not
RestrictedPython-sandboxed; they are fail-closed behind `METHOD_MODULE_ALLOWLIST`
because imported Python code has full process privileges.

The RPC path is also fail-closed by default: `METHOD_RPC_ENABLED=false` refuses
`Method.run`, and enabling it still requires an admin/superuser role. This slice
does not fix the broader RPC identity-default defect; it closes Method execution
while that separate surface remains gated.

## Settings

- `YUANTUS_METHOD_SCRIPT_TIMEOUT_SECONDS` — default `5.0`.
- `YUANTUS_METHOD_SCRIPT_MAX_BYTES` — default `100000`.
- `YUANTUS_METHOD_MODULE_ALLOWLIST` — default empty, refusing all module hooks.
- `YUANTUS_METHOD_RPC_ENABLED` — default `false`.

## Security Notes

- The script sandbox blocks imports, filesystem access, dunder traversal, unsafe
  builtins, and guard-hook replacement through caller-provided context keys.
- Scripts still receive live objects such as `session` and `item` when the caller
  provides them. The sandbox contains OS/filesystem/network/interpreter escapes;
  it is not a substitute for authorizing who can create or trigger Method rows.
- Module hooks remain privileged by design. The allowlist is the containment
  boundary and must be kept narrow.

## Verification

Local verification on this branch:

```text
.venv-wp13/bin/python -m pytest src/yuantus/meta_engine/tests/test_method_sandbox.py -q
25 passed in 0.79s
```

The test suite covers:

- import / open / dunder escapes rejected through the real executor/service seams;
- context-provided `__builtins__` cannot replace RestrictedPython builtins;
- benign script mutation and `result` return semantics preserved;
- print support does not break scripts;
- RPC disabled-by-default and admin-only behavior;
- missing-method compatibility behavior;
- module allowlist fail-closed and exact/dotted-prefix matching;
- static bypass guards for raw `exec`, `eval`, and `importlib.import_module`;
- script timeout and size cap;
- success and violation audit emission;
- settings defaults and runtime-error chaining.

CI wiring adds `test_method_sandbox.py` to the contracts job and change detection
routes Method sandbox changes into contracts.
