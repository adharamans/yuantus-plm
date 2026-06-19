"""CAD-PDM C3 date-obsolete worker CLI entrypoint (`yuantus date-obsolete-worker`).

The command operationalizes the (previously library-only) DateObsoleteWorker so a
deployment can run the auto-obsolete sweep as `--once` or a daemon loop. These tests
exercise CLI dispatch (once vs forever, worker-id passthrough, the operability hints,
and the multi-tenant guard) with run_once/run_forever monkeypatched, so no DB is
touched -- plus a source contract pinning the command registration.
"""
from __future__ import annotations

import pathlib
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import yuantus.cli as cli_mod
from yuantus.cli import app
from yuantus.context import org_id_var, tenant_id_var
from yuantus.meta_engine.services.date_obsolete_worker import DateObsoleteWorker

runner = CliRunner()
_CLI_TEXT = pathlib.Path(cli_mod.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_request_context():
    # The CLI sets tenant_id_var/org_id_var when --tenant/--org are passed, and CliRunner
    # does NOT reliably run the command in a copied context here -- so without this the
    # values leak into later tests in the same process and flip entitlement resolution to a
    # non-default tenant, breaking unrelated entitlement contracts. Snapshot + restore the
    # request-context vars around every test.
    saved_tenant, saved_org = tenant_id_var.get(), org_id_var.get()
    try:
        yield
    finally:
        tenant_id_var.set(saved_tenant)
        org_id_var.set(saved_org)


# --- source contract ---------------------------------------------------------
def test_command_is_registered_and_wired():
    assert '@app.command(name="date-obsolete-worker")' in _CLI_TEXT
    assert "DateObsoleteWorker(" in _CLI_TEXT
    assert "w.run_once()" in _CLI_TEXT and "w.run_forever()" in _CLI_TEXT
    # tenant/org context + system-user override + once flag options present
    assert '"--tenant"' in _CLI_TEXT and '"--org"' in _CLI_TEXT
    assert '"--system-user-id"' in _CLI_TEXT
    assert "once:" in _CLI_TEXT


# --- runtime (DB-free) -------------------------------------------------------
def test_once_runs_one_sweep_and_reports(monkeypatch):
    monkeypatch.setattr(DateObsoleteWorker, "run_once", lambda self: 3)
    result = runner.invoke(app, ["date-obsolete-worker", "--once"])
    assert result.exit_code == 0
    assert "Processed 3 expired date-effectivity row(s)." in result.output


def test_forever_is_used_when_not_once(monkeypatch):
    ran = {}

    def _forever(self):
        ran["forever"] = True

    def _no_run_once(self):
        raise AssertionError("run_once must not be called in daemon mode")

    monkeypatch.setattr(DateObsoleteWorker, "run_forever", _forever)
    monkeypatch.setattr(DateObsoleteWorker, "run_once", _no_run_once)
    result = runner.invoke(app, ["date-obsolete-worker"])
    assert result.exit_code == 0
    assert ran.get("forever") is True


def test_default_and_custom_worker_id(monkeypatch):
    seen = {}

    def _capture(self):
        seen["wid"] = self.worker_id
        return 0

    monkeypatch.setattr(DateObsoleteWorker, "run_once", _capture)

    runner.invoke(app, ["date-obsolete-worker", "--once"])
    assert seen["wid"] == "cadpdm-date-obsolete-worker-1"  # default

    runner.invoke(app, ["date-obsolete-worker", "--once", "--worker-id", "ops-7"])
    assert seen["wid"] == "ops-7"  # passthrough


def test_keyboard_interrupt_stops_cleanly(monkeypatch):
    stopped = {}

    def _forever(self):
        raise KeyboardInterrupt

    def _stop(self):
        stopped["stopped"] = True

    monkeypatch.setattr(DateObsoleteWorker, "run_forever", _forever)
    monkeypatch.setattr(DateObsoleteWorker, "stop", _stop)
    result = runner.invoke(app, ["date-obsolete-worker"])
    assert result.exit_code == 0  # Ctrl+C is a clean stop, not a crash
    assert stopped.get("stopped") is True


def test_disabled_kill_switch_warns_but_proceeds(monkeypatch):
    # Default settings have DATE_EFFECTIVITY_OBSOLETE_ENABLED False -> a clear operator
    # note is emitted, but the command still runs (warn-and-proceed, mirroring the
    # worker's own no-op-when-off design).
    monkeypatch.setattr(DateObsoleteWorker, "run_once", lambda self: 0)
    result = runner.invoke(app, ["date-obsolete-worker", "--once"])
    assert result.exit_code == 0
    assert "DATE_EFFECTIVITY_OBSOLETE_ENABLED is off" in result.output


def test_system_user_zero_warns(monkeypatch):
    # Default SYSTEM_USER_ID is 0 -> warn that the promote will record
    # child_obsolete_failed (parent flags only) until a real service user is set.
    monkeypatch.setattr(DateObsoleteWorker, "run_once", lambda self: 0)
    result = runner.invoke(app, ["date-obsolete-worker", "--once"])
    assert result.exit_code == 0
    assert "system user id is 0" in result.output


def test_multitenant_requires_tenant(monkeypatch):
    # In a non-single TENANCY_MODE, omitting --tenant fails fast with exit 2 instead of
    # crash-looping the per-tenant sweep on an opaque session-layer RuntimeError.
    monkeypatch.setattr(
        cli_mod,
        "get_settings",
        lambda: SimpleNamespace(
            TENANCY_MODE="schema-per-tenant",
            DATE_EFFECTIVITY_OBSOLETE_ENABLED=False,
            DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID=0,
        ),
    )

    def _must_not_run(self):
        raise AssertionError("worker must not run when the tenant guard trips")

    monkeypatch.setattr(DateObsoleteWorker, "run_once", _must_not_run)
    result = runner.invoke(app, ["date-obsolete-worker", "--once"])
    assert result.exit_code == 2
    assert "--tenant is required" in result.output


def test_multitenant_applies_context_and_wires_overrides(monkeypatch):
    # With --tenant the guard passes AND the context must actually be applied (the
    # tenant_id_var.set / org_id_var.set lines are the value-delivery that prevents the
    # session-layer crash the guard exists for). Also assert --system-user-id and
    # --poll-interval reach the worker, not just --worker-id.
    from yuantus.context import org_id_var, tenant_id_var

    seen = {}
    monkeypatch.setattr(
        cli_mod,
        "get_settings",
        lambda: SimpleNamespace(
            TENANCY_MODE="schema-per-tenant",
            DATE_EFFECTIVITY_OBSOLETE_ENABLED=True,
            DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID=42,
        ),
    )

    def _capture(self):
        seen["ran"] = True
        seen["tenant"] = tenant_id_var.get()  # the .set(tenant) line actually applied it
        seen["org"] = org_id_var.get()
        seen["sysuser"] = self.system_user_id  # --system-user-id wired into the worker
        seen["poll"] = self.poll_interval_seconds  # --poll-interval wired into the worker
        return 0

    monkeypatch.setattr(DateObsoleteWorker, "run_once", _capture)
    result = runner.invoke(
        app,
        [
            "date-obsolete-worker", "--once",
            "--tenant", "t-1", "--org", "o-9",
            "--system-user-id", "7", "--poll-interval", "11",
        ],
    )
    assert result.exit_code == 0
    assert seen.get("ran") is True
    assert seen["tenant"] == "t-1"
    assert seen["org"] == "o-9"
    assert seen["sysuser"] == 7
    assert seen["poll"] == 11


def test_db_per_tenant_org_requires_org(monkeypatch):
    # db-per-tenant-org scopes the session by tenant AND org; omitting --org must also
    # fail fast with exit 2 rather than leak the opaque session-layer RuntimeError on the
    # --once path.
    monkeypatch.setattr(
        cli_mod,
        "get_settings",
        lambda: SimpleNamespace(
            TENANCY_MODE="db-per-tenant-org",
            DATE_EFFECTIVITY_OBSOLETE_ENABLED=False,
            DATE_EFFECTIVITY_OBSOLETE_SYSTEM_USER_ID=0,
        ),
    )

    def _must_not_run(self):
        raise AssertionError("worker must not run when the org guard trips")

    monkeypatch.setattr(DateObsoleteWorker, "run_once", _must_not_run)
    result = runner.invoke(app, ["date-obsolete-worker", "--once", "--tenant", "t-1"])
    assert result.exit_code == 2
    assert "--org is required" in result.output
