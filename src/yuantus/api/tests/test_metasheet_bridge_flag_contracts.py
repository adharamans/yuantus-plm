"""PLM-COLLAB-P0-A: ENABLE_METASHEET flag + base-green contract.

Flag OFF (default): no MetaSheet bridge route/state — base PLM surface intact.
Flag ON: exactly one inert bridge seam route mounts, still inert.

Placed in ``api/tests/`` on purpose: ``create_app()`` is DB-free, so these run
without a database (``meta_engine/tests/`` is DB-gated by conftest). These assert
the registration boundary only; no MetaSheet I/O, DB, or entitlement is
exercised. See scope taskbook §5 (first slice) and §6 (verification gates).
"""
from __future__ import annotations

import pytest

BRIDGE_PREFIX = "/api/v1/metasheet-bridge"
BRIDGE_HEALTH = f"{BRIDGE_PREFIX}/health"


@pytest.fixture(autouse=True)
def _isolate_settings_cache():
    """Keep the lru_cached Settings from leaking flag state across tests."""
    from yuantus.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _create_app(monkeypatch, enable):
    from yuantus.config import get_settings

    if enable is None:
        monkeypatch.delenv("YUANTUS_ENABLE_METASHEET", raising=False)
    else:
        monkeypatch.setenv("YUANTUS_ENABLE_METASHEET", "true" if enable else "false")
    get_settings.cache_clear()
    from yuantus.api.app import create_app

    return create_app()


def _paths(app):
    return {getattr(route, "path", "") for route in app.routes}


def test_enable_metasheet_defaults_false(monkeypatch):
    from yuantus.config import get_settings

    monkeypatch.delenv("YUANTUS_ENABLE_METASHEET", raising=False)
    get_settings.cache_clear()
    assert get_settings().ENABLE_METASHEET is False


def test_env_toggles_enable_metasheet(monkeypatch):
    from yuantus.config import get_settings

    monkeypatch.setenv("YUANTUS_ENABLE_METASHEET", "true")
    get_settings.cache_clear()
    assert get_settings().ENABLE_METASHEET is True


def test_flag_off_mounts_no_bridge_route(monkeypatch):
    """Base-green: default (flag OFF) create_app exposes no bridge surface."""
    app = _create_app(monkeypatch, enable=False)
    assert not any(p.startswith(BRIDGE_PREFIX) for p in _paths(app))


def test_flag_on_adds_exactly_the_inert_seam(monkeypatch):
    """Flag ON adds exactly one route (the inert health seam) and nothing else."""
    off_paths = _paths(_create_app(monkeypatch, enable=False))
    on_paths = _paths(_create_app(monkeypatch, enable=True))
    assert on_paths - off_paths == {BRIDGE_HEALTH}


def test_bridge_health_payload_is_inert():
    """The seam reports mounted-but-not-active; it performs no I/O."""
    from yuantus.api.routers.metasheet_bridge import metasheet_bridge_health

    assert metasheet_bridge_health() == {
        "bridge": "metasheet",
        "enabled": True,
        "active": False,
        "entitlement_required": True,
    }
