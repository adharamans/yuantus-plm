"""
Shared execution sandbox for server-side Methods (P0-8a).

This module is the *single* place that executes Method content. Both the
type-hook path (``business_logic/executor.py``) and the RPC path
(``services/method_service.py``) route through here so no unsandboxed
``exec``/``importlib`` bypass remains (taskbook
``docs/development/p0-8a-method-sandbox-taskbook-20260702.md``).

Two execution kinds, two containment models:

* **Script** (``run_script``): raw Python text from the DB, compiled with
  RestrictedPython (compile-time rejection of ``import``, dunder attribute
  access, ``exec``/``eval``; runtime safe-builtins with no ``open``/``getattr``/
  ``__import__``), executed with guard hooks, a code-size cap, and a
  best-effort wall-clock watchdog (a supervising thread re-injecting a
  ``BaseException`` deadline signal into the exec thread).

Watchdog limits (honest, best-effort — a hard CPU/memory bound needs process
isolation, deferred because the live ``session``/``item`` contract can't cross
a process boundary): the watchdog reliably interrupts ordinary infinite loops
and loops using ``except Exception``, but it CANNOT interrupt (a) a single long
C-level op (``[0]*(10**9)``, ``sum(range(10**12))``) — async injection only
fires at bytecode boundaries — or (b) a loop that catches ``BaseException`` /
bare ``except:`` on every iteration. There is also no memory/allocation cap in
v1. These are documented residual DoS limitations, not containment breaches of
OS/filesystem/network/import/interpreter-escape (which hold unconditionally).
* **Module** (``run_module``): a ``module:function`` reference from the DB.
  This is **NOT** RestrictedPython-sandboxed — imported code runs with full
  privileges. It is contained by a **fail-closed allowlist** of trusted
  module-path prefixes (``METHOD_MODULE_ALLOWLIST``); an empty allowlist
  refuses all module execution.

Threat model (honest): a script receives a live SQLAlchemy ``session`` in
scope, which is DB-wide power by itself. The sandbox's job is therefore to
contain OS / filesystem / network / import access and interpreter escapes,
bound resources, and make every execution auditable — not to defeat a
determined attacker who can already author a Method row and get it triggered.
"""

from __future__ import annotations

import ctypes
import threading
import time
import warnings
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from RestrictedPython import compile_restricted, safe_builtins
from RestrictedPython.Guards import (
    guarded_iter_unpack_sequence,
    guarded_unpack_sequence,
    safer_getattr,
)
from RestrictedPython.PrintCollector import PrintCollector

from yuantus.config.settings import get_settings
from yuantus.observability import metrics

_AUDIT_ACTION = "method.execute"
_METRIC_TASK = "method_execute"
_RESERVED_SCOPE_KEYS = {
    "__builtins__",
    "_getattr_",
    "_getitem_",
    "_write_",
    "_getiter_",
    "_iter_unpack_sequence_",
    "_unpack_sequence_",
    "_inplacevar_",
    "_print_",
}


class MethodSandboxViolation(Exception):
    """Raised when Method content violates a sandbox policy (blocks the txn)."""


class _MethodTimeout(BaseException):
    """Internal wall-clock signal injected into the exec thread.

    Subclasses ``BaseException`` (not ``Exception``) on purpose so a script's
    ``except Exception`` cannot swallow it — only a catch-all ``except:`` /
    ``except BaseException:`` on every loop iteration can, which is the
    documented residual limitation.
    """


def _exec_with_deadline(
    byte_code: Any,
    glb: Dict[str, Any],
    local_scope: Dict[str, Any],
    timeout_s: Optional[float],
) -> None:
    """Exec restricted bytecode under a re-injecting wall-clock watchdog.

    A supervising daemon thread injects ``_MethodTimeout`` into the executing
    thread once the deadline passes and KEEPS re-injecting until exec returns,
    so a single swallowed raise cannot disarm the watchdog (the failure mode of
    a one-shot ``sys.settrace`` tracer). Async injection only fires at Python
    bytecode boundaries, so a single long C-level op (e.g. ``[0]*(10**9)``) or a
    loop that catches ``BaseException`` every iteration is NOT interruptible —
    the documented residual limitation. Raises ``_MethodTimeout`` on timeout.
    """
    if not timeout_s or timeout_s <= 0:
        exec(byte_code, glb, local_scope)  # noqa: S102 - restricted bytecode only
        return

    target_tid = threading.get_ident()
    stop = threading.Event()
    deadline = time.monotonic() + timeout_s

    def _watch() -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0 and stop.wait(remaining):
            return  # exec finished before the deadline
        while not stop.is_set():
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_long(target_tid), ctypes.py_object(_MethodTimeout)
            )
            if stop.wait(0.005):
                break

    watcher = threading.Thread(target=_watch, daemon=True)
    watcher.start()
    try:
        exec(byte_code, glb, local_scope)  # noqa: S102 - restricted bytecode only
    finally:
        # Stop the watcher, then clear any async exception it may have queued
        # but that has not yet been raised, so it cannot leak into caller code.
        stop.set()
        watcher.join(timeout=1.0)
        ctypes.pythonapi.PyThreadState_SetAsyncExc(
            ctypes.c_long(target_tid), ctypes.c_long(0)
        )


@dataclass
class AuditContext:
    """Identity/target context for one Method execution audit record."""

    user_id: Optional[str] = None
    method_id: Optional[str] = None
    method_name: Optional[str] = None
    kind: str = "script"  # script | module


def _emit_audit(
    session: Any,
    ctx: Optional[AuditContext],
    outcome: str,
    duration_ms: float,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """Best-effort audit + metric for a Method execution; never raises."""
    try:
        metrics.record_job_lifecycle(_METRIC_TASK, outcome, duration_ms)
    except Exception:  # pragma: no cover - metric must never break execution
        pass
    if ctx is None:
        return
    try:
        from yuantus.meta_engine.services.audit_service import AuditService

        details: Dict[str, Any] = {
            "method_name": ctx.method_name,
            "kind": ctx.kind,
            "outcome": outcome,
            "duration_ms": round(duration_ms, 3),
        }
        if extra:
            details.update(extra)
        AuditService(session).log_action(
            user_id=str(ctx.user_id) if ctx.user_id is not None else "system",
            action=_AUDIT_ACTION,
            target_type="Method",
            target_id=str(ctx.method_id) if ctx.method_id is not None else "?",
            details=details,
        )
    except Exception:  # pragma: no cover - audit is best-effort
        pass


def _safe_globals(scope: Dict[str, Any]) -> Dict[str, Any]:
    """Build the RestrictedPython execution globals for a script."""
    _reject_reserved_scope_keys(scope)
    builtins = dict(safe_builtins)
    glb: Dict[str, Any] = {
        "__builtins__": builtins,
        # RestrictedPython guard hooks (see module docstring).
        "_getattr_": safer_getattr,
        "_getitem_": lambda obj, key: obj[key],
        "_write_": lambda obj: obj,
        "_getiter_": iter,
        "_iter_unpack_sequence_": guarded_iter_unpack_sequence,
        "_unpack_sequence_": guarded_unpack_sequence,
        "_inplacevar_": _inplace_var,
        "_print_": PrintCollector,
    }
    glb.update(scope)
    return glb


def _reject_reserved_scope_keys(scope: Dict[str, Any]) -> None:
    """Prevent caller context from replacing RestrictedPython guard hooks."""
    bad = sorted(
        key
        for key in scope
        if key in _RESERVED_SCOPE_KEYS or (key.startswith("_") and key.endswith("_"))
    )
    if bad:
        raise MethodSandboxViolation(
            f"Method script context contains reserved sandbox keys: {', '.join(bad)}"
        )


def _inplace_var(op: str, val: Any, expr: Any) -> Any:
    """Guarded augmented-assignment (item += x etc.) for a small op set."""
    if op == "+=":
        return val + expr
    if op == "-=":
        return val - expr
    if op == "*=":
        return val * expr
    if op == "/=":
        return val / expr
    if op == "//=":
        return val // expr
    if op == "%=":
        return val % expr
    if op == "**=":
        return val ** expr
    raise MethodSandboxViolation(f"Augmented operator '{op}' is not allowed")


def run_script(
    code: str,
    scope: Dict[str, Any],
    *,
    session: Any,
    audit: Optional[AuditContext] = None,
    timeout_s: Optional[float] = None,
    max_bytes: Optional[int] = None,
) -> Dict[str, Any]:
    """Compile + execute restricted script text; return new local bindings.

    ``scope`` entries (e.g. ``session``/``item``/``payload``/``result`` seeds)
    are exposed to the script; mutations to passed objects persist by
    reference. The returned dict contains bindings the script created (read
    ``result`` from it). Raises :class:`MethodSandboxViolation` on any policy
    breach, size-cap breach, timeout, compile error, or runtime error — the
    caller lets that propagate to block the surrounding transaction.
    """
    settings = get_settings()
    if timeout_s is None:
        timeout_s = float(settings.METHOD_SCRIPT_TIMEOUT_SECONDS)
    if max_bytes is None:
        max_bytes = int(settings.METHOD_SCRIPT_MAX_BYTES)

    started = time.monotonic()
    text = code or ""
    if max_bytes and len(text.encode("utf-8", "ignore")) > max_bytes:
        _emit_audit(session, audit, "violation", 0.0, {"reason": "size_cap"})
        raise MethodSandboxViolation(
            f"Method script exceeds size cap ({max_bytes} bytes)"
        )
    try:
        _reject_reserved_scope_keys(scope)
    except MethodSandboxViolation:
        _emit_audit(
            session,
            audit,
            "violation",
            (time.monotonic() - started) * 1000.0,
            {"reason": "reserved_scope"},
        )
        raise

    try:
        with warnings.catch_warnings():
            # RestrictedPython emits a benign SyntaxWarning for scripts that
            # call print() (PrintCollector's 'printed' var is unused). Suppress
            # only that advisory; genuine compile errors still raise SyntaxError.
            warnings.simplefilter("ignore", SyntaxWarning)
            byte_code = compile_restricted(text, "<method>", "exec")
    except SyntaxError as exc:
        _emit_audit(
            session, audit, "violation",
            (time.monotonic() - started) * 1000.0, {"reason": "compile"},
        )
        raise MethodSandboxViolation(
            f"Method script rejected at compile: {exc}"
        ) from exc

    glb = _safe_globals(scope)
    local_scope: Dict[str, Any] = {}

    try:
        _exec_with_deadline(byte_code, glb, local_scope, timeout_s)
    except _MethodTimeout:
        _emit_audit(
            session, audit, "violation",
            (time.monotonic() - started) * 1000.0, {"reason": "timeout"},
        )
        raise MethodSandboxViolation(
            f"Method script exceeded time budget ({timeout_s}s)"
        ) from None
    except MethodSandboxViolation:
        _emit_audit(
            session, audit, "violation",
            (time.monotonic() - started) * 1000.0, {"reason": "policy"},
        )
        raise
    except Exception as exc:
        _emit_audit(
            session, audit, "error",
            (time.monotonic() - started) * 1000.0,
            {"reason": "runtime", "error": type(exc).__name__},
        )
        raise MethodSandboxViolation(
            f"Method script raised {type(exc).__name__}: {exc}"
        ) from exc

    _emit_audit(
        session, audit, "success", (time.monotonic() - started) * 1000.0,
    )
    return local_scope


def _module_allowed(module_path: str, allowlist: str) -> bool:
    """Exact-or-dotted-prefix match; empty allowlist => nothing allowed."""
    prefixes = [p.strip() for p in (allowlist or "").split(",") if p.strip()]
    for prefix in prefixes:
        if module_path == prefix or module_path.startswith(prefix + "."):
            return True
    return False


def run_module(
    module_path: str,
    *,
    entry: str,
    invoke: Callable[[Callable[..., Any]], Any],
    session: Any,
    audit: Optional[AuditContext] = None,
    allowlist: Optional[str] = None,
) -> Any:
    """Import an allowlisted module and call ``entry`` via ``invoke``.

    NOT RestrictedPython-sandboxed — module code runs with full privileges;
    containment is the fail-closed allowlist. An empty/unset allowlist, a
    non-allowlisted path, an import failure, or a missing entry all raise
    :class:`MethodSandboxViolation` (fail-closed). ``invoke(fn)`` lets each
    caller keep its own calling convention (positional vs kwargs).
    """
    import importlib

    settings = get_settings()
    effective_allowlist = (
        allowlist if allowlist is not None else settings.METHOD_MODULE_ALLOWLIST
    )
    started = time.monotonic()

    if not _module_allowed(module_path, effective_allowlist):
        _emit_audit(session, audit, "violation", 0.0, {"reason": "not_allowlisted"})
        raise MethodSandboxViolation(
            f"Module '{module_path}' is not in METHOD_MODULE_ALLOWLIST"
        )

    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        _emit_audit(
            session, audit, "error",
            (time.monotonic() - started) * 1000.0,
            {"reason": "import", "error": type(exc).__name__},
        )
        raise MethodSandboxViolation(
            f"Failed to import module '{module_path}': {exc}"
        ) from exc

    fn = getattr(module, entry, None)
    if not callable(fn):
        _emit_audit(
            session, audit, "violation",
            (time.monotonic() - started) * 1000.0, {"reason": "missing_entry"},
        )
        raise MethodSandboxViolation(
            f"Module '{module_path}' has no callable entry '{entry}'"
        )

    try:
        result = invoke(fn)
    except Exception as exc:
        _emit_audit(
            session, audit, "error",
            (time.monotonic() - started) * 1000.0,
            {"reason": "runtime", "error": type(exc).__name__},
        )
        raise MethodSandboxViolation(
            f"Module '{module_path}.{entry}' raised {type(exc).__name__}: {exc}"
        ) from exc

    _emit_audit(session, audit, "success", (time.monotonic() - started) * 1000.0)
    return result
