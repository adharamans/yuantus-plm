"""
P0-8a — Method sandbox tests.

Every hostile input goes through the REAL execution seams
(``MethodExecutor.execute_method`` / ``MethodService.execute_method`` — the
exact callables ``add_op``/``update_op``/the RPC path invoke) — only the DB
``session`` is a MagicMock returning a real ``Method``; the sandbox itself is
never stubbed. DB-free (allowlisted in ``conftest.py``).
"""

from __future__ import annotations

import pathlib
import uuid
from unittest.mock import MagicMock

import pytest

from yuantus.config.settings import get_settings
from yuantus.exceptions.handlers import PermissionError as PLMPermissionError
from yuantus.meta_engine.business_logic.executor import MethodExecutor
from yuantus.meta_engine.business_logic.models import Method
from yuantus.meta_engine.business_logic.sandbox import (
    MethodSandboxViolation,
    run_module,
    run_script,
)
from yuantus.meta_engine.services.method_service import MethodService


# --------------------------------------------------------------------------
# Builders — real Method rows, mock session, real execution seams
# --------------------------------------------------------------------------
def _method(content: str, mtype: str = "python_script", name: str = "m") -> Method:
    return Method(id=str(uuid.uuid4()), name=name, type=mtype, content=content)


def _session_for_get(method: Method) -> MagicMock:
    """Session whose .get(Method, id) returns the given method (executor path)."""
    sess = MagicMock()
    sess.get.return_value = method
    return sess


def _session_for_query(method: Method) -> MagicMock:
    """Session whose query(Method).filter_by(name).first() returns method (svc path)."""
    sess = MagicMock()
    sess.query.return_value.filter_by.return_value.first.return_value = method
    return sess


class _FakeItem:
    """Stand-in for a meta Item the hook script mutates in place."""

    def __init__(self):
        self.state = "orig"
        self.properties = {}


def _run_via_executor(content: str, mtype: str = "python_script"):
    method = _method(content, mtype)
    sess = _session_for_get(method)
    item = _FakeItem()
    return MethodExecutor(sess).execute_method(method.id, item, payload=None), item


def _run_via_service(content: str, context=None, mtype: str = "python_script"):
    method = _method(content, mtype)
    sess = _session_for_query(method)
    ctx = {"session": sess, "user_id": 1}
    if context:
        ctx.update(context)
    return MethodService(sess).execute_method(method.name, ctx)


@pytest.fixture
def settings_env(monkeypatch):
    """Set YUANTUS_* env and clear the settings lru_cache around the test."""

    def _apply(**env):
        for key, value in env.items():
            monkeypatch.setenv(f"YUANTUS_{key}", value)
        get_settings.cache_clear()

    get_settings.cache_clear()
    yield _apply
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# 1-3, escapes through the real seams
# --------------------------------------------------------------------------
def test_1_import_os_via_executor_is_violation():
    with pytest.raises(MethodSandboxViolation) as ei:
        _run_via_executor("import os\nresult = os.getcwd()")
    assert ei.value.__cause__ is not None  # chained


def test_2_open_file_via_service_is_violation():
    with pytest.raises(MethodSandboxViolation):
        _run_via_service("result = open('/etc/passwd').read()")


def test_3_dunder_class_walk_is_violation():
    with pytest.raises(MethodSandboxViolation):
        _run_via_service("result = ().__class__.__bases__[0].__subclasses__()")


def test_3b_context_cannot_override_restricted_builtins():
    with pytest.raises(MethodSandboxViolation):
        run_script(
            "result = open('/etc/passwd').read()",
            {"__builtins__": __builtins__},
            session=MagicMock(),
        )


# --------------------------------------------------------------------------
# 4-5, benign scripts still work (feature not broken)
# --------------------------------------------------------------------------
def test_4_benign_hook_mutates_item_in_place_via_executor():
    ret, item = _run_via_executor(
        "item.state = 'UpdatedByMethod'\nitem.properties['x'] = 1"
    )
    assert item.state == "UpdatedByMethod"
    assert item.properties == {"x": 1}
    assert ret is item  # executor returns the same (mutated) object


def test_5_benign_result_returned_via_service():
    out = _run_via_service("a = 2\nb = 3\nresult = a + b")
    assert out == 5


def test_5b_print_in_benign_script_does_not_break():
    # PrintCollector must be wired or print() raises NameError('_print_').
    out = _run_via_service("print('hello from method')\nresult = 'ok'")
    assert out == "ok"


# --------------------------------------------------------------------------
# 6-7, RPC entry gate (D4) — fail-closed + role required
# --------------------------------------------------------------------------
def _rpc(roles, method_name="m", enabled=None, settings_env=None):
    from yuantus.meta_engine.services.engine import AMLEngine

    if enabled is not None and settings_env is not None:
        settings_env(METHOD_RPC_ENABLED="true" if enabled else "false")
    sess = MagicMock()
    eng = AMLEngine(sess, identity_id="u1", roles=roles)
    return eng.rpc_run_method([method_name, {}], {})


def test_6_rpc_disabled_by_default_is_refused(settings_env):
    settings_env()  # defaults: METHOD_RPC_ENABLED unset => False
    with pytest.raises(PLMPermissionError):
        _rpc(["admin"])


def test_7a_rpc_enabled_non_admin_is_refused(settings_env):
    with pytest.raises(PLMPermissionError):
        _rpc(["engineer"], enabled=True, settings_env=settings_env)


def test_7b_rpc_enabled_admin_reaches_execution(settings_env):
    # Enabled + admin passes the gate; a missing Method then raises ValueError
    # (NOT PermissionError) — proving the gate was cleared.
    settings_env(METHOD_RPC_ENABLED="true")
    from yuantus.meta_engine.services.engine import AMLEngine

    sess = MagicMock()
    sess.query.return_value.filter_by.return_value.first.return_value = None
    eng = AMLEngine(sess, identity_id="u1", roles=["superuser"])
    with pytest.raises(ValueError):
        eng.rpc_run_method(["no_such_method", {}], {})


# --------------------------------------------------------------------------
# 8-9, preserved semantics
# --------------------------------------------------------------------------
def test_8_missing_method_id_passes_through_via_executor():
    sess = MagicMock()
    sess.get.return_value = None  # method not found
    item = _FakeItem()
    ret = MethodExecutor(sess).execute_method("missing", item, payload=None)
    assert ret is item  # silent pass-through preserved


def test_9_unknown_method_name_raises_value_error_via_service():
    sess = MagicMock()
    sess.query.return_value.filter_by.return_value.first.return_value = None
    with pytest.raises(ValueError):
        MethodService(sess).execute_method("nope", {"session": sess})


# --------------------------------------------------------------------------
# 10-11, module allowlist (both S2 and S4), fail-closed
# --------------------------------------------------------------------------
@pytest.mark.parametrize("kind", ["executor", "service"])
def test_10_module_not_allowlisted_fails_closed(kind, settings_env):
    settings_env()  # METHOD_MODULE_ALLOWLIST unset => empty => all refused
    with pytest.raises(MethodSandboxViolation):
        if kind == "executor":
            _run_via_executor("os", mtype="python_module")
        else:
            _run_via_service("os:getcwd", mtype="python_module")


def test_11_allowlisted_module_executes(settings_env, tmp_path, monkeypatch):
    # Create a temp allowlisted module with a `run(session,item,payload)` entry.
    mod_dir = tmp_path
    (mod_dir / "yuantus_test_hookmod.py").write_text(
        "def run(session, item, payload):\n"
        "    item.state = 'from_module'\n"
        "    return item\n"
        "def main(**kw):\n"
        "    return 'module_ok'\n"
    )
    monkeypatch.syspath_prepend(str(mod_dir))
    settings_env(METHOD_MODULE_ALLOWLIST="yuantus_test_hookmod")

    _, item = _run_via_executor("yuantus_test_hookmod", mtype="python_module")
    assert item.state == "from_module"

    out = _run_via_service("yuantus_test_hookmod:main", mtype="python_module")
    assert out == "module_ok"


def test_11b_allowlist_prefix_does_not_leak_sibling(settings_env):
    # 'plm.hooks' must NOT allow 'plm.hooks_evil' (dotted-prefix, not str-prefix).
    settings_env(METHOD_MODULE_ALLOWLIST="plm.hooks")
    with pytest.raises(MethodSandboxViolation):
        _run_via_service("plm.hooks_evil:main", mtype="python_module")


# --------------------------------------------------------------------------
# 12, static bypass guard — the two files must contain no raw exec/import
# --------------------------------------------------------------------------
def _src(rel: str) -> str:
    root = pathlib.Path(__file__).resolve().parents[4]
    return (root / rel).read_text()


def test_12_no_raw_exec_or_import_in_cutover_files():
    for rel in (
        "src/yuantus/meta_engine/business_logic/executor.py",
        "src/yuantus/meta_engine/services/method_service.py",
    ):
        text = _src(rel)
        assert "exec(" not in text, f"{rel} still calls exec()"
        assert "eval(" not in text, f"{rel} still calls eval()"
        assert "import_module(" not in text, f"{rel} still imports dynamically"
        assert "from .sandbox import" in text or "business_logic.sandbox" in text, (
            f"{rel} does not route through the sandbox adapter"
        )


def test_12b_importlib_import_module_only_in_sandbox():
    # Repo-wide: the only importlib.import_module on Method content is in sandbox.py.
    for rel in (
        "src/yuantus/meta_engine/business_logic/executor.py",
        "src/yuantus/meta_engine/services/method_service.py",
    ):
        assert "importlib.import_module" not in _src(rel)
    assert "importlib.import_module" in _src(
        "src/yuantus/meta_engine/business_logic/sandbox.py"
    )


# --------------------------------------------------------------------------
# 13-14, resource bounds
# --------------------------------------------------------------------------
def test_13_infinite_loop_hits_timeout(settings_env):
    settings_env(METHOD_SCRIPT_TIMEOUT_SECONDS="0.3")
    with pytest.raises(MethodSandboxViolation):
        _run_via_service("while True:\n    pass\nresult = 1")


def test_14_oversized_script_refused(settings_env):
    settings_env(METHOD_SCRIPT_MAX_BYTES="50")
    with pytest.raises(MethodSandboxViolation):
        _run_via_service("result = '" + "A" * 200 + "'")


# --------------------------------------------------------------------------
# 15, audit emitted on success AND violation
# --------------------------------------------------------------------------
def test_15_audit_on_success_and_violation(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="plm_audit"):
        _run_via_service("result = 1")
        with pytest.raises(MethodSandboxViolation):
            _run_via_service("import os")
    audit_lines = [r.getMessage() for r in caplog.records if "method.execute" in r.getMessage()]
    assert any("success" in m for m in audit_lines)
    assert any("violation" in m or "error" in m for m in audit_lines)


# --------------------------------------------------------------------------
# 16, new Settings fields exist with documented defaults
# --------------------------------------------------------------------------
def test_16_settings_fields_exist_with_defaults(settings_env):
    settings_env()
    s = get_settings()
    assert s.METHOD_SCRIPT_TIMEOUT_SECONDS == 5.0
    assert s.METHOD_SCRIPT_MAX_BYTES == 100_000
    assert s.METHOD_MODULE_ALLOWLIST == ""
    assert s.METHOD_RPC_ENABLED is False


# --------------------------------------------------------------------------
# 17, adapter-raised errors carry a cause when wrapping
# --------------------------------------------------------------------------
def test_17_runtime_error_is_chained():
    with pytest.raises(MethodSandboxViolation) as ei:
        run_script("result = 1 / 0", {}, session=MagicMock())
    assert ei.value.__cause__ is not None
    assert isinstance(ei.value.__cause__, ZeroDivisionError)


# --------------------------------------------------------------------------
# 18, real wiring — add_op → REAL MethodExecutor → sandbox blocks a hostile hook
# --------------------------------------------------------------------------
def test_18_add_op_hook_routes_through_real_sandbox_and_blocks():
    from yuantus.meta_engine.operations.add_op import AddOperation

    hostile = _method("import os\nresult = os.getcwd()")
    sess = _session_for_get(hostile)

    engine = MagicMock()
    engine.user_id = "1"
    engine.roles = ["admin"]
    engine.session = sess
    engine.permission_service.check_permission.return_value = True
    # The load-bearing wiring: a REAL MethodExecutor on the engine.
    engine.method_executor = MethodExecutor(sess)

    item_type = MagicMock()
    item_type.id = "Part"
    item_type.is_relationship = False
    item_type.permission_id = "perm1"
    item_type.on_before_add_method_id = hostile.id

    from yuantus.meta_engine.schemas.aml import AMLAction, GenericItem

    aml = GenericItem(type="Part", action=AMLAction.add, properties={"name": "X"})

    # The onBeforeAdd hook fires inside execute(); the hostile script must be
    # blocked by the real sandbox, aborting the add before the row is used.
    with pytest.raises(MethodSandboxViolation):
        AddOperation(engine).execute(item_type, aml)


def test_17b_module_missing_entry_is_violation(settings_env, tmp_path, monkeypatch):
    (tmp_path / "yuantus_noentry_mod.py").write_text("x = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    with pytest.raises(MethodSandboxViolation):
        run_module(
            "yuantus_noentry_mod",
            entry="run",
            invoke=lambda fn: fn(),
            session=MagicMock(),
            allowlist="yuantus_noentry_mod",
        )
