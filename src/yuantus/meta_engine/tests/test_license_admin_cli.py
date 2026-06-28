"""Operator admin CLI for the L4 licensing surface: ``yuantus license cap-history``
and ``yuantus license revoke``.

These are **DB-backed** (real in-memory SQLite via a redirected ``get_db_session``),
not mock-everything arg-parsing checks: the deliverable is "the command works against
a DB in an operator's hands", so the tests assert the real effect -- ``revoke`` flips
``AppLicense.status`` to Revoked and writes an ``admin:license/revoke`` audit row;
``cap-history`` reads seeded seat-cap audit rows newest-first and honours ``--limit``.
A handful of source contracts pin the command registration + that each command routes
to the shared service/helper (so the CLI and the HTTP route cannot drift apart).
"""
from __future__ import annotations

import contextlib
import pathlib
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from typer.testing import CliRunner

import yuantus.cli as cli_mod
from yuantus.cli import app
from yuantus.context import org_id_var, tenant_id_var
from yuantus.meta_engine.bootstrap import import_all_models
from yuantus.meta_engine.app_framework.store_models import AppLicense
from yuantus.models.audit import AuditLog
from yuantus.models.base import Base

# AppLicense carries an FK to meta_app_registry; register every ORM model so that FK
# target resolves in Base.metadata when we create_all() just the tables we need
# (per CLAUDE.md: a standalone DB process must import_all_models() before touching it).
import_all_models()

runner = CliRunner()
TENANT = "tenant-1"
_CLI_TEXT = pathlib.Path(cli_mod.__file__).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _isolate_request_context():
    # cap-history sets tenant_id_var; CliRunner does not reliably copy the context, so
    # snapshot + restore the request-context vars around every test to avoid leaking a
    # non-default tenant into later entitlement contracts in the same process.
    saved_tenant, saved_org = tenant_id_var.get(), org_id_var.get()
    try:
        yield
    finally:
        tenant_id_var.set(saved_tenant)
        org_id_var.set(saved_org)


@pytest.fixture
def cli_db(monkeypatch):
    """Real SQLite, with the CLI's ``get_db_session`` redirected to this session.

    The CLI opens its own session (it is not a FastAPI route, so dependency_overrides
    do not apply); redirecting ``get_db_session`` to the seeded session is what makes
    these tests exercise the genuine query/mutation path, not a stub. The fake mirrors
    the real commit/rollback-on-exit but leaves close() to this fixture.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine, tables=[AppLicense.__table__, AuditLog.__table__])
    session = sessionmaker(bind=engine, expire_on_commit=False)()

    @contextlib.contextmanager
    def _fake_get_db_session():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr("yuantus.database.get_db_session", _fake_get_db_session)
    try:
        yield session
    finally:
        session.close()


def _license(session, *, key, app_name="plm.collab", status="Active"):
    session.add(
        AppLicense(
            id=uuid.uuid4().hex, app_name=app_name, license_key=key, status=status, tenant_id=TENANT
        )
    )
    session.commit()


def _cap_audit(session, *, max_users, created_at, tenant=TENANT):
    # mirrors record_seat_cap_audit: path=f"cli:license/seat-cap?max_users={cap}"
    session.add(
        AuditLog(
            id=uuid.uuid4().hex,
            method="LICENSE",
            path=f"cli:license/seat-cap?max_users={max_users}",
            tenant_id=tenant,
            status_code=200,
            duration_ms=0,
            created_at=created_at,
        )
    )
    session.commit()


# --- source contracts --------------------------------------------------------
def test_commands_registered_and_routed_to_shared_layer():
    assert '@license_app.command("cap-history")' in _CLI_TEXT
    assert '@license_app.command("revoke")' in _CLI_TEXT
    # cap-history routes to the SHARED helper (same parse as the HTTP route)
    assert "collect_seat_cap_history(" in _CLI_TEXT
    # revoke routes to the append-only service (not an ad-hoc status write)
    assert "LicenseRevocationService(" in _CLI_TEXT
    assert "revoke_license(" in _CLI_TEXT


# --- revoke ------------------------------------------------------------------
def test_revoke_flips_status_and_writes_audit(cli_db):
    _license(cli_db, key="K1")
    result = runner.invoke(app, ["license", "revoke", "--license-key", "K1", "--reason", "leak"])
    assert result.exit_code == 0, result.output
    assert "revoked 1 license row(s) for key K1" in result.output
    assert "status=Revoked" in result.output
    # real DB effect: status flipped + an append-only revoke audit row exists
    lic = cli_db.query(AppLicense).filter(AppLicense.license_key == "K1").one()
    assert lic.status == "Revoked"
    audits = (
        cli_db.query(AuditLog)
        .filter(AuditLog.method == "LICENSE")
        .filter(AuditLog.path.like("admin:license/revoke%"))
        .all()
    )
    assert len(audits) == 1
    assert "reason=leak" in audits[0].path


def test_revoke_records_revoked_by_user_id(cli_db):
    _license(cli_db, key="K9")
    result = runner.invoke(
        app, ["license", "revoke", "--license-key", "K9", "--reason", "x", "--revoked-by", "42"]
    )
    assert result.exit_code == 0, result.output
    audit = (
        cli_db.query(AuditLog).filter(AuditLog.path.like("admin:license/revoke%")).one()
    )
    assert audit.user_id == 42


def test_revoke_unknown_key_exits_nonzero(cli_db):
    result = runner.invoke(app, ["license", "revoke", "--license-key", "NOPE", "--reason", "x"])
    assert result.exit_code == 1
    assert "no license found for key NOPE" in result.output


def test_revoke_blank_key_exits_nonzero(cli_db):
    result = runner.invoke(app, ["license", "revoke", "--license-key", "   ", "--reason", "x"])
    assert result.exit_code == 1
    assert "must be a non-empty key" in result.output


def _mode(monkeypatch, tenancy_mode):
    # revoke reads cli_mod.get_settings() (module-global, @lru_cache) for TENANCY_MODE.
    monkeypatch.setattr(
        cli_mod, "get_settings", lambda: SimpleNamespace(TENANCY_MODE=tenancy_mode)
    )


def test_revoke_multitenant_requires_tenant_id(cli_db, monkeypatch):
    _mode(monkeypatch, "db-per-tenant")
    _license(cli_db, key="K1")
    result = runner.invoke(app, ["license", "revoke", "--license-key", "K1", "--reason", "x"])
    assert result.exit_code == 2  # fail-fast, not an opaque session RuntimeError
    assert "requires --tenant-id" in result.output
    # guard tripped BEFORE any mutation: the license is untouched
    assert cli_db.query(AppLicense).filter(AppLicense.license_key == "K1").one().status == "Active"


def test_revoke_db_per_tenant_org_requires_org(cli_db, monkeypatch):
    _mode(monkeypatch, "db-per-tenant-org")
    _license(cli_db, key="K1")
    result = runner.invoke(
        app, ["license", "revoke", "--license-key", "K1", "--reason", "x", "--tenant-id", "t1"]
    )
    assert result.exit_code == 2
    assert "requires --org" in result.output


def test_revoke_multitenant_with_tenant_id_proceeds(cli_db, monkeypatch):
    # guard passes when context is supplied; the (patched) session resolves + revoke runs
    _mode(monkeypatch, "db-per-tenant")
    _license(cli_db, key="K1")
    result = runner.invoke(
        app, ["license", "revoke", "--license-key", "K1", "--reason", "x", "--tenant-id", "t1"]
    )
    assert result.exit_code == 0, result.output
    assert cli_db.query(AppLicense).filter(AppLicense.license_key == "K1").one().status == "Revoked"


def test_revoke_does_not_clear_seat_cap_audit(cli_db):
    # append-only: revoke must NOT emit a seat-cap change (cap rollback is separate)
    _license(cli_db, key="K2")
    runner.invoke(app, ["license", "revoke", "--license-key", "K2", "--reason", "x"])
    seat_cap = (
        cli_db.query(AuditLog).filter(AuditLog.path.like("cli:license/seat-cap%")).all()
    )
    assert seat_cap == []


# --- cap-history -------------------------------------------------------------
def test_cap_history_lists_changes_newest_first(cli_db):
    base = datetime(2026, 1, 1, 0, 0, 0)
    _cap_audit(cli_db, max_users=5, created_at=base)
    _cap_audit(cli_db, max_users=10, created_at=base + timedelta(hours=1))
    # a non-seat-cap LICENSE audit must be excluded
    cli_db.add(
        AuditLog(
            id=uuid.uuid4().hex,
            method="LICENSE",
            path="cli:license/import",
            tenant_id=TENANT,
            status_code=200,
            duration_ms=0,
            created_at=base,
        )
    )
    cli_db.commit()

    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", TENANT])
    assert result.exit_code == 0, result.output
    assert "2 change(s)" in result.output
    assert "max_users=10" in result.output and "max_users=5" in result.output
    # newest-first: max_users=10 (later) appears before max_users=5
    assert result.output.index("max_users=10") < result.output.index("max_users=5")
    assert "import" not in result.output  # the non-seat-cap audit was excluded


def test_cap_history_renders_cleared_cap(cli_db):
    cli_db.add(
        AuditLog(
            id=uuid.uuid4().hex,
            method="LICENSE",
            path="cli:license/seat-cap?max_users=cleared",
            tenant_id=TENANT,
            status_code=200,
            duration_ms=0,
            created_at=datetime(2026, 1, 2),
        )
    )
    cli_db.commit()
    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", TENANT])
    assert result.exit_code == 0, result.output
    assert "cap cleared -> unlimited" in result.output


def test_cap_history_limit_truncates(cli_db):
    base = datetime(2026, 1, 1)
    for i in range(3):
        _cap_audit(cli_db, max_users=i, created_at=base + timedelta(hours=i))
    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", TENANT, "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "1 change(s)" in result.output
    assert "max_users=2" in result.output  # newest only
    assert "max_users=0" not in result.output


def test_cap_history_unknown_tenant_is_empty_not_error(cli_db):
    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", "nobody"])
    assert result.exit_code == 0, result.output
    assert "0 change(s)" in result.output
    assert "(no seat-cap changes recorded)" in result.output


def test_cap_history_blank_tenant_exits_nonzero(cli_db):
    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", "   "])
    assert result.exit_code == 1
    assert "must be a non-empty tenant id" in result.output


def test_cap_history_limit_below_one_is_rejected(cli_db):
    # parity with the HTTP route's Query(..., ge=1): --limit < 1 must not reach the DB
    result = runner.invoke(app, ["license", "cap-history", "--tenant-id", TENANT, "--limit", "0"])
    assert result.exit_code == 2  # click IntRange(min=1) usage error
