"""PLM-COLLAB-V2 seats (Option A): project_license_seats -> TenantQuota.max_users.

Pins the import-time seat-cap projection (``security/auth/seat_projection.py``): a valid
``seats`` lands on the identity-side ``TenantQuota.max_users`` (the cap the existing
``QuotaService`` provisioning gate enforces), invalid/absent seats are fail-open no-ops,
and re-import re-projects (the license is the source of truth). ``is_entitled()`` is
intentionally not exercised here -- seats live entirely outside the entitlement path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from yuantus.config import get_settings
from yuantus.models.base import Base
from yuantus.security.auth.models import AuthUser, Organization, Tenant, TenantQuota
from yuantus.security.auth.quota_service import QuotaService
from yuantus.security.auth.seat_projection import project_license_seats


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def identity_session():
    engine = create_engine("sqlite:///:memory:")

    # SQLite ignores FK constraints unless this pragma is ON. Enable it so
    # TenantQuota.tenant_id -> auth_tenants is actually enforced and the helper's
    # ensure_tenant precondition is exercised the way production Postgres would --
    # without it a missing-tenant projection would silently pass here.
    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # noqa: ANN001
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(
        bind=engine,
        tables=[
            Tenant.__table__,
            TenantQuota.__table__,
            AuthUser.__table__,
            Organization.__table__,
        ],
    )
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="module")
def signer():
    """Load the dev signer (scripts/dev/ is not a package) by path, without running main()."""
    import importlib.util

    path = Path(__file__).resolve().parents[4] / "scripts" / "dev" / "sign_dogfood_license.py"
    assert path.exists(), f"signer script not found at {path}"
    spec = importlib.util.spec_from_file_location("sign_dogfood_license_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(tenant_id="acme", **extra):
    p = {"tenant_id": tenant_id, "app_names": ["plm.bom_multitable"], "license_key": "K1"}
    p.update(extra)
    return p


def test_projects_valid_seats_to_max_users(identity_session):
    result = project_license_seats(identity_session, _payload(seats=20))
    identity_session.flush()
    assert result == 20
    quota = identity_session.get(TenantQuota, "acme")
    assert quota is not None and quota.max_users == 20
    assert identity_session.get(Tenant, "acme") is not None  # ensure_tenant ran (FK satisfied)


def test_absent_seats_projects_nothing(identity_session):
    assert project_license_seats(identity_session, _payload()) is None
    assert identity_session.get(TenantQuota, "acme") is None


@pytest.mark.parametrize("bad", [0, -1, -5, True, "20", 1.5])
def test_invalid_seats_is_fail_open_noop(identity_session, bad):
    # 0 / negative / bool / str / float -> skipped: no max_users written, no tenant lockout.
    assert project_license_seats(identity_session, _payload(seats=bad)) is None
    assert identity_session.get(TenantQuota, "acme") is None


def test_reimport_updates_cap_license_is_source_of_truth(identity_session):
    project_license_seats(identity_session, _payload(seats=20))
    identity_session.flush()
    project_license_seats(identity_session, _payload(seats=30))
    identity_session.flush()
    assert identity_session.get(TenantQuota, "acme").max_users == 30


def test_fk_is_actually_enforced_in_this_env(identity_session):
    # Control for the fixture pragma: a TenantQuota for a non-existent tenant MUST raise,
    # proving the helper's ensure_tenant precondition is load-bearing (not a SQLite no-op).
    identity_session.add(TenantQuota(tenant_id="ghost", max_users=5))
    with pytest.raises(IntegrityError):
        identity_session.flush()


def test_projected_cap_is_enforceable_at_provisioning(identity_session, monkeypatch):
    # End-to-end purpose: the projected cap is exactly what QuotaService enforces. With
    # seats=2 and two active users, a 3rd ({"users": 1}) would exceed the cap.
    monkeypatch.setenv("YUANTUS_QUOTA_MODE", "enforce")
    get_settings.cache_clear()
    project_license_seats(identity_session, _payload(seats=2))
    for username in ("u1", "u2"):
        identity_session.add(AuthUser(tenant_id="acme", username=username, is_active=True))
    identity_session.flush()
    decisions = QuotaService(identity_session).evaluate("acme", deltas={"users": 1})
    assert decisions, "projected cap should be enforced by QuotaService"
    assert decisions[0].resource == "users"
    assert decisions[0].limit == 2 and decisions[0].used == 2


# --- signer fail-fast: the mint side must refuse what the projection would skip --------------
# Finding: scripts/dev/sign_dogfood_license.py accepted any int for --seats while its help text
# and seat_projection.py both require >= 1, so `--seats 0` minted a self-verifying-but-inert
# license (PASS, imports, then projects nothing). The signer must enforce its own contract.

_SIGN_KW = dict(tenant_id="acme", subject="X", kid="k1", plan_type="Pilot",
                issued_at="2026-01-01T00:00:00+00:00")


@pytest.mark.parametrize("bad", [0, -1, True])
def test_build_and_sign_rejects_invalid_seats(signer, bad):
    # The load-bearing guard: build_and_sign is called outside any try/except in main(), so a
    # ValueError here aborts the mint (non-zero exit, no file) independent of the argparse layer.
    with pytest.raises(ValueError):
        signer.build_and_sign(Ed25519PrivateKey.generate(), seats=bad, **_SIGN_KW)


def test_build_and_sign_mints_valid_seats_and_omits_none(signer):
    priv = Ed25519PrivateKey.generate()
    assert signer.build_and_sign(priv, seats=5, **_SIGN_KW)["payload"]["seats"] == 5
    assert "seats" not in signer.build_and_sign(priv, seats=None, **_SIGN_KW)["payload"]


def test_seats_arg_validator_enforces_ge_1(signer):
    import argparse

    assert signer._seats_arg("5") == 5
    for bad in ("0", "-3", "abc"):
        with pytest.raises(argparse.ArgumentTypeError):
            signer._seats_arg(bad)


def test_cli_rejects_zero_seats_and_writes_no_file(tmp_path):
    # The literal operator scenario from the finding, end-to-end: `--seats 0` must fail before
    # any license is written (returncode-agnostic -> robust to whichever layer catches it).
    signer_path = Path(__file__).resolve().parents[4] / "scripts" / "dev" / "sign_dogfood_license.py"
    out = tmp_path / "lic.json"
    result = subprocess.run(
        [sys.executable, str(signer_path), "--tenant-id", "x", "--seats", "0", "--out", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not out.exists(), "a rejected --seats must not mint a license file"
