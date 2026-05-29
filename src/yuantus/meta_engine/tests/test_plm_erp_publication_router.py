"""R1-B tests for the PLM->ERP publication-readiness API (G2).

Mirrors test_release_readiness_router.py: mock db session + dependency
overrides, and patches ReleaseReadinessService + the two guards in the router
module namespace for deterministic readiness/guard behavior. Covers the R1-A §8
test catalog.
"""
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from yuantus.api.app import create_app
from yuantus.api.dependencies.auth import get_current_user
from yuantus.database import get_db
from yuantus.meta_engine.services.latest_released_guard import NotLatestReleasedError
from yuantus.meta_engine.services.suspended_guard import SuspendedStateError
from yuantus.meta_engine.web.plm_erp_publication_router import get_publication_readiness

_MODULE = "yuantus.meta_engine.web.plm_erp_publication_router"
_ADMIN = SimpleNamespace(id=1, roles=["admin"], is_superuser=False)
_VIEWER = SimpleNamespace(id=2, roles=["viewer"], is_superuser=False)
_URL = "/api/v1/plm-erp/items/item-1/publication-readiness"


@pytest.fixture(autouse=True)
def _auth_optional(monkeypatch):
    # Force AuthEnforcementMiddleware into non-"required" mode for these
    # TestClient tests so the file is self-contained (runs under CI's default
    # env, e.g. in ci.yml's contracts list, without an externally-exported
    # YUANTUS_AUTH_MODE). Patch ONLY the middleware's get_settings reference —
    # do NOT clear the global get_settings lru_cache, which would corrupt other
    # tests' settings in the same session. The middleware bypasses at its first
    # branch when AUTH_MODE != "required" and reads only .AUTH_MODE there.
    monkeypatch.setattr(
        "yuantus.api.middleware.auth_enforce.get_settings",
        lambda: SimpleNamespace(AUTH_MODE="optional"),
    )
    yield


def _client(user, item):
    mock_db = MagicMock()
    mock_db.get.return_value = item

    def override_get_db():
        yield mock_db

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _item(*, current_version=None, state="Released"):
    return SimpleNamespace(state=state, current_version=current_version)


def _payload(*, ok=True, resources=None, esign_manifest=None, error_count=0, warning_count=0):
    resources = resources or []
    return {
        "item_id": "item-1",
        "generated_at": datetime.utcnow(),
        "ruleset_id": "readiness",
        "summary": {
            "ok": ok,
            "resources": len(resources),
            "ok_resources": sum(1 for r in resources if not r.get("errors")),
            "error_count": error_count,
            "warning_count": warning_count,
            "by_kind": {},
        },
        "resources": resources,
        "esign_manifest": esign_manifest,
    }


def _mbom_error_resource():
    return {
        "kind": "mbom_release",
        "resource_type": "mbom",
        "resource_id": "mbom-1",
        "name": "MBOM 1",
        "state": "draft",
        "ruleset_id": "readiness",
        "errors": [{"code": "mbom_empty_structure", "message": "empty", "rule_id": "r", "details": {}}],
        "warnings": [],
    }


def _mbom_warning_resource():
    return {
        "kind": "mbom_release",
        "resource_type": "mbom",
        "resource_id": "mbom-2",
        "name": "MBOM 2",
        "state": "draft",
        "ruleset_id": "readiness",
        "errors": [],
        "warnings": [{"code": "mbom_warn", "message": "w", "rule_id": "r", "details": {}}],
    }


def _patched(payload, *, latest_exc=None, suspended_exc=None):
    """Patch the service + both guards; return the (ExitStack-style) patchers as a
    context manager tuple."""
    svc_p = patch(f"{_MODULE}.ReleaseReadinessService")
    lat_p = patch(f"{_MODULE}.LatestReleasedGuardService")
    sus_p = patch(f"{_MODULE}.SuspendedGuardService")
    svc, lat, sus = svc_p.start(), lat_p.start(), sus_p.start()
    svc.return_value.get_item_release_readiness.return_value = payload
    # MagicMock reserves `assert_*` attribute auto-access for its own assertion
    # API, so accessing `.assert_latest_released` raises AttributeError. Assign
    # the methods explicitly (assignment bypasses that protection). A None
    # side_effect = no raise; an exception side_effect = the guard raises it.
    lat.return_value.assert_latest_released = MagicMock(side_effect=latest_exc)
    sus.return_value.assert_not_suspended = MagicMock(side_effect=suspended_exc)
    return (svc_p, lat_p, sus_p), svc


def _stop(patchers):
    for p in patchers:
        p.stop()


def test_publication_readiness_denies_non_admin():
    client = _client(_VIEWER, _item())
    patchers, _svc = _patched(_payload())
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    assert resp.status_code == 403


def test_publication_readiness_404_when_item_missing():
    client = _client(_ADMIN, None)
    resp = client.get(_URL)
    assert resp.status_code == 404


def test_eligible_when_all_pass():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["eligible"] is True
    assert data["blocking_reasons"] == []
    assert data["esign"]["present"] is False


def test_not_latest_released_blocks():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=True),
        latest_exc=NotLatestReleasedError(reason="not_latest_released", target_id="item-1"),
    )
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is False
    reasons = {b["reason"] for b in data["blocking_reasons"]}
    assert "not_latest_released" in reasons


def test_suspended_blocks():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=True),
        suspended_exc=SuspendedStateError(reason="suspended", target_id="item-1"),
    )
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is False
    assert "suspended" in {b["reason"] for b in data["blocking_reasons"]}


def test_readiness_resource_errors_block():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=False, resources=[_mbom_error_resource()], error_count=1)
    )
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is False
    assert "mbom_release" in {b["reason"] for b in data["blocking_reasons"]}


def test_summary_not_ok_without_resource_errors_is_ineligible():
    # eligible follows the R1-A formula (summary.ok) DIRECTLY, not merely the
    # presence of per-resource blocking_reasons. summary.ok=false with empty
    # resources must still be ineligible.
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(_payload(ok=False, resources=[], error_count=1))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is False


def test_esign_incomplete_blocks():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=True, esign_manifest={"is_complete": False, "completed_at": None})
    )
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is False
    assert "esign" in {b["reason"] for b in data["blocking_reasons"]}
    assert data["esign"]["present"] is True
    assert data["esign"]["is_complete"] is False


def test_esign_none_not_blocked():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(_payload(ok=True, esign_manifest=None))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert "esign" not in {b["reason"] for b in data["blocking_reasons"]}
    assert data["esign"]["present"] is False


def test_warnings_only_eligible():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=True, resources=[_mbom_warning_resource()], warning_count=1)
    )
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["eligible"] is True
    assert data["blocking_reasons"] == []
    assert data["resources"][0]["diagnostics"]["warnings"][0]["code"] == "mbom_warn"


def test_ruleset_and_limits_echoed_and_passed_through():
    client = _client(_ADMIN, _item())
    patchers, svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_URL + "?ruleset_id=custom&mbom_limit=5&routing_limit=6&baseline_limit=7")
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["ruleset_id"] == "custom"
    assert data["limits"] == {"mbom_limit": 5, "routing_limit": 6, "baseline_limit": 7}
    svc.return_value.get_item_release_readiness.assert_called_once_with(
        item_id="item-1", ruleset_id="custom", mbom_limit=5, routing_limit=6, baseline_limit=7
    )


def test_unknown_ruleset_chained_400():
    client = _client(_ADMIN, _item())
    svc_p = patch(f"{_MODULE}.ReleaseReadinessService")
    svc = svc_p.start()
    svc.return_value.get_item_release_readiness.side_effect = ValueError("unknown ruleset")
    try:
        resp = client.get(_URL + "?ruleset_id=bogus")
    finally:
        svc_p.stop()
    assert resp.status_code == 400


def test_unknown_ruleset_raises_httpexception_chained_from_valueerror():
    # Call the handler directly so the HTTPException is observable (TestClient
    # converts it to a 400 response and drops __cause__). Pins the
    # `raise HTTPException(...) from exc` exception-chaining contract.
    mock_db = MagicMock()
    mock_db.get.return_value = _item()
    with patch(f"{_MODULE}.ReleaseReadinessService") as cls:
        cls.return_value.get_item_release_readiness.side_effect = ValueError("unknown ruleset")
        with pytest.raises(HTTPException) as ei:
            get_publication_readiness(
                item_id="item-1",
                ruleset_id="bogus",
                mbom_limit=20,
                routing_limit=20,
                baseline_limit=20,
                user=_ADMIN,
                db=mock_db,
            )
    assert ei.value.status_code == 400
    assert isinstance(ei.value.__cause__, ValueError)
    assert str(ei.value.__cause__) == "unknown ruleset"


def test_version_and_file_refs_from_current_version():
    cv = SimpleNamespace(
        id="ver-1", generation=2, revision="B", version_label="2.B", state="Released",
        is_current=True, is_released=True, released_at=None, primary_file_id="f1",
        version_files=[
            SimpleNamespace(file_id="f1", file_role="native_cad", is_primary=True, sequence=0, snapshot_path="/p/a.dwg"),
            SimpleNamespace(file_id="f2", file_role="preview", is_primary=False, sequence=1, snapshot_path="/p/a.png"),
        ],
    )
    client = _client(_ADMIN, _item(current_version=cv))
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["version"]["version_id"] == "ver-1"
    assert data["version"]["generation"] == 2
    assert data["version"]["primary_file_id"] == "f1"
    assert [f["file_id"] for f in data["file_refs"]] == ["f1", "f2"]
    assert data["file_refs"][0]["is_primary"] is True
    assert data["file_refs"][1]["file_role"] == "preview"


def test_current_version_none_yields_null_version_and_empty_file_refs():
    client = _client(_ADMIN, _item(current_version=None))
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["version"] is None
    assert data["file_refs"] == []


def test_response_contains_no_purchase_sale_transaction():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_URL)
    finally:
        _stop(patchers)
    body = resp.text.lower()
    assert "purchase_order" not in body
    assert "sale_order" not in body
    assert "purchase/sale" not in body


# --- R4 /publication/export (read-only pull) ---------------------------------

_EXPORT_URL = "/api/v1/plm-erp/items/item-1/publication/export"


def test_export_denies_non_admin():
    client = _client(_VIEWER, _item())
    patchers, _svc = _patched(_payload())
    try:
        resp = client.get(_EXPORT_URL)
    finally:
        _stop(patchers)
    assert resp.status_code == 403


def test_export_404_when_item_missing():
    client = _client(_ADMIN, None)
    resp = client.get(_EXPORT_URL)
    assert resp.status_code == 404


def test_export_eligible_returns_canonical_snapshot():
    cv = SimpleNamespace(
        id="ver-1", generation=2, revision="B", version_label="2.B", state="Released",
        is_current=True, is_released=True, released_at=None, primary_file_id="f1",
        version_files=[
            SimpleNamespace(file_id="f1", file_role="native_cad", is_primary=True, sequence=0, snapshot_path="/p/a.dwg"),
        ],
    )
    client = _client(_ADMIN, _item(current_version=cv))
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_EXPORT_URL)
    finally:
        _stop(patchers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["eligible"] is True
    assert data["blocking_reasons"] == []
    snap = data["snapshot"]
    assert snap is not None
    assert snap["target_system"] == ""  # target-agnostic export
    assert snap["item"]["item_id"] == "item-1"
    assert snap["version"]["version_id"] == "ver-1"
    assert snap["file_refs"][0]["file_id"] == "f1"


def test_export_ineligible_returns_null_snapshot():
    client = _client(_ADMIN, _item())
    patchers, _svc = _patched(
        _payload(ok=False, resources=[_mbom_error_resource()], error_count=1)
    )
    try:
        resp = client.get(_EXPORT_URL)
    finally:
        _stop(patchers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["eligible"] is False
    assert data["snapshot"] is None  # nothing publishable -> null, not 4xx
    assert "mbom_release" in {b["reason"] for b in data["blocking_reasons"]}


def test_export_unknown_ruleset_400():
    client = _client(_ADMIN, _item())
    svc_p = patch(f"{_MODULE}.ReleaseReadinessService")
    svc = svc_p.start()
    svc.return_value.get_item_release_readiness.side_effect = ValueError("unknown ruleset")
    try:
        resp = client.get(_EXPORT_URL + "?ruleset_id=bogus")
    finally:
        svc_p.stop()
    assert resp.status_code == 400


def test_export_publication_kind_stamped_in_snapshot():
    client = _client(_ADMIN, _item(current_version=None))
    patchers, _svc = _patched(_payload(ok=True))
    try:
        resp = client.get(_EXPORT_URL + "?publication_kind=package")
    finally:
        _stop(patchers)
    data = resp.json()
    assert data["snapshot"]["publication_kind"] == "package"
