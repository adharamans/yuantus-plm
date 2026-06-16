"""ECM worker CLI entrypoint (`yuantus ecm-publication-worker`).

The command is the production drainer for the ECM publication outbox. These tests
exercise the CLI dispatch (once vs forever, worker-id passthrough, output) with
run_once/run_forever monkeypatched, so no DB is touched -- plus a source contract
pinning the command registration.
"""
from __future__ import annotations

import pathlib

from typer.testing import CliRunner

import yuantus.cli as cli_mod
from yuantus.cli import app
from yuantus.meta_engine.ecm_publication.worker import EcmPublicationOutboxWorker

runner = CliRunner()
_CLI_TEXT = pathlib.Path(cli_mod.__file__).read_text(encoding="utf-8")


# --- source contract ---------------------------------------------------------
def test_command_is_registered_and_wired():
    assert '@app.command(name="ecm-publication-worker")' in _CLI_TEXT
    assert "EcmPublicationOutboxWorker(" in _CLI_TEXT
    assert "w.run_once()" in _CLI_TEXT and "w.run_forever()" in _CLI_TEXT
    # tenant/org context + once flag options present
    assert '"--tenant"' in _CLI_TEXT and '"--org"' in _CLI_TEXT
    assert "once:" in _CLI_TEXT


# --- runtime (DB-free) -------------------------------------------------------
def test_once_drains_one_batch_and_reports(monkeypatch):
    monkeypatch.setattr(EcmPublicationOutboxWorker, "run_once", lambda self: 4)
    result = runner.invoke(app, ["ecm-publication-worker", "--once"])
    assert result.exit_code == 0
    assert "Processed 4 ECM publication outbox row(s)." in result.output


def test_forever_is_used_when_not_once(monkeypatch):
    ran = {}

    def _forever(self):
        ran["forever"] = True

    def _no_run_once(self):
        raise AssertionError("run_once must not be called in daemon mode")

    monkeypatch.setattr(EcmPublicationOutboxWorker, "run_forever", _forever)
    monkeypatch.setattr(EcmPublicationOutboxWorker, "run_once", _no_run_once)
    result = runner.invoke(app, ["ecm-publication-worker"])
    assert result.exit_code == 0
    assert ran.get("forever") is True


def test_default_and_custom_worker_id(monkeypatch):
    seen = {}

    def _capture(self):
        seen["wid"] = self.worker_id
        return 0

    monkeypatch.setattr(EcmPublicationOutboxWorker, "run_once", _capture)

    runner.invoke(app, ["ecm-publication-worker", "--once"])
    assert seen["wid"] == "ecm-publication-worker-1"  # default

    runner.invoke(app, ["ecm-publication-worker", "--once", "--worker-id", "ops-7"])
    assert seen["wid"] == "ops-7"  # passthrough


def test_keyboard_interrupt_stops_cleanly(monkeypatch):
    stopped = {}

    def _forever(self):
        raise KeyboardInterrupt

    def _stop(self):
        stopped["stopped"] = True

    monkeypatch.setattr(EcmPublicationOutboxWorker, "run_forever", _forever)
    monkeypatch.setattr(EcmPublicationOutboxWorker, "stop", _stop)
    result = runner.invoke(app, ["ecm-publication-worker"])
    assert result.exit_code == 0  # Ctrl+C is a clean stop, not a crash
    assert stopped.get("stopped") is True
